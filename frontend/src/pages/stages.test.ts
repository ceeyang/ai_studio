import { describe, expect, it } from 'vitest';
import type { Character, ExportRow, Location } from '../api/types';
import { makeCandidate, makeEpisode, makeShot, makeSlot } from '../test/factories';
import { STAGE_KEYS, castTarget, episodeStages, exportStep, isStageKey, scenesToDraw } from './stages';

function char(id: number, portrait: boolean, over: Partial<Character> = {}): Character {
  return {
    id, project_id: 1, name: `角色${id}`, description: '', visual_prompt: 'look', core_features: '', voice: null,
    portrait_candidate_id: portrait ? id * 10 : null, portrait_url: portrait ? `/media/p${id}.png` : null, ...over,
  };
}
const drawing = { active_jobs: [{ id: 1, type: 'image.portrait', status: 'running' as const }] };

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
    id, code: `S0${id}`, scene_block_id: 1, origin: 'llm',
    cast: castIds.map((c) => ({ character_id: c, variation_id: null })),
    slots: [slot(id * 2, 'first_frame', ff), slot(id * 2 + 1, 'video', video)],
  });
}

function exp(id: number, isFinal = false): ExportRow {
  return { id, episode_id: 4, variant: '9:16', file_path: `x/${id}.mp4`, url: `/media/${id}.mp4`, settings: {}, is_final: isFinal, created_at: '2026-10-08T10:00:00' };
}

const BLOCKS = [{ id: 1, order: 0, location_id: null, text: 'a' }, { id: 2, order: 1, location_id: null, text: 'b' }];
/** 镜头都在第 1 段：只有这一段时剧本算完成 */
const ONE_BLOCK = [BLOCKS[0]];
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
    const ep = makeEpisode({ id: 4, scene_blocks: ONE_BLOCK, shots: [shot(1, 'none', 'none', [1, 2])] });
    const r = episodeStages(ep, [char(1, true), char(2, false), char(3, false)], []);
    const s = byKey(r);
    expect(s.script).toMatchObject({ state: 'done', detail: '1 段 · 1 镜' });
    // 角色 3 没出场，不算
    expect(s.cast).toMatchObject({ state: 'active', detail: '1/2' });
    expect(r.next).toEqual({ label: '抽定妆照（1 个角色缺）', stage: 'cast', action: 'run', count: 1, stale: false });
  });

  it('只出声、不出镜的角色不用定妆照：不算进角色阶段，也不是下一步的目标', () => {
    const ep = makeEpisode({ id: 4, scene_blocks: ONE_BLOCK, shots: [shot(1, 'none', 'none', [1, 2])] });
    const voice = char(2, false, { voice_only: true });
    const r = episodeStages(ep, [char(1, true), voice], []);
    expect(byKey(r).cast).toMatchObject({ state: 'done', detail: '1/1' });
    expect(castTarget(ep.shots, [char(1, false), voice])).toMatchObject({ kind: 'draw', id: 1, missing: 1 });
    // 没有镜头时按全部角色算，也不算只出声的
    const bare = makeEpisode({ id: 4, scene_blocks: ONE_BLOCK });
    expect(byKey(episodeStages(bare, [char(1, true), voice], [])).cast.detail).toBe('1/1');
  });

  it('剧本完成但项目里没有角色：下一步按剧本推荐角色', () => {
    const ep = makeEpisode({ id: 4, scene_blocks: ONE_BLOCK, shots: [shot(1, 'none', 'none', [])] });
    const r = episodeStages(ep, [], []);
    expect(r.next).toEqual({ label: '按剧本推荐角色', stage: 'cast', action: 'run', count: 0, stale: false });
  });

  it('缺定妆照的角色在抽卡中：下一步指向下一个能抽的角色（抽卡中的不算目标）', () => {
    const ep = makeEpisode({ id: 4, scene_blocks: ONE_BLOCK, shots: [shot(1, 'none', 'none', [1, 2, 3])] });
    const chars = [char(1, true), char(2, false, drawing), char(3, false)];
    expect(episodeStages(ep, chars, []).next).toEqual({ label: '抽定妆照（2 个角色缺）', stage: 'cast', action: 'run', count: 2, stale: false });
    expect(castTarget(ep.shots, chars)).toEqual({ kind: 'draw', id: 3, missing: 2 });
  });

  it('剩下缺定妆照的角色没有外观提示词：下一步是补外观提示词（角色名）', () => {
    const ep = makeEpisode({ id: 4, scene_blocks: ONE_BLOCK, shots: [shot(1, 'none', 'none', [1, 2, 3])] });
    const chars = [char(1, true), char(2, false, drawing), char(3, false, { visual_prompt: '  ' })];
    expect(episodeStages(ep, chars, []).next).toEqual({ label: '补外观提示词（角色3）', stage: 'cast', action: 'run', count: 2, stale: false });
    expect(castTarget(ep.shots, chars)).toEqual({ kind: 'prompt', id: 3, missing: 2 });
  });

  it('缺定妆照的角色都在抽卡中：下一步显示抽定妆照中并标为忙', () => {
    const ep = makeEpisode({ id: 4, scene_blocks: ONE_BLOCK, shots: [shot(1, 'none', 'none', [1, 2, 3])] });
    const chars = [char(1, true), char(2, false, drawing), char(3, false, drawing)];
    expect(episodeStages(ep, chars, []).next).toEqual({ label: '抽定妆照中（2 个）', stage: 'cast', action: 'run', count: 2, stale: false, busy: true });
    expect(castTarget(ep.shots, chars)).toEqual({ kind: 'busy', id: null, missing: 2 });
  });

  it('有镜头没首帧：下一步在首帧阶段生成缺的首帧', () => {
    const ep = makeEpisode({ id: 4, scene_blocks: ONE_BLOCK, shots: [shot(1, 'none', 'none'), shot(2, 'none', 'none')] });
    const r = episodeStages(ep, [char(1, true), char(2, false)], []);
    const s = byKey(r);
    expect(s.cast).toMatchObject({ state: 'done', detail: '1/1' });
    expect(s.keyframes).toMatchObject({ state: 'active', detail: '0/2' });
    expect(s.videos).toMatchObject({ state: 'todo', detail: '0/2' });
    expect(r.next).toEqual({ label: '生成首帧（2 镜缺首帧）', stage: 'keyframes', action: 'run', count: 2, stale: false });
  });

  it('部分过期：状态是过期、detail 写几镜过期，下一步先重做过期的；首帧都好了才轮到视频', () => {
    const ep = makeEpisode({ id: 4, scene_blocks: ONE_BLOCK, shots: [shot(1, 'ok', 'ok'), shot(2, 'stale', 'stale'), shot(3, 'stale', 'none')] });
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
    const ep = makeEpisode({ id: 4, scene_blocks: ONE_BLOCK, shots: [shot(1, 'ok', 'ok'), shot(2, 'ok', 'ok')] });
    const before = episodeStages(ep, [char(1, true)], []);
    expect(before.next).toEqual({ label: '合成导出', stage: 'export', action: 'run', count: 2, stale: false });
    const r = episodeStages(ep, [char(1, true)], [exp(1), exp(2, true)]);
    expect(r.stages.every((s) => s.state === 'done')).toBe(true);
    expect(byKey(r).videos.detail).toBe('2/2');
    expect(byKey(r).export.detail).toBe('2 版 · 已定稿');
    expect(byKey(episodeStages(ep, [char(1, true)], [exp(1)])).export.detail).toBe('1 版');
    expect(r.next).toBeNull();
  });

  it('重新生成剧本后：旧的分镜镜头没了段落（origin=llm）——剧本不打勾，下一步回剧本阶段重新生成分镜', () => {
    const old = { ...shot(1, 'ok', 'ok'), scene_block_id: null };
    const ep = makeEpisode({ id: 4, scene_blocks: BLOCKS, shots: [old, shot(2, 'ok', 'ok')] });
    const r = episodeStages(ep, [char(1, true)], [exp(1)]);
    expect(byKey(r).script).toMatchObject({ state: 'active', detail: '2 段 · 2 镜 · 1 镜是上一版剧本的' });
    expect(r.next).toEqual({ label: '重新生成分镜（剧本改过）', stage: 'script', action: 'run', count: 1, stale: false });
  });

  it('有段落还没镜头（其他段有）：剧本不算完成，下一步同样是重新生成分镜', () => {
    const ep = makeEpisode({ id: 4, scene_blocks: BLOCKS, shots: [shot(1, 'none', 'none')] });
    const r = episodeStages(ep, [char(1, true)], []);
    expect(byKey(r).script).toMatchObject({ state: 'active', detail: '2 段 · 1 镜 · 1 段没有镜头' });
    expect(r.next?.label).toBe('重新生成分镜（剧本改过）');
  });

  it('手工添加的镜头没有段落不影响剧本完成', () => {
    const manual = makeShot({ id: 9, code: 'S09', scene_block_id: null, origin: 'manual' });
    const ep = makeEpisode({ id: 4, scene_blocks: ONE_BLOCK, shots: [shot(1, 'none', 'none'), manual] });
    expect(byKey(episodeStages(ep, [char(1, true)], [])).script.state).toBe('done');
  });

  it('exportStep（成片阶段自己的下一步）：缺视频去视频阶段补齐；齐了合成导出；有成片再导出一版；全完成是 done；没镜头 null', () => {
    const some = [shot(1, 'ok', 'ok'), shot(2, 'ok', 'none'), shot(3, 'none', 'none')];
    expect(exportStep(some, [], false)).toEqual({ label: '补齐视频（2 镜缺）', stage: 'videos', action: 'goto', count: 2, stale: false });
    const all = [shot(1, 'ok', 'ok'), shot(2, 'ok', 'stale')];
    expect(exportStep(all, [], false)).toEqual({ label: '合成导出', stage: 'export', action: 'run', count: 2, stale: false });
    expect(exportStep(all, [exp(1)], false)).toEqual({ label: '再导出一版', stage: 'export', action: 'run', count: 2, stale: false });
    expect(exportStep(all, [exp(1)], true)).toBe('done');
    expect(exportStep([], [], false)).toBeNull();
  });

  describe('场景图（角色阶段里的可跳过一步）', () => {
    const blocks = [{ id: 1, order: 0, location_id: 7, text: 'a' }];
    const refShot = (id: number, ff: 'none' | 'ok' | 'stale' = 'none') => ({ ...shot(id, ff, 'none'), scene_block_id: 1, image_max_refs: 9 });
    function loc(id: number, over: Partial<Location> = {}): Location {
      return { id, project_id: 1, name: `场景${id}`, time_of_day: '夜', atmosphere: '', visual_prompt: 'a narrow stairwell', image_candidate_id: null, image_url: null, ...over };
    }
    const locs = [loc(7), loc(8, { name: '没用到的场景' })];

    it('定妆照齐了、本集用到的场景缺场景图：角色阶段 detail 写「场景图 0/1」，下一步生成场景图（可跳过）', () => {
      const ep = makeEpisode({ id: 4, scene_blocks: blocks, shots: [refShot(1), refShot(2)] });
      const r = episodeStages(ep, [char(1, true)], [], { locations: locs });
      expect(byKey(r).cast).toMatchObject({ state: 'active', detail: '1/1 · 场景图 0/1' });
      expect(r.next).toEqual({ label: '生成场景图（1 个）', stage: 'cast', action: 'run', count: 1, stale: false, skippable: true, kind: 'scenes' });
    });

    it('跳过了 / 首帧已经开始做 / 场景图都有：角色阶段算完成，下一步去首帧', () => {
      const ep = makeEpisode({ id: 4, scene_blocks: blocks, shots: [refShot(1), refShot(2)] });
      const skipped = episodeStages(ep, [char(1, true)], [], { locations: locs, skipped: true });
      expect(byKey(skipped).cast).toMatchObject({ state: 'done', detail: '1/1 · 场景图 0/1' });
      expect(skipped.next?.stage).toBe('keyframes');
      const started = makeEpisode({ id: 4, scene_blocks: blocks, shots: [refShot(1, 'ok'), refShot(2)] });
      expect(episodeStages(started, [char(1, true)], [], { locations: locs }).next?.stage).toBe('keyframes');
      const withImg = [loc(7, { image_candidate_id: 70, image_url: '/m/7.png' })];
      const done = episodeStages(ep, [char(1, true)], [], { locations: withImg });
      expect(byKey(done).cast).toMatchObject({ state: 'done', detail: '1/1 · 场景图 1/1' });
    });

    it('过期的场景图不算有；首帧引擎不吃参考图时不提场景图', () => {
      const ep = makeEpisode({ id: 4, scene_blocks: blocks, shots: [refShot(1)] });
      const stale = [loc(7, { image_candidate_id: 70, image_url: '/m/7.png', image_stale: true })];
      expect(byKey(episodeStages(ep, [char(1, true)], [], { locations: stale })).cast.detail).toBe('1/1 · 场景图 0/1');
      const noRefs = makeEpisode({ id: 4, scene_blocks: blocks, shots: [{ ...refShot(1), image_max_refs: 0 }] });
      const r = episodeStages(noRefs, [char(1, true)], [], { locations: locs });
      expect(byKey(r).cast).toMatchObject({ state: 'done', detail: '1/1' });
      expect(r.next?.stage).toBe('keyframes');
    });

    it('缺场景图的都在生成中：下一步显示生成中并标忙；都没有英文画面提示词时不提（只显示 x/y）', () => {
      const ep = makeEpisode({ id: 4, scene_blocks: blocks, shots: [refShot(1)] });
      const busy = [loc(7, { active_jobs: [{ id: 3, type: 'image.location', status: 'running' }] })];
      expect(episodeStages(ep, [char(1, true)], [], { locations: busy }).next).toEqual({
        label: '生成场景图中（1 个）', stage: 'cast', action: 'run', count: 0, stale: false, busy: true, skippable: true, kind: 'scenes',
      });
      const zh = [loc(7, { visual_prompt: '狭窄的楼梯间' })];
      const r = episodeStages(ep, [char(1, true)], [], { locations: zh });
      expect(byKey(r).cast.detail).toBe('1/1 · 场景图 0/1');
      expect(r.next?.stage).toBe('keyframes');
    });

    it('定妆照还没齐：下一步仍是抽定妆照，detail 同时显示场景图进度', () => {
      const ep = makeEpisode({ id: 4, scene_blocks: blocks, shots: [refShot(1)] });
      const r = episodeStages(ep, [char(1, false)], [], { locations: locs });
      expect(byKey(r).cast.detail).toBe('0/1 · 场景图 0/1');
      expect(r.next?.label).toBe('抽定妆照（1 个角色缺）');
    });

    it('scenesToDraw：缺场景图、有英文提示词、没在生成的本集场景', () => {
      const ep = makeEpisode({ id: 4, scene_blocks: blocks, shots: [refShot(1)] });
      expect(scenesToDraw(ep, locs).map((l) => l.id)).toEqual([7]);
      expect(scenesToDraw(ep, [loc(7, { image_url: '/m.png', image_candidate_id: 1 })])).toEqual([]);
    });
  });

  it('isStageKey：只认五段的名字', () => {
    expect(isStageKey('keyframes')).toBe(true);
    expect(isStageKey('director')).toBe(false);
    expect(isStageKey(null)).toBe(false);
  });
});
