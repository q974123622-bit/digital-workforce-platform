/**
 * 后端时间戳的统一解析。
 *
 * 后端存的是 `datetime.now()` 的 naive 值（容器时区为 UTC），序列化后形如
 * `2026-09-16T01:35:15.879767` —— **不带时区标识**。而 ECMAScript 规定这类
 * 字符串按**浏览器本地时区**解析，于是每个服务端时间戳都偏移了一个时区差
 * （UTC+8 环境下偏 8 小时）。
 *
 * 单独显示时间时看不出问题（差 8 小时而已），但只要与服务端其它时间戳或客户端
 * `Date.now()` 混算就会得到离谱结果：
 * - 执行耗时显示 28842 秒（= 8 小时 + 真实耗时）：`started_at` 来自服务端、
 *   `finishedAt` 来自 `Date.now()`；
 * - 轨迹里第一步耗时恒为 0.0s：`lastStepAt` 用客户端时钟，事件 `created_at`
 *   来自服务端，相减为负被 `Math.max(0, ...)` 夹成 0。
 *
 * 这里统一按 UTC 解析：无时区标识就补 `Z`。若将来后端改为输出带时区的 ISO 串，
 * 本函数会原样透传，仍然正确。
 */
export function parseServerTime(value?: string | number | null): number | undefined {
  if (value === null || value === undefined || value === '') return undefined;
  if (typeof value === 'number') return Number.isFinite(value) ? value : undefined;
  const text = String(value).trim();
  // 已带时区标识（Z / +08:00 / -0500）就原样交给 Date，否则按 UTC 解析。
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(text);
  const ms = Date.parse(hasZone ? text : `${text}Z`);
  return Number.isNaN(ms) ? undefined : ms;
}

/** 把服务端时间戳格式化为浏览器本地时间的可读文本。 */
export function formatServerTime(
  value?: string | number | null,
  options?: Intl.DateTimeFormatOptions,
): string {
  const ms = parseServerTime(value);
  return ms === undefined ? '—' : new Date(ms).toLocaleString('zh-CN', options);
}

/** 服务端时间戳 -> 浏览器本地时间的 `Date`（解析失败返回 undefined）。 */
export function serverDate(value?: string | number | null): Date | undefined {
  const ms = parseServerTime(value);
  return ms === undefined ? undefined : new Date(ms);
}
