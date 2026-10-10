import { describe, expect, it } from 'vitest';
import type { Slot } from '../../api/types';
import { makeCandidate, makeJob, makeShot, makeSlot } from '../../test/factories';
import { shotGate } from './shotGate';

function chosen(id: number, kind: string, stale = false): Slot {
  return makeSlot({ id, kind, selected_candidate_id: id * 10, stale, candidates: [makeCandidate({ id: id * 10 })] });
}

function doneJob(id: number, type: string, seconds: number) {
  const end = Date.UTC(2026, 9, 10, 10, id);
  return makeJob({ id, type, status: 'done', started_at: new Date(end - seconds * 1000).toISOString(), finished_at: new Date(end).toISOString() });
}

const JOBS = [doneJob(1, 'image.keyframe', 60), doneJob(2, 'video.shot', 300)];
const none = new Set<number>();

const kf = (id: number, stale = false) => chosen(id, 'first_frame', stale);
const vid = (id: number, stale = false) => chosen(id + 100, 'video', stale);

describe('shotGate', () => {
  it('没有镜头：null（阶段条用原来的下一步）', () => {
    expect(shotGate([], 'keyframes', none, 1, JOBS)).toBeNull();
  });

  it('首帧阶段有缺少 / 过期的首帧：只补这些，缺的不强制、过期的强制；预计耗时按候选数算', () => {
    const shots = [
      makeShot({ id: 1, slots: [kf(1)] }),
      makeShot({ id: 2 }),
      makeShot({ id: 3, slots: [kf(3, true)] }),
      // 正在生成的不再提交
      makeShot({ id: 4, active_jobs: [{ id: 9, type: 'image.keyframe', status: 'queued' }] }),
    ];
    const g = shotGate(shots, 'keyframes', none, 2, JOBS);
    expect(g).toMatchObject({
      kind: 'run',
      label: '生成缺少/过期的首帧（2 张，约 4 分钟）',
      batches: [
        { stage: 'keyframes', ids: [2], force: false },
        { stage: 'keyframes', ids: [3], force: true },
      ],
      parts: [{ type: 'image.keyframe', n: 4 }],
      confirm: true,
    });
    expect(g?.kind === 'run' && g.note).toContain('1 张已过期');
    expect(g?.kind === 'run' && g.switchTo).toBeUndefined();
  });

  it('少于等于 3 个任务不弹确认；没有样本写耗时未知', () => {
    const g = shotGate([makeShot({ id: 2 })], 'keyframes', none, 1, []);
    expect(g).toMatchObject({ kind: 'run', label: '生成缺少/过期的首帧（1 张，耗时未知）', confirm: false });
  });

  it('首帧都就绪：确认首帧，生成全部视频（切到视频阶段），缺的不强制、过期的强制', () => {
    const shots = [
      makeShot({ id: 1, slots: [kf(1)] }),
      makeShot({ id: 2, slots: [kf(2), vid(2, true)] }),
      makeShot({ id: 3, slots: [kf(3), vid(3)] }),
    ];
    expect(shotGate(shots, 'keyframes', none, 3, JOBS)).toMatchObject({
      kind: 'run',
      label: '确认首帧，生成全部视频（2 镜，约 10 分钟）',
      switchTo: 'videos',
      batches: [
        { stage: 'videos', ids: [1], force: false },
        { stage: 'videos', ids: [2], force: true },
      ],
      parts: [{ type: 'video.shot', n: 2 }],
    });
  });

  it('首帧还在生成、没有别的可补：等它生成完（按钮禁用）', () => {
    const shots = [makeShot({ id: 1, slots: [kf(1)] }), makeShot({ id: 2, active_jobs: [{ id: 9, type: 'image.keyframe', status: 'running' }] })];
    expect(shotGate(shots, 'keyframes', none, 1, JOBS)).toEqual({ kind: 'busy', label: '等首帧生成完（1 镜生成中）' });
  });

  it('视频阶段：只给首帧就绪的镜头补视频，没首帧的先跳过并说明', () => {
    const shots = [makeShot({ id: 1, slots: [kf(1)] }), makeShot({ id: 2 }), makeShot({ id: 3, slots: [kf(3), vid(3)] })];
    const g = shotGate(shots, 'videos', none, 1, JOBS);
    expect(g).toMatchObject({
      kind: 'run',
      label: '生成缺少/过期的视频（1 个，约 5 分钟）',
      batches: [{ stage: 'videos', ids: [1], force: false }],
    });
    expect(g?.kind === 'run' && g.note).toContain('1 镜还没有可用的首帧');
  });

  it('视频阶段只剩没首帧的：去首帧阶段先补齐', () => {
    const shots = [makeShot({ id: 1, slots: [kf(1, true)] }), makeShot({ id: 3, slots: [kf(3), vid(3)] })];
    expect(shotGate(shots, 'videos', none, 1, JOBS)).toEqual({ kind: 'goto', label: '先补齐首帧（1 镜缺少或过期）', stage: 'keyframes' });
  });

  it('全部就绪：去成片（两个阶段都是）', () => {
    const shots = [makeShot({ id: 3, slots: [kf(3), vid(3)] })];
    expect(shotGate(shots, 'videos', none, 1, JOBS)).toEqual({ kind: 'goto', label: '去成片', stage: 'export' });
    expect(shotGate(shots, 'keyframes', none, 1, JOBS)).toEqual({ kind: 'goto', label: '去成片', stage: 'export' });
  });

  it('多选：都还没有结果 → 生成（不强制）；总是先确认', () => {
    const shots = [makeShot({ id: 1 }), makeShot({ id: 2 }), makeShot({ id: 3, slots: [kf(3)] })];
    expect(shotGate(shots, 'keyframes', new Set([1, 2]), 1, JOBS)).toMatchObject({
      kind: 'run',
      label: '对选中的 2 镜生成…',
      title: '对选中的 2 镜生成首帧？',
      batches: [{ stage: 'keyframes', ids: [1, 2], force: false }],
      confirm: true,
      selection: true,
    });
  });

  it('多选：都已有结果 → 重拍（强制）', () => {
    const shots = [makeShot({ id: 1, slots: [kf(1)] }), makeShot({ id: 2, slots: [kf(2, true)] })];
    const g = shotGate(shots, 'keyframes', new Set([1, 2]), 1, JOBS);
    expect(g).toMatchObject({
      label: '对选中的 2 镜重拍…',
      title: '对选中的 2 镜重拍首帧？',
      batches: [{ stage: 'keyframes', ids: [1, 2], force: true }],
    });
  });

  it('多选：有的有结果有的没有 → 缺的生成（不强制）、有的重拍（强制）两批，确认里写清楚', () => {
    const shots = [makeShot({ id: 1, slots: [kf(1), vid(1)] }), makeShot({ id: 2, slots: [kf(2)] }), makeShot({ id: 3 })];
    const g = shotGate(shots, 'videos', new Set([1, 2, 3]), 1, JOBS);
    expect(g).toMatchObject({
      label: '对选中的 2 镜生成…',
      title: '对选中的 2 镜生成 / 重拍视频？',
      batches: [
        { stage: 'videos', ids: [2], force: false },
        { stage: 'videos', ids: [1], force: true },
      ],
    });
    const note = g?.kind === 'run' ? g.note : '';
    expect(note).toContain('1 镜还没有视频，生成');
    expect(note).toContain('1 镜已有视频，重拍');
    expect(note).toContain('1 镜还没有首帧，先跳过');
  });

  it('多选的镜头都还没有首帧（视频阶段）：去首帧阶段', () => {
    const shots = [makeShot({ id: 1 }), makeShot({ id: 2 })];
    expect(shotGate(shots, 'videos', new Set([1, 2]), 1, JOBS)).toEqual({
      kind: 'goto', label: '先出首帧（选中的 2 镜还没有首帧）', stage: 'keyframes',
    });
  });

  it('勾选里已删掉的镜头不算', () => {
    const g = shotGate([makeShot({ id: 1 })], 'keyframes', new Set([1, 99]), 1, JOBS);
    expect(g).toMatchObject({ label: '对选中的 1 镜生成…' });
  });
});
