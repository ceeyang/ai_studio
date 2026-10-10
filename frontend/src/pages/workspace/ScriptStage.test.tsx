import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { keys } from '../../api/queries';
import { installFetch } from '../../test/fetch';
import { makeEpisode, makeJob, makeShot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { ScriptStage, toCharacterPatch } from './ScriptStage';

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
const AT = { route: '/projects/demo/episodes/4?stage=script', path: '/projects/:slug/episodes/:episodeId' };
const IMPACT = {
  stage: 'storyboard', will_run: true, blocks: [3, 5], shots_deleted: 12, selected_first_frames: 10,
  selected_videos: 8, manual_kept: 2, overwrites: ['角色描述', '场景氛围'],
};

// 生成剧本没有可删的、可覆盖的：不弹确认，直接提交
const NO_IMPACT = { ...IMPACT, stage: 'script', blocks: [], shots_deleted: 0, selected_first_frames: 0, selected_videos: 0, manual_kept: 0, overwrites: [] };
const BAD_BLOCK = () =>
  new Response(JSON.stringify({ detail: '段落不能为空' }), { status: 400, headers: { 'Content-Type': 'application/json' } });

const writes = <T extends { method: string }>(calls: T[]) => calls.filter((c) => c.method !== 'GET');

describe('ScriptStage', () => {
  it('只管剧本：不带页头，没有角色 / 场景 / 风格页签和角色条（角色在左栏和角色阶段）', async () => {
    installFetch(ROUTES);
    renderAt(<ScriptStage />, AT);
    expect(await screen.findByRole('heading', { name: '剧本段落（1）' })).toBeTruthy();
    expect(screen.queryByRole('heading', { level: 2 })).toBeNull();
    expect(screen.queryByRole('navigation', { name: '面包屑' })).toBeNull();
    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.queryByTestId('character-8')).toBeNull();
    expect(screen.queryByRole('link', { name: /本集角色/ })).toBeNull();
  });

  it('还没有镜头时「生成分镜」：直接提交并跟踪任务；没有强制重跑勾选框', async () => {
    const calls = installFetch({ ...ROUTES, 'POST /api/episodes/4/run': { job_ids: [9] }, 'GET /api/jobs/9': makeJob({ id: 9, status: 'running' }) });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '生成分镜' }));
    expect((await screen.findByText(/生成分镜：已提交，进行中/)).textContent).toBeTruthy();
    expect(calls.some((c) => c.url.includes('rerun-impact'))).toBe(false);
    expect(writes(calls)[0]?.body).toEqual({ stage: 'storyboard', force: false });
    expect(screen.queryByLabelText(/强制重跑/)).toBeNull();
  });

  it('已有镜头：「重新生成分镜…」打开对话框，按强制查影响并原样列出，确认后强制提交并跟踪', async () => {
    const calls = installFetch({
      ...ROUTES,
      'GET /api/episodes/4': WITH_SHOTS,
      'GET /api/episodes/4/rerun-impact': IMPACT,
      'POST /api/episodes/4/run': { job_ids: [9] },
      'GET /api/jobs/9': makeJob({ id: 9, status: 'running' }),
    });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '重新生成分镜…' }));
    expect(screen.queryByRole('button', { name: '生成分镜' })).toBeNull();
    const dlg = await screen.findByRole('dialog', { name: '重新生成分镜' });
    expect(dlg.textContent).toContain('范围：整集');
    expect(
      await within(dlg).findByText('将重建段落 3、5 的镜头：删除 12 个镜头（其中 8 个有选定视频），保留 2 个手工镜头；会覆盖角色描述、场景氛围'),
    ).toBeTruthy();
    expect(calls.find((c) => c.url.includes('rerun-impact'))?.url).toBe('/api/episodes/4/rerun-impact?stage=storyboard&force=1');
    expect(writes(calls)).toHaveLength(0);
    await user.type(within(dlg).getByLabelText('修改意见（可选）'), '镜头更紧凑');
    await user.click(within(dlg).getByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0].body).toEqual({ stage: 'storyboard', force: true, note: '镜头更紧凑' });
    expect((await screen.findByText(/重新生成分镜：已提交，进行中/)).textContent).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('已有段落：「重新生成剧本…」打开剧本的对话框，取消就不提交', async () => {
    const calls = installFetch({ ...ROUTES, 'GET /api/episodes/4/rerun-impact': { ...NO_IMPACT, overwrites: ['角色外观'] } });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '重新生成剧本…' }));
    expect(screen.queryByRole('button', { name: '生成剧本' })).toBeNull();
    const dlg = await screen.findByRole('dialog', { name: '重新生成剧本' });
    expect(await within(dlg).findByText('会覆盖角色外观')).toBeTruthy();
    expect(calls.find((c) => c.url.includes('rerun-impact'))?.url).toBe('/api/episodes/4/rerun-impact?stage=script&force=1');
    await user.click(within(dlg).getByRole('button', { name: '取消' }));
    expect(writes(calls)).toHaveLength(0);
  });

  it('每一段都有「重写本段分镜…」（没镜头的段落就是生成这一段）：只重写这一段（scene_block_ids）', async () => {
    const ep = {
      ...EP,
      scene_blocks: [...EP.scene_blocks, { id: 22, order: 1, location_id: null, text: '第二段' }],
      shots: [makeShot({ id: 1, scene_block_id: 21 })],
    };
    const calls = installFetch({
      ...ROUTES,
      'GET /api/episodes/4': ep,
      'GET /api/episodes/4/rerun-impact': { ...IMPACT, blocks: [1], shots_deleted: 1, selected_videos: 0, selected_first_frames: 0, manual_kept: 0, overwrites: [] },
      'POST /api/episodes/4/run': { job_ids: [9] },
      'GET /api/jobs/9': makeJob({ id: 9, status: 'running' }),
    });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    const b1 = await screen.findByTestId('block-21');
    expect(within(screen.getByTestId('block-22')).getByRole('button', { name: '重写本段分镜…' })).toBeTruthy();
    await user.click(within(b1).getByRole('button', { name: '重写本段分镜…' }));
    const dlg = await screen.findByRole('dialog', { name: '重新生成分镜' });
    expect(dlg.textContent).toContain('范围：第 1 段');
    expect(await within(dlg).findByText('将重建段落 1 的镜头：删除 1 个镜头')).toBeTruthy();
    expect(calls.find((c) => c.url.includes('rerun-impact'))?.url).toBe('/api/episodes/4/rerun-impact?stage=storyboard&force=1&scene_block_ids=21');
    await user.click(within(dlg).getByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0].body).toEqual({ stage: 'storyboard', force: true, scene_block_ids: [21] });
  });

  it('还没有段落、原文有未保存修改：按钮变「保存并生成剧本」，先保存再运行', async () => {
    const calls = installFetch({
      ...ROUTES,
      'GET /api/episodes/4': makeEpisode({ id: 4, source_text: '' }),
      'PATCH /api/episodes/4': makeEpisode({ id: 4, source_text: '新的梗概' }),
      'GET /api/episodes/4/rerun-impact': NO_IMPACT,
      'POST /api/episodes/4/run': { job_ids: [9] },
      'GET /api/jobs/9': makeJob({ id: 9, status: 'running' }),
    });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText('原文 / 梗概'), '新的梗概');
    await user.click(screen.getByRole('button', { name: '保存并生成剧本' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    expect(writes(calls)[0]).toMatchObject({ method: 'PATCH', url: '/api/episodes/4', body: { source_text: '新的梗概' } });
    expect(writes(calls)[1]).toMatchObject({ method: 'POST', body: { stage: 'script', force: false } });
  });

  it('已有段落、原文有未保存修改：对话框提示会先保存，确认后先保存原文再强制重新生成', async () => {
    const calls = installFetch({
      ...ROUTES,
      'PATCH /api/episodes/4': { ...EP, source_text: '梗概改了' },
      'GET /api/episodes/4/rerun-impact': NO_IMPACT,
      'POST /api/episodes/4/run': { job_ids: [9] },
      'GET /api/jobs/9': makeJob({ id: 9, status: 'running' }),
    });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /原文 \/ 梗概/ }));
    await user.type(screen.getByLabelText('原文 / 梗概'), '改了');
    await user.click(screen.getByRole('button', { name: '重新生成剧本…' }));
    const dlg = await screen.findByRole('dialog', { name: '重新生成剧本' });
    expect(within(dlg).getByText('有未保存的修改，重新生成前会先保存')).toBeTruthy();
    await user.click(within(dlg).getByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    expect(writes(calls)[0]).toMatchObject({ method: 'PATCH', url: '/api/episodes/4', body: { source_text: '梗概改了' } });
    expect(writes(calls)[1]).toMatchObject({ method: 'POST', body: { stage: 'script', force: true } });
  });

  it('目标时长 5–3600 秒、不能为空，不合法时不能保存', async () => {
    installFetch({ ...ROUTES, 'GET /api/episodes/4': makeEpisode({ id: 4, source_text: '' }) });
    renderAt(<ScriptStage />, AT);
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
    expect((screen.getByRole('button', { name: '保存并生成剧本' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('剧本任务完成：提示下一步生成分镜（还在剧本阶段）', async () => {
    installFetch({
      ...ROUTES,
      'GET /api/episodes/4': makeEpisode({ id: 4, scene_blocks: [] }),
      'GET /api/episodes/4/rerun-impact': NO_IMPACT,
      'POST /api/episodes/4/run': { job_ids: [9] },
      'GET /api/jobs/9': makeJob({ id: 9, type: 'script', status: 'done', result: {} }),
    });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '生成剧本' }));
    expect(await screen.findByText('剧本好了。下一步：生成分镜')).toBeTruthy();
  });

  it('分镜任务完成：提示下一步去角色阶段确认定妆照', async () => {
    installFetch({
      ...ROUTES,
      'POST /api/episodes/4/run': { job_ids: [9] },
      'GET /api/jobs/9': makeJob({ id: 9, type: 'storyboard', status: 'done', result: {} }),
    });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '生成分镜' }));
    expect(await screen.findByText('分镜好了。下一步：确认角色定妆照，再生成首帧')).toBeTruthy();
    expect(screen.getByRole('link', { name: '去角色' }).getAttribute('href')).toBe('/projects/demo/episodes/4?stage=cast');
  });

  it('重新生成分镜完成：也提示下一步', async () => {
    installFetch({
      ...ROUTES,
      'GET /api/episodes/4': WITH_SHOTS,
      'GET /api/episodes/4/rerun-impact': IMPACT,
      'POST /api/episodes/4/run': { job_ids: [9] },
      'GET /api/jobs/9': makeJob({ id: 9, type: 'storyboard', status: 'done', result: {} }),
    });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '重新生成分镜…' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '重新生成' }));
    expect(await screen.findByText('分镜好了。下一步：确认角色定妆照，再生成首帧')).toBeTruthy();
  });

  it('保存段落：提交文字和场景', async () => {
    const calls = installFetch({
      ...ROUTES,
      'PATCH /api/scene-blocks/21': { id: 21, order: 0, location_id: null, text: '新段落' },
    });
    renderAt(<ScriptStage />, AT);
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
    installFetch({ ...ROUTES, 'PATCH /api/scene-blocks/21': BAD_BLOCK() });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    const block = await screen.findByTestId('block-21');
    await user.click(within(block).getByRole('button', { name: /段落 1/ }));
    await user.type(within(block).getByLabelText('段落内容'), '！');
    await user.click(within(block).getByRole('button', { name: '保存' }));
    expect(await within(block).findByText(/段落不能为空/)).toBeTruthy();
  });

  it('收起段落 / 全部收起不丢正在编辑的内容', async () => {
    installFetch(ROUTES);
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    const block = await screen.findByTestId('block-21');
    await user.click(within(block).getByRole('button', { name: /段落 1/ }));
    await user.type(within(block).getByLabelText('段落内容'), '（改）');
    await user.click(screen.getByRole('button', { name: '全部收起' }));
    expect(within(block).getByRole('button', { name: /段落 1/ }).getAttribute('aria-expanded')).toBe('false');
    await user.click(within(block).getByRole('button', { name: /段落 1/ }));
    expect((within(block).getByLabelText('段落内容') as HTMLTextAreaElement).value).toBe('直播间里，林夏念稿。（改）');
  });

  it('还没有段落但项目已有角色时「生成剧本」：先查影响（不强制），会覆盖的原样列出，取消就不提交', async () => {
    const calls = installFetch({
      ...ROUTES,
      'GET /api/episodes/4': makeEpisode({ id: 4, scene_blocks: [] }),
      'GET /api/episodes/4/rerun-impact': { ...NO_IMPACT, overwrites: ['角色外观', '场景外观', '风格设定'] },
    });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '生成剧本' }));
    const dlg = await screen.findByRole('dialog');
    expect(dlg.textContent).toContain('会覆盖角色外观、场景外观、风格设定');
    expect(calls.find((c) => c.url.includes('rerun-impact'))?.url).toBe('/api/episodes/4/rerun-impact?stage=script&force=0');
    await user.click(within(dlg).getByRole('button', { name: '取消' }));
    expect(writes(calls)).toHaveLength(0);
  });

  it('全新的集（没段落、项目里没角色 / 场景 / 风格）生成剧本：不查影响直接提交', async () => {
    const calls = installFetch({
      ...ROUTES,
      'GET /api/episodes/4': makeEpisode({ id: 4, scene_blocks: [] }),
      'GET /api/projects/demo/characters': [],
      'GET /api/projects/demo/locations': [],
      'POST /api/episodes/4/run': { job_ids: [9] },
      'GET /api/jobs/9': makeJob({ id: 9, status: 'running' }),
    });
    const { qc } = renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    // 等项目的角色、场景、风格都读到（是空的）
    await waitFor(() => expect(qc.getQueryData(keys.characters('demo'))).toEqual([]));
    await waitFor(() => expect(qc.getQueryData(keys.locations('demo'))).toEqual([]));
    await waitFor(() => expect(qc.getQueryState(keys.style('demo'))?.status).toBe('success'));
    await user.click(screen.getByRole('button', { name: '生成剧本' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(calls.some((c) => c.url.includes('rerun-impact'))).toBe(false);
  });

  it('还没有镜头、段落有未保存修改：按钮变「保存并生成分镜」，先保存段落再运行', async () => {
    const calls = installFetch({
      ...ROUTES,
      'PATCH /api/scene-blocks/21': { id: 21, order: 0, location_id: 3, text: '直播间里，林夏念稿。！' },
      'POST /api/episodes/4/run': { job_ids: [9] },
      'GET /api/jobs/9': makeJob({ id: 9, status: 'running' }),
    });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /段落 1/ }));
    await user.type(screen.getByLabelText('段落内容'), '！');
    await user.click(screen.getByRole('button', { name: '保存并生成分镜' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    expect(writes(calls)[0]).toMatchObject({ method: 'PATCH', url: '/api/scene-blocks/21', body: { text: '直播间里，林夏念稿。！', location_id: 3 } });
    expect(writes(calls)[1]).toMatchObject({ method: 'POST', body: { stage: 'storyboard', force: false } });
  });

  it('保存并生成分镜时段落保存失败：报错，不提交分镜', async () => {
    const calls = installFetch({ ...ROUTES, 'PATCH /api/scene-blocks/21': BAD_BLOCK() });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /段落 1/ }));
    await user.type(screen.getByLabelText('段落内容'), '！');
    await user.click(screen.getByRole('button', { name: '保存并生成分镜' }));
    expect((await screen.findAllByText(/段落不能为空/)).length).toBeGreaterThan(0);
    expect(writes(calls).filter((c) => c.method === 'POST')).toHaveLength(0);
  });

  it('已有镜头、段落有未保存修改：重新生成分镜对话框提示会先保存；保存失败就不提交', async () => {
    const calls = installFetch({
      ...ROUTES,
      'GET /api/episodes/4': WITH_SHOTS,
      'GET /api/episodes/4/rerun-impact': IMPACT,
      'PATCH /api/scene-blocks/21': BAD_BLOCK(),
    });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /段落 1/ }));
    await user.type(screen.getByLabelText('段落内容'), '！');
    await user.click(screen.getByRole('button', { name: '重新生成分镜…' }));
    const dlg = await screen.findByRole('dialog', { name: '重新生成分镜' });
    expect(within(dlg).getByText('有未保存的修改，重新生成前会先保存')).toBeTruthy();
    await user.click(within(dlg).getByRole('button', { name: '重新生成' }));
    expect(await within(dlg).findByText('保存没成功，没有重新生成')).toBeTruthy();
    expect(writes(calls).filter((c) => c.method === 'POST')).toHaveLength(0);
  });

  it('已有镜头、段落有未保存修改：确认后先保存段落再强制重新生成分镜', async () => {
    const calls = installFetch({
      ...ROUTES,
      'GET /api/episodes/4': WITH_SHOTS,
      'GET /api/episodes/4/rerun-impact': IMPACT,
      'PATCH /api/scene-blocks/21': { id: 21, order: 0, location_id: 3, text: '直播间里，林夏念稿。！' },
      'POST /api/episodes/4/run': { job_ids: [9] },
      'GET /api/jobs/9': makeJob({ id: 9, status: 'running' }),
    });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /段落 1/ }));
    await user.type(screen.getByLabelText('段落内容'), '！');
    await user.click(screen.getByRole('button', { name: '重新生成分镜…' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    expect(writes(calls)[0]).toMatchObject({ method: 'PATCH', url: '/api/scene-blocks/21' });
    expect(writes(calls)[1]).toMatchObject({ method: 'POST', body: { stage: 'storyboard', force: true } });
  });

  it('段落有未保存修改时重新生成剧本：对话框说明这些修改会丢掉，取消就不提交', async () => {
    const calls = installFetch({ ...ROUTES, 'GET /api/episodes/4/rerun-impact': NO_IMPACT });
    renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /段落 1/ }));
    await user.type(screen.getByLabelText('段落内容'), '！');
    await user.click(screen.getByRole('button', { name: '重新生成剧本…' }));
    const dlg = await screen.findByRole('dialog', { name: '重新生成剧本' });
    expect(dlg.textContent).toContain('有 1 个段落改了还没保存，重新拆段落时这些修改会丢掉');
    await user.click(within(dlg).getByRole('button', { name: '取消' }));
    expect(writes(calls)).toHaveLength(0);
  });

  it('还没有段落时不能生成分镜', async () => {
    installFetch({ ...ROUTES, 'GET /api/episodes/4': makeEpisode({ id: 4, scene_blocks: [] }) });
    renderAt(<ScriptStage />, AT);
    expect(((await screen.findByRole('button', { name: '生成分镜' })) as HTMLButtonElement).disabled).toBe(true);
  });

  it('toCharacterPatch：清空音色提交 null', () => {
    expect(toCharacterPatch({ voice: '', name: '林夏' })).toEqual({ voice: null, name: '林夏' });
  });

  it('原文或段落改了没保存时换页面先问；留下就不走', async () => {
    installFetch(ROUTES);
    const { router } = renderAt(<ScriptStage />, AT);
    const leave = () => act(() => void router.navigate('/jobs'));
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /原文 \/ 梗概/ }));
    const src = screen.getByLabelText('原文 / 梗概');
    await user.type(src, '加一句');
    expect(screen.getAllByText(/有未保存的修改/).length).toBeGreaterThan(0);
    await leave();
    const dlg = await screen.findByRole('dialog', { name: '有未保存的修改，确定离开？' });
    await user.click(within(dlg).getByRole('button', { name: '留下' }));
    expect(screen.getByTestId('location').textContent).toBe('/projects/demo/episodes/4?stage=script');
    // 段落里的修改也算
    await user.clear(src);
    await user.type(src, '梗概');
    await user.click(screen.getByRole('button', { name: /段落 1/ }));
    await user.type(screen.getByLabelText('段落内容'), '！');
    await leave();
    const dlg2 = await screen.findByRole('dialog', { name: '有未保存的修改，确定离开？' });
    await user.click(within(dlg2).getByRole('button', { name: '离开' }));
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/jobs'));
  });

  it('改过没保存的段落被重跑剧本删掉后，换页面不再误拦', async () => {
    let ep = EP;
    installFetch({ ...ROUTES, 'GET /api/episodes/4': () => ep });
    const { qc, router } = renderAt(<ScriptStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /段落 1/ }));
    await user.type(screen.getByLabelText('段落内容'), '！');
    ep = { ...EP, scene_blocks: [{ id: 22, order: 0, location_id: null, text: '新的段落' }] };
    await qc.invalidateQueries({ queryKey: ['episode', 4] });
    await screen.findByTestId('block-22');
    await act(() => void router.navigate('/jobs'));
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/jobs'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('下一步是生成剧本 / 生成分镜时：对应按钮打上 data-next-action，但不是主按钮（主按钮在阶段条）', async () => {
    installFetch({ ...ROUTES, 'GET /api/episodes/4': { ...EP, scene_blocks: [] } });
    const { container, unmount } = renderAt(<ScriptStage />, AT);
    const run = await screen.findByRole('button', { name: '生成剧本' });
    await waitFor(() => expect(run.hasAttribute('data-next-action')).toBe(true));
    expect(container.querySelectorAll('.primary')).toHaveLength(0);
    unmount();
    installFetch(ROUTES);
    renderAt(<ScriptStage />, AT);
    const sb = await screen.findByRole('button', { name: '生成分镜' });
    await waitFor(() => expect(sb.hasAttribute('data-next-action')).toBe(true));
    expect(screen.getByRole('button', { name: '重新生成剧本…' }).hasAttribute('data-next-action')).toBe(false);
  });

  it('重新生成过剧本、旧镜头没了段落：下一步「重新生成分镜（剧本改过）」落在「重新生成分镜…」上', async () => {
    const ep = { ...EP, shots: [makeShot({ id: 1, scene_block_id: 21, origin: 'llm' }), makeShot({ id: 2, code: 'S02', scene_block_id: null, origin: 'llm' })] };
    installFetch({ ...ROUTES, 'GET /api/episodes/4': ep, 'GET /api/episodes/4/exports': [] });
    renderAt(<ScriptStage />, AT);
    const btn = await screen.findByRole('button', { name: '重新生成分镜…' });
    await waitFor(() => expect(btn.hasAttribute('data-next-action')).toBe(true));
    expect(screen.getByRole('button', { name: '重新生成剧本…' }).hasAttribute('data-next-action')).toBe(false);
  });

  it('已有段落和镜头时：工具栏没有主按钮（重新生成不是下一步）', async () => {
    installFetch({ ...ROUTES, 'GET /api/episodes/4': WITH_SHOTS });
    const { container } = renderAt(<ScriptStage />, AT);
    await screen.findByRole('button', { name: '重新生成分镜…' });
    expect(container.querySelectorAll('.toolbar .primary')).toHaveLength(0);
  });
});
