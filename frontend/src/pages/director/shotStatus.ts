// 镜头首帧 / 视频这一格的状态（选定的结果、过期、进行中、失败、新候选）和卡片主按钮该做什么。纯函数，镜头卡片和抽屉共用。
import type { Candidate, JobBrief, ShotDetail } from '../../api/types';
import { type SlotMark, selectedOf, slotMark, slotOf } from './slots';

export type GenStage = 'keyframes' | 'videos';

export const STALE_TIP = '提示词、引擎或参考图改过，这个结果是按旧输入生成的，需要重新生成';

type CellKind = 'first_frame' | 'video';

/** 格子 → 出它的任务类型（types：会出这一格的全部任务类型，首帧还有「接上一镜」截尾帧）和生成阶段 */
export const CELL: Record<CellKind, { type: string; types: string[]; stage: GenStage; label: string }> = {
  first_frame: { type: 'image.keyframe', types: ['image.keyframe', 'image.continue_frame'], stage: 'keyframes', label: '首帧' },
  video: { type: 'video.shot', types: ['video.shot'], stage: 'videos', label: '视频' },
};

export const cellOf = (stage: GenStage) => (stage === 'keyframes' ? CELL.first_frame : CELL.video);
export const kindOf = (stage: GenStage): CellKind => (stage === 'keyframes' ? 'first_frame' : 'video');

/** 这个镜头这一格已经有选定的结果（不管过没过期）：再出就是「重拍」，要强制提交 */
export function hasSelected(shot: ShotDetail, stage: GenStage): boolean {
  return !!selectedOf(slotOf(shot, kindOf(stage)));
}

/** 格子按钮的字：已有选定结果「重拍首帧」，没有「生成首帧」 */
export function genLabel(shot: ShotDetail, stage: GenStage): string {
  return `${hasSelected(shot, stage) ? '重拍' : '生成'}${cellOf(stage).label}`;
}

/** 这个镜头这一格正在排队/生成的任务 */
export function activeJob(shot: ShotDetail, stage: GenStage): JobBrief | undefined {
  const types = cellOf(stage).types;
  return (shot.active_jobs ?? []).find((j) => types.includes(j.type));
}

/** 这个镜头这一格最近一次失败（之后没出过新的） */
export function failedJob(shot: ShotDetail, stage: GenStage): JobBrief | undefined {
  const types = cellOf(stage).types;
  // 接上一镜的镜头在等上一镜视频时，「上一镜还没有视频」那次失败不算（卡片显示「等上一镜视频」）
  if (stage === 'keyframes' && waitsPrevVideo(shot)) return undefined;
  return (shot.failures ?? []).find((j) => types.includes(j.type));
}

/** 开了「接上一镜」、而且有上一镜（第一镜开了也没用，按普通镜头算） */
export function continues(shot: ShotDetail): boolean {
  return !!shot.continue_from_prev && !!shot.continue_prev;
}

/** 接上一镜、上一镜还没有选定视频：首帧没法取，卡片显示「等上一镜视频」 */
export function waitsPrevVideo(shot: ShotDetail): boolean {
  return continues(shot) && !shot.continue_prev!.has_video;
}

/** 进行中任务的一句话：排队中 / 生成中 42% / 等待输入 */
export function activeText(j: JobBrief): string {
  if (j.status === 'queued') return '排队中';
  if (j.status === 'waiting_input') return '等待输入';
  return typeof j.progress === 'number' ? `生成中 ${Math.round(j.progress * 100)}%` : '生成中';
}

export interface CellStatus {
  /** none 没有选定的；ok 选定且最新；stale 选定的是按旧输入出的 */
  mark: SlotMark;
  /** 选定的那个候选 */
  cand?: Candidate;
  /** 进行中的任务 */
  active?: JobBrief;
  /** 最近一次失败（有进行中的任务时不算） */
  failed?: JobBrief;
  /** 还没看过的新候选数 */
  fresh: number;
  /** 过期原因（后端没给时用通用说法） */
  staleReason: string;
}

export function cellStatus(shot: ShotDetail, stage: GenStage): CellStatus {
  const slot = slotOf(shot, kindOf(stage));
  const active = activeJob(shot, stage);
  return {
    mark: slotMark(slot),
    cand: selectedOf(slot),
    active,
    failed: active ? undefined : failedJob(shot, stage),
    fresh: slot?.new_candidates ?? 0,
    staleReason: slot?.stale_reason || STALE_TIP,
  };
}

/** 出片自检发现的片段内部硬切（秒）：后端写在候选 params.qc.hard_cuts 里，没检测过或没有就是空数组 */
export function hardCuts(c?: Candidate): number[] {
  const qc = c?.params?.qc as { hard_cuts?: unknown } | undefined;
  return Array.isArray(qc?.hard_cuts) ? qc.hard_cuts.filter((t): t is number => typeof t === 'number') : [];
}

/** 「疑似跳切」角标的悬停说明 */
export function cutTip(c: Candidate): string {
  return c.note || `疑似跳切：${hardCuts(c).map((t) => `${t.toFixed(1)}s`).join('、')} 处画面突变，建议重拍`;
}

/** 出片自检发现视频开头没接上首帧（第一帧和选定首帧的相似度低，H3 重新构图了）：返回相似度，没有就是 null */
export function startMismatch(c?: Candidate): number | null {
  const qc = c?.params?.qc as { start_mismatch?: unknown; first_frame_ssim?: unknown } | undefined;
  if (qc?.start_mismatch !== true) return null;
  return typeof qc.first_frame_ssim === 'number' ? qc.first_frame_ssim : 0;
}

export interface QcIssue {
  /** 角标上的字 */
  label: string;
  /** 悬停说明 */
  tip: string;
  /** 就绪检查里镜头编号后面括号里的字 */
  short: string;
}

/** 出片自检发现的问题（每个一个角标）：开头没接上首帧、疑似跳切、疑似声音性别不对 */
export function qcIssues(c?: Candidate): QcIssue[] {
  if (!c) return [];
  const out: QcIssue[] = [];
  const ssim = startMismatch(c);
  if (ssim !== null) {
    out.push({
      label: '开头没接上首帧',
      tip: `开头没接上首帧：视频第一帧和选定的首帧不像（相似度 ${ssim.toFixed(2)}），可能重新构图了，建议重拍`,
      short: '开头没接上首帧',
    });
  }
  const cuts = hardCuts(c);
  if (cuts.length > 0) {
    out.push({ label: '疑似跳切', tip: cutTip(c), short: cuts.map((t) => `${t.toFixed(1)}s`).join('、') });
  }
  const voices = voiceMismatch(c);
  if (voices.length > 0) {
    const want = (g: string) => (g === 'male' ? '男声' : '女声');
    const heard = (g: string) => (g === 'male' ? '女声' : '男声');
    const parts = voices.map(
      (v) => `${v.speakers.join('、') || '这句'}的台词应是${want(v.expected)}，测到的音高约 ${Math.round(v.f0)} Hz（像${heard(v.expected)}）`,
    );
    out.push({
      label: '疑似声音性别不对',
      tip: `疑似声音性别不对：${parts.join('；')}，可能把台词给了画面里的人，建议听一下，不对就重拍`,
      short: '声音性别不对',
    });
  }
  const ghosts = ghostRefs(c);
  if (ghosts.length > 0) {
    const parts = ghosts.map(
      (g) => `${g.t.toFixed(1)}s 前后画面和${g.ref || '参考图'}很像（相似度 ${g.ssim.toFixed(2)}，开头 ${g.base.toFixed(2)}）`,
    );
    out.push({
      label: '疑似叠入参考图',
      tip: `疑似叠入参考图：${parts.join('；')}，可能把参考图（影棚灰底定妆照）半透明叠进了画面，建议看一下，不对就重拍`,
      short: '叠入参考图',
    });
  }
  return out;
}

export interface GhostRef {
  ref: string;
  t: number;
  ssim: number;
  base: number;
}

/** 出片自检：视频中段某帧和这条视频带的某张参考图明显像（后端 params.qc.ghost_refs） */
export function ghostRefs(c?: Candidate): GhostRef[] {
  const qc = c?.params?.qc as { ghost_refs?: unknown } | undefined;
  if (!Array.isArray(qc?.ghost_refs)) return [];
  const num = (x: unknown) => (typeof x === 'number' ? x : 0);
  return qc.ghost_refs
    .filter((g): g is Record<string, unknown> => !!g && typeof g === 'object')
    .map((g) => ({ ref: typeof g.ref === 'string' ? g.ref : '', t: num(g.t), ssim: num(g.ssim), base: num(g.base) }));
}

export interface VoiceMismatch {
  speakers: string[];
  expected: string;
  f0: number;
}

/** 出片自检粗查的声音性别：台词说话人是男声 / 女声，音轨里测到的基频明显对不上（后端 params.qc.voice_mismatch） */
export function voiceMismatch(c?: Candidate): VoiceMismatch[] {
  const qc = c?.params?.qc as { voice_mismatch?: unknown } | undefined;
  if (!Array.isArray(qc?.voice_mismatch)) return [];
  return qc.voice_mismatch
    .filter((v): v is Record<string, unknown> => !!v && typeof v === 'object')
    .map((v) => ({
      speakers: Array.isArray(v.speakers) ? v.speakers.filter((x): x is string => typeof x === 'string') : [],
      expected: typeof v.expected === 'string' ? v.expected : '',
      f0: typeof v.f0 === 'number' ? v.f0 : 0,
    }))
    .filter((v) => v.expected === 'male' || v.expected === 'female');
}

/** 能拿来出视频的首帧：选定了、而且不过期（过期的先回首帧阶段重拍，不拿旧首帧出视频） */
export function firstFrameReady(shot: ShotDetail): boolean {
  // 接上一镜：出视频前后端会先取上一镜的尾帧，只要上一镜有视频就行
  if (continues(shot)) return shot.continue_prev!.has_video;
  return slotMark(slotOf(shot, 'first_frame')) === 'ok';
}

export type CardActionKind = 'busy' | 'wait_prev' | 'need_first_frame' | 'retry' | 'redo' | 'generate';

export interface CardAction {
  kind: CardActionKind;
  /** 按钮上的字 */
  label: string;
  /** 无障碍名（再加镜号），如「重试首帧」 */
  aria: string;
}

/**
 * 镜头卡片唯一的按钮：进行中「生成中」（禁用）；接上一镜而上一镜还没有视频「等上一镜视频」（禁用）；
 * 视频阶段首帧缺少「先出首帧」、过期「先重拍首帧」；失败「重试」（强制）；
 * 已有选定结果「重拍…」（强制）；否则「生成…」。
 */
export function cardAction(shot: ShotDetail, stage: GenStage): CardAction {
  const label = cellOf(stage).label;
  if (activeJob(shot, stage)) return { kind: 'busy', label: '生成中', aria: `${label}生成中` };
  if (waitsPrevVideo(shot)) return { kind: 'wait_prev', label: '等上一镜视频', aria: '等上一镜视频' };
  if (stage === 'videos' && !firstFrameReady(shot)) {
    const what = hasSelected(shot, 'keyframes') ? '先重拍首帧' : '先出首帧';
    return { kind: 'need_first_frame', label: what, aria: what };
  }
  if (failedJob(shot, stage)) return { kind: 'retry', label: '重试', aria: `重试${label}` };
  const what = genLabel(shot, stage);
  return { kind: hasSelected(shot, stage) ? 'redo' : 'generate', label: what, aria: what };
}
