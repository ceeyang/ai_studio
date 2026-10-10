import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
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
        id: 1, kind: 'first_frame', selected_candidate_id: 101, stale: true, stale_reason: '林夏 定妆照换了',
        candidates: [makeCandidate({ id: 101, url: '/media/demo/media/first_frame/a.png' })],
      }),
      makeSlot({
        id: 2, kind: 'video', selected_candidate_id: 201, stale: false, new_candidates: 2,
        candidates: [makeCandidate({ id: 201, url: '/media/demo/media/video/a.mp4' })],
      }),
    ],
  }),
  makeShot({ id: 12, code: 'S02', action: '', slots: [] }),
];

function setup(extra: Partial<ComponentProps<typeof ShotTable>> = {}) {
  const h = {
    onToggle: vi.fn(), onToggleAll: vi.fn(), onOpen: vi.fn(), onMove: vi.fn(), onSplit: vi.fn(),
    onInsertAfter: vi.fn(), onDelete: vi.fn(), onGenerate: vi.fn(),
  };
  render(<ShotTable shots={SHOTS} selected={new Set([12])} {...h} {...extra} />);
  return h;
}

describe('ShotTable', () => {
  it('过期的首帧显示"已过期"并在提示里写原因；没过期的视频有「已选定」圆点；没有产物显示"未生成"', () => {
    setup();
    const row1 = screen.getByTestId('shot-S01');
    const img = within(row1).getByAltText('S01 首帧');
    expect(img.getAttribute('src')).toBe('/media/demo/media/first_frame/a.png');
    const stale = within(img.closest('td')!).getByText('已过期');
    expect(stale.getAttribute('title')).toBe('林夏 定妆照换了');
    const video = within(row1).getByLabelText('S01 视频');
    expect(within(video.closest('td')!).queryByText('已过期')).toBeNull();
    expect(within(video.closest('td')!).getByText('已选定')).toBeTruthy();
    expect(within(screen.getByTestId('shot-S02')).getAllByText('未生成')).toHaveLength(2);
    expect(within(row1).getByText('林夏：欢迎回来')).toBeTruthy();
    expect(within(row1).getByText('5.5s')).toBeTruthy();
  });

  it('点缩略图、点「N 张新候选」都打开抽屉', async () => {
    const h = setup();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '查看 S01 首帧' }));
    expect(h.onOpen).toHaveBeenLastCalledWith(11);
    await user.click(screen.getByRole('button', { name: '2 张新候选' }));
    expect(h.onOpen).toHaveBeenCalledTimes(2);
  });

  it('每行只有生成（重拍）首帧、生成（重拍）视频和「⋯」菜单；菜单里首行不能上移、末行不能下移，回调带镜头 id', async () => {
    const h = setup();
    const user = userEvent.setup();
    expect(screen.queryByRole('button', { name: '上移 S01' })).toBeNull();
    await user.click(screen.getByRole('button', { name: '更多操作 S01' }));
    expect((screen.getByRole('menuitem', { name: '上移' }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole('menuitem', { name: '在后面插入' }));
    expect(h.onInsertAfter).toHaveBeenCalledWith(11);
    await user.click(screen.getByRole('button', { name: '更多操作 S02' }));
    expect((screen.getByRole('menuitem', { name: '下移' }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole('menuitem', { name: '上移' }));
    expect(h.onMove).toHaveBeenCalledWith(12, -1);
    await user.click(screen.getByRole('button', { name: '更多操作 S02' }));
    await user.click(screen.getByRole('menuitem', { name: '拆分' }));
    expect(h.onSplit).toHaveBeenCalledWith(12);
    await user.click(screen.getByRole('button', { name: '更多操作 S02' }));
    await user.click(screen.getByRole('menuitem', { name: '删除' }));
    expect(h.onDelete).toHaveBeenCalledWith(12);
    await user.click(screen.getByRole('button', { name: '重拍视频 S01' }));
    expect(h.onGenerate).toHaveBeenCalledWith(11, 'videos');
    await user.click(screen.getByRole('button', { name: '打开 S02' }));
    expect(h.onOpen).toHaveBeenCalledWith(12);
  });

  it('已有选定结果的格子按钮叫「重拍…」，还没出过的叫「生成…」', () => {
    setup();
    const r1 = screen.getByTestId('shot-S01');
    expect(within(r1).getByRole('button', { name: '重拍首帧 S01' }).textContent).toBe('重拍首帧');
    expect(within(r1).getByRole('button', { name: '重拍视频 S01' }).textContent).toBe('重拍视频');
    const r2 = screen.getByTestId('shot-S02');
    expect(within(r2).getByRole('button', { name: '生成首帧 S02' }).textContent).toBe('生成首帧');
    expect(within(r2).getByRole('button', { name: '生成视频 S02' }).textContent).toBe('生成视频');
  });

  it('勾选状态来自 selected，点勾选框回调', async () => {
    const h = setup();
    expect((screen.getByLabelText('选择 S02') as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText('选择 S01') as HTMLInputElement).checked).toBe(false);
    await userEvent.setup().click(screen.getByLabelText('选择 S01'));
    expect(h.onToggle).toHaveBeenCalledWith(11);
  });

  it('提交运行中时，生成首帧/生成视频按钮禁用，点击不触发回调', async () => {
    const h = setup({ generating: true });
    const user = userEvent.setup();
    const genFrame = screen.getByRole('button', { name: '重拍首帧 S01' }) as HTMLButtonElement;
    const genVideo = screen.getByRole('button', { name: '重拍视频 S01' }) as HTMLButtonElement;
    expect(genFrame.disabled).toBe(true);
    expect(genVideo.disabled).toBe(true);
    await user.click(genFrame);
    await user.click(genVideo);
    expect(h.onGenerate).not.toHaveBeenCalled();
  });

  it('格子里显示排队中 / 生成中 xx%，对应按钮禁用并写「生成中」', () => {
    const shots = [
      makeShot({
        id: 11, code: 'S01',
        active_jobs: [
          { id: 70, type: 'image.keyframe', status: 'running', progress: 0.42 },
          { id: 71, type: 'video.shot', status: 'queued' },
        ],
      }),
    ];
    render(
      <ShotTable shots={shots} selected={new Set()} onToggle={vi.fn()} onToggleAll={vi.fn()} onOpen={vi.fn()} onMove={vi.fn()}
        onSplit={vi.fn()} onInsertAfter={vi.fn()} onDelete={vi.fn()} onGenerate={vi.fn()} />,
    );
    const row = screen.getByTestId('shot-S01');
    expect(within(row).getByText('生成中 42%')).toBeTruthy();
    expect(within(row).getByText('排队中')).toBeTruthy();
    const genFrame = screen.getByRole('button', { name: '生成首帧 S01' }) as HTMLButtonElement;
    expect(genFrame.disabled).toBe(true);
    expect(genFrame.textContent).toBe('生成中');
    expect((screen.getByRole('button', { name: '生成视频 S01' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('失败的格子带「失败」角标和原因，点「重试」重新生成这一格', async () => {
    const onGenerate = vi.fn();
    const onRetry = vi.fn();
    const shots = [
      makeShot({
        id: 11, code: 'S01',
        failures: [{ id: 80, type: 'video.shot', status: 'failed', error: '显存不足' }],
      }),
    ];
    render(
      <ShotTable shots={shots} selected={new Set()} onToggle={vi.fn()} onToggleAll={vi.fn()} onOpen={vi.fn()} onMove={vi.fn()}
        onSplit={vi.fn()} onInsertAfter={vi.fn()} onDelete={vi.fn()} onGenerate={onGenerate} onRetry={onRetry} />,
    );
    const row = screen.getByTestId('shot-S01');
    expect(within(row).getByText('失败')).toBeTruthy();
    expect(within(row).getByText('显存不足')).toBeTruthy();
    await userEvent.setup().click(screen.getByRole('button', { name: '重试视频 S01' }));
    expect(onRetry).toHaveBeenCalledWith(11, 'videos');
    expect(onGenerate).not.toHaveBeenCalled();
  });

  it('提交运行中时，失败格子的「重试」也禁用', async () => {
    const onRetry = vi.fn();
    const shots = [
      makeShot({
        id: 11, code: 'S01',
        failures: [{ id: 80, type: 'video.shot', status: 'failed', error: '显存不足' }],
      }),
    ];
    render(
      <ShotTable shots={shots} selected={new Set()} generating onToggle={vi.fn()} onToggleAll={vi.fn()} onOpen={vi.fn()} onMove={vi.fn()}
        onSplit={vi.fn()} onInsertAfter={vi.fn()} onDelete={vi.fn()} onGenerate={vi.fn()} onRetry={onRetry} />,
    );
    const retry = screen.getByRole('button', { name: '重试视频 S01' }) as HTMLButtonElement;
    expect(retry.disabled).toBe(true);
    await userEvent.setup().click(retry);
    expect(onRetry).not.toHaveBeenCalled();
  });
});
