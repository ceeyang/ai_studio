import { describe, expect, it } from 'vitest';
import { makeCandidate, makeShot, makeSlot } from '../../test/factories';
import { moveId, selectedOf, slotMark, slotOf } from './slots';

describe('slots', () => {
  it('没有槽位或没选定候选算"未生成"；选定了看 stale', () => {
    expect(slotMark(undefined)).toBe('none');
    expect(slotMark(makeSlot({ candidates: [makeCandidate({ id: 5 })] }))).toBe('none');
    const ok = makeSlot({ selected_candidate_id: 5, candidates: [makeCandidate({ id: 5 })] });
    expect(slotMark(ok)).toBe('ok');
    expect(slotMark({ ...ok, stale: true })).toBe('stale');
    expect(selectedOf(ok)?.id).toBe(5);
  });

  it('按 kind 找槽位', () => {
    const shot = makeShot({ slots: [makeSlot({ id: 1, kind: 'first_frame' }), makeSlot({ id: 2, kind: 'video' })] });
    expect(slotOf(shot, 'video')?.id).toBe(2);
    expect(slotOf(shot, 'end_frame')).toBeUndefined();
  });

  it('moveId 交换相邻位置，越界返回 null', () => {
    expect(moveId([1, 2, 3], 2, -1)).toEqual([2, 1, 3]);
    expect(moveId([1, 2, 3], 2, 1)).toEqual([1, 3, 2]);
    expect(moveId([1, 2, 3], 1, -1)).toBeNull();
    expect(moveId([1, 2, 3], 3, 1)).toBeNull();
    expect(moveId([1, 2, 3], 9, 1)).toBeNull();
  });
});
