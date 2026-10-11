import { useEffect, useState } from 'react';

/** 当前时间，enabled 时每 intervalMs 走一次（运行中的耗时、相对时间实时刷新）。 */
export function useNow(enabled: boolean, intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [enabled, intervalMs]);
  return now;
}
