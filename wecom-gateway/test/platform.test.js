import assert from "node:assert/strict";
import test from "node:test";

import {
  checkPlatformHealth,
  createOrReuseDirectConversation,
  sendPlatformConversationMessage,
  waitForAssistantReply,
} from "../src/platform.js";

test("平台健康检查成功时返回服务信息", async () => {
  const result = await checkPlatformHealth({
    baseUrl: "http://platform.test:8000",
    fetchImpl: async (url) => {
      assert.equal(url.toString(), "http://platform.test:8000/health");
      return new Response(
        JSON.stringify({
          status: "ok",
          service: "digital-workforce-platform",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    },
  });

  assert.deepEqual(result, {
    service: "digital-workforce-platform",
    url: "http://platform.test:8000/health",
  });
});

test("平台返回非成功状态码时报告错误", async () => {
  await assert.rejects(
    checkPlatformHealth({
      baseUrl: "http://platform.test:8000",
      fetchImpl: async () => new Response(null, { status: 503 }),
    }),
    /HTTP 503/,
  );
});

test("平台响应不是健康状态时报告错误", async () => {
  await assert.rejects(
    checkPlatformHealth({
      baseUrl: "http://platform.test:8000",
      fetchImpl: async () =>
        new Response(JSON.stringify({ status: "starting" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    }),
    /status 不是 ok/,
  );
});

test("创建私聊会话时发送固定身份和目标员工", async () => {
  const conversation = await createOrReuseDirectConversation({
    actorNo: "E10281",
    employeeNo: "VE-0003",
    baseUrl: "http://platform.test:8000",
    fetchImpl: async (url, options) => {
      assert.equal(url.toString(), "http://platform.test:8000/api/v1/conversations");
      assert.equal(options.method, "POST");
      assert.deepEqual(JSON.parse(options.body), {
        actor_no: "E10281",
        kind: "direct",
        participant_employee_nos: ["VE-0003"],
      });
      return Response.json({ id: "CONV-TEST" });
    },
  });

  assert.equal(conversation.id, "CONV-TEST");
});

test("向指定平台会话写入员工消息", async () => {
  const conversation = await sendPlatformConversationMessage({
    conversationId: "CONV/TEST",
    actorNo: "E10281",
    content: "测试消息",
    baseUrl: "http://platform.test:8000",
    fetchImpl: async (url, options) => {
      assert.equal(
        url.toString(),
        "http://platform.test:8000/api/v1/conversations/CONV%2FTEST/messages",
      );
      assert.deepEqual(JSON.parse(options.body), {
        actor_no: "E10281",
        content: "测试消息",
      });
      return Response.json({
        id: "CONV/TEST",
        messages: [{ role: "user", content: "测试消息", seq: 3 }],
      });
    },
  });

  assert.equal(conversation.messages[0].seq, 3);
});

test("轮询平台会话直到出现指定消息之后的AI回复", async () => {
  let calls = 0;
  const reply = await waitForAssistantReply({
    conversationId: "CONV-TEST",
    afterSeq: 3,
    baseUrl: "http://platform.test:8000",
    pollIntervalMs: 10,
    maxWaitMs: 30,
    sleep: async () => {},
    fetchImpl: async (url) => {
      calls += 1;
      assert.equal(
        url.toString(),
        "http://platform.test:8000/api/v1/conversations/CONV-TEST",
      );
      return Response.json({
        id: "CONV-TEST",
        messages:
          calls === 1
            ? [{ role: "user", content: "测试消息", seq: 3 }]
            : [
                { role: "user", content: "测试消息", seq: 3 },
                { role: "assistant", content: "处理完成", seq: 4 },
              ],
      });
    },
  });

  assert.equal(calls, 2);
  assert.equal(reply.content, "处理完成");
  assert.equal(reply.seq, 4);
});

test("规定时间内没有AI回复时报告超时", async () => {
  await assert.rejects(
    waitForAssistantReply({
      conversationId: "CONV-TEST",
      afterSeq: 3,
      baseUrl: "http://platform.test:8000",
      pollIntervalMs: 10,
      maxWaitMs: 20,
      sleep: async () => {},
      fetchImpl: async () =>
        Response.json({
          id: "CONV-TEST",
          messages: [{ role: "user", content: "测试消息", seq: 3 }],
        }),
    }),
    /等待AI员工回复超时（1秒）/,
  );
});
