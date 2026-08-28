const DEFAULT_PLATFORM_BASE_URL = "http://127.0.0.1:8000";

export function platformBaseUrl() {
  return (
    process.env.PLATFORM_BASE_URL?.trim().replace(/\/+$/, "") ||
    DEFAULT_PLATFORM_BASE_URL
  );
}

export function platformRoutingConfig() {
  return {
    actorNo: process.env.PLATFORM_ACTOR_NO?.trim() || "E10281",
    employeeNo: process.env.PLATFORM_EMPLOYEE_NO?.trim() || "VE-0003",
  };
}

async function platformRequest(
  path,
  { baseUrl = platformBaseUrl(), fetchImpl = fetch, timeoutMs = 10_000, ...options } = {},
) {
  const requestUrl = new URL(path, `${baseUrl}/`);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetchImpl(requestUrl, {
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
      signal: controller.signal,
    });

    let payload;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }

    if (!response.ok) {
      const detail = payload?.error?.message || payload?.detail;
      throw new Error(detail || `平台接口返回 HTTP ${response.status}`);
    }

    return payload;
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error(`调用平台超时（${timeoutMs}ms）`, { cause: error });
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

export async function createOrReuseDirectConversation({
  actorNo,
  employeeNo,
  baseUrl,
  fetchImpl,
} = {}) {
  return platformRequest("/api/v1/conversations", {
    baseUrl,
    fetchImpl,
    method: "POST",
    body: JSON.stringify({
      actor_no: actorNo,
      kind: "direct",
      participant_employee_nos: [employeeNo],
    }),
  });
}

export async function sendPlatformConversationMessage({
  conversationId,
  actorNo,
  content,
  baseUrl,
  fetchImpl,
} = {}) {
  return platformRequest(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`,
    {
      baseUrl,
      fetchImpl,
      method: "POST",
      body: JSON.stringify({ actor_no: actorNo, content }),
    },
  );
}

export async function getPlatformConversation({
  conversationId,
  baseUrl,
  fetchImpl,
} = {}) {
  return platformRequest(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}`,
    { baseUrl, fetchImpl },
  );
}

export async function waitForAssistantReply({
  conversationId,
  afterSeq,
  baseUrl,
  fetchImpl,
  pollIntervalMs = 2_500,
  maxWaitMs = 60_000,
  sleep = (delay) => new Promise((resolve) => setTimeout(resolve, delay)),
} = {}) {
  const maxAttempts = Math.max(1, Math.ceil(maxWaitMs / pollIntervalMs));

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const conversation = await getPlatformConversation({
      conversationId,
      baseUrl,
      fetchImpl,
    });
    const reply = (conversation.messages || []).find(
      (message) => message.role === "assistant" && message.seq > afterSeq,
    );

    if (reply) {
      return reply;
    }

    if (attempt < maxAttempts - 1) {
      await sleep(pollIntervalMs);
    }
  }

  throw new Error(`等待AI员工回复超时（${Math.ceil(maxWaitMs / 1_000)}秒）`);
}

export async function checkPlatformHealth({
  baseUrl = platformBaseUrl(),
  fetchImpl = fetch,
  timeoutMs = 5_000,
} = {}) {
  const payload = await platformRequest("/health", {
    baseUrl,
    fetchImpl,
    timeoutMs,
  });

  if (payload?.status !== "ok") {
    throw new Error("健康检查响应中的 status 不是 ok");
  }

  return {
    service: payload.service || "digital-workforce-platform",
    url: new URL("/health", `${baseUrl}/`).toString(),
  };
}
