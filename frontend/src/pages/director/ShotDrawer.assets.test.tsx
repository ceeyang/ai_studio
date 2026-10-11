import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Asset } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeCandidate, makeShot, makeSlot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { ShotDrawer } from './ShotDrawer';

const ENGINES = {
  image: [
    { name: 'zimage', family: 'zimage', caps: { max_refs: 0 } },
    { name: 'gemini_image', family: 'browser', caps: { max_refs: 3 } },
  ],
  video: [{ name: 'h3_video', family: 'h3', caps: { has_audio: true } }],
};
const PROJECT = { id: 1, slug: 'demo', title: 'demo', aspect: '9:16', image_engine: 'zimage', video_engine: 'h3_video' };

function asset(p: Partial<Asset> & Pick<Asset, 'id' | 'kind' | 'label'>): Asset {
  return { slot_kind: 'first_frame', media: 'image', source: '', url: '/m.png', thumb_url: '/m.png', width: 768, height: 1344, ...p };
}

const ASSETS: Asset[] = [
  asset({ id: 'cand-50', kind: 'shot', label: 'S01 首帧', source: 'S01', shot_id: 4 }),
  asset({ id: 'cand-60', kind: 'location', label: '场景图·修车行', source: '修车行', slot_kind: 'location_image', width: 1344, height: 768 }),
  asset({ id: 'cand-101', kind: 'shot', label: 'S03 首帧', source: 'S03', shot_id: 5 }),
];

const SHOT = makeShot({
  id: 5,
  code: 'S03',
  image_max_refs: 0,
  slots: [makeSlot({ id: 9, kind: 'first_frame', selected_candidate_id: 101, candidates: [makeCandidate({ id: 101 })] })],
});

describe('ShotDrawer 资产', () => {
  it('用已有图片当首帧：自己的首帧不列；画幅不对时提示并默认居中裁切，确定后设为选定', async () => {
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'GET /api/projects/demo': PROJECT,
      'GET /api/episodes/1/assets': ASSETS,
      'POST /api/shots/5/first-frame/from-asset': SHOT,
    });
    renderAt(<ShotDrawer shot={SHOT} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '用已有图片当首帧' }));
    const dlg = await screen.findByRole('dialog', { name: '给 S03 选一张首帧' });
    // 默认打开第一个有图的分类（场景图）；镜头页签里没有这一镜自己的首帧
    await user.click(within(dlg).getByRole('tab', { name: '镜头1' }));
    expect(within(dlg).queryByRole('button', { name: /S03 首帧/ })).toBeNull();
    await user.click(within(dlg).getByRole('button', { name: '选择 S01 首帧' }));
    expect(within(dlg).queryByTestId('aspect-mismatch')).toBeNull();
    await user.click(within(dlg).getByRole('tab', { name: '场景图1' }));
    await user.click(within(dlg).getByRole('button', { name: '选择 场景图·修车行' }));
    expect(within(dlg).getByTestId('aspect-mismatch').textContent).toContain('1344×768');
    expect((within(dlg).getByRole('checkbox', { name: /居中裁切成项目画幅/ }) as HTMLInputElement).checked).toBe(true);
    await user.click(within(dlg).getByRole('button', { name: '用作首帧' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    const post = calls.find((c) => c.method === 'POST');
    expect(post?.url).toBe('/api/shots/5/first-frame/from-asset');
    expect(post?.body).toEqual({ id: 'cand-60', crop: true, select: true });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '给 S03 选一张首帧' })).toBeNull());
  });

  it('开了接上一镜：不能用已有图片当首帧', () => {
    installFetch({ 'GET /api/engines': ENGINES });
    renderAt(
      <ShotDrawer shot={{ ...SHOT, order: 2, continue_from_prev: true }} episodeId={1} onClose={() => {}} onGenerate={() => {}} />,
    );
    expect((screen.getByRole('button', { name: '用已有图片当首帧' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('构图参考从资产里选：最多补满 2 张', async () => {
    const shot = { ...SHOT, image_max_refs: 3, shot_refs: [makeCandidate({ id: 31, engine: 'upload', params: {} })] };
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'GET /api/episodes/1/assets': ASSETS,
      'POST /api/shots/5/refs/from-assets': shot,
    });
    renderAt(<ShotDrawer shot={shot} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    const user = userEvent.setup();
    await user.click(within(screen.getByRole('region', { name: '构图参考' })).getByRole('button', { name: '从资产里选' }));
    const dlg = await screen.findByRole('dialog', { name: '给 S03 选构图参考' });
    await user.click(await within(dlg).findByRole('button', { name: '选择 场景图·修车行' }));
    expect(within(dlg).getByText(/还能加 1 张/)).toBeTruthy();
    // 只剩 1 个名额：再点别的就换成别的
    await user.click(within(dlg).getByRole('tab', { name: '镜头2' }));
    await user.click(within(dlg).getByRole('button', { name: '选择 S01 首帧' }));
    expect(within(dlg).getByTestId('asset-picked-count').textContent).toBe('已选：S01 首帧');
    await user.click(within(dlg).getByRole('button', { name: '用作构图参考' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ assets: [{ id: 'cand-50' }] });
  });

  it('构图参考的「不吃参考图」提示按抽屉里正在选的图像引擎算（还没保存也即时变）', async () => {
    installFetch({ 'GET /api/engines': ENGINES, 'GET /api/projects/demo': PROJECT });
    renderAt(<ShotDrawer shot={SHOT} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    const user = userEvent.setup();
    expect(screen.getByTestId('refs-unsupported')).toBeTruthy();
    const select = screen.getByLabelText('图像引擎');
    await waitFor(() => expect(within(select).getByRole('option', { name: /Gemini 网页/ })).toBeTruthy());
    await user.selectOptions(select, 'gemini_image');
    expect(screen.queryByTestId('refs-unsupported')).toBeNull();
    await user.selectOptions(select, 'zimage');
    expect(screen.getByTestId('refs-unsupported')).toBeTruthy();
    // 留空 = 项目默认（zimage）：仍提示
    await user.selectOptions(select, '');
    expect(screen.getByTestId('refs-unsupported')).toBeTruthy();
  });

  it('保存的引擎不吃参考图、草稿换回能吃的项目默认：不提示', async () => {
    const gem = { ...PROJECT, image_engine: 'gemini_image' };
    installFetch({ 'GET /api/engines': ENGINES, 'GET /api/projects/demo': gem });
    renderAt(<ShotDrawer shot={{ ...SHOT, image_engine: 'zimage' }} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    const user = userEvent.setup();
    expect(screen.getByTestId('refs-unsupported')).toBeTruthy();
    const select = screen.getByLabelText('图像引擎');
    await waitFor(() => expect(within(select).getByRole('option', { name: '默认（Gemini 网页）' })).toBeTruthy());
    await user.selectOptions(select, '');
    expect(screen.queryByTestId('refs-unsupported')).toBeNull();
  });
});
