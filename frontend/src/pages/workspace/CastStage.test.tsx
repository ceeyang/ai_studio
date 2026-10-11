import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Character, TurnaroundCell } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeEpisode, makeJob, makeShot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { CastStage } from './CastStage';

const LIN: Character = {
  id: 8, project_id: 1, name: '林夏', description: '女主播', visual_prompt: 'young woman', core_features: '', voice: null,
  portrait_candidate_id: 80, portrait_url: '/media/p/lin.png',
};
const LAO: Character = { ...LIN, id: 9, name: '老周', description: '', voice: 'zh-CN-YunjianNeural', portrait_candidate_id: null, portrait_url: null };
const EP = makeEpisode({
  id: 4,
  title: '提词器',
  scene_blocks: [{ id: 1, order: 0, location_id: null, text: 'a' }],
  shots: [makeShot({ id: 1, scene_block_id: 1, cast: [{ character_id: 8, variation_id: null }, { character_id: 9, variation_id: null }] })],
});
const ROUTES = {
  'GET /api/episodes/4': EP,
  'GET /api/projects/demo/locations': [
    { id: 3, project_id: 1, name: '直播间', time_of_day: '夜', atmosphere: '压抑', visual_prompt: 'a dim studio' },
  ],
  'GET /api/projects/demo/characters': [LIN, LAO],
  'GET /api/projects/demo/style': null,
};
const AT = { route: '/projects/demo/episodes/4?stage=cast', path: '/projects/:slug/episodes/:episodeId' };
const writes = <T extends { method: string }>(calls: T[]) => calls.filter((c) => c.method !== 'GET');
const where = () => screen.getByTestId('location').textContent;

function cells(withUrl: number): TurnaroundCell[] {
  return Array.from({ length: 7 }, (_, i) => ({
    view: 'front_full' as const, label: `格 ${i}`, sheet: 'body' as const,
    selected_candidate_id: i < withUrl ? i + 1 : null, url: i < withUrl ? `/media/c${i}.png` : null, stale: false,
  }));
}

describe('CastStage', () => {
  it('角色卡网格：当前定妆照、名字、音色、状态；页面上没有表单，也没有主按钮', async () => {
    installFetch({
      ...ROUTES,
      'GET /api/projects/demo/characters': [
        { ...LIN, turnaround: { look: '', default_engine: 'h3_image', sheets: [], cells: cells(3) } },
        { ...LAO, active_jobs: [{ id: 5, type: 'image.portrait', status: 'running' }] },
      ],
    });
    const { container } = renderAt(<CastStage />, AT);
    const lin = await screen.findByTestId('character-8');
    expect(screen.queryByRole('heading', { level: 2 })).toBeNull();
    expect((within(lin).getByRole('img', { name: '林夏 定妆照' }) as HTMLImageElement).getAttribute('src')).toBe('/media/p/lin.png');
    expect(lin.textContent).toContain('林夏');
    expect(lin.textContent).toContain('未设音色');
    expect(lin.textContent).toContain('已定妆');
    expect(lin.textContent).toContain('三视图 3/7');
    expect(within(lin).getAllByRole('button').filter((b) => !b.classList.contains('cast-open')).map((b) => b.textContent)).toEqual(['编辑']);
    const lao = screen.getByTestId('character-9');
    expect(lao.textContent).toContain('普通话 · 男声 云健（沉稳）');
    expect(lao.textContent).toContain('缺定妆照');
    expect(lao.textContent).toContain('生成中');
    // 资料表单都收在抽屉里
    expect(screen.queryByLabelText('人物描述')).toBeNull();
    expect(container.querySelectorAll('.primary')).toHaveLength(0);
    expect(screen.getByText('1/2 个角色有定妆照')).toBeTruthy();
  });

  it('只出声、不出镜的角色：卡上标「只出声」、不提示缺定妆照、没有「抽定妆照」，也不算进定妆照计数', async () => {
    installFetch({
      ...ROUTES,
      'GET /api/projects/demo/characters': [LIN, { ...LAO, voice_only: true }],
    });
    renderAt(<CastStage />, AT);
    const lao = await screen.findByTestId('character-9');
    expect(lao.textContent).toContain('只出声');
    expect(lao.textContent).not.toContain('缺定妆照');
    expect(lao.classList.contains('is-missing')).toBe(false);
    expect(within(lao).queryByRole('button', { name: '抽定妆照' })).toBeNull();
    expect(within(lao).getByRole('button', { name: '编辑 老周' })).toBeTruthy();
    expect(screen.getByText('1/1 个角色有定妆照')).toBeTruthy();
  });

  it('最近一次抽卡失败：卡片标「抽卡失败」', async () => {
    installFetch({
      ...ROUTES,
      'GET /api/projects/demo/characters': [
        LIN,
        { ...LAO, last_failure: { id: 77, type: 'image.portrait', status: 'failed', error: 'ComfyUI 没开' } },
      ],
    });
    renderAt(<CastStage />, AT);
    expect((await screen.findByTestId('character-9')).textContent).toContain('抽卡失败');
  });

  it('缺定妆照的卡：「抽定妆照」按默认 2 张、项目默认引擎提交；它是阶段条下一步要触发的那个', async () => {
    const calls = installFetch({ ...ROUTES, 'POST /api/characters/9/portraits/draw': { job_ids: [70, 71] } });
    const { container } = renderAt(<CastStage />, AT);
    const user = userEvent.setup();
    const lao = await screen.findByTestId('character-9');
    const draw = within(lao).getByRole('button', { name: '抽定妆照' });
    expect(draw.className).toContain('soft');
    await waitFor(() => expect(draw.hasAttribute('data-next-action')).toBe(true));
    expect(container.querySelectorAll('[data-next-action]')).toHaveLength(1);
    await user.click(draw);
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]).toMatchObject({ url: '/api/characters/9/portraits/draw', body: { count: 2 } });
    // 抽卡不打开抽屉
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('缺定妆照又没有外观提示词：卡上是「补外观提示词」（下一步的目标），点了打开抽屉的资料页签、展开高级并聚焦提示词', async () => {
    installFetch({ ...ROUTES, 'GET /api/projects/demo/characters': [LIN, { ...LAO, visual_prompt: '' }] });
    const { container } = renderAt(<CastStage />, AT);
    const user = userEvent.setup();
    const lao = await screen.findByTestId('character-9');
    expect(within(lao).queryByRole('button', { name: '抽定妆照' })).toBeNull();
    const fill = within(lao).getByRole('button', { name: '补外观提示词' });
    await waitFor(() => expect(fill.hasAttribute('data-next-action')).toBe(true));
    expect(container.querySelectorAll('[data-next-action]')).toHaveLength(1);
    await user.click(fill);
    const d = await screen.findByRole('dialog', { name: '角色 老周' });
    expect(within(d).getByRole('tab', { name: '资料' }).getAttribute('aria-selected')).toBe('true');
    const vp = within(d).getByLabelText(/外观提示词/);
    expect((vp.closest('details') as HTMLDetailsElement).open).toBe(true);
    await waitFor(() => expect(document.activeElement).toBe(vp));
  });

  it('前一个缺定妆照的角色已经在抽卡：下一步指向下一个能抽的角色', async () => {
    const XIAO: Character = { ...LAO, id: 10, name: '小李' };
    installFetch({
      ...ROUTES,
      'GET /api/episodes/4': makeEpisode({
        ...EP,
        shots: [makeShot({ id: 1, scene_block_id: 1, cast: [8, 9, 10].map((character_id) => ({ character_id, variation_id: null })) })],
      }),
      'GET /api/projects/demo/characters': [LIN, { ...LAO, active_jobs: [{ id: 5, type: 'image.portrait', status: 'running' }] }, XIAO],
    });
    const { container } = renderAt(<CastStage />, AT);
    const xiao = await screen.findByTestId('character-10');
    await waitFor(() => expect(within(xiao).getByRole('button', { name: '抽定妆照' }).hasAttribute('data-next-action')).toBe(true));
    expect(container.querySelectorAll('[data-next-action]')).toHaveLength(1);
    expect((within(screen.getByTestId('character-9')).getByRole('button', { name: '抽定妆照' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('抽屉有没保存的修改时地址换成别的角色（左栏 / 提示 / 后退）：先问，继续编辑就留在原角色', async () => {
    installFetch(ROUTES);
    const { router } = renderAt(<CastStage />, { ...AT, route: '/projects/demo/episodes/4?stage=cast&char=8' });
    const user = userEvent.setup();
    const d = await screen.findByRole('dialog', { name: '角色 林夏' });
    await user.click(within(d).getByRole('tab', { name: '资料' }));
    await user.type(within(d).getByLabelText('人物描述'), '（改）');
    await act(() => void router.navigate('/projects/demo/episodes/4?stage=cast&char=9'));
    const ask = await screen.findByRole('dialog', { name: '放弃没保存的修改？' });
    await user.click(within(ask).getByRole('button', { name: '继续编辑' }));
    await waitFor(() => expect(where()).toBe('/projects/demo/episodes/4?stage=cast&char=8'));
    expect((within(screen.getByRole('dialog', { name: '角色 林夏' })).getByLabelText('人物描述') as HTMLTextAreaElement).value).toBe('女主播（改）');
    // 去掉 ?char= 同样要问；放弃修改才换
    await act(() => void router.navigate('/projects/demo/episodes/4?stage=cast'));
    await user.click(within(await screen.findByRole('dialog', { name: '放弃没保存的修改？' })).getByRole('button', { name: '继续编辑' }));
    await waitFor(() => expect(where()).toBe('/projects/demo/episodes/4?stage=cast&char=8'));
    await act(() => void router.navigate('/projects/demo/episodes/4?stage=cast&char=9'));
    await user.click(within(await screen.findByRole('dialog', { name: '放弃没保存的修改？' })).getByRole('button', { name: '放弃修改' }));
    expect(await screen.findByRole('dialog', { name: '角色 老周' })).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: '角色 林夏' })).toBeNull();
    expect(where()).toBe('/projects/demo/episodes/4?stage=cast&char=9');
  });

  it('抽屉没有修改时地址换角色：直接换', async () => {
    installFetch(ROUTES);
    const { router } = renderAt(<CastStage />, { ...AT, route: '/projects/demo/episodes/4?stage=cast&char=8' });
    await screen.findByRole('dialog', { name: '角色 林夏' });
    await act(() => void router.navigate('/projects/demo/episodes/4?stage=cast&char=9'));
    expect(await screen.findByRole('dialog', { name: '角色 老周' })).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: '放弃没保存的修改？' })).toBeNull();
  });

  it('还没有角色：「按剧本推荐角色」是下一步要触发的那个（次要按钮）', async () => {
    installFetch({ ...ROUTES, 'GET /api/projects/demo/characters': [] });
    const { container } = renderAt(<CastStage />, AT);
    const rec = await screen.findByRole('button', { name: '按剧本推荐角色' });
    expect(rec.className).toContain('soft');
    await waitFor(() => expect(rec.hasAttribute('data-next-action')).toBe(true));
    expect(container.querySelectorAll('.primary')).toHaveLength(0);
  });

  it('点卡片或「编辑」打开角色抽屉（?char=，不记历史）；关闭去掉 ?char=', async () => {
    installFetch(ROUTES);
    renderAt(<CastStage />, AT);
    const user = userEvent.setup();
    const lin = await screen.findByTestId('character-8');
    await user.click(within(lin).getByRole('button', { name: '编辑 林夏' }));
    const drawer = await screen.findByRole('dialog', { name: '角色 林夏' });
    expect(where()).toBe('/projects/demo/episodes/4?stage=cast&char=8');
    await user.click(within(drawer).getByRole('button', { name: '关闭' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(where()).toBe('/projects/demo/episodes/4?stage=cast');
    await user.click(within(screen.getByTestId('character-9')).getByRole('button', { name: /^打开 老周/ }));
    expect(await screen.findByRole('dialog', { name: '角色 老周' })).toBeTruthy();
    expect(where()).toBe('/projects/demo/episodes/4?stage=cast&char=9');
  });

  it('地址里带 ?char=：直接打开那个角色的抽屉；阶段没在显示时不打开', async () => {
    installFetch(ROUTES);
    const { unmount } = renderAt(<CastStage />, { ...AT, route: '/projects/demo/episodes/4?stage=cast&char=9' });
    expect(await screen.findByRole('dialog', { name: '角色 老周' })).toBeTruthy();
    unmount();
    installFetch(ROUTES);
    renderAt(<CastStage active={false} />, { ...AT, route: '/projects/demo/episodes/4?stage=cast&char=9' });
    await screen.findByTestId('character-9');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('「设定」默认收起；展开后场景卡可编辑', async () => {
    const calls = installFetch({ ...ROUTES, 'PATCH /api/locations/3': ROUTES['GET /api/projects/demo/locations'][0] });
    renderAt(<CastStage />, AT);
    const user = userEvent.setup();
    const fold = await screen.findByRole('button', { name: /^设定/ });
    expect(fold.getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByTestId('location-3').closest('[hidden]')).toBeTruthy();
    await user.click(fold);
    expect(fold.getAttribute('aria-expanded')).toBe('true');
    const loc = screen.getByTestId('location-3');
    expect(loc.closest('[hidden]')).toBeNull();
    expect(screen.getByLabelText(/风格锚/).closest('[hidden]')).toBeNull();
    await user.type(within(loc).getByLabelText('氛围'), '，安静');
    await user.click(within(loc).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]).toMatchObject({ url: '/api/locations/3', body: { atmosphere: '压抑，安静' } });
  });

  it('收起「设定」不丢正在编辑的内容；场景或风格改了没保存时换页面先问', async () => {
    installFetch(ROUTES);
    const { router } = renderAt(<CastStage />, AT);
    const user = userEvent.setup();
    const leave = () => act(() => void router.navigate('/jobs'));
    const fold = await screen.findByRole('button', { name: /^设定/ });
    await user.click(fold);
    await user.type(screen.getByLabelText('色板'), '冷色');
    await user.click(fold);
    await user.click(fold);
    expect((screen.getByLabelText('色板') as HTMLInputElement).value).toBe('冷色');
    await leave();
    const dlg = await screen.findByRole('dialog', { name: '有未保存的修改，确定离开？' });
    await user.click(within(dlg).getByRole('button', { name: '留下' }));
    expect(where()).toBe('/projects/demo/episodes/4?stage=cast');
    await user.clear(screen.getByLabelText('色板'));
    await user.type(within(screen.getByTestId('location-3')).getByLabelText('时间'), '晚');
    await leave();
    expect(await screen.findByRole('dialog', { name: '有未保存的修改，确定离开？' })).toBeTruthy();
  });
  it('按剧本推荐角色：任务完成后列出没建的角色，一键添加', async () => {
    const result = {
      characters: [
        { name: '林夏', description: '', visual_prompt: 'x', core_features: '' },
        { name: '前台小李', description: '修车厂前台', visual_prompt: 'young clerk', core_features: '工牌' },
      ],
    };
    const calls = installFetch({
      ...ROUTES,
      'POST /api/episodes/4/characters/recommend': { job_ids: [90] },
      'GET /api/jobs/90': makeJob({ id: 90, type: 'character.recommend', status: 'done', result }),
      'POST /api/projects/demo/characters': { ...LIN, id: 10, name: '前台小李' },
    });
    renderAt(<CastStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '按剧本推荐角色' }));
    const rec = await screen.findByTestId('recommend-前台小李');
    expect(screen.queryByTestId('recommend-林夏')).toBeNull(); // 已有的不再列
    // 任务号记在地址里：刷新或离开再回来还能看到结果
    expect(where()).toBe('/projects/demo/episodes/4?stage=cast&recommend=90');
    await user.click(within(rec).getByRole('button', { name: '添加角色' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    expect(writes(calls)[0]).toMatchObject({ url: '/api/episodes/4/characters/recommend', body: { count: 3 } });
    expect(writes(calls)[1]).toMatchObject({
      url: '/api/projects/demo/characters',
      body: { name: '前台小李', description: '修车厂前台', visual_prompt: 'young clerk', core_features: '工牌' },
    });
  });

  it('地址里带 ?recommend=<任务号>：直接显示那次推荐的结果', async () => {
    installFetch({
      ...ROUTES,
      'GET /api/jobs/90': makeJob({
        id: 90, type: 'character.recommend', status: 'done',
        result: { characters: [{ name: '前台小李', description: '', visual_prompt: 'young clerk', core_features: '' }] },
      }),
    });
    renderAt(<CastStage />, { ...AT, route: '/projects/demo/episodes/4?stage=cast&recommend=90' });
    expect(await screen.findByTestId('recommend-前台小李')).toBeTruthy();
  });

  it('定妆照齐了、本集场景缺场景图：「生成场景图（N 个）」是下一步要触发的那个，点了给每个缺的场景出一张', async () => {
    const ep = makeEpisode({
      id: 4,
      scene_blocks: [{ id: 1, order: 0, location_id: 3, text: 'a' }],
      shots: [makeShot({ id: 1, scene_block_id: 1, image_max_refs: 9, cast: [{ character_id: 8, variation_id: null }] })],
    });
    const calls = installFetch({
      ...ROUTES,
      'GET /api/episodes/4': ep,
      'GET /api/projects/demo/characters': [LIN],
      'POST /api/locations/3/images/draw': { job_ids: [91] },
    });
    renderAt(<CastStage />, { ...AT, route: '/projects/demo/episodes/4?stage=cast' });
    const user = userEvent.setup();
    const btn = await screen.findByRole('button', { name: '生成场景图（1 个）' });
    await waitFor(() => expect(btn.hasAttribute('data-next-action')).toBe(true));
    await user.click(btn);
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]).toMatchObject({ method: 'POST', url: '/api/locations/3/images/draw', body: { count: 1 } });
    // 设定展开，看得到生成进度
    expect(screen.getByRole('button', { name: /设定/ }).getAttribute('aria-expanded')).toBe('true');
  });

  it('首帧引擎不吃参考图（image_max_refs 0）：不给生成场景图按钮', async () => {
    const ep = makeEpisode({
      id: 4,
      scene_blocks: [{ id: 1, order: 0, location_id: 3, text: 'a' }],
      shots: [makeShot({ id: 1, scene_block_id: 1, image_max_refs: 0, cast: [{ character_id: 8, variation_id: null }] })],
    });
    installFetch({ ...ROUTES, 'GET /api/episodes/4': ep, 'GET /api/projects/demo/characters': [LIN] });
    renderAt(<CastStage />, AT);
    await screen.findByTestId('character-8');
    expect(screen.queryByRole('button', { name: /生成场景图/ })).toBeNull();
  });
});
