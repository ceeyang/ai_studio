import { describe, expect, it } from 'vitest';
import type { Character, ExportRow } from '../api/types';
import { makeCandidate, makeEpisode, makeShot, makeSlot } from '../test/factories';
import { STAGE_KEYS, episodeStages, isStageKey } from './stages';

function char(id: number, portrait: boolean): Character {
  return {
    id, project_id: 1, name: `角色${id}`, description: '', visual_prompt: '', core_features: '', voice: null,
    portrait_candidate_id: portrait ? id * 10 : null, portrait_url: portrait ? `/media/p${id}.png` : null,
  };
}

/** 一个镜头：首帧、视频各自 'none'（没选定）/ 'ok' / 'stale' */
function shot(id: number, ff: 'none' | 'ok' | 'stale', video: 'none' | 'ok' | 'stale', castIds: number[] = [1]) {
  const slot = (sid: number, kind: string, mark: 'none' | 'ok' | 'stale') =>
    makeSlot({
      id: sid, kind,
      selected_candidate_id: mark === 'none' ? null : sid * 100,
      stale: mark === 'stale',
      candidates: mark === 'none' ? [] : [makeCandidate({ id: sid * 100, slot_id: sid })],
    });
  return makeShot({
    id, code: `S0${id}`,
    cast: castIds.map((c) => ({ character_id: c, variation_id: null })),
    slots: [slot(id * 2, 'first_frame', ff), slot(id * 2 + 1, 'video', video)],
  });
}

function exp(id: number, isFinal = false): ExportRow {
  return { id, episode_id: 4, variant: '9:16', file_path: `x/${id}.mp4`, url: `/media/${id}.mp4`, settings: {}, is_final: isFinal, created_at: '2026-10-08T10:00:00' };
}

const BLOCKS = [{ id: 1, order: 0, location_id: null, text: 'a' }, { id: 2, order: 1, location_id: null, text: 'b' }];
const byKey = (r: ReturnType<typeof episodeStages>) => Object.fromEntries(r.stages.map((s) => [s.key, s]));

describe('episodeStages', () => {
  it('五段：剧本 / 角色 / 首帧 / 视频 / 成片；空剧集下一步是在剧本阶段生成剧本', () => {
    const r = episodeStages(makeEpisode({ id: 4 }), [], []);
    expect(r.stages.map((s) => s.key)).toEqual(['script', 'cast', 'keyframes', 'videos', 'export']);
    expect(STAGE_KEYS).toEqual(['script', 'cast', 'keyframes', 'videos', 'export']);
    expect(r.stages.map((s) => s.label)).toEqual(['剧本', '角色', '首帧', '视频', '成片']);
    expect(r.stages.map((s) => s.state)).toEqual(['active', 'todo', 'todo', 'todo', 'todo']);
    const s = byKey(r);
    expect(s.script.detail).toBe('还没有段落');
    expect(s.cast.detail).toBe('还没有角色');
    expect(s.keyframes.detail).toBe('还没有镜头');
    expect(s.export.detail).toBe('还没有成片');
    expect(r.next).toEqual({ label: '生成剧本', stage: 'script', action: 'run', count: 0, stale: false });
  });

  it('有段落没镜头：剧本还没完成，下一步生成分镜（按段数）', () => {
    const ep = makeEpisode({ id: 4, scene_blocks: BLOCKS });
    const r = episodeStages(ep, [char(1, false)], []);
    expect(byKey(r).script).toMatchObject({ state: 'active', detail: '2 段 · 还没有镜头' });
    expect(r.next).toEqual({ label: '生成分镜', stage: 'script', action: 'run', count: 2, stale: false });
  });

  it('有段落有镜头：剧本完成；出场角色缺定妆照时下一步在角色阶段', () => {
    const ep = makeEpisode({ id: 4, scene_blocks: BLOCKS, shots: [shot(1, 'none', 'none', [1, 2])] });
    const r = episodeStages(ep, [char(1, true), char(2, false), char(3, false)], []);
    const s = byKey(r);
    expect(s.script).toMatchObject({ state: 'done', detail: '2 段 · 1 镜' });
    // 角色 3 没出场，不算
    expect(s.cast).toMatchObject({ state: 'active', detail: '1/2' });
    expect(r.next).toEqual({ label: '抽定妆照（1 个角色缺定妆照）', stage: 'cast', action: 'run', count: 1, stale: false });
  });

  it('剧本完成但项目里没有角色：下一步添加角色', () => {
    const ep = makeEpisode({ id: 4, scene_blocks: BLOCKS, shots: [shot(1, 'none', 'none', [])] });
    const r = episodeStages(ep, [], []);
    expect(r.next).toEqual({ label: '添加角色', stage: 'cast', action: 'run', count: 0, stale: false });
  });

  it('有镜头没首帧：下一步在首帧阶段生成缺的首帧', () => {
    const ep = makeEpisode({ id: 4, scene_blocks: BLOCKS, shots: [shot(1, 'none', 'none'), shot(2, 'none', 'none')] });
    const r = episodeStages(ep, [char(1, true), char(2, false)], []);
    const s = byKey(r);
    expect(s.cast).toMatchObject({ state: 'done', detail: '1/1' });
    expect(s.keyframes).toMatchObject({ state: 'active', detail: '0/2' });
    expect(s.videos).toMatchObject({ state: 'todo', detail: '0/2' });
    expect(r.next).toEqual({ label: '生成首帧（2 镜缺首帧）', stage: 'keyframes', action: 'run', count: 2, stale: false });
  });

  it('部分过期：状态是过期、detail 写几镜过期，下一步先重做过期的；首帧都好了才轮到视频', () => {
    const ep = makeEpisode({ id: 4, scene_blocks: BLOCKS, shots: [shot(1, 'ok', 'ok'), shot(2, 'stale', 'stale'), shot(3, 'stale', 'none')] });
    const r = episodeStages(ep, [char(1, true)], []);
    const s = byKey(r);
    expect(s.keyframes).toMatchObject({ state: 'stale', detail: '3/3 · 2 过期' });
    expect(s.videos).toMatchObject({ state: 'stale', detail: '2/3 · 1 过期' });
    expect(r.next).toEqual({ label: '重做过期的首帧（2 镜过期）', stage: 'keyframes', action: 'run', count: 2, stale: true });
    const ep2 = { ...ep, shots: [shot(1, 'ok', 'ok'), shot(2, 'ok', 'none'), shot(3, 'ok', 'none')] };
    const r2 = episodeStages(ep2, [char(1, true)], []);
    expect(byKey(r2).keyframes).toMatchObject({ state: 'done', detail: '3/3' });
    expect(r2.next).toEqual({ label: '生成视频（2 镜缺视频）', stage: 'videos', action: 'run', count: 2, stale: false });
  });

  it('视频都有了：下一步在成片阶段合成导出；有成片后全部完成，没有下一步', () => {
    const ep = makeEpisode({ id: 4, scene_blocks: BLOCKS, shots: [shot(1, 'ok', 'ok'), shot(2, 'ok', 'ok')] });
    const before = episodeStages(ep, [char(1, true)], []);
    expect(before.next).toEqual({ label: '合成导出', stage: 'export', action: 'run', count: 2, stale: false });
    const r = episodeStages(ep, [char(1, true)], [exp(1), exp(2, true)]);
    expect(r.stages.every((s) => s.state === 'done')).toBe(true);
    expect(byKey(r).videos.detail).toBe('2/2');
    expect(byKey(r).export.detail).toBe('2 版 · 已定稿');
    expect(byKey(episodeStages(ep, [char(1, true)], [exp(1)])).export.detail).toBe('1 版');
    expect(r.next).toBeNull();
  });

  it('isStageKey：只认五段的名字', () => {
    expect(isStageKey('keyframes')).toBe(true);
    expect(isStageKey('director')).toBe(false);
    expect(isStageKey(null)).toBe(false);
  });
});
