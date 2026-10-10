import { describe, expect, it } from 'vitest';
import { makeJob } from '../../test/factories';
import { avgSeconds, estimateText, etaText } from './estimate';

function done(id: number, type: string, seconds: number, finished = `2026-10-10T10:${String(id).padStart(2, '0')}:00Z`) {
  const end = new Date(finished).getTime();
  return makeJob({
    id, type, status: 'done',
    started_at: new Date(end - seconds * 1000).toISOString(),
    finished_at: finished,
  });
}

describe('estimate', () => {
  it('同类型最近 20 条的平均耗时；没有样本是 null', () => {
    const jobs = [done(1, 'image.keyframe', 50), done(2, 'image.keyframe', 66), done(3, 'video.shot', 300)];
    expect(avgSeconds(jobs, 'image.keyframe')).toBe(58);
    expect(avgSeconds(jobs, 'video.shot')).toBe(300);
    expect(avgSeconds(jobs, 'export.episode')).toBeNull();
    // 只算最近 20 条：最早那条 1000 秒不计
    const many = [done(1, 'image.keyframe', 1000), ...Array.from({ length: 20 }, (_, i) => done(i + 2, 'image.keyframe', 10))];
    expect(avgSeconds(many, 'image.keyframe')).toBe(10);
    // 没有开始/结束时间的不算
    expect(avgSeconds([makeJob({ type: 'image.keyframe', status: 'done' })], 'image.keyframe')).toBeNull();
  });

  it('一种任务：任务数、平均耗时、总时长', () => {
    const jobs = [done(1, 'image.keyframe', 50), done(2, 'image.keyframe', 66)];
    expect(estimateText([{ type: 'image.keyframe', n: 12 }], jobs)).toBe(
      '将提交 12 个首帧任务，按最近同类任务平均 58 秒，约 12 分钟，会占用显卡',
    );
    expect(estimateText([{ type: 'image.keyframe', n: 4 }], [done(1, 'image.keyframe', 10)])).toBe(
      '将提交 4 个首帧任务，按最近同类任务平均 10 秒，不到 1 分钟，会占用显卡',
    );
    expect(estimateText([{ type: 'video.shot', n: 5 }], [done(1, 'video.shot', 125)])).toBe(
      '将提交 5 个视频任务，按最近同类任务平均 2 分 5 秒，约 10 分钟，会占用显卡',
    );
  });

  it('没有样本写「耗时未知」；两种任务合在一起算', () => {
    expect(estimateText([{ type: 'image.keyframe', n: 12 }], [])).toBe('将提交 12 个首帧任务，耗时未知，会占用显卡');
    const jobs = [done(1, 'image.keyframe', 60), done(2, 'video.shot', 240)];
    expect(
      estimateText(
        [
          { type: 'image.keyframe', n: 3 },
          { type: 'video.shot', n: 2 },
        ],
        jobs,
      ),
    ).toBe('将提交 3 个首帧任务、2 个视频任务，按最近同类任务平均算约 11 分钟，会占用显卡');
    expect(
      estimateText(
        [
          { type: 'image.keyframe', n: 3 },
          { type: 'video.shot', n: 2 },
        ],
        [done(1, 'image.keyframe', 60)],
      ),
    ).toBe('将提交 3 个首帧任务、2 个视频任务，耗时未知，会占用显卡');
  });

  it('etaText：阶段条上的一句预计耗时；没有样本写「耗时未知」', () => {
    const jobs = [done(1, 'image.keyframe', 50), done(2, 'image.keyframe', 66), done(3, 'video.shot', 300)];
    expect(etaText([{ type: 'image.keyframe', n: 4 }], jobs)).toBe('约 4 分钟');
    expect(etaText([{ type: 'image.keyframe', n: 1 }], jobs)).toBe('不到 1 分钟');
    expect(etaText([{ type: 'video.shot', n: 30 }], jobs)).toBe('约 2.5 小时');
    expect(etaText([{ type: 'image.keyframe', n: 2 }, { type: 'video.shot', n: 2 }], jobs)).toBe('约 12 分钟');
    expect(etaText([{ type: 'export.episode', n: 1 }], jobs)).toBe('耗时未知');
  });
});
