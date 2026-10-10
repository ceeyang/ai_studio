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

/** 首帧 / 视频这一格：几镜有选定的、几镜过期、几镜缺 */
function slotStats(shots: ShotDetail[], kind: string) {
  const marks = shots.map((s) => slotMark(slotOf(s, kind)));
  const missing = marks.filter((m) => m === 'none').length;
  const stale = marks.filter((m) => m === 'stale').length;
  return { total: shots.length, selected: shots.length - missing, missing, stale };
}

type Draft = Omit<Stage, 'state'> & { state: 'done' | 'todo' | 'stale'; next: string; count: number };

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
  const blocks = ep.scene_blocks.length;
  const cast = castOf(shots, chars);
  const withPortrait = cast.filter(hasPortrait).length;
  const finalCut = exports.some((x) => x.is_final);

  const drafts: Draft[] = [
    {
      key: 'script',
      label: '剧本',
      // 剧本 = 有段落且有镜头（分镜也在剧本阶段做）
      state: blocks > 0 && shots.length > 0 ? 'done' : 'todo',
      detail: blocks === 0 ? '还没有段落' : `${blocks} 段 · ${shots.length > 0 ? `${shots.length} 镜` : '还没有镜头'}`,
      next: blocks === 0 ? '生成剧本' : '生成分镜',
      count: blocks === 0 ? 0 : blocks,
    },
    {
      key: 'cast',
      label: '角色',
      state: cast.length > 0 && withPortrait === cast.length ? 'done' : 'todo',
      detail: cast.length === 0 ? '还没有角色' : `${withPortrait}/${cast.length}`,
      next: cast.length === 0 ? '添加角色' : `抽定妆照（${cast.length - withPortrait} 个角色缺定妆照）`,
      count: cast.length - withPortrait,
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
  const stages: Stage[] = drafts.map(({ next: _next, count: _count, ...d }, i) => ({
    ...d,
    state: i === first && d.state === 'todo' ? 'active' : d.state,
  }));
  const d = first >= 0 ? drafts[first] : null;
  const next: NextStep | null = d
    ? { label: d.next, stage: d.key, action: 'run', count: d.count, stale: d.state === 'stale' }
    : null;
  return { stages, next };
}
