import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Character } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeCandidate, makeEpisode, makeJob, makeShot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { EventsContext } from '../../ws/EventsContext';
import { initialEvents } from '../../ws/events';
import { CastPage } from '../CastPage';

const UPLOAD = makeCandidate({ id: 19, engine: 'upload', url: '/media/p/up.png', params: { source: 'chen.png' } });
const DRAWN = makeCandidate({ id: 46, engine: 'h3_image', url: '/media/p/ai.png', params: { visual_prompt: 'smiling mechanic' } });
const CHEN: Character = {
  id: 6, project_id: 1, name: '陈师傅', description: '汽修师傅', visual_prompt: 'lean mechanic', core_features: '',
  voice: null, portrait_candidate_id: 19, portrait_url: '/media/p/up.png', portraits: [UPLOAD, DRAWN],
  design_options: [], active_jobs: [],
  turnaround: { look: '', default_engine: 'h3_image', sheets: [], cells: [] },
};
const ROUTES = {
  'GET /api/episodes/5': makeEpisode({
    id: 5,
    scene_blocks: [{ id: 1, order: 0, location_id: null, text: '修车厂' }],
    shots: [
      makeShot({ id: 1, cast: [{ character_id: 6, variation_id: null }] }),
      makeShot({ id: 2, code: 'S02', cast: [{ character_id: 6, variation_id: null }, { character_id: 7, variation_id: null }] }),
      makeShot({ id: 3, code: 'S03', cast: [{ character_id: 7, variation_id: null }] }),
    ],
  }),
  'GET /api/projects/demo/locations': [],
  'GET /api/projects/demo/style': null,
};
const AT = { route: '/projects/demo/episodes/5/cast', path: '/projects/:slug/episodes/:episodeId/cast' };

function setup(chen: Character, extra: Record<string, unknown> = {}) {
  const calls = installFetch({ ...ROUTES, 'GET /api/projects/demo/characters': [chen], ...extra });
  renderAt(<CastPage />, AT);
  return { calls, user: userEvent.setup() };
}

const posts = (calls: { method: string; url: string; body: unknown }[]) => calls.filter((c) => c.method !== 'GET');

describe('角色卡', () => {
  it('单击定妆照只打开预览，不换造型；预览里 Esc 关、左右键切换、标出当前', async () => {
    const { calls, user } = setup(CHEN);
    const card = await screen.findByTestId('character-6');
    const current = within(card).getByRole('button', { name: '定妆照 #19（当前）' });
    expect(current.closest('.pcard')!.className).toContain('is-selected');
    await user.click(within(card).getByRole('button', { name: '定妆照 #46' }));
    const box = await screen.findByRole('dialog', { name: /陈师傅 定妆照/ });
    expect((within(box).getByRole('img') as HTMLImageElement).getAttribute('src')).toBe('/media/p/ai.png');
    expect(within(box).queryByText('当前')).toBeNull();
    expect(posts(calls)).toHaveLength(0);
    await user.keyboard('{ArrowLeft}');
    expect((within(box).getByRole('img') as HTMLImageElement).getAttribute('src')).toBe('/media/p/up.png');
    expect(within(box).getByText('当前')).toBeTruthy();
    expect(within(box).queryByRole('button', { name: '设为当前造型' })).toBeNull();
    await user.keyboard('{ArrowRight}');
    expect((within(box).getByRole('img') as HTMLImageElement).getAttribute('src')).toBe('/media/p/ai.png');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(posts(calls)).toHaveLength(0);
  });

  it('设为当前造型：先确认（写明覆盖外貌描述和过期的镜头数），确认后才切换', async () => {
    const { calls, user } = setup(CHEN, { 'POST /api/characters/6/portraits/46/use': { ...CHEN, portrait_candidate_id: 46 } });
    const card = await screen.findByTestId('character-6');
    await user.click(within(card).getByRole('button', { name: '定妆照 #46' }));
    const box = await screen.findByRole('dialog', { name: /陈师傅 定妆照/ });
    await user.click(within(box).getByRole('button', { name: '设为当前造型' }));
    const ask = await screen.findByRole('dialog', { name: '设为当前造型？' });
    expect(ask.textContent).toContain('用这张的提示词覆盖外貌描述，出场的 2 个镜头首帧会过期');
    await user.click(within(ask).getByRole('button', { name: '取消' }));
    expect(posts(calls)).toHaveLength(0);
    // 卡片悬停按钮同样要确认
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await user.click(within(card).getByRole('button', { name: '把定妆照 #46 设为当前造型' }));
    await user.click(within(await screen.findByRole('dialog', { name: '设为当前造型？' })).getByRole('button', { name: '设为当前造型' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ method: 'POST', url: '/api/characters/6/portraits/46/use' });
  });

  it('没有出场镜头时确认框不写过期那句', async () => {
    const { user } = setup(CHEN, { 'GET /api/episodes/5': makeEpisode({ id: 5 }) });
    const card = await screen.findByTestId('character-6');
    await user.click(within(card).getByRole('button', { name: '把定妆照 #46 设为当前造型' }));
    const ask = await screen.findByRole('dialog', { name: '设为当前造型？' });
    expect(ask.textContent).toContain('用这张的提示词覆盖外貌描述');
    expect(ask.textContent).not.toContain('过期');
  });

  it('角色资料有未保存的修改：设为当前造型先提示保存或放弃，不弹确认', async () => {
    const { calls, user } = setup(CHEN);
    const card = await screen.findByTestId('character-6');
    await user.type(within(card).getByLabelText('人物描述'), '（改）');
    await user.click(within(card).getByRole('button', { name: '把定妆照 #46 设为当前造型' }));
    expect(within(card).getByText('先保存或放弃修改')).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(posts(calls)).toHaveLength(0);
  });

  it('最近一次抽卡失败：定妆照条末尾显示失败卡，可重试（同张数和引擎）和查看任务', async () => {
    const failed: Character = {
      ...CHEN,
      last_failure: { id: 77, type: 'image.portrait', status: 'failed', error: 'ComfyUI 没开', finished_at: '2026-10-10T10:00:00Z' },
    };
    const { calls, user } = setup(failed, { 'POST /api/characters/6/portraits/draw': { job_ids: [78] } });
    const card = await screen.findByTestId('character-6');
    const fail = within(card).getByTestId('portrait-failed');
    expect(fail.textContent).toContain('抽卡失败：ComfyUI 没开');
    expect(within(fail).getByRole('link', { name: '查看任务' }).getAttribute('href')).toBe('/jobs?job=77');
    await user.selectOptions(within(card).getByLabelText('抽卡张数'), '3');
    await user.click(within(fail).getByRole('button', { name: '重试' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ url: '/api/characters/6/portraits/draw', body: { count: 3 } });
  });

  it('抽卡按选的张数提交；进行中的抽卡显示占位和进度', async () => {
    const running = { ...CHEN, active_jobs: [{ id: 80, type: 'image.portrait', status: 'running' as const }, { id: 81, type: 'image.portrait', status: 'queued' as const }] };
    installFetch({ ...ROUTES, 'GET /api/projects/demo/characters': [running], 'POST /api/characters/6/portraits/draw': { job_ids: [82] } });
    renderAt(
      <EventsContext.Provider value={{ ...initialEvents, progress: { 80: { value: 5, max: 20 } } }}>
        <CastPage />
      </EventsContext.Provider>,
      AT,
    );
    const user = userEvent.setup();
    const card = await screen.findByTestId('character-6');
    expect(within(card).getByTestId('drawing-80').textContent).toContain('生成中');
    expect(within(card).getByTestId('drawing-80').textContent).toContain('25%');
    expect(within(card).getByTestId('drawing-81').textContent).toContain('排队中');
    await user.selectOptions(within(card).getByLabelText('抽卡张数'), '3');
    await user.click(within(card).getByRole('button', { name: /^抽卡/ }));
    const fetchCalls = (globalThis.fetch as unknown as { mock: { calls: [string, RequestInit?][] } }).mock.calls;
    await waitFor(() => expect(fetchCalls.some(([, i]) => i?.method === 'POST')).toBe(true));
    const [url, init] = fetchCalls.find(([, i]) => i?.method === 'POST')!;
    expect(url).toBe('/api/characters/6/portraits/draw');
    expect(JSON.parse(init!.body as string)).toEqual({ count: 3 });
  });

  it('抽卡可以换成浏览器引擎（gpt_image），请求带上 engine', async () => {
    const { calls, user } = setup(CHEN, {
      'GET /api/engines': { image: [{ name: 'zimage', family: 'zimage', runtime: 'comfyui', caps: { max_refs: 0 } }, { name: 'gpt_image', family: 'chatgpt_web', runtime: 'browser', caps: { max_refs: 4 } }], video: [] },
      'POST /api/characters/6/portraits/draw': { job_ids: [83] },
    });
    const card = await screen.findByTestId('character-6');
    const pick = within(card).getByLabelText('抽卡引擎');
    await waitFor(() => expect(within(pick).getByRole('option', { name: 'ChatGPT 网页' })).toBeTruthy());
    await user.selectOptions(pick, 'gpt_image');
    await user.click(within(card).getByRole('button', { name: /^抽卡/ }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ url: '/api/characters/6/portraits/draw', body: { count: 2, engine: 'gpt_image' } });
  });

  it('抽卡采样完成（value>=max）但任务还在跑：占位显示"解码保存中"而不是卡在 100%', async () => {
    const running = { ...CHEN, active_jobs: [{ id: 80, type: 'image.portrait', status: 'running' as const }] };
    installFetch({ ...ROUTES, 'GET /api/projects/demo/characters': [running] });
    renderAt(
      <EventsContext.Provider value={{ ...initialEvents, progress: { 80: { value: 20, max: 20 } } }}>
        <CastPage />
      </EventsContext.Provider>,
      AT,
    );
    const card = await screen.findByTestId('character-6');
    const placeholder = within(card).getByTestId('drawing-80');
    expect(placeholder.textContent).toContain('解码保存中');
    expect(placeholder.textContent).not.toContain('100%');
    expect(placeholder.querySelector('.spinner')).toBeTruthy(); // spinner 还在
  });

  it('AI 推荐造型：提交 suggest；方案卡可改提示词后抽卡或设为角色提示词', async () => {
    const opts = [
      { label: '老练精干款', visual_prompt: 'buzz cut mechanic', core_features: '寸头、藏青工装' },
      { label: '憨厚笑眼款', visual_prompt: 'smiling mechanic', core_features: '胡茬' },
    ];
    const { calls, user } = setup(
      { ...CHEN, design_options: opts },
      {
        'POST /api/characters/6/suggest': { job_ids: [71] },
        'GET /api/jobs/71': makeJob({ id: 71, type: 'character.suggest', status: 'queued' }),
        'POST /api/characters/6/portraits/draw': { job_ids: [72, 73] },
        'PATCH /api/characters/6': CHEN,
      },
    );
    const card = await screen.findByTestId('character-6');
    await user.click(within(card).getByRole('button', { name: 'AI 推荐造型' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ url: '/api/characters/6/suggest', body: { count: 3, episode_id: 5 } });

    const second = within(card).getByTestId('option-1');
    expect(within(second).getByText('憨厚笑眼款')).toBeTruthy();
    expect(within(second).getByText('胡茬')).toBeTruthy();
    const ta = within(second).getByLabelText('憨厚笑眼款 提示词');
    await user.clear(ta);
    await user.type(ta, 'grinning mechanic with pencil');
    await user.click(within(second).getByRole('button', { name: '用这个提示词抽卡' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[1]).toMatchObject({
      url: '/api/characters/6/portraits/draw',
      body: { count: 2, visual_prompt: 'grinning mechanic with pencil' },
    });
    await user.click(within(within(card).getByTestId('option-0')).getByRole('button', { name: '设为角色提示词' }));
    const ask = await screen.findByRole('dialog', { name: '设为角色提示词？' });
    expect(ask.textContent).toContain('用这个方案的提示词覆盖外貌描述，出场的 2 个镜头首帧会过期');
    expect(posts(calls)).toHaveLength(2);
    await user.click(within(ask).getByRole('button', { name: '设为角色提示词' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(3));
    expect(posts(calls)[2]).toMatchObject({ method: 'PATCH', url: '/api/characters/6', body: { visual_prompt: 'buzz cut mechanic' } });
  });

  it('按剧本推荐角色：任务完成后列出没建的角色，一键添加', async () => {
    const result = {
      characters: [
        { name: '陈师傅', description: '', visual_prompt: 'x', core_features: '' },
        { name: '前台小李', description: '修车厂前台', visual_prompt: 'young clerk', core_features: '工牌' },
      ],
    };
    const { calls, user } = setup(CHEN, {
      'POST /api/episodes/5/characters/recommend': { job_ids: [90] },
      'GET /api/jobs/90': makeJob({ id: 90, type: 'character.recommend', status: 'done', result }),
      'POST /api/projects/demo/characters': { ...CHEN, id: 9, name: '前台小李' },
    });
    await user.click(await screen.findByRole('button', { name: '按剧本推荐角色' }));
    const rec = await screen.findByTestId('recommend-前台小李');
    expect(screen.queryByTestId('recommend-陈师傅')).toBeNull(); // 已有的不再列
    // 任务号记在地址里：刷新或离开再回来还能看到结果
    expect(screen.getByTestId('location').textContent).toBe('/projects/demo/episodes/5/cast?recommend=90');
    await user.click(within(rec).getByRole('button', { name: '添加角色' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[0]).toMatchObject({ url: '/api/episodes/5/characters/recommend', body: { count: 3 } });
    expect(posts(calls)[1]).toMatchObject({
      url: '/api/projects/demo/characters',
      body: { name: '前台小李', description: '修车厂前台', visual_prompt: 'young clerk', core_features: '工牌' },
    });
  });

  it('角色卡里有三视图', async () => {
    setup(CHEN);
    const card = await screen.findByTestId('character-6');
    expect(within(card).getByRole('region', { name: '陈师傅 的三视图' })).toBeTruthy();
    expect(within(card).getByRole('button', { name: '全部生成' })).toBeTruthy();
  });

  it('地址里带 ?recommend=<任务号>：直接显示那次推荐的结果', async () => {
    installFetch({
      ...ROUTES,
      'GET /api/projects/demo/characters': [CHEN],
      'GET /api/jobs/90': makeJob({
        id: 90, type: 'character.recommend', status: 'done',
        result: { characters: [{ name: '前台小李', description: '', visual_prompt: 'young clerk', core_features: '' }] },
      }),
    });
    renderAt(<CastPage />, { ...AT, route: '/projects/demo/episodes/5/cast?recommend=90' });
    expect(await screen.findByTestId('recommend-前台小李')).toBeTruthy();
  });
});
