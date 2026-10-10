import { describe, expect, it } from 'vitest';
import { splitTraceback } from './log';

describe('splitTraceback', () => {
  it('没有调用栈：原样返回', () => {
    expect(splitTraceback('[10:00] 开始\n[10:01] 完成\n')).toEqual({ brief: '[10:00] 开始\n[10:01] 完成\n', hasTrace: false });
  });

  it('调用栈收起来，只留异常那一行和前后的日志', () => {
    const log = [
      '[10:00] 视频 S01',
      '[10:01] Traceback (most recent call last):',
      '  File "a.py", line 3, in run',
      '    do()',
      '  File "b.py", line 9, in do',
      '    raise RuntimeError("显存不足")',
      'RuntimeError: 显存不足',
      '[10:02] 任务失败',
    ].join('\n');
    const r = splitTraceback(log);
    expect(r.hasTrace).toBe(true);
    expect(r.brief).toBe(['[10:00] 视频 S01', '（调用栈已收进「完整日志」）', 'RuntimeError: 显存不足', '[10:02] 任务失败'].join('\n'));
  });
});
