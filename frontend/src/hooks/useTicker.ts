import { useEffect, useState } from 'react';

/**
 * 运行中的计时器：每 `intervalMs` 返回一次新的 `Date.now()`，用于让「进行中」
 * 的耗时实时增长，而不是等下一个事件到达时才跳一次。
 *
 * `active` 为 false 时不启动定时器，组件保持静态（已完成的历史轨迹不再重渲染）。
 */
export function useTicker(active: boolean, intervalMs = 200): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [active, intervalMs]);
  return now;
}
