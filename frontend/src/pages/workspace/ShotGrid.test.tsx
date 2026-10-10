import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { Location } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeShot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { ShotGrid } from './ShotGrid';

const BLOCKS = [
  { id: 21, order: 0, location_id: 3, text: '直播间里' },
  { id: 22, order: 1, location_id: null, text: '老周进来' },
];
const LOCS: Location[] = [{ id: 3, project_id: 1, name: '直播间', time_of_day: '夜', atmosphere: '', visual_prompt: '' }];
const SHOTS = [
  makeShot({ id: 11, code: 'S01', order: 0, scene_block_id: 21 }),
  makeShot({ id: 12, code: 'S02', order: 1, scene_block_id: 21 }),
  makeShot({ id: 13, code: 'S03', order: 2, scene_block_id: null }),
  makeShot({ id: 14, code: 'S04', order: 3, scene_block_id: 22 }),
];

function setup(p: Partial<ComponentProps<typeof ShotGrid>> = {}) {
  const h = {
    onToggle: vi.fn(), onSelectMany: vi.fn(), onOpen: vi.fn(), onAction: vi.fn(), onMove: vi.fn(), onSplit: vi.fn(),
    onInsertAfter: vi.fn(), onDelete: vi.fn(), onRegenerated: vi.fn(),
  };
  renderAt(
    <ShotGrid episodeId={4} shots={SHOTS} blocks={BLOCKS} locations={LOCS} characters={[]} stage="keyframes" selected={new Set()} {...h} {...p} />,
  );
  return h;
}

const groups = () => screen.getAllByRole('region').map((r) => r.getAttribute('aria-label'));

describe('ShotGrid', () => {
  it('按段落分组：组头「第 N 段 · 场景名 · M 镜」；没有段落的手工镜头单独一组放最后', () => {
    installFetch({});
    setup();
    expect(groups()).toEqual(['第 1 段 · 直播间 · 2 镜', '第 2 段 · 未指定场景 · 1 镜', '手工添加 · 1 镜']);
    const g1 = screen.getByRole('region', { name: '第 1 段 · 直播间 · 2 镜' });
    expect(within(g1).getAllByTestId(/^shot-/).map((el) => el.dataset.testid)).toEqual(['shot-S01', 'shot-S02']);
    const manual = screen.getByRole('region', { name: '手工添加 · 1 镜' });
    expect(within(manual).getByTestId('shot-S03')).toBeTruthy();
    expect(within(manual).queryByRole('button', { name: /重写本段分镜/ })).toBeNull();
  });

  it('上移 / 下移按全集顺序：第一个镜头不能上移，最后一个不能下移', async () => {
    installFetch({});
    const h = setup();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '更多操作 S04' }));
    expect((screen.getByRole('menuitem', { name: '下移' }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole('menuitem', { name: '上移' }));
    expect(h.onMove).toHaveBeenCalledWith(14, -1);
  });

  it('「重写本段分镜…」打开重新生成对话框：范围是这一段，提交带 scene_block_ids 并强制', async () => {
    const calls = installFetch({
      'GET /api/episodes/4/rerun-impact': { blocks: [2], shots_deleted: 1, selected_first_frames: 0, selected_videos: 0, manual_kept: 0, overwrites: [] },
      'POST /api/episodes/4/run': { job_ids: [77] },
    });
    const h = setup();
    const user = userEvent.setup();
    const g2 = screen.getByRole('region', { name: '第 2 段 · 未指定场景 · 1 镜' });
    await user.click(within(g2).getByRole('button', { name: '重写本段分镜…' }));
    const dlg = await screen.findByRole('dialog', { name: '重新生成分镜' });
    expect(dlg.textContent).toContain('范围：第 2 段');
    await waitFor(() => expect(calls.some((c) => c.url.includes('rerun-impact') && c.url.includes('scene_block_ids=22'))).toBe(true));
    await user.click(within(dlg).getByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(h.onRegenerated).toHaveBeenCalledWith([77], '重写第 2 段分镜'));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ stage: 'storyboard', force: true, scene_block_ids: [22] });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('多选：卡片勾选回调镜头 id；组头勾选整段（全选 / 全不选）', async () => {
    installFetch({});
    const h = setup({ selected: new Set([11]) });
    const user = userEvent.setup();
    await user.click(screen.getByRole('checkbox', { name: '选择 S02' }));
    expect(h.onToggle).toHaveBeenCalledWith(12);
    const all = screen.getByRole('checkbox', { name: '选择第 1 段的全部镜头' }) as HTMLInputElement;
    expect(all.checked).toBe(false);
    expect(all.indeterminate).toBe(true);
    await user.click(all);
    expect(h.onSelectMany).toHaveBeenCalledWith([11, 12], true);
  });

  it('段落已全选时组头勾选是选中的，再点全不选', async () => {
    installFetch({});
    const h = setup({ selected: new Set([11, 12]) });
    const all = screen.getByRole('checkbox', { name: '选择第 1 段的全部镜头' }) as HTMLInputElement;
    expect(all.checked).toBe(true);
    await userEvent.setup().click(all);
    expect(h.onSelectMany).toHaveBeenCalledWith([11, 12], false);
  });

  it('卡片主按钮回调带镜头和动作；点卡片回调打开', async () => {
    installFetch({});
    const h = setup();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '生成首帧 S02' }));
    expect(h.onAction).toHaveBeenCalledWith(SHOTS[1], expect.objectContaining({ kind: 'generate' }));
    await user.click(screen.getByRole('button', { name: '打开 S03' }));
    expect(h.onOpen).toHaveBeenCalledWith(13);
  });

  it('段落还没有镜头：组头照样有「重写本段分镜…」，写明这一段还没有镜头', () => {
    installFetch({});
    setup({ shots: SHOTS.filter((s) => s.scene_block_id !== 22) });
    const g2 = screen.getByRole('region', { name: '第 2 段 · 未指定场景 · 0 镜' });
    expect(within(g2).getByText('这一段还没有镜头')).toBeTruthy();
    expect(within(g2).getByRole('button', { name: '重写本段分镜…' })).toBeTruthy();
  });
});
