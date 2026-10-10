// 一集的五段制作进度（剧本 → 角色 → 首帧 → 视频 → 成片）和「下一步」。纯函数，单集工作台的阶段条和测试共用。
import type { Character, EpisodeDetail, ExportRow, ShotDetail } from '../api/types';
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

type Draft = Omit<Stage, 'state'> & { state: 'done' | 'todo' | 'stale'; next: string; count: number; busy?: boolean };

function slotStage(key: 'keyframes' | 'videos', label: string, shots: ShotDetail[]): Draft {
  const st = slotStats(shots, key === 'keyframes' ? 'first_frame' : 'video');
  const detail = st.total === 0 ? '还没有镜头' : `${st.selected}/${st.total}${st.stale > 0 ? ` · ${st.stale} 过期` : ''}`;
  if (st.total > 0 && st.stale > 0) {
    return { key, label, detail, state: 'stale', next: `重做过期的${label}（${st.stale} 镜过期）`, count: st.stale };
  }
  const done = st.total > 0 && st.missing === 0;
  return { key, label, detail, state: done ? 'done' : 'todo', next: `生成${label}（${st.missing} 镜缺${label}）`, count: st.missing };
}

/** 算一集的五段进度。next 取第一段没完成的（过期也算没完成）；全部完成时为 null。 */
export function episodeStages(
  ep: EpisodeDetail,
  chars: Character[],
  exports: ExportRow[],
): { stages: Stage[]; next: NextStep | null } {
  const shots = ep.shots;
  const cast = castOf(shots, chars);
  const withPortrait = cast.filter(hasPortrait).length;
  const finalCut = exports.some((x) => x.is_final);
  const target = castTarget(shots, chars);
  const castNext =
    cast.length === 0
      ? '按剧本推荐角色'
      : target?.kind === 'prompt'
        ? `补外观提示词（${chars.find((c) => c.id === target.id)?.name ?? ''}）`
        : target?.kind === 'busy'
          ? `抽定妆照中（${target.missing} 个）`
          : `抽定妆照（${cast.length - withPortrait} 个角色缺）`;

  const drafts: Draft[] = [
    // 剧本 = 有段落、每段都有镜头、没有上一版剧本留下的镜头（分镜也在剧本阶段做）
    scriptStage(ep),
    {
      key: 'cast',
      label: '角色',
      state: cast.length > 0 && withPortrait === cast.length ? 'done' : 'todo',
      detail: cast.length === 0 ? '还没有角色' : `${withPortrait}/${cast.length}`,
      next: castNext,
      count: cast.length - withPortrait,
      busy: target?.kind === 'busy',
    },
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
  const stages: Stage[] = drafts.map(({ next: _next, count: _count, busy: _busy, ...d }, i) => ({
    ...d,
    state: i === first && d.state === 'todo' ? 'active' : d.state,
  }));
  const d = first >= 0 ? drafts[first] : null;
  const next: NextStep | null = d
    ? { label: d.next, stage: d.key, action: 'run', count: d.count, stale: d.state === 'stale', ...(d.busy ? { busy: true } : {}) }
    : null;
  return { stages, next };
}
