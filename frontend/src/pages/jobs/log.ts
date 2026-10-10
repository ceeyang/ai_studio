const TRACE_HEAD = 'Traceback (most recent call last)';

/**
 * 把日志里 Python 的调用栈收起来：「Traceback …」那一行和下面缩进的几行换成一句提示，异常那一行（不缩进的）留着。
 * 完整日志另外放在折叠里。
 */
export function splitTraceback(log: string): { brief: string; hasTrace: boolean } {
  if (!log.includes(TRACE_HEAD)) return { brief: log, hasTrace: false };
  const lines = log.split('\n');
  const out: string[] = [];
  for (let i = 0; i < lines.length; i += 1) {
    if (!lines[i].includes(TRACE_HEAD)) {
      out.push(lines[i]);
      continue;
    }
    out.push('（调用栈已收进「完整日志」）');
    while (i + 1 < lines.length && /^\s+\S/.test(lines[i + 1])) i += 1;
  }
  return { brief: out.join('\n'), hasTrace: true };
}
