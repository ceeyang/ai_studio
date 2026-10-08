import type { Candidate, ShotDetail, Slot } from '../../api/types';

export const SLOT_LABEL: Record<string, string> = { first_frame: '首帧', end_frame: '尾帧', video: '视频' };

export type SlotMark = 'none' | 'ok' | 'stale';

export function slotOf(shot: ShotDetail, kind: string): Slot | undefined {
  return shot.slots.find((s) => s.kind === kind);
}

export function selectedOf(slot: Slot | undefined): Candidate | undefined {
  if (!slot || slot.selected_candidate_id == null) return undefined;
  return slot.candidates.find((c) => c.id === slot.selected_candidate_id);
}

/** 过期只对"选定的那个候选"有意义：后端按当前输入哈希比对后给出 slot.stale。 */
export function slotMark(slot: Slot | undefined): SlotMark {
  if (!slot || !selectedOf(slot)) return 'none';
  return slot.stale ? 'stale' : 'ok';
}

/** 把 id 和相邻的交换位置；越界返回 null。 */
export function moveId(ids: number[], id: number, delta: -1 | 1): number[] | null {
  const i = ids.indexOf(id);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= ids.length) return null;
  const next = [...ids];
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}
