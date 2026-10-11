import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { keys } from '../api/queries';
import type { AssetImportOut, AssetSummary, Project } from '../api/types';
import { installFetch } from '../test/fetch';
import { renderAt } from '../test/render';
import { ImportAssetsButton } from './ImportAssetsDialog';

function project(slug: string, title: string, archived = false): Project {
  return {
    id: slug.length,
    slug,
    title,
    aspect: '9:16',
    image_engine: 'zimage',
    video_engine: 'h3_video',
    seed: 1,
    negative: '',
    style_preset: 'live_action',
    style_bible_id: null,
    archived,
    created_at: '2026-01-01T00:00:00',
  };
}

const SOURCE: AssetSummary = {
  characters: [
    { id: 1, name: '林夏', detail: '主播', thumb_url: '/media/s1/media/portrait/a.png', image_count: 3 },
    { id: 2, name: '老周', detail: '', thumb_url: null, image_count: 0 },
  ],
  locations: [{ id: 5, name: '直播间', detail: '夜', thumb_url: null, image_count: 1 }],
  props: [],
  has_style: true,
};

const MINE: AssetSummary = {
  characters: [{ id: 9, name: '林夏', detail: '', thumb_url: null, image_count: 0 }],
  locations: [],
  props: [],
  has_style: false,
};

const REPORT: AssetImportOut = {
  imported: [{ kind: 'location', source_id: 5, id: 50, name: '直播间', renamed_from: null, files: 1 }],
  skipped: [{ kind: 'character', source_id: 1, name: '林夏', reason: '目标项目里已经有同名角色' }],
  warnings: [],
  files_copied: 1,
  style_copied: false,
};

afterEach(() => vi.unstubAllGlobals());

describe('ImportAssetsDialog', () => {
  it('选源项目 → 默认全选、标出重名 → 去掉一个后导入，显示报告并刷新角色 / 场景', async () => {
    const user = userEvent.setup();
    const calls = installFetch({
      'GET /api/projects': [project('s2', '第二季'), project('s1', '第一季', true)],
      'GET /api/projects/s1/asset-summary': SOURCE,
      'GET /api/projects/s2/asset-summary': MINE,
      'POST /api/projects/s2/import-assets': REPORT,
    });
    const { qc } = renderAt(<ImportAssetsButton slug="s2" />);
    const spy = vi.spyOn(qc, 'invalidateQueries');
    await user.click(screen.getByRole('button', { name: '从其他项目导入' }));
    const dialog = screen.getByRole('dialog');
    // 本项目不在源项目里，归档的往季也能选
    const select = within(dialog).getByRole('combobox');
    await waitFor(() => expect(within(select).getByRole('option', { name: '第一季（已归档）' })).toBeTruthy());
    expect(within(select).queryByRole('option', { name: '第二季' })).toBeNull();
    await user.selectOptions(select, 's1');

    const chars = await within(dialog).findByRole('group', { name: '角色' });
    const boxes = within(chars).getAllByRole('checkbox') as HTMLInputElement[];
    expect(boxes.map((b) => b.checked)).toEqual([true, true]);
    expect(within(chars).getByText('本项目已有同名')).toBeTruthy();
    expect(chars.querySelector('img')?.getAttribute('src')).toBe('/media/s1/media/portrait/a.png');
    // 道具一个都没有：不显示这一组
    expect(within(dialog).queryByRole('group', { name: '道具' })).toBeNull();

    await user.click(boxes[1]);
    await user.click(within(dialog).getByRole('radio', { name: /改名导入/ }));
    const suffix = within(dialog).getByLabelText('改名后缀');
    await user.clear(suffix);
    await user.type(suffix, '（第二季）');
    await user.click(within(dialog).getByRole('checkbox', { name: /沿用源项目的风格设定/ }));
    await user.click(within(dialog).getByRole('button', { name: '导入 2 项' }));

    const report = await screen.findByTestId('import-report');
    expect(report.textContent).toContain('导入了 1 项');
    expect(report.textContent).toContain('林夏」：目标项目里已经有同名角色');
    const post = calls.find((c) => c.method === 'POST');
    expect(post?.body).toEqual({
      source: 's1',
      character_ids: [1],
      location_ids: [5],
      prop_ids: [],
      on_conflict: 'rename',
      rename_suffix: '（第二季）',
      candidates: 'selected',
      with_style: true,
    });
    const invalidated = spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(invalidated).toContain(JSON.stringify(keys.characters('s2')));
    expect(invalidated).toContain(JSON.stringify(keys.locations('s2')));
    expect(invalidated).toContain(JSON.stringify(keys.props('s2')));

    await user.click(screen.getByRole('button', { name: '完成' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('全不选时不能导入；导入失败显示原因', async () => {
    const user = userEvent.setup();
    installFetch({
      'GET /api/projects': [project('s1', '第一季')],
      'GET /api/projects/s1/asset-summary': { ...SOURCE, has_style: false },
      'GET /api/projects/s2/asset-summary': MINE,
      'POST /api/projects/s2/import-assets': new Response(JSON.stringify({ detail: '找不到项目 s1' }), { status: 404 }),
    });
    renderAt(<ImportAssetsButton slug="s2" />);
    await user.click(screen.getByRole('button', { name: '从其他项目导入' }));
    const dialog = screen.getByRole('dialog');
    await waitFor(() => expect(within(dialog).getByRole('option', { name: '第一季' })).toBeTruthy());
    await user.selectOptions(within(dialog).getByRole('combobox'), 's1');
    const chars = await within(dialog).findByRole('group', { name: '角色' });
    await user.click(within(chars).getByRole('button', { name: '全不选' }));
    const locs = within(dialog).getByRole('group', { name: '场景' });
    await user.click(within(locs).getByRole('button', { name: '全不选' }));
    expect(within(dialog).queryByRole('checkbox', { name: /沿用源项目的风格设定/ })).toBeNull();
    expect((within(dialog).getByRole('button', { name: '导入' }) as HTMLButtonElement).disabled).toBe(true);

    await user.click(within(locs).getByRole('button', { name: '全选' }));
    await user.click(within(dialog).getByRole('button', { name: '导入 1 项' }));
    expect(await within(dialog).findByText(/找不到项目 s1/)).toBeTruthy();
  });
});
