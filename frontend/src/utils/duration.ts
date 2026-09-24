/**
 * 耗时格式化：短耗时保留一位小数（`7.7s`），超过一分钟改用 `1m23s`，
 * 避免出现 `28842 秒` 这类无法阅读的数字。
 */
export function formatDuration(ms: number): string {
  const safe = Number.isFinite(ms) && ms > 0 ? ms : 0;
  const seconds = safe / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) return `${minutes}m${rest}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h${minutes % 60}m`;
}
