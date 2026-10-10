// 一集的六段制作进度（剧本 → 角色 → 分镜 → 首帧 → 视频 → 导出）和「下一步」。纯函数，剧集页头和测试共用。
import type { Character, EpisodeDetail, ExportRow, ShotDetail } from '../api/types';
import { slotMark, slotOf } from './director/slots';

export type StageKey = 'script' | 'cast' | 'storyboard' | 'keyframes' | 'videos' | 'export';

export interface Stage {
  key: StageKey;
  label: string;
  /** done 完成；active 第一段没完成的（该做它了）；todo 还没轮到；stale 有过期的结果要重做 */
  state: 'done' | 'active' | 'todo' | 'stale';
  detail: string;
  to: string;
}

/** 如 {label: '下一步：生成视频（2 镜缺视频）', to: '.../director'} */
export interface NextStep {
  label: string;
  to: string;
}

export const STAGE_STATE_TEXT: Record<Stage['state'], string> = {
  done: '已完成',
  active: '进行中',
  todo: '未开始',
  stale: '有过期',
};

/** 出场角色：镜头 cast 里出现过的；还没有镜头（或镜头都没标角色）时按项目全部角色算 */
function castOf(shots: ShotDetail[], chars: Character[]): Character[] {
  const ids = new Set(shots.flatMap((s) => s.cast.map((c) => c.character_id)));
  const appearing = chars.filter((c) => ids.has(c.id));
  return appearing.length > 0 ? appearing : chars;
}

const hasPortrait = (c: Character) => !!(c.portrait_url || c.portrait_candidate_id);

/** 首帧 / 视频这一格：几镜有选定的、几镜过期、几镜缺 */
function slotStats(shots: ShotDetail[], kind: string) {
  const marks = shots.map((s) => slotMark(slotOf(s, kind)));
  const missing = marks.filter((m) => m === 'none').length;
  const stale = marks.filter((m) => m === 'stale').length;
  return { total: shots.length, selected: shots.length - missing, missing, stale };
}

type Draft = Omit<Stage, 'state'> & { state: 'done' | 'todo' | 'stale'; next: string };

function slotStage(key: 'keyframes' | 'videos', label: string, shots: ShotDetail[], to: string): Draft {
  const st = slotStats(shots, key === 'keyframes' ? 'first_frame' : 'video');
  const detail = st.total === 0 ? '还没有镜头' : `${st.selected}/${st.total}${st.stale > 0 ? ` · ${st.stale} 过期` : ''}`;
  if (st.total > 0 && st.stale > 0) {
    return { key, label, to, detail, state: 'stale', next: `重做过期的${label}（${st.stale} 镜过期）` };
  }
  const done = st.total > 0 && st.missing === 0;
  return { key, label, to, detail, state: done ? 'done' : 'todo', next: `生成${label}（${st.missing} 镜缺${label}）` };
}

/**
 * 算一集的六段进度。base 是这一集的路由前缀（/projects/<slug>/episodes/<id>），各段的 to 指向要去的页面。
 * next 取第一段没完成的（过期也算没完成）；全部完成时为 null。
 */
export function episodeStages(
  ep: EpisodeDetail,
  chars: Character[],
  exports: ExportRow[],
  base: string,
): { stages: Stage[]; next: NextStep | null } {
  const shots = ep.shots;
  const cast = castOf(shots, chars);
  const withPortrait = cast.filter(hasPortrait).length;
  const finalCut = exports.some((x) => x.is_final);

  const drafts: Draft[] = [
    {
      key: 'script',
      label: '剧本',
      to: `${base}/script`,
      state: ep.scene_blocks.length > 0 ? 'done' : 'todo',
      detail: ep.scene_blocks.length > 0 ? `${ep.scene_blocks.length} 段` : '还没有段落',
      next: '运行剧本',
    },
    {
      key: 'cast',
      label: '角色',
      to: `${base}/cast`,
      state: cast.length > 0 && withPortrait === cast.length ? 'done' : 'todo',
      detail: cast.length === 0 ? '还没有角色' : `${withPortrait}/${cast.length} 有定妆照`,
      next: cast.length === 0 ? '添加角色' : `抽定妆照（${cast.length - withPortrait} 个角色缺定妆照）`,
    },
    {
      key: 'storyboard',
      label: '分镜',
      to: `${base}/script`,
      state: shots.length > 0 ? 'done' : 'todo',
      detail: shots.length > 0 ? `${shots.length} 镜` : '还没有镜头',
      next: '运行分镜',
    },
    slotStage('keyframes', '首帧', shots, `${base}/director`),
    slotStage('videos', '视频', shots, `${base}/director`),
    {
      key: 'export',
      label: '导出',
      to: `${base}/export`,
      state: exports.length > 0 ? 'done' : 'todo',
      detail: exports.length > 0 ? `${exports.length} 版${finalCut ? ' · 已定稿' : ''}` : '还没有成片',
      next: '合成导出',
    },
  ];

  const first = drafts.findIndex((d) => d.state !== 'done');
  const stages: Stage[] = drafts.map(({ next: _next, ...d }, i) => ({
    ...d,
    state: i === first && d.state === 'todo' ? 'active' : d.state,
  }));
  const next = first >= 0 ? { label: `下一步：${drafts[first].next}`, to: drafts[first].to } : null;
  return { stages, next };
}
