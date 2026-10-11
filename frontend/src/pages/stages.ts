// 一集的五段制作进度（剧本 → 角色 → 首帧 → 视频 → 成片）和「下一步」。纯函数，单集工作台的阶段条和测试共用。
import type { Character, EpisodeDetail, ExportRow, Location, ShotDetail } from '../api/types';
import { slotMark, slotOf } from './director/slots';

export type StageKey = 'script' | 'cast' | 'keyframes' | 'videos' | 'export';

export const STAGE_KEYS: StageKey[] = ['script', 'cast', 'keyframes', 'videos', 'export'];

export function isStageKey(v: string | null | undefined): v is StageKey {
  return !!v && (STAGE_KEYS as string[]).includes(v);
}

export interface Stage {
  key: StageKey;
  label: string;
  /** done 完成；active 第一段没完成的（该做它了）；todo 还没轮到；stale 有过期的结果要重做 */
  state: 'done' | 'active' | 'todo' | 'stale';
  detail: string;
}

/**
 * 下一步，如 {label: '生成视频（2 镜缺视频）', stage: 'videos', action: 'run', count: 2, stale: false}。
 * label 不带「下一步：」前缀（阶段条自己加）；stage 是要在哪个阶段做；
 * action：run = 在那个阶段触发它的动作按钮（data-next-action），goto = 只切过去；
 * count 是这一步涉及几个（镜头 / 角色 / 段落），没有可数的为 0；stale 表示是因为有过期的结果才没完成。
 */
export interface NextStep {
  label: string;
  stage: StageKey;
  action: 'goto' | 'run';
  count: number;
  stale: boolean;
  /** 这一步正在进行（如缺定妆照的角色都在抽卡）：阶段条的按钮禁用，字照样显示 */
  busy?: boolean;
  /** 可以跳过的建议步骤（场景图）：阶段条在主按钮旁给「跳过」 */
  skippable?: boolean;
  /** 角色阶段里具体是哪一步：scenes = 生成场景图（角色阶段的场景图按钮打 data-next-action） */
  kind?: 'scenes';
}

/** 场景图这一步要用的：项目的场景（带选定的场景图和进行中的任务）；skipped = 用户在阶段条点过「跳过」 */
export interface SceneInfo {
  locations: Location[];
  skipped?: boolean;
}

/** 场景有没有能用来出场景图的英文画面提示词（后端没有时直接拒绝） */
export function hasEnglishPrompt(loc: Location): boolean {
  const vp = loc.visual_prompt.trim();
  return vp !== '' && !/[⺀-鿿]/.test(vp);
}

const hasLocationImage = (l: Location) => !!(l.image_candidate_id || l.image_url) && !l.image_stale;
const isDrawingLocation = (l: Location) => (l.active_jobs ?? []).some((j) => j.type === 'image.location');

/** 本集段落用到的场景 */
export function episodeLocations(ep: EpisodeDetail, locations: Location[]): Location[] {
  const ids = new Set(ep.scene_blocks.map((b) => b.location_id).filter((x): x is number => x != null));
  return locations.filter((l) => ids.has(l.id));
}

/** 角色阶段「生成场景图」要出的：本集用到、缺场景图（或已过期）、有英文画面提示词、没在生成的场景 */
export function scenesToDraw(ep: EpisodeDetail, locations: Location[]): Location[] {
  return episodeLocations(ep, locations).filter((l) => !hasLocationImage(l) && !isDrawingLocation(l) && hasEnglishPrompt(l));
}

/**
 * 场景图进度：首帧引擎吃参考图（镜头的 image_max_refs > 0）时才有意义——场景图排在首帧参考图第一张，
 * 引擎不吃参考图就用不上。没有可统计的（不吃参考图、本集没用到场景）返回 null。
 */
function sceneProgress(ep: EpisodeDetail, scenes: SceneInfo | undefined) {
  if (!scenes || !ep.shots.some((s) => (s.image_max_refs ?? 0) > 0)) return null;
  const used = episodeLocations(ep, scenes.locations);
  if (used.length === 0) return null;
  const missing = used.filter((l) => !hasLocationImage(l));
  return {
    total: used.length,
    ok: used.length - missing.length,
    drawing: missing.filter(isDrawingLocation).length,
    toDraw: scenesToDraw(ep, scenes.locations).length,
  };
}

export const STAGE_STATE_TEXT: Record<Stage['state'], string> = {
  done: '已完成',
  active: '进行中',
  todo: '未开始',
  stale: '有过期',
};

/** 出场角色：镜头 cast 里出现过的；还没有镜头（或镜头都没标角色）时按项目全部角色算 */
export function castOf(shots: ShotDetail[], chars: Character[]): Character[] {
  const ids = new Set(shots.flatMap((s) => s.cast.map((c) => c.character_id)));
  const appearing = chars.filter((c) => ids.has(c.id));
  return appearing.length > 0 ? appearing : chars;
}

export const hasPortrait = (c: Character) => !!(c.portrait_url || c.portrait_candidate_id);

const isDrawing = (c: Character) => (c.active_jobs ?? []).some((j) => j.type === 'image.portrait');

/**
 * 角色阶段的下一步落在哪个角色（出场角色里缺定妆照的，missing 是缺几个）：
 * draw = 第一个没在抽卡、有外观提示词的（点它的「抽定妆照」）；prompt = 剩下的都没提示词，第一个没在抽的（去补提示词）；
 * busy = 缺的都在抽卡中；都不缺时为 null。
 */
export type CastTarget = { kind: 'draw' | 'prompt'; id: number; missing: number } | { kind: 'busy'; id: null; missing: number };

export function castTarget(shots: ShotDetail[], chars: Character[]): CastTarget | null {
  const missing = castOf(shots, chars).filter((c) => !hasPortrait(c));
  if (missing.length === 0) return null;
  const idle = missing.filter((c) => !isDrawing(c));
  const draw = idle.find((c) => c.visual_prompt.trim());
  if (draw) return { kind: 'draw', id: draw.id, missing: missing.length };
  if (idle.length > 0) return { kind: 'prompt', id: idle[0].id, missing: missing.length };
  return { kind: 'busy', id: null, missing: missing.length };
}

/**
 * 上一版剧本留下的镜头：分镜生成的（origin=llm），但段落已经没了（重新生成剧本后段落换了一批，旧镜头的 scene_block_id 变成空）。
 * 手工添加的镜头本来就没有段落，不算。
 */
export function isOrphanShot(shot: Pick<ShotDetail, 'origin' | 'scene_block_id'>, blockIds: ReadonlySet<number>): boolean {
  return shot.origin === 'llm' && (shot.scene_block_id == null || !blockIds.has(shot.scene_block_id));
}

/** 剧本这一格：分镜跟得上剧本没有（每段都有镜头、没有上一版剧本留下的镜头） */
function scriptStage(ep: EpisodeDetail): Draft {
  const blocks = ep.scene_blocks;
  const shots = ep.shots;
  const blockIds = new Set(blocks.map((b) => b.id));
  const covered = new Set(shots.map((s) => s.scene_block_id));
  const bare = blocks.filter((b) => !covered.has(b.id)).length;
  const orphans = shots.filter((s) => isOrphanShot(s, blockIds)).length;
  const base = { key: 'script' as const, label: '剧本' };
  if (blocks.length === 0) return { ...base, state: 'todo', detail: '还没有段落', next: '生成剧本', count: 0 };
  if (shots.length === 0) {
    return { ...base, state: 'todo', detail: `${blocks.length} 段 · 还没有镜头`, next: '生成分镜', count: blocks.length };
  }
  const detail = `${blocks.length} 段 · ${shots.length} 镜`;
  if (bare === 0 && orphans === 0) return { ...base, state: 'done', detail, next: '', count: 0 };
  // 重新生成过剧本（或有段落还没镜头）：分镜跟不上了，去剧本阶段点「重新生成分镜…」
  const why = orphans > 0 ? `${orphans} 镜是上一版剧本的` : `${bare} 段没有镜头`;
  return { ...base, state: 'todo', detail: `${detail} · ${why}`, next: '重新生成分镜（剧本改过）', count: bare };
}

/** 首帧 / 视频这一格：几镜有选定的、几镜过期、几镜缺 */
function slotStats(shots: ShotDetail[], kind: string) {
  const marks = shots.map((s) => slotMark(slotOf(s, kind)));
  const missing = marks.filter((m) => m === 'none').length;
  const stale = marks.filter((m) => m === 'stale').length;
  return { total: shots.length, selected: shots.length - missing, missing, stale };
}

type Draft = Omit<Stage, 'state'> & {
  state: 'done' | 'todo' | 'stale';
  next: string;
  count: number;
  busy?: boolean;
  skippable?: boolean;
  kind?: 'scenes';
};

function slotStage(key: 'keyframes' | 'videos', label: string, shots: ShotDetail[]): Draft {
  const st = slotStats(shots, key === 'keyframes' ? 'first_frame' : 'video');
  const detail = st.total === 0 ? '还没有镜头' : `${st.selected}/${st.total}${st.stale > 0 ? ` · ${st.stale} 过期` : ''}`;
  if (st.total > 0 && st.stale > 0) {
    return { key, label, detail, state: 'stale', next: `重做过期的${label}（${st.stale} 镜过期）`, count: st.stale };
  }
  const done = st.total > 0 && st.missing === 0;
  return { key, label, detail, state: done ? 'done' : 'todo', next: `生成${label}（${st.missing} 镜缺${label}）`, count: st.missing };
}

/**
 * 角色阶段这一格。定妆照齐了以后，本集用到的场景还缺场景图（首帧引擎吃参考图时）：下一步建议「生成场景图」——
 * 场景图排在首帧参考图第一张，同一场景各镜的布景才一致。它可以跳过（scenes.skipped），首帧已经开始做了也不再提，
 * 不拦着去首帧阶段；detail 里一直显示「场景图 x/y」。
 */
function castStage(ep: EpisodeDetail, chars: Character[], scenes: SceneInfo | undefined): Draft {
  const shots = ep.shots;
  const cast = castOf(shots, chars);
  const withPortrait = cast.filter(hasPortrait).length;
  const target = castTarget(shots, chars);
  const sp = sceneProgress(ep, scenes);
  const portraitsDone = cast.length > 0 && withPortrait === cast.length;
  const base = cast.length === 0 ? '还没有角色' : `${withPortrait}/${cast.length}`;
  const detail = sp ? `${base} · 场景图 ${sp.ok}/${sp.total}` : base;
  const keyframesStarted = shots.some((s) => slotMark(slotOf(s, 'first_frame')) !== 'none');
  if (portraitsDone && sp && sp.ok < sp.total && !scenes?.skipped && !keyframesStarted && (sp.toDraw > 0 || sp.drawing > 0)) {
    const busy = sp.toDraw === 0;
    return {
      key: 'cast', label: '角色', state: 'todo', detail,
      next: busy ? `生成场景图中（${sp.drawing} 个）` : `生成场景图（${sp.toDraw} 个）`,
      count: sp.toDraw, skippable: true, kind: 'scenes', ...(busy ? { busy: true } : {}),
    };
  }
  const next =
    cast.length === 0
      ? '按剧本推荐角色'
      : target?.kind === 'prompt'
        ? `补外观提示词（${chars.find((c) => c.id === target.id)?.name ?? ''}）`
        : target?.kind === 'busy'
          ? `抽定妆照中（${target.missing} 个）`
          : `抽定妆照（${cast.length - withPortrait} 个角色缺）`;
  return {
    key: 'cast', label: '角色', state: portraitsDone ? 'done' : 'todo', detail, next,
    count: cast.length - withPortrait, busy: target?.kind === 'busy',
  };
}

/** 算一集的五段进度。next 取第一段没完成的（过期也算没完成）；全部完成时为 null。scenes 给了才看场景图。 */
export function episodeStages(
  ep: EpisodeDetail,
  chars: Character[],
  exports: ExportRow[],
  scenes?: SceneInfo,
): { stages: Stage[]; next: NextStep | null } {
  const shots = ep.shots;
  const finalCut = exports.some((x) => x.is_final);

  const drafts: Draft[] = [
    // 剧本 = 有段落、每段都有镜头、没有上一版剧本留下的镜头（分镜也在剧本阶段做）
    scriptStage(ep),
    castStage(ep, chars, scenes),
    slotStage('keyframes', '首帧', shots),
    slotStage('videos', '视频', shots),
    {
      key: 'export',
      label: '成片',
      state: exports.length > 0 ? 'done' : 'todo',
      detail: exports.length > 0 ? `${exports.length} 版${finalCut ? ' · 已定稿' : ''}` : '还没有成片',
      next: '合成导出',
      count: shots.length,
    },
  ];

  const first = drafts.findIndex((d) => d.state !== 'done');
  const stages: Stage[] = drafts.map(({ next: _next, count: _count, busy: _busy, skippable: _skip, kind: _kind, ...d }, i) => ({
    ...d,
    state: i === first && d.state === 'todo' ? 'active' : d.state,
  }));
  const d = first >= 0 ? drafts[first] : null;
  const next: NextStep | null = d
    ? {
        label: d.next,
        stage: d.key,
        action: 'run',
        count: d.count,
        stale: d.state === 'stale',
        ...(d.busy ? { busy: true } : {}),
        ...(d.skippable ? { skippable: true } : {}),
        ...(d.kind ? { kind: d.kind } : {}),
      }
    : null;
  return { stages, next };
}

/**
 * 成片阶段自己的下一步（人已经在成片阶段，不再指回前面没做完的角色等）：
 * 还有镜头缺视频 → 去视频阶段补齐；视频齐了还没成片 → 合成导出；已经有成片 → 再导出一版（五段都完成了时
 * 返回 'done'，阶段条显示「五段都完成了」）。还没有镜头时返回 null（沿用整集的下一步：去剧本生成分镜）。
 */
export function exportStep(shots: ShotDetail[], exports: ExportRow[], allDone: boolean): NextStep | 'done' | null {
  if (shots.length === 0) return null;
  const missing = shots.filter((s) => slotMark(slotOf(s, 'video')) === 'none').length;
  if (missing > 0) return { label: `补齐视频（${missing} 镜缺）`, stage: 'videos', action: 'goto', count: missing, stale: false };
  if (exports.length > 0 && allDone) return 'done';
  const label = exports.length > 0 ? '再导出一版' : '合成导出';
  return { label, stage: 'export', action: 'run', count: shots.length, stale: false };
}
