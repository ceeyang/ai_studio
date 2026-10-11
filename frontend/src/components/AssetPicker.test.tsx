import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { Asset } from '../api/types';
import { installFetch } from '../test/fetch';
import { renderAt } from '../test/render';
import { AssetPicker, aspectMismatch } from './AssetPicker';

function asset(p: Partial<Asset> & Pick<Asset, 'id' | 'kind' | 'label'>): Asset {
  return {
    slot_kind: 'portrait',
    media: 'image',
    source: '',
    url: `/media/demo/media/x/${p.id}.png`,
    thumb_url: `/media/demo/media/x/${p.id}.png`,
    width: 768,
    height: 1344,
    selected: false,
    ...p,
  };
}

const ASSETS: Asset[] = [
  asset({ id: 'cand-1', kind: 'character', label: '陈师傅 定妆照', source: '陈师傅', selected: true }),
  asset({ id: 'cand-2', kind: 'character', label: '陈师傅 三视图·正面', source: '陈师傅', slot_kind: 'turnaround_view_front_full' }),
  asset({ id: 'cand-3', kind: 'character', label: '小李 定妆照', source: '小李' }),
  asset({ id: 'cand-4', kind: 'location', label: '场景图·修车行', source: '修车行', slot_kind: 'location_image' }),
  asset({ id: 'cand-5', kind: 'shot', label: 'S02 首帧', source: 'S02', slot_kind: 'first_frame', shot_id: 2 }),
  asset({ id: 'cand-6', kind: 'shot', label: 'S02 视频', source: 'S02', slot_kind: 'video', media: 'video', width: null, height: null, url: '/media/v.mp4', thumb_url: '/media/v.mp4' }),
];

describe('AssetPicker', () => {
  it('按分类分页签（带数量），默认打开第一个有图的分类；搜索按名字 / 镜头号过滤', async () => {
    installFetch({ 'GET /api/episodes/7/assets': ASSETS });
    renderAt(<AssetPicker episodeId={7} title="选图" max={2} onConfirm={() => {}} onClose={() => {}} />);
    const tabs = screen.getByRole('tablist', { name: '资产分类' });
    await waitFor(() => expect(within(tabs).getByRole('tab', { name: '角色3' }).getAttribute('aria-selected')).toBe('true'));
    expect(within(tabs).getByRole('tab', { name: '场景图1' })).toBeTruthy();
    expect(within(tabs).getByRole('tab', { name: '道具0' })).toBeTruthy();
    const grid = screen.getByRole('list', { name: '角色资产' });
    expect(within(grid).getAllByRole('listitem')).toHaveLength(3);
    // 原槽位里选定的那张标「已选」
    expect(within(within(grid).getAllByRole('listitem')[0]).getByText('已选')).toBeTruthy();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('搜索资产'), '小李');
    expect(within(screen.getByRole('list', { name: '角色资产' })).getAllByRole('listitem')).toHaveLength(1);
    await user.clear(screen.getByLabelText('搜索资产'));
    await user.type(screen.getByLabelText('搜索资产'), 's02');
    await user.click(within(tabs).getByRole('tab', { name: '镜头2' }));
    expect(within(screen.getByRole('list', { name: '镜头资产' })).getAllByRole('listitem')).toHaveLength(2);
    await user.click(within(tabs).getByRole('tab', { name: '道具0' }));
    expect(screen.getByText('没有符合搜索的图片。')).toBeTruthy();
  });

  it('多选受上限约束：选满后别的不能再选；确定把选中的交出去', async () => {
    installFetch({ 'GET /api/episodes/7/assets': ASSETS });
    const onConfirm = vi.fn();
    renderAt(<AssetPicker episodeId={7} title="选图" max={2} confirmLabel="用作构图参考" onConfirm={onConfirm} onClose={() => {}} />);
    const user = userEvent.setup();
    const confirm = screen.getByRole('button', { name: '用作构图参考' }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    await user.click(await screen.findByRole('button', { name: '选择 陈师傅 定妆照' }));
    await user.click(screen.getByRole('button', { name: '选择 小李 定妆照' }));
    expect(screen.getByTestId('asset-picked-count').textContent).toContain('已选 2/2 张');
    expect((screen.getByRole('button', { name: '选择 陈师傅 三视图·正面' }) as HTMLButtonElement).disabled).toBe(true);
    // 取消一张又能选
    await user.click(screen.getByRole('button', { name: '取消选择 小李 定妆照' }));
    expect((screen.getByRole('button', { name: '选择 陈师傅 三视图·正面' }) as HTMLButtonElement).disabled).toBe(false);
    await user.click(screen.getByRole('tab', { name: '镜头2' }));
    await user.click(screen.getByRole('button', { name: '选择 S02 视频' }));
    // 视频要选取哪一帧，默认尾帧
    await user.click(screen.getByRole('radio', { name: '取首帧' }));
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm.mock.calls[0][0]).toEqual([{ id: 'cand-1' }, { id: 'cand-6', frame: 'first' }]);
    expect(onConfirm.mock.calls[0][1].map((a: Asset) => a.label)).toEqual(['陈师傅 定妆照', 'S02 视频']);
  });

  it('只能选一张时，点别的就换成别的；排除的资产不出现', async () => {
    installFetch({ 'GET /api/episodes/7/assets': ASSETS });
    const onConfirm = vi.fn();
    renderAt(
      <AssetPicker episodeId={7} title="选首帧" max={1} exclude={(a) => a.id === 'cand-3'} onConfirm={onConfirm} onClose={() => {}} />,
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '选择 陈师傅 定妆照' }));
    await user.click(screen.getByRole('button', { name: '选择 陈师傅 三视图·正面' }));
    expect(screen.queryByRole('button', { name: /小李/ })).toBeNull();
    expect(screen.getByTestId('asset-picked-count').textContent).toBe('已选：陈师傅 三视图·正面');
    await user.click(screen.getByRole('button', { name: '确定' }));
    expect(onConfirm.mock.calls[0][0]).toEqual([{ id: 'cand-2' }]);
  });

  it('画幅比较', () => {
    expect(aspectMismatch({ width: 768, height: 1344 }, '9:16')).toBe(false);
    expect(aspectMismatch({ width: 1344, height: 768 }, '9:16')).toBe(true);
    expect(aspectMismatch({ width: null, height: null }, '9:16')).toBe(false);
  });
});
