export function routeTextMessage(rawContent) {
  const content = rawContent?.trim() ?? "";

  if (!content) return { kind: "empty", content: "" };
  if (content === "/状态") return { kind: "health", content: "" };
  if (content === "/echo") return { kind: "echo", content: "" };
  if (content.startsWith("/echo ")) {
    return { kind: "echo", content: content.slice("/echo".length).trim() };
  }

  // 兼容联调阶段的 /平台 前缀；正常聊天不再要求此前缀。
  if (content === "/平台") return { kind: "platform", content: "" };
  if (content.startsWith("/平台 ")) {
    return { kind: "platform", content: content.slice("/平台".length).trim() };
  }

  return { kind: "platform", content };
}
