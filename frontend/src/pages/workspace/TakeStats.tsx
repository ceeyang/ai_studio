// 抽卡统计（首帧 / 视频阶段）：镜头卡片上一行「首帧 3 抽 · 视频 5 抽（选第 4 条）」，
// 工具栏下面一行本集合计「本集共抽 N 次（出成 A 条，失败 M 次），视频可用率 X%，平均每镜 Y 抽；按引擎：…」。
// 「抽」一律指尝试次数（出成的 + 失败的），失败数放括号里，免得读成「抽 6 次失败 7 次」。
// 数据来自 GET /api/episodes/{id}/take-stats，挂在 ['episode', id] 下，任务结束时跟着刷新。
import type { EngineTakes, KindTakes, ShotTakes, TakeStats } from '../../api/types';
import { engineLabel } from '../../format';

const KIND_LABEL: Record<string, string> = { first_frame: '首帧', video: '视频' };

function kindText(label: string, k: KindTakes): string | null {
  if (k.takes === 0 && k.failed === 0) return null;
  const notes: string[] = [];
  if (k.failed > 0) notes.push(`失败 ${k.failed}`);
  // 出成不止一条才说选第几条（只有一条时不言自明）
  if (k.selected_index != null && k.takes > 1) notes.push(`选第 ${k.selected_index} 条`);
  const s = `${label} ${k.takes + k.failed} 抽`;
  return notes.length > 0 ? `${s}（${notes.join('，')}）` : s;
}

/** 镜头卡片上的一行；首帧和视频都还没抽过时为 null */
export function shotTakesText(t: ShotTakes | undefined): string | null {
  if (!t) return null;
  const parts = [kindText('首帧', t.first_frame), kindText('视频', t.video)].filter((x): x is string => !!x);
  return parts.length > 0 ? parts.join(' · ') : null;
}

function engineText(e: EngineTakes): string {
  const s = `${KIND_LABEL[e.kind] ?? e.kind} ${e.engine ? engineLabel(e.engine) : '未知引擎'} ${e.takes + e.failed} 抽`;
  return e.failed > 0 ? `${s}（失败 ${e.failed}）` : s;
}

const pct = (r: number) => `${Math.round(r * 100)}%`;

/** 本集合计的一句话；一次都还没抽过时为 null */
export function takeStatsSummary(st: TakeStats): string | null {
  if (st.total_takes === 0 && st.failed === 0) return null;
  const head = `本集共抽 ${st.total_takes + st.failed} 次`;
  const parts = [st.failed > 0 ? `${head}（出成 ${st.total_takes} 条，失败 ${st.failed} 次）` : head];
  if (st.usable_rate != null) parts.push(`视频可用率 ${pct(st.usable_rate)}`);
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
    `视频可用率 = 有选定视频的镜头（${stats.shots_with_video}）÷ 视频总抽数（${stats.video_takes}）；` +
    '平均每镜按有选定视频的镜头算，首帧 + 视频，含失败';
  return (
    <p className="muted small take-stats" role="note" data-testid="take-stats" title={tip}>
      {text}
    </p>
  );
}
