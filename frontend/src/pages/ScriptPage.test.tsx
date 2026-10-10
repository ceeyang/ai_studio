import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFetch } from '../test/fetch';
import { makeEpisode, makeJob, makeShot } from '../test/factories';
import { renderAt } from '../test/render';
import { ScriptPage, toCharacterPatch } from './ScriptPage';

const EP = makeEpisode({
  id: 4,
  title: '提词器',
  scene_blocks: [{ id: 21, order: 0, location_id: 3, text: '直播间里，林夏念稿。' }],
});
const WITH_SHOTS = { ...EP, shots: [makeShot({ id: 1 }), makeShot({ id: 2, code: 'S02' })] };
const ROUTES = {
  'GET /api/episodes/4': EP,
  'GET /api/projects/demo/locations': [
    { id: 3, project_id: 1, name: '直播间', time_of_day: '夜', atmosphere: '压抑', visual_prompt: 'a dim studio' },
  ],
  'GET /api/projects/demo/characters': [
    { id: 8, project_id: 1, name: '林夏', description: '女主播', visual_prompt: 'young woman', core_features: '', voice: null, portrait_url: '/media/p/lin.png' },
    { id: 9, project_id: 1, name: '老周', description: '', visual_prompt: '', core_features: '', voice: null, portrait_url: null },
  ],
  'GET /api/projects/demo/style': null,
};
const AT = { route: '/projects/demo/episodes/4/script', path: '/projects/:slug/episodes/:episodeId/script' };
const IMPACT = {
  stage: 'storyboard', will_run: true, blocks: [3, 5], shots_deleted: 12, selected_first_frames: 10,
  selected_videos: 8, manual_kept: 2, overwrites: ['角色描述', '场景氛围'],
};

const writes = <T extends { method: string }>(calls: T[]) => calls.filter((c) => c.method !== 'GET');

describe('ScriptPage', () => {
  it('只管剧本：没有角色 / 场景 / 风格页签，右上是本集角色条，点了去角色页', async () => {
    installFetch(ROUTES);
    renderAt(<ScriptPage />, AT);
    expect(await screen.findByRole('heading', { name: /剧本 · 第 1 集/ })).toBeTruthy();
    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.queryByTestId('character-8')).toBeNull();
    const strip = await screen.findByRole('link', { name: /本集角色/ });
    expect(strip.getAttribute('href')).toBe('/projects/demo/episodes/4/cast');
    await waitFor(() => expect(within(strip).getByText('林夏')).toBeTruthy());
    expect(within(strip).getByText('老周')).toBeTruthy();
    expect((within(strip).getByRole('img', { name: '林夏' }) as HTMLImageElement).getAttribute('src')).toBe('/media/p/lin.png');
    expect(within(strip).getByText('1/2 有定妆照')).toBeTruthy();
  });

  it('还没有镜头时运行分镜：直接提交并跟踪任务', async () => {
    const calls = installFetch({ ...ROUTES, 'POST /api/episodes/4/run': { job_ids: [9] }, 'GET /api/jobs/9': makeJob({ id: 9, status: 'running' }) });
    renderAt(<ScriptPage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '运行分镜' }));
    expect((await screen.findByText(/运行分镜：已提交，进行中/)).textContent).toBeTruthy();
    expect(calls.some((c) => c.url.includes('rerun-impact'))).toBe(false);
    expect(writes(calls)[0]?.body).toEqual({ stage: 'storyboard', force: false });
  });

  it('已有镜头时重跑：先查影响，确认框原样列出会删的和会覆盖的，确认后才提交', async () => {
    const calls = installFetch({
      ...ROUTES,
      'GET /api/episodes/4': WITH_SHOTS,
      'GET /api/episodes/4/rerun-impact': IMPACT,
      'POST /api/episodes/4/run': { job_ids: [9] },
      'GET /api/jobs/9': makeJob({ id: 9, status: 'running' }),
    });
    renderAt(<ScriptPage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '运行分镜' }));
    const dlg = await screen.findByRole('dialog');
    expect(dlg.textContent).toContain('将重建段落 3、5 的镜头：删除 12 个镜头（其中 8 个有选定视频），保留 2 个手工镜头；会覆盖角色描述、场景氛围');
    expect(calls.find((c) => c.url.includes('rerun-impact'))?.url).toBe('/api/episodes/4/rerun-impact?stage=storyboard&force=0');
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dlg).getByRole('button', { name: '取消' }));
    expect(writes(calls)).toHaveLength(0);

    await user.click(screen.getByLabelText(/强制重跑/));
    await user.click(screen.getByRole('button', { name: '运行分镜' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '继续运行' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0].body).toEqual({ stage: 'storyboard', force: true });
    expect(calls.filter((c) => c.url.includes('rerun-impact')).at(-1)?.url).toContain('force=1');
  });

  it('输入没变（will_run=false）：不提交，提示勾强制重跑', async () => {
    const calls = installFetch({
      ...ROUTES,
      'GET /api/episodes/4': WITH_SHOTS,
      'GET /api/episodes/4/rerun-impact': { ...IMPACT, stage: 'script', will_run: false },
    });
    renderAt(<ScriptPage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '运行剧本' }));
    expect(await screen.findByText('输入没变，会跳过；要重做请勾强制重跑')).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(writes(calls)).toHaveLength(0);
  });

  it('原文有未保存修改：按钮变「保存并运行剧本」，先保存再运行', async () => {
    const calls = installFetch({
      ...ROUTES,
      'GET /api/episodes/4': makeEpisode({ id: 4, source_text: '' }),
      'PATCH /api/episodes/4': makeEpisode({ id: 4, source_text: '新的梗概' }),
      'POST /api/episodes/4/run': { job_ids: [9] },
      'GET /api/jobs/9': makeJob({ id: 9, status: 'running' }),
    });
    renderAt(<ScriptPage />, AT);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText('原文 / 梗概'), '新的梗概');
    await user.click(screen.getByRole('button', { name: '保存并运行剧本' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    expect(writes(calls)[0]).toMatchObject({ method: 'PATCH', url: '/api/episodes/4', body: { source_text: '新的梗概' } });
    expect(writes(calls)[1]).toMatchObject({ method: 'POST', body: { stage: 'script', force: false } });
  });

  it('目标时长 5–3600 秒、不能为空，不合法时不能保存', async () => {
    installFetch({ ...ROUTES, 'GET /api/episodes/4': makeEpisode({ id: 4, source_text: '' }) });
    renderAt(<ScriptPage />, AT);
    const user = userEvent.setup();
    const secs = await screen.findByLabelText('目标时长（秒）');
    expect(secs.getAttribute('min')).toBe('5');
    expect(secs.getAttribute('max')).toBe('3600');
    await user.clear(secs);
    const src = screen.getByLabelText('原文 / 梗概').closest('form')!;
    expect((within(src).getByRole('button', { name: '保存' }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(src).getByText(/目标时长（秒）不能为空/)).toBeTruthy();
    await user.type(secs, '3');
    expect(within(src).getByText(/目标时长（秒）要在 5–3600 之间/)).toBeTruthy();
    expect((within(src).getByRole('button', { name: '保存' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '保存并运行剧本' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('剧本任务完成：提示下一步去角色页', async () => {
    installFetch({
      ...ROUTES,
      'POST /api/episodes/4/run': { job_ids: [9] },
      'GET /api/jobs/9': makeJob({ id: 9, type: 'script', status: 'done', result: {} }),
    });
    renderAt(<ScriptPage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '运行剧本' }));
    expect(await screen.findByText('剧本好了。下一步：去角色页抽定妆照')).toBeTruthy();
    expect(screen.getByRole('link', { name: '去角色页' }).getAttribute('href')).toBe('/projects/demo/episodes/4/cast');
  });

  it('分镜任务完成：提示下一步去导演台', async () => {
    installFetch({
      ...ROUTES,
      'POST /api/episodes/4/run': { job_ids: [9] },
      'GET /api/jobs/9': makeJob({ id: 9, type: 'storyboard', status: 'done', result: {} }),
    });
    renderAt(<ScriptPage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '运行分镜' }));
    expect(await screen.findByText('分镜好了。下一步：去导演台生成首帧')).toBeTruthy();
    expect(screen.getAllByRole('link', { name: '去导演台' }).some((a) => a.closest('.job-notice'))).toBe(true);
  });

  it('保存段落：提交文字和场景', async () => {
    const calls = installFetch({
      ...ROUTES,
      'PATCH /api/scene-blocks/21': { id: 21, order: 0, location_id: null, text: '新段落' },
    });
    renderAt(<ScriptPage />, AT);
    const user = userEvent.setup();
    const block = await screen.findByTestId('block-21');
    // 段落默认收起成一行摘要（编号 · 场景 · 正文开头），点开才编辑
    const row = within(block).getByRole('button', { name: /段落 1/ });
    expect(row.getAttribute('aria-expanded')).toBe('false');
    expect(within(block).getByLabelText('段落内容').closest('[hidden]')).toBeTruthy();
    await user.click(row);
    await within(block).findByRole('option', { name: '直播间' });
    const ta = within(block).getByLabelText('段落内容');
    await user.clear(ta);
    await user.type(ta, '新段落');
    await user.selectOptions(within(block).getByLabelText('场景'), '');
    await user.click(within(block).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]).toMatchObject({ url: '/api/scene-blocks/21', body: { text: '新段落', location_id: null } });
  });

  it('段落保存失败：错误显示在这一段里', async () => {
    installFetch({
      ...ROUTES,
      'PATCH /api/scene-blocks/21': new Response(JSON.stringify({ detail: '段落不能为空' }), { status: 400, headers: { 'Content-Type': 'application/json' } }),
    });
    renderAt(<ScriptPage />, AT);
    const user = userEvent.setup();
    const block = await screen.findByTestId('block-21');
    await user.click(within(block).getByRole('button', { name: /段落 1/ }));
    await user.type(within(block).getByLabelText('段落内容'), '！');
    await user.click(within(block).getByRole('button', { name: '保存' }));
    expect(await within(block).findByText(/段落不能为空/)).toBeTruthy();
  });

  it('收起段落 / 全部收起不丢正在编辑的内容', async () => {
    installFetch(ROUTES);
    renderAt(<ScriptPage />, AT);
    const user = userEvent.setup();
    const block = await screen.findByTestId('block-21');
    await user.click(within(block).getByRole('button', { name: /段落 1/ }));
    await user.type(within(block).getByLabelText('段落内容'), '（改）');
    await user.click(screen.getByRole('button', { name: '全部收起' }));
    expect(within(block).getByRole('button', { name: /段落 1/ }).getAttribute('aria-expanded')).toBe('false');
    await user.click(within(block).getByRole('button', { name: /段落 1/ }));
    expect((within(block).getByLabelText('段落内容') as HTMLTextAreaElement).value).toBe('直播间里，林夏念稿。（改）');
  });

  it('还没有段落时不能运行分镜', async () => {
    installFetch({ ...ROUTES, 'GET /api/episodes/4': makeEpisode({ id: 4, scene_blocks: [] }) });
    renderAt(<ScriptPage />, AT);
    expect(((await screen.findByRole('button', { name: '运行分镜' })) as HTMLButtonElement).disabled).toBe(true);
  });

  it('toCharacterPatch：清空音色提交 null', () => {
    expect(toCharacterPatch({ voice: '', name: '林夏' })).toEqual({ voice: null, name: '林夏' });
  });

  it('原文或段落改了没保存时换页面先问；留下就不走', async () => {
    installFetch(ROUTES);
    renderAt(<ScriptPage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /原文 \/ 梗概/ }));
    const src = screen.getByLabelText('原文 / 梗概');
    await user.type(src, '加一句');
    expect(screen.getAllByText(/有未保存的修改/).length).toBeGreaterThan(0);
    await user.click(screen.getByRole('link', { name: /本集角色/ }));
    const dlg = await screen.findByRole('dialog', { name: '有未保存的修改，确定离开？' });
    await user.click(within(dlg).getByRole('button', { name: '留下' }));
    expect(screen.getByTestId('location').textContent).toBe('/projects/demo/episodes/4/script');
    // 段落里的修改也算
    await user.clear(src);
    await user.type(src, '梗概');
    await user.click(screen.getByRole('button', { name: /段落 1/ }));
    await user.type(screen.getByLabelText('段落内容'), '！');
    await user.click(screen.getByRole('link', { name: /本集角色/ }));
    const dlg2 = await screen.findByRole('dialog', { name: '有未保存的修改，确定离开？' });
    await user.click(within(dlg2).getByRole('button', { name: '离开' }));
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/projects/demo/episodes/4/cast'));
  });
});
