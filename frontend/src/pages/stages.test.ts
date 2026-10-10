import { describe, expect, it } from 'vitest';
import type { Character, ExportRow } from '../api/types';
import { makeCandidate, makeEpisode, makeShot, makeSlot } from '../test/factories';
import { episodeStages } from './stages';

const BASE = '/projects/demo/episodes/4';

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

const byKey = (r: ReturnType<typeof episodeStages>) => Object.fromEntries(r.stages.map((s) => [s.key, s]));

describe('episodeStages', () => {
  it('空剧集：第一段是进行中的剧本，其余待做；下一步去运行剧本', () => {
    const r = episodeStages(makeEpisode({ id: 4 }), [], [], BASE);
    expect(r.stages.map((s) => s.key)).toEqual(['script', 'cast', 'storyboard', 'keyframes', 'videos', 'export']);
    expect(r.stages.map((s) => s.label)).toEqual(['剧本', '角色', '分镜', '首帧', '视频', '导出']);
    expect(r.stages.map((s) => s.state)).toEqual(['active', 'todo', 'todo', 'todo', 'todo', 'todo']);
    const s = byKey(r);
    expect(s.script.detail).toBe('还没有段落');
    expect(s.cast.detail).toBe('还没有角色');
    expect(s.storyboard.detail).toBe('还没有镜头');
    expect(s.export.detail).toBe('还没有成片');
    expect(s.script.to).toBe(`${BASE}/script`);
    expect(s.cast.to).toBe(`${BASE}/cast`);
    expect(s.storyboard.to).toBe(`${BASE}/script`);
    expect(s.keyframes.to).toBe(`${BASE}/director`);
    expect(s.videos.to).toBe(`${BASE}/director`);
    expect(s.export.to).toBe(`${BASE}/export`);
    expect(r.next).toEqual({ label: '下一步：运行剧本', to: `${BASE}/script` });
  });

  it('只有剧本：角色缺定妆照时下一步去抽定妆照；没有镜头时按项目全部角色算', () => {
    const ep = makeEpisode({ id: 4, scene_blocks: [{ id: 1, order: 0, location_id: null, text: 'a' }, { id: 2, order: 1, location_id: null, text: 'b' }] });
    const r = episodeStages(ep, [char(1, true), char(2, false)], [], BASE);
    const s = byKey(r);
    expect(s.script).toMatchObject({ state: 'done', detail: '2 段' });
    expect(s.cast).toMatchObject({ state: 'active', detail: '1/2 有定妆照' });
    expect(s.storyboard.state).toBe('todo');
    expect(r.next).toEqual({ label: '下一步：抽定妆照（1 个角色缺定妆照）', to: `${BASE}/cast` });
    // 角色都有定妆照了：下一步运行分镜
    const r2 = episodeStages(ep, [char(1, true), char(2, true)], [], BASE);
    expect(byKey(r2).cast).toMatchObject({ state: 'done', detail: '2/2 有定妆照' });
    expect(r2.next).toEqual({ label: '下一步：运行分镜', to: `${BASE}/script` });
  });

  it('有镜头没首帧：只看出场的角色；下一步生成首帧并写缺几镜', () => {
    const ep = makeEpisode({
      id: 4,
      scene_blocks: [{ id: 1, order: 0, location_id: null, text: 'a' }],
      shots: [shot(1, 'none', 'none', [1]), shot(2, 'none', 'none', [1])],
    });
    // 角色 2 没出场，没定妆照也不挡路
    const r = episodeStages(ep, [char(1, true), char(2, false)], [], BASE);
    const s = byKey(r);
    expect(s.cast).toMatchObject({ state: 'done', detail: '1/1 有定妆照' });
    expect(s.storyboard).toMatchObject({ state: 'done', detail: '2 镜' });
    expect(s.keyframes).toMatchObject({ state: 'active', detail: '0/2' });
    expect(s.videos).toMatchObject({ state: 'todo', detail: '0/2' });
    expect(r.next).toEqual({ label: '下一步：生成首帧（2 镜缺首帧）', to: `${BASE}/director` });
  });

  it('部分过期：状态是过期、detail 写几镜过期，下一步先重做过期的', () => {
    const ep = makeEpisode({
      id: 4,
      scene_blocks: [{ id: 1, order: 0, location_id: null, text: 'a' }],
      shots: [shot(1, 'ok', 'ok'), shot(2, 'stale', 'stale'), shot(3, 'stale', 'none')],
    });
    const r = episodeStages(ep, [char(1, true)], [], BASE);
    const s = byKey(r);
    expect(s.keyframes).toMatchObject({ state: 'stale', detail: '3/3 · 2 过期' });
    expect(s.videos).toMatchObject({ state: 'stale', detail: '2/3 · 1 过期' });
    expect(r.next).toEqual({ label: '下一步：重做过期的首帧（2 镜过期）', to: `${BASE}/director` });
    // 首帧都是最新的、只是视频缺：下一步生成视频
    const ep2 = { ...ep, shots: [shot(1, 'ok', 'ok'), shot(2, 'ok', 'none'), shot(3, 'ok', 'none')] };
    const r2 = episodeStages(ep2, [char(1, true)], [], BASE);
    expect(byKey(r2).keyframes).toMatchObject({ state: 'done', detail: '3/3' });
    expect(r2.next).toEqual({ label: '下一步：生成视频（2 镜缺视频）', to: `${BASE}/director` });
  });

  it('全部完成并定稿：没有下一步', () => {
    const ep = makeEpisode({
      id: 4,
      scene_blocks: [{ id: 1, order: 0, location_id: null, text: 'a' }],
      shots: [shot(1, 'ok', 'ok'), shot(2, 'ok', 'ok')],
    });
    const before = episodeStages(ep, [char(1, true)], [], BASE);
    expect(before.next).toEqual({ label: '下一步：合成导出', to: `${BASE}/export` });
    const r = episodeStages(ep, [char(1, true)], [exp(1), exp(2, true)], BASE);
    expect(r.stages.every((s) => s.state === 'done')).toBe(true);
    expect(byKey(r).videos.detail).toBe('2/2');
    expect(byKey(r).export.detail).toBe('2 版 · 已定稿');
    expect(byKey(episodeStages(ep, [char(1, true)], [exp(1)], BASE)).export.detail).toBe('1 版');
    expect(r.next).toBeNull();
  });
});
