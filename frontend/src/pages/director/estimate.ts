// 批量生成前的耗时预估：按最近同类已完成任务的平均耗时（started_at → finished_at）估算。
import type { Job } from '../../api/types';
import { jobTypeLabel } from '../../format';

const SAMPLE = 20;

function ms(iso: string | null): number {
  if (!iso) return NaN;
  return new Date(/[zZ]$|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`).getTime();
}

/** 同类型最近 SAMPLE 条已完成任务的平均秒数（四舍五入）；没有可用样本时 null。 */
export function avgSeconds(jobs: Job[], type: string): number | null {
  const spans = jobs
    .filter((j) => j.type === type && j.status === 'done')
    .map((j) => ({ end: ms(j.finished_at), dur: (ms(j.finished_at) - ms(j.started_at)) / 1000 }))
    .filter((x) => Number.isFinite(x.dur) && x.dur > 0)
    .sort((a, b) => b.end - a.end)
    .slice(0, SAMPLE);
  if (spans.length === 0) return null;
  return Math.round(spans.reduce((a, x) => a + x.dur, 0) / spans.length);
}

function fmtAvg(s: number): string {
  return s < 60 ? `${s} 秒` : s % 60 === 0 ? `${s / 60} 分钟` : `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
}

function fmtTotal(s: number): string {
  if (s < 60) return '不到 1 分钟';
  const min = Math.round(s / 60);
  return min >= 120 ? `约 ${(min / 60).toFixed(1)} 小时` : `约 ${min} 分钟`;
}

export interface EstimatePart {
  /** 任务类型：image.keyframe / video.shot */
  type: string;
  n: number;
}

/** 「将提交 12 个首帧任务，按最近同类任务平均 58 秒，约 12 分钟，会占用显卡」；没有样本写「耗时未知」。 */
export function estimateText(parts: EstimatePart[], jobs: Job[]): string {
  const list = parts.filter((p) => p.n > 0);
  const what = list.map((p) => `${p.n} 个${jobTypeLabel(p.type)}任务`).join('、');
  const avgs = list.map((p) => avgSeconds(jobs, p.type));
  if (avgs.some((a) => a === null)) return `将提交 ${what}，耗时未知，会占用显卡`;
  const total = list.reduce((a, p, i) => a + p.n * (avgs[i] as number), 0);
  if (list.length === 1) return `将提交 ${what}，按最近同类任务平均 ${fmtAvg(avgs[0] as number)}，${fmtTotal(total)}，会占用显卡`;
  return `将提交 ${what}，按最近同类任务平均算${fmtTotal(total)}，会占用显卡`;
}
