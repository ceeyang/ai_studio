// 抽卡统计（首帧 / 视频阶段）：镜头卡片上一行「首帧 3 抽 · 视频 5 抽（选第 4 条）」，
// 工具栏下面一行本集合计「本集共抽 N 次，失败 M 次，可用率 X%，平均每镜 Y 抽；按引擎：…」。
// 数据来自 GET /api/episodes/{id}/take-stats，挂在 ['episode', id] 下，任务结束时跟着刷新。
import type { EngineTakes, KindTakes, ShotTakes, TakeStats } from '../../api/types';
import { engineLabel } from '../../format';

const KIND_LABEL: Record<string, string> = { first_frame: '首帧', video: '视频' };

function kindText(label: string, k: KindTakes): string | null {
  if (k.takes === 0 && k.failed === 0) return null;
  let s = `${label} ${k.takes} 抽`;
  if (k.failed > 0) s += `，失败 ${k.failed}`;
  // 抽了不止一条才说选第几条（只有一条时不言自明）
  if (k.selected_index != null && k.takes > 1) s += `（选第 ${k.selected_index} 条）`;
  return s;
}

/** 镜头卡片上的一行；首帧和视频都还没抽过时为 null */
export function shotTakesText(t: ShotTakes | undefined): string | null {
  if (!t) return null;
  const parts = [kindText('首帧', t.first_frame), kindText('视频', t.video)].filter((x): x is string => !!x);
  return parts.length > 0 ? parts.join(' · ') : null;
}

function engineText(e: EngineTakes): string {
  const s = `${KIND_LABEL[e.kind] ?? e.kind} ${e.engine ? engineLabel(e.engine) : '未知引擎'} ${e.takes} 抽`;
  return e.failed > 0 ? `${s}（失败 ${e.failed}）` : s;
}

const pct = (r: number) => `${Math.round(r * 100)}%`;

/** 本集合计的一句话；一次都还没抽过时为 null */
export function takeStatsSummary(st: TakeStats): string | null {
  if (st.total_takes === 0 && st.failed === 0) return null;
  const parts = [`本集共抽 ${st.total_takes} 次`, `失败 ${st.failed} 次`];
  if (st.usable_rate != null) parts.push(`可用率 ${pct(st.usable_rate)}`);
  if (st.avg_takes_per_finished_shot != null) parts.push(`平均每镜 ${st.avg_takes_per_finished_shot.toFixed(1)} 抽`);
  let s = parts.join('，');
  if (st.engines.length > 0) s += `；按引擎：${st.engines.map(engineText).join('、')}`;
  const archived = Object.values(st.archived_takes).reduce((a, b) => a + b, 0);
  if (archived > 0) s += `；废弃镜头的抽卡 ${archived} 条`;
  return s;
}

/** 工具栏下面那一行（不抢眼：小号灰字） */
export function TakeStatsLine({ stats }: { stats: TakeStats | undefined }) {
  const text = stats ? takeStatsSummary(stats) : null;
  if (!text || !stats) return null;
  const tip =
    `可用率 = 有选定视频的镜头（${stats.shots_with_video}）÷ 视频总抽数（${stats.video_takes}）；` +
    '平均每镜按有选定视频的镜头算，首帧 + 视频，含失败';
  return (
    <p className="muted small take-stats" role="note" data-testid="take-stats" title={tip}>
      {text}
    </p>
  );
}
