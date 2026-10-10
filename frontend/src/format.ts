import type { JobStatus } from './api/types';

export const JOB_TYPE_LABEL: Record<string, string> = {
  script: '剧本',
  storyboard: '分镜',
  'image.keyframe': '首帧',
  'video.shot': '视频',
  'export.episode': '合成导出',
};

export const JOB_STATUS_LABEL: Record<JobStatus, string> = {
  queued: '排队中',
  running: '运行中',
  waiting_input: '等待输入',
  done: '完成',
  failed: '失败',
  canceled: '已取消',
};

export const EPISODE_STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  scripted: '已出剧本',
  storyboarded: '已分镜',
  exported: '已导出',
};

export const ACTIVE_STATUSES: JobStatus[] = ['queued', 'running', 'waiting_input'];

/** 引擎在下拉框里的名字：网页出图的引擎标上「浏览器」，和本机显卡上的区分开。 */
export function engineLabel(e: { name: string; runtime?: string }): string {
  return e.runtime === 'browser' ? `${e.name}（浏览器）` : e.name;
}

export function jobTypeLabel(t: string): string {
  return JOB_TYPE_LABEL[t] ?? t;
}

export function jobStatusLabel(s: string): string {
  return (JOB_STATUS_LABEL as Record<string, string>)[s] ?? s;
}

/** 后端有的时间带 Z（任务），有的不带（候选、成片，存的是 UTC）：不带时区的一律按 UTC。 */
function parse(iso: string): Date {
  return new Date(/[zZ]$|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = parse(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('zh-CN', { hour12: false });
}

export function fmtDuration(
  start: string | null | undefined,
  end: string | null | undefined,
  now: number = Date.now(),
): string {
  if (!start) return '—';
  const ms = (end ? parse(end).getTime() : now) - parse(start).getTime();
  if (!(ms >= 0)) return '—';
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s} 秒` : `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
}
