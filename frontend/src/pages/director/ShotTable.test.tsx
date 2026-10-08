import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { makeCandidate, makeShot, makeSlot } from '../../test/factories';
import { ShotTable } from './ShotTable';

const SHOTS = [
  makeShot({
    id: 11,
    code: 'S01',
    duration: 5.5,
    dialogue: [{ speaker: '林夏', zh: '欢迎回来', start: 0.5, end: 2 }],
    slots: [
      makeSlot({
        id: 1, kind: 'first_frame', selected_candidate_id: 101, stale: true,
        candidates: [makeCandidate({ id: 101, url: '/media/demo/media/first_frame/a.png' })],
      }),
      makeSlot({
        id: 2, kind: 'video', selected_candidate_id: 201, stale: false,
        candidates: [makeCandidate({ id: 201, url: '/media/demo/media/video/a.mp4' })],
      }),
    ],
  }),
  makeShot({ id: 12, code: 'S02', action: '', slots: [] }),
];

function setup() {
  const h = {
    onToggle: vi.fn(), onToggleAll: vi.fn(), onOpen: vi.fn(), onMove: vi.fn(), onSplit: vi.fn(),
    onInsertAfter: vi.fn(), onDelete: vi.fn(), onGenerate: vi.fn(),
  };
  render(<ShotTable shots={SHOTS} selected={new Set([12])} {...h} />);
  return h;
}

describe('ShotTable', () => {
  it('过期的首帧显示"已过期"，没过期的视频不显示；没有产物显示"未生成"', () => {
    setup();
    const row1 = screen.getByTestId('shot-S01');
    const img = within(row1).getByAltText('S01 首帧');
    expect(img.getAttribute('src')).toBe('/media/demo/media/first_frame/a.png');
    expect(within(img.closest('td')!).getByText('已过期')).toBeTruthy();
    const video = within(row1).getByLabelText('S01 视频');
    expect(within(video.closest('td')!).queryByText('已过期')).toBeNull();
    expect(within(screen.getByTestId('shot-S02')).getAllByText('未生成')).toHaveLength(2);
    expect(within(row1).getByText('林夏：欢迎回来')).toBeTruthy();
    expect(within(row1).getByText('5.5s')).toBeTruthy();
  });

  it('首行不能上移、末行不能下移；按钮回调带镜头 id', async () => {
    const h = setup();
    const user = userEvent.setup();
    expect((screen.getByRole('button', { name: '上移 S01' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '下移 S02' }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole('button', { name: '上移 S02' }));
    expect(h.onMove).toHaveBeenCalledWith(12, -1);
    await user.click(screen.getByRole('button', { name: '生成视频 S01' }));
    expect(h.onGenerate).toHaveBeenCalledWith(11, 'videos');
    await user.click(screen.getByRole('button', { name: '在 S01 后插入' }));
    expect(h.onInsertAfter).toHaveBeenCalledWith(11);
    await user.click(screen.getByRole('button', { name: '打开 S02' }));
    expect(h.onOpen).toHaveBeenCalledWith(12);
  });

  it('勾选状态来自 selected，点勾选框回调', async () => {
    const h = setup();
    expect((screen.getByLabelText('选择 S02') as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText('选择 S01') as HTMLInputElement).checked).toBe(false);
    await userEvent.setup().click(screen.getByLabelText('选择 S01'));
    expect(h.onToggle).toHaveBeenCalledWith(11);
  });
});
