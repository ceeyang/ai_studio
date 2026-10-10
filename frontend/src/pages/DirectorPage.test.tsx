import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Slot } from '../api/types';
import { installFetch } from '../test/fetch';
import { makeCandidate, makeEpisode, makeJob, makeShot, makeSlot } from '../test/factories';
import { renderAt } from '../test/render';
import { DirectorPage } from './DirectorPage';

const EP = makeEpisode({
  id: 4,
  shots: [makeShot({ id: 11, code: 'S01', duration: 5 }), makeShot({ id: 12, code: 'S02', order: 1, duration: 6 })],
});
const ENGINES = { image: [], video: [] };
const AT = { route: '/projects/demo/episodes/4/director', path: '/projects/:slug/episodes/:episodeId/director' };

/** 选定了一张的槽位；stale 表示已过期 */
function chosen(id: number, kind: string, stale = false): Slot {
  return makeSlot({ id, kind, selected_candidate_id: id * 10, stale, candidates: [makeCandidate({ id: id * 10 })] });
}

function doneJob(id: number, type: string, seconds: number) {
  const end = Date.UTC(2026, 9, 10, 10, id);
  return makeJob({
    id, type, status: 'done', started_at: new Date(end - seconds * 1000).toISOString(), finished_at: new Date(end).toISOString(),
  });
}

function routes(extra: Record<string, unknown> = {}) {
  return {
    'GET /api/episodes/4': EP,
    'GET /api/engines': ENGINES,
    'GET /api/jobs': [],
    ...extra,
  };
}

describe('DirectorPage', () => {
  it('没勾选时整集生成；勾选后只提交选中镜头的 id；超过 3 个任务先确认并给出耗时预估', async () => {
    const calls = installFetch(
      routes({
        'POST /api/episodes/4/run': { job_ids: [31, 32] },
        'GET /api/jobs': [doneJob(1, 'image.keyframe', 50), doneJob(2, 'image.keyframe', 66), doneJob(3, 'video.shot', 300)],
      }),
    );
    renderAt(<DirectorPage />, AT);
    const user = userEvent.setup();
    await screen.findByTestId('shot-S01');
    expect(screen.getByText('2 个镜头，共 11.0 秒')).toBeTruthy();
    await user.selectOptions(screen.getByLabelText('候选数'), '2');
    await user.click(screen.getByRole('button', { name: '生成首帧（全部）' }));
    const dlg = await screen.findByRole('dialog', { name: '批量生成首帧？' });
    expect(dlg.textContent).toContain('将提交 4 个首帧任务，按最近同类任务平均 58 秒，约 4 分钟，会占用显卡');
    await user.click(within(dlg).getByRole('button', { name: '继续提交' }));
    expect((await screen.findByRole('status')).textContent).toContain('已提交');
    await user.click(screen.getByLabelText('选择 S02'));
    await user.click(screen.getByRole('button', { name: '生成视频（选中 1 镜）' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(2));
    const runs = calls.filter((c) => c.method === 'POST');
    expect(runs[0].body).toEqual({ stage: 'keyframes', candidates: 2, force: false });
    expect(runs[1].body).toEqual({ stage: 'videos', candidates: 2, force: false, shots: ['12'] });
  });

  it('没有同类任务的耗时记录时写「耗时未知」；取消就不提交', async () => {
    const shots = [11, 12, 13, 14].map((id, i) => makeShot({ id, code: `S0${i + 1}`, order: i }));
    const calls = installFetch(routes({ 'GET /api/episodes/4': makeEpisode({ id: 4, shots }) }));
    renderAt(<DirectorPage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '生成视频（全部）' }));
    const dlg = await screen.findByRole('dialog', { name: '批量生成视频？' });
    expect(dlg.textContent).toContain('将提交 4 个视频任务，耗时未知，会占用显卡');
    await user.click(within(dlg).getByRole('button', { name: '取消' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  });

  it('单候选、不强制、要出的格子都是最新的：不提交，提示勾强制重跑', async () => {
    const fresh = makeShot({ id: 11, code: 'S01', slots: [chosen(1, 'first_frame')] });
    const calls = installFetch(
      routes({ 'GET /api/episodes/4': makeEpisode({ id: 4, shots: [fresh] }), 'POST /api/episodes/4/run': { job_ids: [90] } }),
    );
    renderAt(<DirectorPage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '生成首帧 S01' }));
    expect((await screen.findByRole('status')).textContent).toContain('都是最新的，勾强制重跑才会重出');
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    await user.click(screen.getByLabelText('强制重跑'));
    await user.click(screen.getByRole('button', { name: '生成首帧 S01' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ stage: 'keyframes', candidates: 1, force: true, shots: ['11'] });
  });

  it('「只重做过期的」：首帧过期的镜头首帧接着视频一起重出，只有视频过期的单独重出视频，都强制', async () => {
    const shots = [
      makeShot({ id: 11, code: 'S01', slots: [chosen(1, 'first_frame', true), chosen(2, 'video')] }),
      makeShot({ id: 12, code: 'S02', order: 1, slots: [chosen(3, 'first_frame', true), chosen(4, 'video', true)] }),
      makeShot({ id: 13, code: 'S03', order: 2, slots: [chosen(5, 'first_frame'), chosen(6, 'video', true)] }),
    ];
    const calls = installFetch(
      routes({ 'GET /api/episodes/4': makeEpisode({ id: 4, shots }), 'POST /api/episodes/4/run': { job_ids: [91] } }),
    );
    renderAt(<DirectorPage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '只重做过期的（4）' }));
    // 2 个首帧 + 接着的 2 个视频 + 单独 1 个视频 = 5 个任务：先预估
    const dlg = await screen.findByRole('dialog', { name: '重做过期的格子？' });
    expect(dlg.textContent).toContain('将提交 2 个首帧任务、3 个视频任务');
    await user.click(within(dlg).getByRole('button', { name: '继续提交' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(2));
    const runs = calls.filter((c) => c.method === 'POST');
    expect(runs[0].body).toEqual({ stage: 'keyframes', candidates: 1, force: true, shots: ['11', '12'], then: ['videos'] });
    // S02 的视频已经在链里了，不再单独提交
    expect(runs[1].body).toEqual({ stage: 'videos', candidates: 1, force: true, shots: ['13'] });
  });

  it('失败格子已有最新的选定结果时，「重试」也要强制提交，不被「都是最新的」吞掉', async () => {
    const shot = makeShot({
      id: 11, code: 'S01', slots: [chosen(1, 'first_frame')],
      failures: [{ id: 80, type: 'image.keyframe', status: 'failed', error: '显存不足' }],
    });
    const calls = installFetch(
      routes({ 'GET /api/episodes/4': makeEpisode({ id: 4, shots: [shot] }), 'POST /api/episodes/4/run': { job_ids: [94] } }),
    );
    renderAt(<DirectorPage />, AT);
    await userEvent.setup().click(await screen.findByRole('button', { name: '重试首帧 S01' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ stage: 'keyframes', candidates: 1, force: true, shots: ['11'] });
  });

  it('没有过期的格子时「只重做过期的（0）」禁用', async () => {
    installFetch(routes());
    renderAt(<DirectorPage />, AT);
    expect(((await screen.findByRole('button', { name: '只重做过期的（0）' })) as HTMLButtonElement).disabled).toBe(true);
  });

  it('提交被后端拒绝时就地显示原因', async () => {
    installFetch(
      routes({
        'POST /api/episodes/4/run': () => new Response(JSON.stringify({ detail: 'S01 还没有首帧，不能出视频' }), { status: 400 }),
      }),
    );
    renderAt(<DirectorPage />, AT);
    await userEvent.setup().click(await screen.findByRole('button', { name: '生成视频 S01' }));
    expect((await screen.findByRole('alert')).textContent).toContain('S01 还没有首帧');
  });

  it('上移镜头时提交完整的新顺序', async () => {
    const calls = installFetch(routes({ 'POST /api/episodes/4/shots/reorder': [] }));
    renderAt(<DirectorPage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '更多操作 S02' }));
    await user.click(screen.getByRole('menuitem', { name: '上移' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/episodes/4/shots/reorder')).toBe(true));
    expect(calls.find((c) => c.url === '/api/episodes/4/shots/reorder')?.body).toEqual({ shot_ids: [12, 11] });
  });

  it('删除前用确认框问，取消就不删', async () => {
    const calls = installFetch(routes({ 'DELETE /api/shots/11': () => new Response(null, { status: 204 }) }));
    renderAt(<DirectorPage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '更多操作 S01' }));
    await user.click(screen.getByRole('menuitem', { name: '删除' }));
    const dlg = await screen.findByRole('dialog', { name: '删除镜头 S01？' });
    await user.click(within(dlg).getByRole('button', { name: '取消' }));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    await user.click(screen.getByRole('button', { name: '更多操作 S01' }));
    await user.click(screen.getByRole('menuitem', { name: '删除' }));
    await user.click(within(await screen.findByRole('dialog', { name: '删除镜头 S01？' })).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/shots/11')).toBe(true));
  });

  it('没有分镜时引导去剧本页', async () => {
    installFetch(routes({ 'GET /api/episodes/4': makeEpisode({ id: 4, shots: [] }) }));
    renderAt(<DirectorPage />, AT);
    expect((await screen.findByRole('link', { name: '去剧本页运行分镜' })).getAttribute('href')).toBe('/projects/demo/episodes/4/script');
  });

  it('导出在导出页：导演台只给「去导出」链接并写明几镜有视频', async () => {
    const withVideo = makeShot({ id: 11, code: 'S01', duration: 5, slots: [chosen(1, 'video')] });
    installFetch(routes({ 'GET /api/episodes/4': makeEpisode({ id: 4, shots: [withVideo, EP.shots[1]] }) }));
    renderAt(<DirectorPage />, AT);
    const link = await screen.findByRole('link', { name: '去导出（1/2 镜有视频）' });
    expect(link.getAttribute('href')).toBe('/projects/demo/episodes/4/export');
    expect(screen.queryByRole('button', { name: '合成导出' })).toBeNull();
    expect(screen.queryByLabelText('高棉语字幕')).toBeNull();
    expect(screen.queryByRole('button', { name: '翻译高棉语字幕' })).toBeNull();
    expect(screen.queryByText('片尾卡设置')).toBeNull();
  });

  it('点编号在地址栏记下镜头并打开详情抽屉；抽屉和工具栏共用候选数', async () => {
    installFetch(routes());
    renderAt(<DirectorPage />, AT);
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText('候选数'), '3');
    await user.click(screen.getByRole('button', { name: '打开 S02' }));
    expect(screen.getByTestId('location').textContent).toBe('/projects/demo/episodes/4/director?shot=12');
    const dlg = await screen.findByRole('dialog', { name: '镜头 S02 详情' });
    expect((within(dlg).getByLabelText('候选数') as HTMLSelectElement).value).toBe('3');
    await user.click(within(dlg).getByRole('button', { name: '关闭' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByTestId('location').textContent).toBe('/projects/demo/episodes/4/director');
  });

  it('抽屉里「保存并生成」：刚保存的提示词让格子过期，不能当成最新的跳过', async () => {
    const fresh = makeShot({ id: 11, code: 'S01', image_prompt: 'old', slots: [chosen(1, 'first_frame')] });
    const calls = installFetch(
      routes({
        'GET /api/episodes/4': makeEpisode({ id: 4, shots: [fresh] }),
        'PATCH /api/shots/11': { ...fresh, image_prompt: 'new' },
        'POST /api/episodes/4/run': { job_ids: [93] },
      }),
    );
    renderAt(<DirectorPage />, { ...AT, route: '/projects/demo/episodes/4/director?shot=11' });
    const user = userEvent.setup();
    const dlg = await screen.findByRole('dialog', { name: '镜头 S01 详情' });
    const ta = within(dlg).getByLabelText('图像提示词（英文）');
    await user.clear(ta);
    await user.type(ta, 'new');
    await user.click(within(dlg).getByRole('button', { name: '保存并生成首帧' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ stage: 'keyframes', candidates: 1, force: false, shots: ['11'] });
  });

  it('用剧集页头：只有一个主按钮（下一步），工具栏的生成按钮是次要的', async () => {
    installFetch(routes());
    const { container } = renderAt(<DirectorPage />, AT);
    await screen.findByRole('heading', { level: 2, name: '导演台' });
    expect(screen.getByRole('link', { name: '下一步：运行剧本' })).toBeTruthy();
    expect(container.querySelectorAll('.primary')).toHaveLength(1);
    expect(screen.getByRole('button', { name: '生成首帧（全部）' }).className).not.toContain('primary');
  });

  it('剧集加载失败：显示原因和「回到项目」', async () => {
    installFetch({ 'GET /api/episodes/4': new Response(JSON.stringify({ detail: '剧集 4 不存在' }), { status: 404 }) });
    renderAt(<DirectorPage />, AT);
    expect(await screen.findByText(/剧集 4 不存在/)).toBeTruthy();
    expect(screen.getByRole('link', { name: '回到项目' }).getAttribute('href')).toBe('/projects/demo');
  });

  it('下一步在导演台：对应的生成按钮是本页唯一的主按钮', async () => {
    const ep = { ...EP, scene_blocks: [{ id: 1, order: 0, location_id: null, text: 'a' }] };
    installFetch(routes({ 'GET /api/episodes/4': ep, 'GET /api/projects/demo/characters': [{ id: 8, project_id: 1, name: '林夏', description: '', visual_prompt: '', core_features: '', voice: null, portrait_url: '/p.png' }], 'GET /api/episodes/4/exports': [] }));
    const { container } = renderAt(<DirectorPage />, AT);
    const kf = await screen.findByRole('button', { name: '生成首帧（全部）' });
    await waitFor(() => expect(kf.className).toContain('primary'));
    expect(kf.hasAttribute('data-next-action')).toBe(true);
    expect(screen.getByRole('button', { name: '生成视频（全部）' }).className).not.toContain('primary');
    expect(screen.getByRole('button', { name: /下一步：生成首帧/ })).toBeTruthy();
    expect(container.querySelectorAll('.primary')).toHaveLength(1);
  });

  it('首帧有过期：「只重做过期的」是主按钮', async () => {
    const ep = {
      ...EP,
      scene_blocks: [{ id: 1, order: 0, location_id: null, text: 'a' }],
      shots: [makeShot({ id: 11, code: 'S01', slots: [chosen(1, 'first_frame', true)] })],
    };
    installFetch(routes({ 'GET /api/episodes/4': ep, 'GET /api/projects/demo/characters': [{ id: 8, project_id: 1, name: '林夏', description: '', visual_prompt: '', core_features: '', voice: null, portrait_url: '/p.png' }], 'GET /api/episodes/4/exports': [] }));
    renderAt(<DirectorPage />, AT);
    const redo = await screen.findByRole('button', { name: '只重做过期的（1）' });
    await waitFor(() => expect(redo.className).toContain('primary'));
    expect(screen.getByRole('button', { name: '生成首帧（全部）' }).className).not.toContain('primary');
  });
});
