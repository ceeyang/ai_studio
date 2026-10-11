import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ExportRow } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeCandidate, makeEpisode, makeShot, makeSlot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { ExportStage, OPTIONS_KEY } from './ExportStage';

/** 有选定视频的镜头；stale 表示视频已过期 */
function withVideo(id: number, code: string, stale = false) {
  return makeShot({
    id,
    code,
    order: id,
    slots: [
      makeSlot({
        id: id * 10, kind: 'video', selected_candidate_id: id * 100, stale,
        candidates: [makeCandidate({ id: id * 100, url: `/media/v${id}.mp4` })],
      }),
    ],
  });
}

const READY = makeEpisode({ id: 4, shots: [withVideo(11, 'S01'), withVideo(12, 'S02')] });
const AT = { route: '/projects/demo/episodes/4?stage=export', path: '/projects/:slug/episodes/:episodeId' };

function exportRow(p: Partial<ExportRow> = {}): ExportRow {
  return {
    id: 1, episode_id: 4, variant: '9x16', file_path: 'demo/exports/ep01_9x16_a.mp4',
    url: '/media/demo/exports/ep01_9x16_a.mp4', settings: { clips: 2 }, label: '9:16 · 12.0 秒',
    is_final: false, created_at: '2026-10-08T11:36:09', ...p,
  };
}

function routes(extra: Record<string, unknown> = {}) {
  return {
    'GET /api/episodes/4': READY,
    'GET /api/episodes/4/exports': [],
    'GET /api/projects/demo/end-card': null,
    ...extra,
  };
}

describe('ExportStage', () => {
  it('缺视频时「合成导出」禁用，列出缺的镜号并给出去视频阶段的链接', async () => {
    installFetch(routes({ 'GET /api/episodes/4': makeEpisode({ id: 4, shots: [withVideo(11, 'S01'), makeShot({ id: 12, code: 'S02' }), makeShot({ id: 13, code: 'S03' })] }) }));
    renderAt(<ExportStage />, AT);
    expect(await screen.findByText('1/3 镜有视频')).toBeTruthy();
    expect((screen.getByRole('button', { name: '合成导出' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId('missing-videos').textContent).toContain('S02、S03');
    expect(screen.getByRole('link', { name: '去视频阶段' }).getAttribute('href')).toBe('/projects/demo/episodes/4?stage=videos');
  });

  it('就绪检查标出视频已过期的镜头；都有视频时可以导出', async () => {
    installFetch(routes({ 'GET /api/episodes/4': makeEpisode({ id: 4, shots: [withVideo(11, 'S01'), withVideo(12, 'S02', true)] }) }));
    renderAt(<ExportStage />, AT);
    expect((await screen.findByTestId('readiness')).textContent).toBe('2/2 镜有视频 · 1 镜视频已过期（S02）');
    expect((screen.getByRole('button', { name: '合成导出' }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByTestId('missing-videos')).toBeNull();
  });

  it('选定的视频出片自检疑似跳切：就绪检查里警告（不拦导出）', async () => {
    const cut = withVideo(12, 'S02');
    cut.slots[0].candidates[0].params = { qc: { hard_cuts: [2.04] } };
    installFetch(routes({ 'GET /api/episodes/4': makeEpisode({ id: 4, shots: [withVideo(11, 'S01'), cut] }) }));
    renderAt(<ExportStage />, AT);
    const warn = await screen.findByTestId('flagged-videos');
    expect(warn.textContent).toContain('选定的视频疑似跳切：S02（2.0s）');
    expect((screen.getByRole('button', { name: '合成导出' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('合成导出带上字幕、配音、片尾卡选项；勾高棉语字幕会连带勾中文字幕', async () => {
    localStorage.clear();
    const calls = installFetch(routes({ 'POST /api/episodes/4/run': { job_ids: [41] } }));
    renderAt(<ExportStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '合成导出' }));
    await user.click(screen.getByLabelText('高棉语字幕'));
    expect((screen.getByLabelText('中文字幕') as HTMLInputElement).checked).toBe(true);
    await user.click(screen.getByLabelText('高棉语配音'));
    await user.click(screen.getByLabelText('片尾卡'));
    await user.click(screen.getByRole('button', { name: '合成导出' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(2));
    const runs = calls.filter((c) => c.method === 'POST');
    expect(runs[0].body).toEqual({
      stage: 'export',
      export: { subtitles: 'none', dub: 'off', end_card: false, bed: true, align: 'timeline' },
    });
    expect(runs[1].body).toEqual({
      stage: 'export',
      export: { subtitles: 'zh+km', dub: 'km', end_card: true, bed: true, align: 'timeline' },
    });
    expect(screen.getByRole('status').textContent).toContain('合成导出');
    await user.click(screen.getByLabelText('中文字幕'));
    expect((screen.getByLabelText('高棉语字幕') as HTMLInputElement).checked).toBe(false);
  });

  it('不保留原声底、配音对齐实际说话；对齐只在勾了配音时可选', async () => {
    localStorage.clear();
    const calls = installFetch(routes({ 'POST /api/episodes/4/run': { job_ids: [44] } }));
    renderAt(<ExportStage />, AT);
    const user = userEvent.setup();
    await screen.findByTestId('readiness');
    const bed = screen.getByLabelText('保留原声底') as HTMLInputElement;
    const align = screen.getByLabelText('配音对齐实际说话') as HTMLInputElement;
    expect(bed.checked).toBe(true);
    expect(align.disabled).toBe(true);
    await user.click(screen.getByLabelText('高棉语配音'));
    expect(align.disabled).toBe(false);
    await user.click(align);
    await user.click(bed);
    await user.click(screen.getByRole('button', { name: '合成导出' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({
      stage: 'export',
      export: { subtitles: 'none', dub: 'km', end_card: false, bed: false, align: 'detected' },
    });
  });

  it('导出被后端拒绝时就地显示原因', async () => {
    localStorage.clear();
    installFetch(
      routes({
        'POST /api/episodes/4/run': () =>
          new Response(JSON.stringify({ detail: 'S02 还没有视频，不能导出' }), { status: 400 }),
      }),
    );
    renderAt(<ExportStage />, AT);
    await userEvent.setup().click(await screen.findByRole('button', { name: '合成导出' }));
    expect((await screen.findByRole('alert')).textContent).toContain('S02 还没有视频');
  });

  it('选项按剧集记住：重新打开还在；本地存储不可用时照常工作', async () => {
    localStorage.clear();
    installFetch(routes());
    const first = renderAt(<ExportStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByLabelText('高棉语字幕'));
    await user.click(screen.getByLabelText('保留原声底'));
    expect(JSON.parse(localStorage.getItem(OPTIONS_KEY(4)) ?? '{}')).toMatchObject({ subKm: true, subZh: true, bed: false });
    first.unmount();

    const second = renderAt(<ExportStage />, AT);
    expect(((await screen.findByLabelText('高棉语字幕')) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText('中文字幕') as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText('保留原声底') as HTMLInputElement).checked).toBe(false);
    // 别的剧集不受影响
    expect(localStorage.getItem(OPTIONS_KEY(5))).toBeNull();
    second.unmount();

    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
    try {
      renderAt(<ExportStage />, AT);
      const zh = (await screen.findByLabelText('中文字幕')) as HTMLInputElement;
      expect(zh.checked).toBe(false);
      await user.click(zh);
      expect(zh.checked).toBe(true);
    } finally {
      get.mockRestore();
      set.mockRestore();
    }
  });

  it('成片列表：可读标签、定稿置顶带标记、待校对只在列表头提示一次、文件名是下载链接', async () => {
    installFetch(
      routes({
        'GET /api/episodes/4/exports': [
          exportRow({ id: 3, label: '9:16 · 中高字幕 · 高棉语配音 · 37.1 秒', is_final: true, settings: { km_review: 'ai_draft' } }),
          exportRow({ id: 2, file_path: 'demo/exports/ep01_b.mp4', url: '/media/b.mp4', label: '9:16 · 中文字幕 · 30.0 秒', settings: { km_review: 'ai_draft' } }),
        ],
      }),
    );
    renderAt(<ExportStage />, AT);
    const list = await screen.findByRole('list', { name: '成片列表' });
    const items = within(list).getAllByRole('listitem');
    expect(items[0].textContent).toContain('9:16 · 中高字幕 · 高棉语配音 · 37.1 秒');
    expect(within(items[0]).getByText('定稿')).toBeTruthy();
    expect(within(items[0]).queryByRole('button', { name: /设为定稿/ })).toBeNull();
    expect(within(items[1]).getByRole('button', { name: '设为定稿' })).toBeTruthy();
    expect(screen.getAllByText(/高棉语待母语校对/)).toHaveLength(1);
    const links = screen.getAllByTestId('export-link');
    expect(links[1].getAttribute('href')).toBe('/media/b.mp4');
    expect(links[1].textContent).toBe('ep01_b.mp4');
    expect(links[1].hasAttribute('download')).toBe(true);
  });

  it('定稿可以取消：点「取消定稿」发 PATCH is_final=false', async () => {
    const calls = installFetch(
      routes({
        'GET /api/episodes/4/exports': [exportRow({ id: 3, is_final: true })],
        'PATCH /api/exports/3': exportRow({ id: 3, is_final: false }),
      }),
    );
    renderAt(<ExportStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '取消定稿' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(calls.find((c) => c.method === 'PATCH')).toMatchObject({ url: '/api/exports/3', body: { is_final: false } });
  });

  it('没有标签的旧成片按设置拼出标签', async () => {
    installFetch(
      routes({
        'GET /api/episodes/4/exports': [
          exportRow({ id: 2, label: '', settings: { clips: 6, subtitles: 'zh+km', dub: 'km', end_card: true, duration: 33.6, bed: false, align: 'detected' } }),
        ],
      }),
    );
    renderAt(<ExportStage />, AT);
    const item = within(await screen.findByRole('list', { name: '成片列表' })).getAllByRole('listitem')[0];
    for (const t of ['中高字幕', '高棉语配音', '片尾卡', '33.6 秒', '无原声底', '配音对齐说话']) expect(item.textContent).toContain(t);
  });

  it('点列表里的成片切换主播放器；设为定稿发 PATCH', async () => {
    const calls = installFetch(
      routes({
        'GET /api/episodes/4/exports': [exportRow({ id: 3, url: '/media/a.mp4' }), exportRow({ id: 2, url: '/media/b.mp4', label: '第二版' })],
        'PATCH /api/exports/2': exportRow({ id: 2, is_final: true }),
      }),
    );
    renderAt(<ExportStage />, AT);
    const user = userEvent.setup();
    const player = (await screen.findByTestId('export-player')) as HTMLVideoElement;
    expect(player.getAttribute('src')).toBe('/media/a.mp4');
    await user.click(screen.getByRole('button', { name: /第二版/ }));
    expect(screen.getByTestId('export-player').getAttribute('src')).toBe('/media/b.mp4');
    await user.click(screen.getAllByRole('button', { name: '设为定稿' })[1]);
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    const patch = calls.find((c) => c.method === 'PATCH');
    expect(patch?.url).toBe('/api/exports/2');
    expect(patch?.body).toEqual({ is_final: true });
  });

  it('没有成片时说明怎么导出', async () => {
    installFetch(routes());
    renderAt(<ExportStage />, AT);
    expect(await screen.findByText(/还没有导出/)).toBeTruthy();
  });

  it('高棉语工具：翻译字幕、按高棉语时长改写中文（整集）', async () => {
    const calls = installFetch(
      routes({
        'POST /api/episodes/4/subtitles/translate': { job_ids: [50] },
        'POST /api/episodes/4/dialogue/fit-khmer': { job_ids: [60] },
      }),
    );
    renderAt(<ExportStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '翻译高棉语字幕' }));
    expect((await screen.findByRole('status')).textContent).toContain('翻译高棉语字幕');
    await user.click(screen.getByRole('button', { name: '按高棉语时长改写中文' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(2));
    const posts = calls.filter((c) => c.method === 'POST');
    expect(posts[0].url).toBe('/api/episodes/4/subtitles/translate');
    expect(posts[0].body).toEqual({ force: false });
    expect(posts[1].url).toBe('/api/episodes/4/dialogue/fit-khmer');
    expect(posts[1].body).toEqual({});
  });

  it('前面都做完、下一步是合成导出：「合成导出」打上 data-next-action（主按钮在阶段条）', async () => {
    const full = (id: number, code: string) =>
      makeShot({
        id, code, order: id, scene_block_id: 1, cast: [{ character_id: 8, variation_id: null }],
        slots: [
          makeSlot({ id: id * 10 + 1, kind: 'first_frame', selected_candidate_id: id * 100 + 1, candidates: [makeCandidate({ id: id * 100 + 1 })] }),
          withVideo(id, code).slots[0],
        ],
      });
    const ep = makeEpisode({ id: 4, scene_blocks: [{ id: 1, order: 0, location_id: null, text: 'a' }], shots: [full(11, 'S01'), full(12, 'S02')] });
    installFetch(
      routes({
        'GET /api/episodes/4': ep,
        'GET /api/projects/demo/characters': [
          { id: 8, project_id: 1, name: '林夏', description: '', visual_prompt: '', core_features: '', voice: null, portrait_url: '/media/p.png' },
        ],
      }),
    );
    const { container } = renderAt(<ExportStage />, AT);
    const btn = await screen.findByRole('button', { name: '合成导出' });
    await waitFor(() => expect(btn.hasAttribute('data-next-action')).toBe(true));
    expect(container.querySelectorAll('.primary')).toHaveLength(0);
  });
});
