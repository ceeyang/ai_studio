import { describe, expect, it } from 'vitest';
import { fmtDuration, fmtTime, jobStatusLabel, jobTypeLabel } from './format';

describe('format', () => {
  it('任务类型和状态的中文名，没见过的原样返回', () => {
    expect(jobTypeLabel('video.shot')).toBe('视频');
    expect(jobTypeLabel('upscale')).toBe('upscale');
    expect(jobStatusLabel('waiting_input')).toBe('等待输入');
  });

  it('耗时：不到一分钟按秒，超过按分秒；没有时区的时间按 UTC', () => {
    expect(fmtDuration('2026-10-08T10:00:00Z', '2026-10-08T10:01:05Z')).toBe('1 分 5 秒');
    expect(fmtDuration('2026-10-08T10:00:00', '2026-10-08T10:00:30Z')).toBe('30 秒');
    expect(fmtDuration(null, null)).toBe('—');
    expect(fmtDuration('2026-10-08T10:00:00Z', null, Date.parse('2026-10-08T10:00:10Z'))).toBe('10 秒');
  });

  it('时间为空时显示占位符', () => {
    expect(fmtTime(null)).toBe('—');
    expect(fmtTime('2026-10-08T10:00:00Z')).not.toBe('—');
  });
});
