// 接上一镜（D）：卡片按钮、首帧确认门怎么对待「等上一镜视频」的镜头，出视频时整条链合成一批。
import { describe, expect, it } from 'vitest';
import type { Slot } from '../../api/types';
import { makeCandidate, makeShot, makeSlot } from '../../test/factories';
import { cardAction, failedJob, firstFrameReady, waitsPrevVideo } from '../director/shotStatus';
import { shotGate, videoReady } from './shotGate';

function chosen(id: number, kind: string, stale = false): Slot {
  return makeSlot({ id, kind, selected_candidate_id: id * 10, stale, candidates: [makeCandidate({ id: id * 10 })] });
}
const kf = (id: number) => chosen(id, 'first_frame');
const vid = (id: number, stale = false) => chosen(id + 100, 'video', stale);
const none = new Set<number>();

/** S01 普通镜头；S02、S03 接上一镜 */
function chain(opts: { v1?: boolean; v2?: boolean } = {}) {
  return [
    makeShot({ id: 1, code: 'S01', order: 0, slots: [kf(1), ...(opts.v1 ? [vid(1)] : [])] }),
    makeShot({
      id: 2,
      code: 'S02',
      order: 1,
      continue_from_prev: true,
      continue_prev: { shot_id: 1, code: 'S01', has_video: !!opts.v1 },
      slots: opts.v2 ? [vid(2)] : [],
    }),
    makeShot({
      id: 3,
      code: 'S03',
      order: 2,
      continue_from_prev: true,
      continue_prev: { shot_id: 2, code: 'S02', has_video: !!opts.v2 },
    }),
  ];
}

describe('接上一镜：镜头状态', () => {
  it('上一镜没有视频：等上一镜视频（首帧、视频两格都等），不算失败', () => {
    const s = chain()[1];
    expect(waitsPrevVideo(s)).toBe(true);
    expect(firstFrameReady(s)).toBe(false);
    expect(cardAction(s, 'keyframes')).toEqual({ kind: 'wait_prev', label: '等上一镜视频', aria: '等上一镜视频' });
    expect(cardAction(s, 'videos').kind).toBe('wait_prev');
    const failed = { ...s, failures: [{ id: 5, type: 'image.continue_frame', status: 'failed' as const, error: '上一镜 S01 还没有选定视频' }] };
    expect(failedJob(failed, 'keyframes')).toBeUndefined();
  });

  it('上一镜有视频：首帧没取也能直接出视频（后端先截尾帧）', () => {
    const s = chain({ v1: true })[1];
    expect(waitsPrevVideo(s)).toBe(false);
    expect(firstFrameReady(s)).toBe(true);
    expect(cardAction(s, 'videos')).toMatchObject({ kind: 'generate', label: '生成视频' });
    expect(cardAction(s, 'keyframes')).toMatchObject({ kind: 'generate', label: '生成首帧' });
  });

  it('截尾帧任务在跑：首帧这一格显示生成中', () => {
    const s = { ...chain({ v1: true })[1], active_jobs: [{ id: 7, type: 'image.continue_frame', status: 'running' as const }] };
    expect(cardAction(s, 'keyframes').kind).toBe('busy');
  });

  it('第一镜开了也按普通镜头算', () => {
    const s = makeShot({ id: 1, continue_from_prev: true, continue_prev: null, slots: [kf(1)] });
    expect(waitsPrevVideo(s)).toBe(false);
    expect(firstFrameReady(s)).toBe(true);
  });
});

describe('接上一镜：首帧确认门', () => {
  it('能出视频的镜头沿着链传下去', () => {
    expect([...videoReady(chain())]).toEqual([1, 2, 3]);
    const noKf = chain();
    noKf[0] = makeShot({ id: 1, code: 'S01' });
    expect([...videoReady(noKf)]).toEqual([]);
  });

  it('首帧阶段：等上一镜视频的不算缺首帧，直接到「确认首帧，生成全部视频」', () => {
    const g = shotGate(chain(), 'keyframes', none, 1, []);
    expect(g).toMatchObject({ kind: 'run', batches: [{ stage: 'videos', ids: [1, 2, 3], force: false }], switchTo: 'videos' });
    expect(g?.kind === 'run' && g.label).toMatch(/^确认首帧，生成全部视频（3 镜/);
  });

  it('视频阶段：有接上一镜的又有过期要强制的，合成一批（后端才能按顺序串起来）', () => {
    const shots = chain();
    shots[0] = makeShot({ id: 1, code: 'S01', slots: [kf(1), vid(1, true)] });
    shots[1] = { ...shots[1], continue_prev: { shot_id: 1, code: 'S01', has_video: true } };
    const g = shotGate(shots, 'videos', none, 1, []);
    expect(g).toMatchObject({ kind: 'run', batches: [{ stage: 'videos', ids: [2, 3, 1], force: true }] });
  });

  it('勾选首帧阶段：等上一镜视频的先跳过', () => {
    const shots = chain({ v1: true });
    const g = shotGate(shots, 'keyframes', new Set([2, 3]), 1, []);
    expect(g).toMatchObject({ kind: 'run', batches: [{ stage: 'keyframes', ids: [2], force: false }] });
    expect(g?.kind === 'run' && g.note).toContain('另有 1 镜接上一镜、上一镜还没有视频，先跳过');
    expect(shotGate(chain(), 'keyframes', new Set([2]), 1, [])).toEqual({ kind: 'busy', label: '等上一镜视频（选中的 1 镜接上一镜）' });
  });
});
