import AiBot, { generateReqId } from "@wecom/aibot-node-sdk";

import {
  checkPlatformHealth,
  createOrReuseDirectConversation,
  platformBaseUrl,
  platformRoutingConfig,
  sendPlatformConversationMessage,
  waitForAssistantReply,
} from "./platform.js";
import { routeTextMessage } from "./routing.js";

const botId = process.env.WECOM_BOT_ID?.trim();
const secret = process.env.WECOM_BOT_SECRET?.trim();
const testUserId = process.env.WECOM_TEST_USER_ID?.trim();

if (!botId || !secret) {
  console.error(
    "缺少企微机器人凭证。请复制 .env.example 为 .env，并填写 WECOM_BOT_ID 和 WECOM_BOT_SECRET。",
  );
  process.exit(1);
}

const sdkLogger = {
  // DEBUG 会包含 userid、msgid 和临时 response_url，默认关闭。
  debug: () => {},
  info: (message, ...args) => console.log(`[AiBotSDK] ${message}`, ...args),
  warn: (message, ...args) => console.warn(`[AiBotSDK] ${message}`, ...args),
  error: (message, ...args) => console.error(`[AiBotSDK] ${message}`, ...args),
};

const client = new AiBot.WSClient({
  botId,
  secret,
  maxReconnectAttempts: -1,
  logger: sdkLogger,
});

client.on("connected", () => {
  console.log("[企微] WebSocket 已连接，正在认证……");
});

client.on("authenticated", () => {
  console.log(
    "[企微] 认证成功。普通文字进入AI员工平台；/状态 检查后端；/echo <文字> 测试连接。",
  );
});

client.on("message.text", async (frame) => {
  const route = routeTextMessage(frame.body.text?.content);
  console.log(`[企微] 收到文字消息，路由：${route.kind}`);

  try {
    if (route.kind === "empty") {
      await client.replyStream(frame, generateReqId("empty"), "请输入文字消息。", true);
      return;
    }

    if (route.kind === "health") {
      console.log(`[平台] 正在检查 ${platformBaseUrl()}/health`);
      try {
        const result = await checkPlatformHealth();
        await client.replyStream(
          frame,
          generateReqId("health"),
          `AI员工平台后端正常\n服务：${result.service}`,
          true,
        );
        console.log("[平台] 健康检查通过");
      } catch (error) {
        await client.replyStream(
          frame,
          generateReqId("health"),
          `AI员工平台后端不可用\n原因：${error.message}`,
          true,
        );
        console.error("[平台] 健康检查失败：", error.message);
      }
      return;
    }

    if (route.kind === "echo") {
      const echoContent = route.content || "请在 /echo 后输入要测试的文字。";
      await client.replyStream(
        frame,
        generateReqId("echo"),
        `连接测试成功，我收到了：${echoContent}`,
        true,
      );
      console.log("[企微] Echo 回复已发送");
      return;
    }

    if (!route.content) {
      await client.replyStream(
        frame,
        generateReqId("platform"),
        "请输入要交给AI员工处理的消息。",
        true,
      );
      return;
    }

    if (!testUserId) {
      await client.replyStream(
        frame,
        generateReqId("platform"),
        "平台通道尚未配置测试用户，请先设置 WECOM_TEST_USER_ID。",
        true,
      );
      console.warn("[平台] 拒绝处理：未配置 WECOM_TEST_USER_ID");
      return;
    }

    const senderUserId = frame.body.from?.userid?.trim();
    if (!senderUserId || senderUserId !== testUserId) {
      await client.replyStream(
        frame,
        generateReqId("platform"),
        "当前账号未被授权使用AI员工平台测试通道。",
        true,
      );
      console.warn("[平台] 拒绝处理：发送者不在测试白名单");
      return;
    }

    const { actorNo, employeeNo } = platformRoutingConfig();
    const streamId = generateReqId("platform");
    console.log(`[平台] 正在创建或复用会话：${actorNo} -> ${employeeNo}`);

    try {
      const conversation = await createOrReuseDirectConversation({ actorNo, employeeNo });
      const updatedConversation = await sendPlatformConversationMessage({
        conversationId: conversation.id,
        actorNo,
        content: route.content,
      });
      const userMessage = [...(updatedConversation.messages || [])]
        .reverse()
        .find((message) => message.role === "user");

      if (!userMessage) {
        throw new Error("平台未返回刚写入的用户消息");
      }

      await client.replyStream(
        frame,
        streamId,
        `消息已进入AI员工平台，${employeeNo} 正在处理……`,
        false,
      );
      console.log(
        `[平台] 消息已写入会话 ${conversation.id}，等待序号 ${userMessage.seq} 之后的AI回复`,
      );

      const assistantReply = await waitForAssistantReply({
        conversationId: conversation.id,
        afterSeq: userMessage.seq,
      });
      await client.replyStream(
        frame,
        streamId,
        assistantReply.content || "AI员工已完成处理，但没有返回文字内容。",
        true,
      );
      console.log(
        `[平台] 已将AI回复返回企微：会话 ${conversation.id}，消息序号 ${assistantReply.seq}`,
      );
    } catch (error) {
      await client.replyStream(
        frame,
        streamId,
        `AI员工处理失败\n原因：${error.message}`,
        true,
      );
      console.error("[平台] AI员工处理失败：", error.message);
    }
  } catch (error) {
    console.error("[企微] 消息处理失败：", error);
  }
});

client.on("reconnecting", (attempt) => {
  console.log(`[企微] 连接中断，正在进行第 ${attempt} 次重连……`);
});

client.on("disconnected", (reason) => {
  console.warn(`[企微] 连接已断开：${reason}`);
});

client.on("error", (error) => {
  console.error("[企微] 连接错误：", error);
});

function shutdown(signal) {
  console.log(`\n[企微] 收到 ${signal}，正在关闭连接……`);
  client.disconnect();
  process.exit(0);
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));

console.log("[企微] 正在启动AI员工平台企微渠道……");
console.log(`[平台] 后端地址：${platformBaseUrl()}`);
client.connect();
