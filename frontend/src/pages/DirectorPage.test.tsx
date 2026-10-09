import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { installFetch } from '../test/fetch';
import { makeEpisode, makeShot } from '../test/factories';
import { renderAt } from '../test/render';
import { DirectorPage } from './DirectorPage';

const EP = makeEpisode({
  id: 4,
  shots: [makeShot({ id: 11, code: 'S01', duration: 5 }), makeShot({ id: 12, code: 'S02', order: 1, duration: 6 })],
});
const ENGINES = { image: [], video: [] };
const AT = { route: '/projects/demo/episodes/4/director', path: '/projects/:slug/episodes/:episodeId/director' };

function routes(extra: Record<string, unknown> = {}) {
  return {
    'GET /api/episodes/4': EP,
    'GET /api/episodes/4/exports': [],
    'GET /api/engines': ENGINES,
    'GET /api/projects/demo/end-card': null,
    ...extra,
  };
}

describe('DirectorPage', () => {
  it('没勾选时整集生成；勾选后只提交选中镜头的 id', async () => {
    const calls = installFetch(routes({ 'POST /api/episodes/4/run': { job_ids: [31, 32] } }));
    renderAt(<DirectorPage />, AT);
    const user = userEvent.setup();
    await screen.findByTestId('shot-S01');
    expect(screen.getByText('2 个镜头，共 11.0 秒')).toBeTruthy();
    await user.selectOptions(screen.getByLabelText('候选数'), '2');
    await user.click(screen.getByRole('button', { name: '生成首帧（全部）' }));
    expect((await screen.findByRole('status')).textContent).toContain('已提交 2 个任务');
    await user.click(screen.getByLabelText('选择 S02'));
    await user.click(screen.getByRole('button', { name: '生成视频（选中 1 镜）' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(2));
    const runs = calls.filter((c) => c.method === 'POST');
    expect(runs[0].body).toEqual({ stage: 'keyframes', candidates: 2, force: false });
    expect(runs[1].body).toEqual({ stage: 'videos', candidates: 2, force: false, shots: ['12'] });
  });

  it('上移镜头时提交完整的新顺序', async () => {
    const calls = installFetch(routes({ 'POST /api/episodes/4/shots/reorder': [] }));
    renderAt(<DirectorPage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '上移 S02' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/episodes/4/shots/reorder')).toBe(true));
    expect(calls.find((c) => c.url === '/api/episodes/4/shots/reorder')?.body).toEqual({ shot_ids: [12, 11] });
  });

  it('删除前要确认，取消就不删', async () => {
    const calls = installFetch(routes({ 'DELETE /api/shots/11': () => new Response(null, { status: 204 }) }));
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    renderAt(<DirectorPage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '删除 S01' }));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    await user.click(screen.getByRole('button', { name: '删除 S01' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/shots/11')).toBe(true));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(confirm.mock.calls[0][0]).toContain('S01');
  });

  it('成片列表给出下载链接；没有分镜时引导去剧本页', async () => {
    installFetch(
      routes({
        'GET /api/episodes/4': makeEpisode({ id: 4, shots: [] }),
        'GET /api/episodes/4/exports': [
          {
            id: 1, episode_id: 4, variant: '9x16', file_path: 'demo/exports/ep01_9x16_x.mp4',
            url: '/media/demo/exports/ep01_9x16_x.mp4', settings: { clips: 2 }, created_at: '2026-10-08T11:36:09',
          },
        ],
      }),
    );
    renderAt(<DirectorPage />, AT);
    const link = await screen.findByTestId('export-link');
    expect(link.getAttribute('href')).toBe('/media/demo/exports/ep01_9x16_x.mp4');
    expect(link.textContent).toBe('ep01_9x16_x.mp4');
    expect(link.hasAttribute('download')).toBe(true);
    expect(screen.getByRole('link', { name: '去剧本页运行分镜' }).getAttribute('href')).toBe('/projects/demo/episodes/4/script');
  });

  it('点编号在地址栏记下镜头并打开详情抽屉', async () => {
    installFetch(routes());
    renderAt(<DirectorPage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '打开 S02' }));
    expect(screen.getByTestId('location').textContent).toBe('/projects/demo/episodes/4/director?shot=12');
    expect(await screen.findByRole('dialog', { name: '镜头 S02 详情' })).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '关闭' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('合成导出带上字幕、配音、片尾卡选项；勾高棉语字幕会连带勾中文字幕', async () => {
    const calls = installFetch(routes({ 'POST /api/episodes/4/run': { job_ids: [41, 42, 43] } }));
    renderAt(<DirectorPage />, AT);
    const user = userEvent.setup();
    await screen.findByTestId('shot-S01');
    await user.click(screen.getByRole('button', { name: '合成导出' }));
    await user.click(screen.getByLabelText('高棉语字幕'));
    expect((screen.getByLabelText('中文字幕') as HTMLInputElement).checked).toBe(true);
    await user.click(screen.getByLabelText('高棉语配音'));
    await user.click(screen.getByLabelText('片尾卡'));
    await user.click(screen.getByRole('button', { name: '合成导出' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(2));
    const runs = calls.filter((c) => c.method === 'POST');
    expect(runs[0].body).toEqual({
      stage: 'export', candidates: 1, force: false,
      export: { subtitles: 'none', dub: 'off', end_card: false },
    });
    expect(runs[1].body).toEqual({
      stage: 'export', candidates: 1, force: false,
      export: { subtitles: 'zh+km', dub: 'km', end_card: true },
    });
    await user.click(screen.getByLabelText('中文字幕'));
    expect((screen.getByLabelText('高棉语字幕') as HTMLInputElement).checked).toBe(false);
  });

  it('翻译高棉语字幕提交翻译任务', async () => {
    const calls = installFetch(routes({ 'POST /api/episodes/4/subtitles/translate': { job_ids: [50] } }));
    renderAt(<DirectorPage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '翻译高棉语字幕' }));
    expect((await screen.findByRole('status')).textContent).toContain('#50');
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ force: false });
  });

  it('成片列表标出字幕、配音、片尾卡和待校对', async () => {
    installFetch(
      routes({
        'GET /api/episodes/4/exports': [
          {
            id: 2, episode_id: 4, variant: '9x16', file_path: 'demo/exports/ep01_9x16_zhkm_dubkm_card_x.mp4',
            url: '/media/demo/exports/ep01_9x16_zhkm_dubkm_card_x.mp4',
            settings: { clips: 6, subtitles: 'zh+km', dub: 'km', end_card: true, duration: 33.6, km_review: 'ai_draft' },
            created_at: '2026-10-09T12:00:00',
          },
        ],
      }),
    );
    renderAt(<DirectorPage />, AT);
    const row = (await screen.findByTestId('export-link')).closest('li') as HTMLElement;
    expect(row.textContent).toContain('中高字幕');
    expect(row.textContent).toContain('高棉语配音');
    expect(row.textContent).toContain('片尾卡');
    expect(row.textContent).toContain('33.6 秒');
    expect(row.textContent).toContain('高棉语待母语校对');
  });
});
