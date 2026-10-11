import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFetch } from '../../test/fetch';
import { renderAt } from '../../test/render';
import { ArchivedCandidates } from './ArchivedCandidates';

const ROW = {
  id: 7, episode_id: 3, shot_code: 'S04', kind: 'video', file_path: 'demo/v/old.mp4', url: '/media/demo/v/old.mp4',
  engine: 'h3_video', params: {}, note: '', was_selected: true, shot_snapshot: { action: '她抬头看向镜头' },
  reason: 'storyboard', created_at: '2026-10-01T10:00:00', archived_at: '2026-10-02T10:00:00',
};
const FF = { ...ROW, id: 8, kind: 'first_frame', file_path: 'demo/ff/old.png', url: '/media/demo/ff/old.png', was_selected: false };

describe('ArchivedCandidates', () => {
  it('收着时不请求；展开后列出同一集的历史候选，带预览和当时的镜头', async () => {
    const calls = installFetch({ 'GET /api/episodes/3/archived-candidates': [ROW, FF] });
    renderAt(<ArchivedCandidates episodeId={3} shotId={5} />);
    expect(calls.length).toBe(0);
    const user = userEvent.setup();
    await user.click(screen.getByText('历史候选'));
    const list = await screen.findByRole('list', { name: '历史候选' });
    const items = within(list).getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(items[0].querySelector('video')?.getAttribute('src')).toBe('/media/demo/v/old.mp4');
    expect(items[1].querySelector('img')?.getAttribute('src')).toBe('/media/demo/ff/old.png');
    expect(items[0].textContent).toContain('原 S04');
    expect(items[0].textContent).toContain('当时选定');
    expect(items[0].textContent).toContain('她抬头看向镜头');
  });

  it('构图参考图（shot_ref）也列出来，标「构图参考图」，能恢复', async () => {
    const REF = { ...ROW, id: 9, kind: 'shot_ref', file_path: 'demo/shot_ref/a.png', url: '/media/demo/shot_ref/a.png', engine: 'upload', was_selected: false };
    const calls = installFetch({
      'GET /api/episodes/3/archived-candidates': [REF],
      'POST /api/archived-candidates/9/restore': { id: 100 },
    });
    renderAt(<ArchivedCandidates episodeId={3} shotId={5} />);
    const user = userEvent.setup();
    await user.click(screen.getByText('历史候选'));
    const list = await screen.findByRole('list', { name: '历史候选' });
    const item = within(list).getByRole('listitem');
    expect(item.querySelector('img')?.getAttribute('src')).toBe('/media/demo/shot_ref/a.png');
    expect(item.textContent).toContain('构图参考图 · 原 S04');
    await user.click(within(item).getByRole('button', { name: '恢复到这个镜头' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.url).toBe('/api/archived-candidates/9/restore'));
  });

  it('没有历史候选时说明一句', async () => {
    installFetch({ 'GET /api/episodes/3/archived-candidates': [] });
    renderAt(<ArchivedCandidates episodeId={3} shotId={5} />);
    await userEvent.setup().click(screen.getByText('历史候选'));
    expect(await screen.findByText(/没有历史候选/)).toBeTruthy();
  });

  it('「恢复到这个镜头」提交镜头 id，成功后刷新列表', async () => {
    let rows = [ROW, FF];
    const calls = installFetch({
      'GET /api/episodes/3/archived-candidates': () => rows,
      'POST /api/archived-candidates/7/restore': () => {
        rows = [FF];
        return { id: 99 };
      },
    });
    renderAt(<ArchivedCandidates episodeId={3} shotId={5} />);
    const user = userEvent.setup();
    await user.click(screen.getByText('历史候选'));
    await screen.findByRole('list', { name: '历史候选' });
    await user.click(screen.getAllByRole('button', { name: '恢复到这个镜头' })[0]);
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    const post = calls.find((c) => c.method === 'POST');
    expect(post?.url).toBe('/api/archived-candidates/7/restore');
    expect(post?.body).toEqual({ shot_id: 5 });
    await waitFor(() => expect(within(screen.getByRole('list', { name: '历史候选' })).getAllByRole('listitem')).toHaveLength(1));
  });

  it('恢复失败就地显示错误', async () => {
    installFetch({
      'GET /api/episodes/3/archived-candidates': [ROW],
      'POST /api/archived-candidates/7/restore': new Response(JSON.stringify({ detail: '只能恢复到同一集的镜头上' }), { status: 400 }),
    });
    renderAt(<ArchivedCandidates episodeId={3} shotId={5} />);
    const user = userEvent.setup();
    await user.click(screen.getByText('历史候选'));
    await user.click(await screen.findByRole('button', { name: '恢复到这个镜头' }));
    expect(await screen.findByText(/只能恢复到同一集的镜头上/)).toBeTruthy();
  });
});
