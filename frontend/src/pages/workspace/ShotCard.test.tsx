import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { Character, Slot } from '../../api/types';
import { makeCandidate, makeShot, makeSlot } from '../../test/factories';
import { ShotCard } from './ShotCard';

function chosen(id: number, kind: string, extra: Partial<Slot> = {}): Slot {
  return makeSlot({ id, kind, selected_candidate_id: id * 10, candidates: [makeCandidate({ id: id * 10, url: `/media/${kind}/${id}` })], ...extra });
}

const LIN: Character = {
  id: 8, project_id: 1, name: '林夏', description: '', visual_prompt: '', core_features: '', voice: null, portrait_url: '/media/p8.png',
};
const ZHOU: Character = { id: 9, project_id: 1, name: '老周', description: '', visual_prompt: '', core_features: '', voice: null };

/** 按钮 aria-describedby 指向的文字 */
function description(el: HTMLElement): string {
  return (el.getAttribute('aria-describedby') ?? '')
    .split(/\s+/)
    .filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? '')
    .join(' ');
}

function setup(p: Partial<ComponentProps<typeof ShotCard>> = {}) {
  const h = {
    onToggle: vi.fn(), onOpen: vi.fn(), onAction: vi.fn(), onMove: vi.fn(), onSplit: vi.fn(), onInsertAfter: vi.fn(), onDelete: vi.fn(),
  };
  const shot = p.shot ?? makeShot({ id: 11, code: 'S01' });
  const r = render(<ShotCard shot={shot} stage="keyframes" selected={false} first={false} last={false} cast={[]} {...h} {...p} />);
  return { h, card: screen.getByTestId(`shot-${shot.code}`), unmount: r.unmount };
}

describe('ShotCard', () => {
  it('卡片内容：镜号、时长、动作、台词数、出场角色头像（没定妆照写首字）', () => {
    const shot = makeShot({
      id: 11, code: 'S01', duration: 5.5, action: '林夏念稿',
      dialogue: [{ speaker: '林夏', zh: '欢迎回来', start: 0, end: 1 }, { speaker: '老周', zh: '嗯', start: 1, end: 2 }],
    });
    const { card } = setup({ shot, cast: [LIN, ZHOU] });
    expect(within(card).getByText('S01')).toBeTruthy();
    expect(within(card).getByText('5.5 秒')).toBeTruthy();
    expect(within(card).getByText('林夏念稿')).toBeTruthy();
    expect(within(card).getByText('2 句台词')).toBeTruthy();
    expect(within(card).getByRole('img', { name: '林夏' }).getAttribute('src')).toBe('/media/p8.png');
    expect(within(card).getByRole('img', { name: '老周' }).textContent).toBe('老');
  });

  it('首帧阶段：没有首帧 = 「未生成」+「生成首帧」；按钮不是主按钮', async () => {
    const { h, card } = setup();
    expect(within(card).getByText('未生成')).toBeTruthy();
    const btn = within(card).getByRole('button', { name: '生成首帧 S01' });
    expect(btn.textContent).toBe('生成首帧');
    expect(btn.className).not.toContain('primary');
    await userEvent.setup().click(btn);
    expect(h.onAction).toHaveBeenCalledWith(expect.objectContaining({ kind: 'generate' }));
    expect(h.onOpen).not.toHaveBeenCalled();
  });

  it('首帧阶段：已有选定首帧 = 缩略图 +「重拍首帧」；过期带角标和原因；新候选角标', () => {
    const shot = makeShot({ id: 11, code: 'S01', slots: [chosen(1, 'first_frame', { stale: true, stale_reason: '林夏 定妆照换了', new_candidates: 2 })] });
    const { card } = setup({ shot });
    expect(within(card).getByAltText('S01 首帧').getAttribute('src')).toBe('/media/first_frame/1');
    expect(within(card).getByText('已过期').getAttribute('title')).toBe('林夏 定妆照换了');
    // 过期原因也直接写在卡片上（不只是悬停提示）
    expect(within(card).getByText('已过期：林夏 定妆照换了')).toBeTruthy();
    expect(within(card).getByText('2 张新候选')).toBeTruthy();
    expect(within(card).getByRole('button', { name: '重拍首帧 S01' }).textContent).toBe('重拍首帧');
  });

  it('失败：角标「失败」和原因，主按钮是「重试」', async () => {
    const shot = makeShot({ id: 11, code: 'S01', failures: [{ id: 80, type: 'image.keyframe', status: 'failed', error: '显存不足' }] });
    const { h, card } = setup({ shot });
    expect(within(card).getByText('失败')).toBeTruthy();
    expect(within(card).getByText('显存不足')).toBeTruthy();
    const btn = within(card).getByRole('button', { name: '重试首帧 S01' });
    expect(btn.textContent).toBe('重试');
    await userEvent.setup().click(btn);
    expect(h.onAction).toHaveBeenCalledWith(expect.objectContaining({ kind: 'retry' }));
  });

  it('进行中：显示「排队中 / 生成中 xx%」，按钮禁用', () => {
    const shot = makeShot({ id: 11, code: 'S01', active_jobs: [{ id: 70, type: 'image.keyframe', status: 'running', progress: 0.42 }] });
    const { card } = setup({ shot });
    expect(within(card).getByText('生成中 42%')).toBeTruthy();
    expect((within(card).getByRole('button', { name: '首帧生成中 S01' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('提交中（generating）时主按钮禁用', () => {
    const { card } = setup({ generating: true });
    expect((within(card).getByRole('button', { name: '生成首帧 S01' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('视频阶段：没首帧 =「先出首帧」；有首帧没视频 =「生成视频」；有视频 = 视频缩略图 + 播放角标 +「重拍视频」', () => {
    const a = setup({ stage: 'videos' });
    expect(within(a.card).getByRole('button', { name: '先出首帧 S01' }).textContent).toBe('先出首帧');
    a.unmount();
    const b = setup({ stage: 'videos', shot: makeShot({ id: 12, code: 'S02', slots: [chosen(1, 'first_frame')] }) });
    expect(within(b.card).getByRole('button', { name: '生成视频 S02' })).toBeTruthy();
    // 还没视频：拿首帧当底图
    expect(within(b.card).getByAltText('S02 首帧')).toBeTruthy();
    expect(within(b.card).getByText('还没有视频')).toBeTruthy();
    b.unmount();
    const c = setup({ stage: 'videos', shot: makeShot({ id: 13, code: 'S03', slots: [chosen(1, 'first_frame'), chosen(2, 'video')] }) });
    expect(within(c.card).getByLabelText('S03 视频').getAttribute('src')).toBe('/media/video/2');
    expect(within(c.card).getByText('播放')).toBeTruthy();
    expect(within(c.card).getByRole('button', { name: '重拍视频 S03' })).toBeTruthy();
  });

  it('点卡片打开抽屉；左上角勾选只切换选择，不打开抽屉', async () => {
    const { h, card } = setup({ selected: true });
    const user = userEvent.setup();
    const box = within(card).getByRole('checkbox', { name: '选择 S01' }) as HTMLInputElement;
    expect(box.checked).toBe(true);
    expect(card.className).toContain('is-selected');
    await user.click(box);
    expect(h.onToggle).toHaveBeenCalledTimes(1);
    expect(h.onOpen).not.toHaveBeenCalled();
    await user.click(within(card).getByRole('button', { name: '打开 S01' }));
    expect(h.onOpen).toHaveBeenCalledTimes(1);
  });

  it('「⋯」菜单：在后面插入、拆分、上移、下移、删除；首个不能上移、最后一个不能下移', async () => {
    const { h, card } = setup({ first: true });
    const user = userEvent.setup();
    await user.click(within(card).getByRole('button', { name: '更多操作 S01' }));
    expect(screen.getAllByRole('menuitem').map((m) => m.textContent)).toEqual(['在后面插入', '拆分', '上移', '下移', '删除']);
    expect((screen.getByRole('menuitem', { name: '上移' }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole('menuitem', { name: '下移' }));
    expect(h.onMove).toHaveBeenCalledWith(1);
    await user.click(within(card).getByRole('button', { name: '更多操作 S01' }));
    await user.click(screen.getByRole('menuitem', { name: '在后面插入' }));
    expect(h.onInsertAfter).toHaveBeenCalled();
    await user.click(within(card).getByRole('button', { name: '更多操作 S01' }));
    await user.click(screen.getByRole('menuitem', { name: '拆分' }));
    expect(h.onSplit).toHaveBeenCalled();
    await user.click(within(card).getByRole('button', { name: '更多操作 S01' }));
    await user.click(screen.getByRole('menuitem', { name: '删除' }));
    expect(h.onDelete).toHaveBeenCalled();
    expect(h.onOpen).not.toHaveBeenCalled();
  });

  it('读屏：打开按钮的名字是「打开 S01」，描述里有状态角标和原因', () => {
    const stale = makeShot({ id: 11, code: 'S01', slots: [chosen(1, 'first_frame', { stale: true, stale_reason: '定妆照换了', new_candidates: 2 })] });
    const a = setup({ shot: stale });
    const open = within(a.card).getByRole('button', { name: '打开 S01' });
    expect(description(open)).toContain('已过期');
    expect(description(open)).toContain('2 张新候选');
    expect(description(open)).toContain('定妆照换了');
    a.unmount();
    const ok = setup({ shot: makeShot({ id: 12, code: 'S02', slots: [chosen(1, 'first_frame'), chosen(2, 'video')] }), stage: 'videos' });
    const d = description(within(ok.card).getByRole('button', { name: '打开 S02' }));
    expect(d).toContain('已选定');
    expect(d).toContain('播放');
    ok.unmount();
    const busy = setup({ shot: makeShot({ id: 13, code: 'S03', active_jobs: [{ id: 70, type: 'image.keyframe', status: 'running', progress: 0.42 }] }) });
    expect(description(within(busy.card).getByRole('button', { name: '打开 S03' }))).toContain('生成中 42%');
    busy.unmount();
    const failed = setup({ shot: makeShot({ id: 14, code: 'S04', failures: [{ id: 80, type: 'image.keyframe', status: 'failed', error: '显存不足' }] }) });
    const fd = description(within(failed.card).getByRole('button', { name: '打开 S04' }));
    expect(fd).toContain('失败');
    expect(fd).toContain('显存不足');
  });

  it('视频阶段首帧过期：「先重拍首帧」', () => {
    const shot = makeShot({ id: 11, code: 'S01', slots: [chosen(1, 'first_frame', { stale: true })] });
    const { card } = setup({ shot, stage: 'videos' });
    expect(within(card).getByRole('button', { name: '先重拍首帧 S01' }).textContent).toBe('先重拍首帧');
  });
});
