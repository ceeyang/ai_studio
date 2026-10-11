import { describe, expect, it } from 'vitest';
import type { Slot } from '../../api/types';
import { makeCandidate, makeShot, makeSlot } from '../../test/factories';
import { activeText, cardAction, cellStatus, genLabel, hasSelected, qcIssues } from './shotStatus';

function chosen(id: number, kind: string, extra: Partial<Slot> = {}): Slot {
  return makeSlot({ id, kind, selected_candidate_id: id * 10, candidates: [makeCandidate({ id: id * 10, url: `/m/${id}` })], ...extra });
}

describe('shotStatus', () => {
  it('cellStatus：没选定 = none；选定且最新 = ok；过期带原因；新候选数', () => {
    const shot = makeShot({
      slots: [chosen(1, 'first_frame', { stale: true, stale_reason: '定妆照换了' }), chosen(2, 'video', { new_candidates: 2 })],
    });
    const kf = cellStatus(shot, 'keyframes');
    expect(kf.mark).toBe('stale');
    expect(kf.staleReason).toBe('定妆照换了');
    expect(kf.cand?.url).toBe('/m/1');
    const vid = cellStatus(shot, 'videos');
    expect(vid.mark).toBe('ok');
    expect(vid.fresh).toBe(2);
    expect(cellStatus(makeShot(), 'keyframes').mark).toBe('none');
  });

  it('cellStatus：有进行中的任务时不算失败', () => {
    const shot = makeShot({
      active_jobs: [{ id: 1, type: 'image.keyframe', status: 'running', progress: 0.42 }],
      failures: [{ id: 2, type: 'image.keyframe', status: 'failed', error: 'x' }, { id: 3, type: 'video.shot', status: 'failed', error: '显存不足' }],
    });
    expect(cellStatus(shot, 'keyframes').active?.id).toBe(1);
    expect(cellStatus(shot, 'keyframes').failed).toBeUndefined();
    expect(cellStatus(shot, 'videos').failed?.error).toBe('显存不足');
  });

  it('activeText：排队中 / 生成中 xx% / 等待输入', () => {
    expect(activeText({ id: 1, type: 'video.shot', status: 'queued' })).toBe('排队中');
    expect(activeText({ id: 1, type: 'video.shot', status: 'running', progress: 0.421 })).toBe('生成中 42%');
    expect(activeText({ id: 1, type: 'video.shot', status: 'running' })).toBe('生成中');
    expect(activeText({ id: 1, type: 'video.shot', status: 'waiting_input' })).toBe('等待输入');
  });

  it('hasSelected / genLabel：已有选定结果 = 重拍，否则生成', () => {
    const shot = makeShot({ slots: [chosen(1, 'first_frame')] });
    expect(hasSelected(shot, 'keyframes')).toBe(true);
    expect(hasSelected(shot, 'videos')).toBe(false);
    expect(genLabel(shot, 'keyframes')).toBe('重拍首帧');
    expect(genLabel(shot, 'videos')).toBe('生成视频');
  });

  it('cardAction：首帧阶段 生成首帧 / 重拍首帧 / 重试 / 生成中', () => {
    expect(cardAction(makeShot(), 'keyframes')).toEqual({ kind: 'generate', label: '生成首帧', aria: '生成首帧' });
    expect(cardAction(makeShot({ slots: [chosen(1, 'first_frame')] }), 'keyframes')).toEqual({ kind: 'redo', label: '重拍首帧', aria: '重拍首帧' });
    const failed = makeShot({ failures: [{ id: 2, type: 'image.keyframe', status: 'failed', error: 'x' }] });
    expect(cardAction(failed, 'keyframes')).toEqual({ kind: 'retry', label: '重试', aria: '重试首帧' });
    const busy = makeShot({ active_jobs: [{ id: 1, type: 'image.keyframe', status: 'queued' }] });
    expect(cardAction(busy, 'keyframes')).toEqual({ kind: 'busy', label: '生成中', aria: '首帧生成中' });
  });

  it('cardAction：视频阶段 没首帧 = 先出首帧；有首帧 生成视频 / 重拍视频；失败 重试', () => {
    expect(cardAction(makeShot(), 'videos')).toEqual({ kind: 'need_first_frame', label: '先出首帧', aria: '先出首帧' });
    // 首帧过期：先回首帧阶段重拍首帧，不拿旧首帧出视频
    const kf = makeShot({ slots: [chosen(1, 'first_frame', { stale: true })] });
    expect(cardAction(kf, 'videos')).toEqual({ kind: 'need_first_frame', label: '先重拍首帧', aria: '先重拍首帧' });
    expect(cardAction(makeShot({ slots: [chosen(1, 'first_frame', { stale: true }), chosen(2, 'video')] }), 'videos').label).toBe('先重拍首帧');
    const both = makeShot({ slots: [chosen(1, 'first_frame'), chosen(2, 'video')] });
    expect(cardAction(both, 'videos').label).toBe('重拍视频');
    const failed = makeShot({ slots: [chosen(1, 'first_frame')], failures: [{ id: 2, type: 'video.shot', status: 'failed', error: 'x' }] });
    expect(cardAction(failed, 'videos')).toEqual({ kind: 'retry', label: '重试', aria: '重试视频' });
  });

  it('qcIssues：疑似跳切、开头没接上首帧各一个角标；没有问题时为空', () => {
    expect(qcIssues(makeCandidate({ params: { qc: { hard_cuts: [], first_frame_ssim: 0.9, start_mismatch: false } } }))).toEqual([]);
    expect(qcIssues(undefined)).toEqual([]);
    const both = qcIssues(makeCandidate({ params: { qc: { hard_cuts: [2.04], first_frame_ssim: 0.29, start_mismatch: true } } }));
    expect(both.map((x) => x.label)).toEqual(['开头没接上首帧', '疑似跳切']);
    expect(both[0].tip).toContain('0.29');
    expect(both[1].tip).toContain('2.0s');
    expect(both.map((x) => x.short)).toEqual(['开头没接上首帧', '2.0s']);
  });

  it('qcIssues：疑似声音性别不对一个角标，说明写谁的台词、应是男声还是女声、测到多少 Hz', () => {
    const voice = qcIssues(makeCandidate({
      params: { qc: { hard_cuts: [], voice_mismatch: [{ speakers: ['店长'], expected: 'male', f0: 251.3 }] } },
    }));
    expect(voice.map((x) => x.label)).toEqual(['疑似声音性别不对']);
    expect(voice[0].tip).toContain('店长');
    expect(voice[0].tip).toContain('男声');
    expect(voice[0].tip).toContain('251 Hz');
    expect(voice[0].short).toBe('声音性别不对');
    expect(qcIssues(makeCandidate({ params: { qc: { voice_mismatch: [] } } }))).toEqual([]);
  });

  it('qcIssues：疑似叠入参考图一个角标，说明写哪张参考图、在第几秒、相似度', () => {
    const ghost = qcIssues(makeCandidate({
      params: { qc: { hard_cuts: [], ghost_refs: [{ ref: '江屿 定妆照', t: 1.1, ssim: 0.3, base: 0.01 }] } },
    }));
    expect(ghost.map((x) => x.label)).toEqual(['疑似叠入参考图']);
    expect(ghost[0].tip).toContain('江屿 定妆照');
    expect(ghost[0].tip).toContain('1.1s');
    expect(ghost[0].tip).toContain('0.30');
    expect(ghost[0].short).toBe('叠入参考图');
    expect(qcIssues(makeCandidate({ params: { qc: { ghost_refs: [] } } }))).toEqual([]);
  });
});
