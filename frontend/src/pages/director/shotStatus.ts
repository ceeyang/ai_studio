// 镜头首帧 / 视频这一格的状态（选定的结果、过期、进行中、失败、新候选）和卡片主按钮该做什么。纯函数，镜头卡片和抽屉共用。
import type { Candidate, JobBrief, ShotDetail } from '../../api/types';
import { type SlotMark, selectedOf, slotMark, slotOf } from './slots';

export type GenStage = 'keyframes' | 'videos';

export const STALE_TIP = '提示词、引擎或参考图改过，这个结果是按旧输入生成的，需要重新生成';

type CellKind = 'first_frame' | 'video';

/** 格子 → 出它的任务类型和生成阶段 */
export const CELL: Record<CellKind, { type: string; stage: GenStage; label: string }> = {
  first_frame: { type: 'image.keyframe', stage: 'keyframes', label: '首帧' },
  video: { type: 'video.shot', stage: 'videos', label: '视频' },
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
  const type = cellOf(stage).type;
  return (shot.active_jobs ?? []).find((j) => j.type === type);
}

/** 这个镜头这一格最近一次失败（之后没出过新的） */
export function failedJob(shot: ShotDetail, stage: GenStage): JobBrief | undefined {
  const type = cellOf(stage).type;
  return (shot.failures ?? []).find((j) => j.type === type);
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

/** 能拿来出视频的首帧：选定了、而且不过期（过期的先回首帧阶段重拍，不拿旧首帧出视频） */
export function firstFrameReady(shot: ShotDetail): boolean {
  return slotMark(slotOf(shot, 'first_frame')) === 'ok';
}

export type CardActionKind = 'busy' | 'need_first_frame' | 'retry' | 'redo' | 'generate';

export interface CardAction {
  kind: CardActionKind;
  /** 按钮上的字 */
  label: string;
  /** 无障碍名（再加镜号），如「重试首帧」 */
  aria: string;
}

/**
 * 镜头卡片唯一的按钮：进行中「生成中」（禁用）；视频阶段首帧缺少「先出首帧」、过期「先重拍首帧」；失败「重试」（强制）；
 * 已有选定结果「重拍…」（强制）；否则「生成…」。
 */
export function cardAction(shot: ShotDetail, stage: GenStage): CardAction {
  const label = cellOf(stage).label;
  if (activeJob(shot, stage)) return { kind: 'busy', label: '生成中', aria: `${label}生成中` };
  if (stage === 'videos' && !firstFrameReady(shot)) {
    const what = hasSelected(shot, 'keyframes') ? '先重拍首帧' : '先出首帧';
    return { kind: 'need_first_frame', label: what, aria: what };
  }
  if (failedJob(shot, stage)) return { kind: 'retry', label: '重试', aria: `重试${label}` };
  const what = genLabel(shot, stage);
  return { kind: hasSelected(shot, stage) ? 'redo' : 'generate', label: what, aria: what };
}
