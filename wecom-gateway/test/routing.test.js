import assert from "node:assert/strict";
import test from "node:test";

import { routeTextMessage } from "../src/routing.js";

test("普通文字默认进入AI员工平台", () => {
  assert.deepEqual(routeTextMessage("  你能提供哪些帮助  "), {
    kind: "platform",
    content: "你能提供哪些帮助",
  });
});

test("状态命令进入健康检查", () => {
  assert.deepEqual(routeTextMessage("/状态"), { kind: "health", content: "" });
});

test("echo命令只用于连接测试", () => {
  assert.deepEqual(routeTextMessage("/echo 测试123"), {
    kind: "echo",
    content: "测试123",
  });
});

test("继续兼容原有平台命令前缀", () => {
  assert.deepEqual(routeTextMessage("/平台 查询VPN流程"), {
    kind: "platform",
    content: "查询VPN流程",
  });
});
