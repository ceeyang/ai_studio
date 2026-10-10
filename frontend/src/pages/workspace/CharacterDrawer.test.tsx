import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { Character } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeCandidate, makeEpisode, makeJob, makeShot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { EventsContext } from '../../ws/EventsContext';
import { initialEvents } from '../../ws/events';
import { CastStage } from './CastStage';

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
const AT = { route: '/projects/demo/episodes/5?stage=cast&char=6', path: '/projects/:slug/episodes/:episodeId' };

function setup(chen: Character, extra: Record<string, unknown> = {}) {
  const calls = installFetch({ ...ROUTES, 'GET /api/projects/demo/characters': [chen], ...extra });
  const r = renderAt(<CastStage />, AT);
  return { calls, user: userEvent.setup(), ...r };
}

/** 抽屉（造型页签默认打开） */
const drawer = () => screen.findByRole('dialog', { name: `角色 陈师傅` });
const posts = (calls: { method: string; url: string; body: unknown }[]) => calls.filter((c) => c.method !== 'GET');
const where = () => screen.getByTestId('location').textContent;

describe('角色抽屉', () => {
  it('三个页签：造型 / 三视图 / 资料；页签用 hidden 切换，切页签不丢正在编辑的内容', async () => {
    const { user } = setup(CHEN);
    const d = await drawer();
    const tabs = within(d).getAllByRole('tab').map((t) => t.textContent);
    expect(tabs).toEqual(['造型', '三视图', '资料']);
    expect(within(d).getByRole('tab', { name: '造型' }).getAttribute('aria-selected')).toBe('true');
    expect(within(d).getByRole('list', { name: '陈师傅 的定妆照' }).closest('[hidden]')).toBeNull();
    expect(within(d).getByRole('region', { name: '陈师傅 的三视图', hidden: true }).closest('[hidden]')).toBeTruthy();
    await user.click(within(d).getByRole('tab', { name: '资料' }));
    await user.type(within(d).getByLabelText('人物描述'), '（夜班）');
    await user.click(within(d).getByRole('tab', { name: '三视图' }));
    expect(within(d).getByRole('region', { name: '陈师傅 的三视图' }).closest('[hidden]')).toBeNull();
    expect(within(d).getByRole('button', { name: '全部生成' })).toBeTruthy();
    await user.click(within(d).getByRole('tab', { name: '资料' }));
    expect((within(d).getByLabelText('人物描述') as HTMLTextAreaElement).value).toBe('汽修师傅（夜班）');
  });

  it('资料：外貌提示词收在「高级」里；音色是下拉，「其他（手填）」切成输入框', async () => {
    const { calls, user } = setup(CHEN, { 'PATCH /api/characters/6': CHEN });
    const d = await drawer();
    await user.click(within(d).getByRole('tab', { name: '资料' }));
    const vp = within(d).getByLabelText(/外貌提示词/);
    expect(vp.closest('details')?.querySelector('summary')?.textContent).toBe('高级');
    expect(within(d).getByLabelText('人物描述').closest('details')).toBeNull();
    const voice = within(d).getByLabelText(/配音音色/) as HTMLSelectElement;
    expect(voice.tagName).toBe('SELECT');
    expect(within(voice).getByRole('option', { name: '高棉语 · 女声' })).toBeTruthy();
    await user.selectOptions(voice, 'km-KH-SreymomNeural');
    await user.click(within(d).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0].body).toEqual({ voice: 'km-KH-SreymomNeural' });

    await user.selectOptions(voice, '其他（手填）');
    await user.type(within(d).getByLabelText('配音音色（手填）'), 'en-US-AriaNeural');
    await user.click(within(d).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[1].body).toEqual({ voice: 'en-US-AriaNeural' });
  });

  it('不在常用列表里的音色：直接显示成手填', async () => {
    const { user } = setup({ ...CHEN, voice: 'en-US-AriaNeural' });
    const d = await drawer();
    await user.click(within(d).getByRole('tab', { name: '资料' }));
    expect((within(d).getByLabelText('配音音色（手填）') as HTMLInputElement).value).toBe('en-US-AriaNeural');
  });

  it('关抽屉时有没保存的修改先问：继续编辑留下，放弃修改才关（去掉 ?char=）', async () => {
    const { user } = setup(CHEN);
    const d = await drawer();
    await user.click(within(d).getByRole('tab', { name: '资料' }));
    await user.type(within(d).getByLabelText('人物描述'), '（改）');
    await user.click(within(d).getByRole('button', { name: '关闭' }));
    const ask = await screen.findByRole('dialog', { name: '放弃没保存的修改？' });
    await user.click(within(ask).getByRole('button', { name: '继续编辑' }));
    expect(screen.getByRole('dialog', { name: '角色 陈师傅' })).toBeTruthy();
    expect(where()).toBe('/projects/demo/episodes/5?stage=cast&char=6');
    // Esc 同样要问
    await user.keyboard('{Escape}');
    await user.click(within(await screen.findByRole('dialog', { name: '放弃没保存的修改？' })).getByRole('button', { name: '放弃修改' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '角色 陈师傅' })).toBeNull());
    expect(where()).toBe('/projects/demo/episodes/5?stage=cast');
  });

  it('三视图全身描述改了没保存：关抽屉同样先问', async () => {
    const { user } = setup(CHEN);
    const d = await drawer();
    await user.click(within(d).getByRole('tab', { name: '三视图' }));
    await user.type(within(d).getByLabelText('全身描述'), 'tall');
    await user.click(screen.getByTestId('drawer-scrim'));
    expect(await screen.findByRole('dialog', { name: '放弃没保存的修改？' })).toBeTruthy();
  });

  it('没有修改时 Esc 直接关；焦点回到打开前的元素', async () => {
    const { user } = setup(CHEN);
    await drawer();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(where()).toBe('/projects/demo/episodes/5?stage=cast');
    await user.click(within(screen.getByTestId('character-6')).getByRole('button', { name: '编辑 陈师傅' }));
    const d = await drawer();
    expect(d.contains(document.activeElement)).toBe(true);
    await user.click(within(d).getByRole('button', { name: '关闭' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(document.activeElement?.textContent).toBe('编辑');
  });

  it('资料改了没保存时换页面先问', async () => {
    const { user, router } = setup(CHEN);
    const d = await drawer();
    await user.click(within(d).getByRole('tab', { name: '资料' }));
    await user.type(within(d).getByLabelText('人物描述'), '（改）');
    await act(() => void router.navigate('/jobs'));
    expect(await screen.findByRole('dialog', { name: '有未保存的修改，确定离开？' })).toBeTruthy();
  });

  it('单击定妆照只打开预览，不换造型；预览里 Esc 关、左右键切换、标出当前', async () => {
    const { calls, user } = setup(CHEN);
    const d = await drawer();
    const current = within(d).getByRole('button', { name: '定妆照 #19（当前）' });
    expect(current.closest('.pcard')!.className).toContain('is-selected');
    await user.click(within(d).getByRole('button', { name: '定妆照 #46' }));
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
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /陈师傅 定妆照/ })).toBeNull());
    // 只关了预览，抽屉还在
    expect(screen.getByRole('dialog', { name: '角色 陈师傅' })).toBeTruthy();
    expect(posts(calls)).toHaveLength(0);
  });

  it('设为当前造型：先确认（写明覆盖外貌描述和过期的镜头数），确认后才切换', async () => {
    const { calls, user } = setup(CHEN, { 'POST /api/characters/6/portraits/46/use': { ...CHEN, portrait_candidate_id: 46 } });
    const d = await drawer();
    await user.click(within(d).getByRole('button', { name: '定妆照 #46' }));
    const box = await screen.findByRole('dialog', { name: /陈师傅 定妆照/ });
    await user.click(within(box).getByRole('button', { name: '设为当前造型' }));
    const ask = await screen.findByRole('dialog', { name: '设为当前造型？' });
    expect(ask.textContent).toContain('用这张的提示词覆盖外貌描述，本集出场的 2 个镜头首帧会过期，其他集出场的镜头也会过期');
    await user.click(within(ask).getByRole('button', { name: '取消' }));
    expect(posts(calls)).toHaveLength(0);
    // 卡片悬停按钮同样要确认
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /陈师傅 定妆照/ })).toBeNull());
    await user.click(within(d).getByRole('button', { name: '把定妆照 #46 设为当前造型' }));
    await user.click(within(await screen.findByRole('dialog', { name: '设为当前造型？' })).getByRole('button', { name: '设为当前造型' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ method: 'POST', url: '/api/characters/6/portraits/46/use' });
  });

  it('没有出场镜头时确认框不写过期那句', async () => {
    const { user } = setup(CHEN, { 'GET /api/episodes/5': makeEpisode({ id: 5 }) });
    const d = await drawer();
    await user.click(within(d).getByRole('button', { name: '把定妆照 #46 设为当前造型' }));
    const ask = await screen.findByRole('dialog', { name: '设为当前造型？' });
    expect(ask.textContent).toContain('用这张的提示词覆盖外貌描述');
    expect(ask.textContent).not.toContain('过期');
  });

  it('资料有未保存的修改：设为当前造型先提示保存或放弃，不弹确认', async () => {
    const { calls, user } = setup(CHEN);
    const d = await drawer();
    await user.click(within(d).getByRole('tab', { name: '资料' }));
    await user.type(within(d).getByLabelText('人物描述'), '（改）');
    await user.click(within(d).getByRole('tab', { name: '造型' }));
    await user.click(within(d).getByRole('button', { name: '把定妆照 #46 设为当前造型' }));
    expect(within(d).getByText('先保存或放弃修改')).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: '设为当前造型？' })).toBeNull();
    expect(posts(calls)).toHaveLength(0);
  });

  it('最近一次抽卡失败：定妆照条末尾显示失败卡，可重试（同张数和引擎）和查看任务', async () => {
    const failed: Character = {
      ...CHEN,
      last_failure: { id: 77, type: 'image.portrait', status: 'failed', error: 'ComfyUI 没开', finished_at: '2026-10-10T10:00:00Z' },
    };
    const { calls, user } = setup(failed, { 'POST /api/characters/6/portraits/draw': { job_ids: [78] } });
    const d = await drawer();
    const fail = within(d).getByTestId('portrait-failed');
    expect(fail.textContent).toContain('抽卡失败：ComfyUI 没开');
    expect(within(fail).getByRole('link', { name: '查看任务' }).getAttribute('href')).toBe('/jobs?job=77');
    await user.selectOptions(within(d).getByLabelText('抽卡张数'), '3');
    await user.click(within(fail).getByRole('button', { name: '重试' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ url: '/api/characters/6/portraits/draw', body: { count: 3 } });
  });

  it('抽卡按选的张数提交；进行中的抽卡显示占位和进度', async () => {
    const running = { ...CHEN, active_jobs: [{ id: 80, type: 'image.portrait', status: 'running' as const }, { id: 81, type: 'image.portrait', status: 'queued' as const }] };
    installFetch({ ...ROUTES, 'GET /api/projects/demo/characters': [running], 'POST /api/characters/6/portraits/draw': { job_ids: [82] } });
    renderAt(
      <EventsContext.Provider value={{ ...initialEvents, progress: { 80: { value: 5, max: 20 } } }}>
        <CastStage />
      </EventsContext.Provider>,
      AT,
    );
    const user = userEvent.setup();
    const d = await drawer();
    expect(within(d).getByTestId('drawing-80').textContent).toContain('生成中');
    expect(within(d).getByTestId('drawing-80').textContent).toContain('25%');
    expect(within(d).getByTestId('drawing-81').textContent).toContain('排队中');
    await user.selectOptions(within(d).getByLabelText('抽卡张数'), '3');
    await user.click(within(d).getByRole('button', { name: /^抽卡/ }));
    const fetchCalls = vi.mocked(fetch).mock.calls as [string, RequestInit?][];
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
    const d = await drawer();
    const pick = within(d).getByLabelText('抽卡引擎');
    await waitFor(() => expect(within(pick).getByRole('option', { name: 'ChatGPT 网页' })).toBeTruthy());
    await user.selectOptions(pick, 'gpt_image');
    await user.click(within(d).getByRole('button', { name: /^抽卡/ }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ url: '/api/characters/6/portraits/draw', body: { count: 2, engine: 'gpt_image' } });
  });

  it('抽卡采样完成（value>=max）但任务还在跑：占位显示"解码保存中"而不是卡在 100%', async () => {
    const running = { ...CHEN, active_jobs: [{ id: 80, type: 'image.portrait', status: 'running' as const }] };
    installFetch({ ...ROUTES, 'GET /api/projects/demo/characters': [running] });
    renderAt(
      <EventsContext.Provider value={{ ...initialEvents, progress: { 80: { value: 20, max: 20 } } }}>
        <CastStage />
      </EventsContext.Provider>,
      AT,
    );
    const d = await drawer();
    const placeholder = within(d).getByTestId('drawing-80');
    expect(placeholder.textContent).toContain('解码保存中');
    expect(placeholder.textContent).not.toContain('100%');
    expect(placeholder.querySelector('.spinner')).toBeTruthy(); // spinner 还在
  });

  it('上传定妆照：提交 multipart 并刷新', async () => {
    const withPortrait = { ...CHEN, portraits: [], portrait_candidate_id: 31, portrait_url: '/media/demo/media/portrait/a.png' };
    let uploaded = false;
    const calls = installFetch({
      ...ROUTES,
      'GET /api/projects/demo/characters': () => [uploaded ? withPortrait : { ...withPortrait, portrait_candidate_id: null, portrait_url: null }],
      'POST /api/characters/6/portrait': () => {
        uploaded = true;
        return withPortrait;
      },
    });
    renderAt(<CastStage />, AT);
    const user = userEvent.setup();
    const d = await drawer();
    expect(within(d).getByText('还没有定妆照')).toBeTruthy();
    await user.upload(within(d).getByLabelText('上传定妆照'), new File(['png'], 'chen.png', { type: 'image/png' }));
    const img = (await within(d).findByRole('img', { name: '陈师傅 定妆照' })) as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('/media/demo/media/portrait/a.png');
    expect(posts(calls)[0].url).toBe('/api/characters/6/portrait');
    const init = vi.mocked(fetch).mock.calls.find(([, i]) => i?.method === 'POST')?.[1];
    expect(init?.body).toBeInstanceOf(FormData);
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
    const d = await drawer();
    await user.click(within(d).getByRole('button', { name: 'AI 推荐造型' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ url: '/api/characters/6/suggest', body: { count: 3, episode_id: 5 } });

    const second = within(d).getByTestId('option-1');
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
    await user.click(within(within(d).getByTestId('option-0')).getByRole('button', { name: '设为角色提示词' }));
    const ask = await screen.findByRole('dialog', { name: '设为角色提示词？' });
    expect(ask.textContent).toContain('用这个方案的提示词覆盖外貌描述，本集出场的 2 个镜头首帧会过期，其他集出场的镜头也会过期');
    expect(posts(calls)).toHaveLength(2);
    await user.click(within(ask).getByRole('button', { name: '设为角色提示词' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(3));
    expect(posts(calls)[2]).toMatchObject({ method: 'PATCH', url: '/api/characters/6', body: { visual_prompt: 'buzz cut mechanic' } });
  });

  it('抽屉里没有主按钮（全页唯一的主按钮在阶段条）', async () => {
    setup({ ...CHEN, design_options: [{ label: '老练款', visual_prompt: 'x', core_features: '' }] });
    const d = await drawer();
    expect(d.querySelectorAll('.primary')).toHaveLength(0);
  });
});
