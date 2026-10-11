// 任务页的纯函数：标题、类型分组、相对时间、日期分组、失败原因摘要、搜索匹配。
import type { Job, JobStatus } from '../../api/types';
import { engineLabel, jobTypeLabel } from '../../format';

/** 列表和详情里的任务名：任务类型 + 它在说哪一集 / 哪个镜头 / 哪个角色 */
export function jobTitle(j: Job): string {
  const label = j.context?.label;
  return label ? `${jobTypeLabel(j.type)} · ${label}` : jobTypeLabel(j.type);
}

/** 类型筛选：按用户关心的「步骤」分组（一个分组可能对应好几种后端任务类型） */
export interface TypeGroup {
  value: string;
  label: string;
  types: string[];
}

export const TYPE_GROUPS: TypeGroup[] = [
  { value: 'script', label: '剧本', types: ['script'] },
  { value: 'storyboard', label: '分镜', types: ['storyboard'] },
  { value: 'portrait', label: '定妆照', types: ['image.portrait'] },
  { value: 'turnaround', label: '三视图', types: ['image.turnaround', 'character.turnaround_look'] },
  { value: 'character', label: '角色推荐', types: ['character.suggest', 'character.recommend', 'character.from_photo'] },
  { value: 'scene', label: '场景 / 道具', types: ['image.location', 'image.prop'] },
  { value: 'keyframe', label: '首帧', types: ['image.keyframe', 'image.continue_frame'] },
  { value: 'video', label: '视频', types: ['video.shot'] },
  { value: 'post', label: '配音 / 字幕', types: ['post.dub', 'subtitles.translate', 'dialogue.fit_khmer'] },
  { value: 'export', label: '导出', types: ['export.episode'] },
];

/** 任务类型 → 分组 value（用来给类型色点上色）；没登记的归到 other */
export function typeGroupOf(type: string): string {
  return TYPE_GROUPS.find((g) => g.types.includes(type))?.value ?? 'other';
}

/** 状态分段：顺序就是页面上的顺序 */
export const STATUS_SEGMENTS: { value: '' | JobStatus; label: string }[] = [
  { value: '', label: '全部' },
  { value: 'running', label: '运行中' },
  { value: 'waiting_input', label: '等待输入' },
  { value: 'queued', label: '排队' },
  { value: 'failed', label: '失败' },
  { value: 'done', label: '完成' },
  { value: 'canceled', label: '已取消' },
];

/** 后端有的时间带 Z，有的不带（存的是 UTC）：不带时区的一律按 UTC。 */
export function parseTime(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(/[zZ]$|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** 「刚刚」「3 分钟前」「2 小时前」「昨天 20:29」「10/3 20:29」 */
export function relTime(iso: string | null | undefined, now: number = Date.now()): string {
  const d = parseTime(iso);
  if (!d) return '—';
  const s = Math.round((now - d.getTime()) / 1000);
  if (s < 45) return '刚刚';
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} 分钟前`;
  const p2 = (n: number) => String(n).padStart(2, '0');
  const hm = `${p2(d.getHours())}:${p2(d.getMinutes())}`;
  const g = dateGroup(iso, now);
  if (g === 'today') return `${Math.round(s / 3600)} 小时前`;
  if (g === 'yesterday') return `昨天 ${hm}`;
  return `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

export type DateGroup = 'today' | 'yesterday' | 'earlier';
export const DATE_GROUP_LABEL: Record<DateGroup, string> = { today: '今天', yesterday: '昨天', earlier: '更早' };

/** 按本地日期分：今天 / 昨天 / 更早（没有时间的算更早） */
export function dateGroup(iso: string | null | undefined, now: number = Date.now()): DateGroup {
  const d = parseTime(iso);
  if (!d) return 'earlier';
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  const t = d.getTime();
  if (t >= start.getTime()) return 'today';
  if (t >= start.getTime() - 86_400_000) return 'yesterday';
  return 'earlier';
}

/** 按日期分组，组内保持原顺序；空组不出现 */
export function groupByDate(jobs: Job[], now: number = Date.now()): { group: DateGroup; jobs: Job[] }[] {
  const out: { group: DateGroup; jobs: Job[] }[] = [];
  for (const j of jobs) {
    const g = dateGroup(j.created_at ?? j.started_at, now);
    const last = out[out.length - 1];
    if (last && last.group === g) last.jobs.push(j);
    else out.push({ group: g, jobs: [j] });
  }
  return out;
}

/** 失败原因的一行摘要：只取第一行非空的内容，超长截断加省略号 */
export function errorBrief(error: string | null | undefined, max = 80): string {
  if (!error) return '';
  const first = error.split('\n').map((l) => l.trim()).find(Boolean) ?? '';
  return first.length > max ? `${first.slice(0, max - 1)}…` : first;
}

/** 任务用的引擎 / 模型：payload 里的 engine 优先，再退到模型族 */
export function jobEngine(j: Job): string {
  const e = j.payload?.engine;
  if (typeof e === 'string' && e) return engineLabel(e);
  return j.model_family ?? '';
}

/** 搜索：#id、编号、任务类型中文名、所属对象（项目 / 集 / 镜头）、引擎都能搜；空格分开的词都要命中 */
export function matchesSearch(j: Job, q: string): boolean {
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const hay = [jobTitle(j), j.type, j.context?.project_slug ?? '', jobEngine(j), j.error ?? ''].join(' ').toLowerCase();
  // 纯数字 / #数字 按编号精确匹配（输 29 不会命中 #293），也允许命中标题里的数字（如「第 12 集」）
  return words.every((w) => (/^#?\d+$/.test(w) ? j.id === Number(w.replace('#', '')) || (!w.startsWith('#') && hay.includes(w)) : hay.includes(w)));
}

/** 已经过了多少秒（排队 / 运行耗时的实时显示），格式和 fmtDuration 一致 */
export function fmtSeconds(sec: number): string {
  if (!(sec >= 0)) return '—';
  const s = Math.round(sec);
  if (s < 60) return `${s} 秒`;
  if (s < 3600) return `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
  return `${Math.floor(s / 3600)} 小时 ${Math.floor((s % 3600) / 60)} 分`;
}

export function secondsBetween(a: string | null | undefined, b: string | null | undefined, now = Date.now()): number | null {
  const s = parseTime(a);
  if (!s) return null;
  const e = b ? parseTime(b) : null;
  return ((e ? e.getTime() : now) - s.getTime()) / 1000;
}
