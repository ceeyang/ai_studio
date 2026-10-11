import type { JobStatus } from './api/types';

/** 后端全部任务类型（backend 里 HANDLERS.register 登记的）→ 中文名。新增任务类型时这里也要补。 */
export const JOB_TYPE_LABEL: Record<string, string> = {
  script: '剧本',
  storyboard: '分镜',
  'script.review': '剧本体检',
  'image.keyframe': '首帧',
  'image.continue_frame': '接上一镜尾帧',
  'image.location': '场景图',
  'image.prop': '道具图',
  'video.shot': '视频',
  'export.episode': '合成导出',
  'image.portrait': '定妆照',
  'image.turnaround': '三视图',
  'character.suggest': '推荐造型',
  'character.recommend': '推荐角色',
  'character.turnaround_look': '三视图描述',
  'character.from_photo': '参考图写外观',
  'post.dub': '配音',
  'subtitles.translate': '字幕翻译',
  'dialogue.fit_khmer': '高棉语台词适配',
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

/** 引擎 id → 界面上的中文名。网页出图的名字里已经带「网页」，和本机显卡上的区分开。 */
export const ENGINE_LABEL: Record<string, string> = {
  zimage: 'Z-Image 快速',
  h3_image: 'H3 图像',
  krea2: 'Krea2 人像',
  gpt_image: 'ChatGPT 网页',
  gemini_image: 'Gemini 网页',
  muse_image: 'Muse 网页',
  gemini_video: 'Gemini 网页（Veo · 有声）',
  muse_video: 'Muse 网页（有声）',
  jimeng_video: '即梦网页（Seedance · 有声）',
  kling_web_video: '可灵网页（有声）',
  h3_video: 'H3 视频（有声）',
  wan22_i2v: 'Wan2.2 图生视频（无声）',
  // 官方 API（Key 在设置页「API 服务」里填）
  openai_image: 'OpenAI 图像（API）',
  gemini_api_image: 'Gemini 图像（API）',
  veo_video: 'Google Veo（API · 有声）',
  sora_video: 'OpenAI Sora（API · 有声）',
  seedance_video: 'Seedance（火山方舟 API · 有声）',
  kling_video: '可灵（API · 有声）',
  // 任务列表里显示的是模型族：和引擎 id 不同的补上中文名
  h3: 'H3',
  wan22: 'Wan2.2',
  gemini_web: 'Gemini 网页',
  chatgpt_web: 'ChatGPT 网页',
  claude_web: 'Claude 网页',
  openai_api_image: 'OpenAI 图像（API）',
  sora_api: 'OpenAI Sora（API）',
  veo_api: 'Google Veo（API）',
  seedance_api: 'Seedance（火山方舟 API）',
  kling_api: '可灵（API）',
  jimeng_web: '即梦网页',
  kling_web: '可灵网页',
  muse_web: 'Muse 网页',
  // 不是引擎：候选的来源
  upload: '上传',
  continue: '接上一镜尾帧',
  asset: '已有图片',
};

/** 下拉框里引擎的写法：API 引擎没配 Key 时后面写明（同时 disabled），别的不可用写「现在不可用」 */
export function engineOptionText(e: {
  name: string; runtime?: string; label?: string; ready?: boolean; hint?: string | null;
}): string {
  const hint = engineUnreadyHint(e);
  return hint ? `${engineLabel(e)}（${hint}）` : engineLabel(e);
}

/** 引擎现在用不了时的短说明（下拉框括号里、表单下面的提示用）；能用时返回 null。
 *  后端给了 hint 就用它（即梦 / 可灵在老扩展上是「扩展需要重新加载」） */
export function engineUnreadyHint(e: { runtime?: string; ready?: boolean; hint?: string | null }): string | null {
  if (e.ready !== false) return null;
  if (e.hint) return e.hint;
  if (e.runtime === 'api') return '先在 API 服务里填 Key';
  if (e.runtime === 'comfyui') return '未配置：ComfyUI 没连上';
  if (e.runtime === 'browser') return '未配置：浏览器扩展没连上';
  return '现在不可用';
}

/** 没配 Key 的 API 引擎在下拉框里禁用（别的不可用引擎照旧能选，提交时再报原因） */
export function engineOptionDisabled(e: { runtime?: string; ready?: boolean }): boolean {
  return e.runtime === 'api' && e.ready === false;
}

/** 引擎名字：传 id 或 /api/engines 的一行都行；没见过的用后端给的 label，再回退到 id
 *  （浏览器引擎标上「浏览器」，RunningHub 云端引擎标上「RunningHub 云端」）。 */
export function engineLabel(e: string | { name: string; runtime?: string; label?: string }): string {
  const name = typeof e === 'string' ? e : e.name;
  const known = ENGINE_LABEL[name];
  if (known) return known;
  if (typeof e === 'string') return name;
  const base = e.label && e.label !== name ? e.label : name;
  if (e.runtime === 'runninghub') return `${base}（RunningHub 云端）`;
  if (e.runtime === 'browser') return `${base}（浏览器）`;
  return base;
}

export function jobTypeLabel(t: string): string {
  return JOB_TYPE_LABEL[t] ?? '其他任务';
}

export interface VoiceOption {
  id: string;
  label: string;
}

/** 配音常用音色（Edge TTS 的音色 id）。下拉框显示 label，存 id。 */
export const VOICES: VoiceOption[] = [
  { id: 'zh-CN-XiaoxiaoNeural', label: '普通话 · 女声 晓晓（温暖）' },
  { id: 'zh-CN-XiaoyiNeural', label: '普通话 · 女声 晓伊（活泼）' },
  { id: 'zh-CN-YunxiNeural', label: '普通话 · 男声 云希（阳光）' },
  { id: 'zh-CN-YunjianNeural', label: '普通话 · 男声 云健（沉稳）' },
  { id: 'km-KH-PisethNeural', label: '高棉语 · 男声' },
  { id: 'km-KH-SreymomNeural', label: '高棉语 · 女声' },
];

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

/** 列表里用的短时间：「10/10 20:29」，年份和秒在详情里看。 */
export function fmtShortTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = parse(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p2 = (n: number) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}/${d.getDate()} ${p2(d.getHours())}:${p2(d.getMinutes())}`;
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
