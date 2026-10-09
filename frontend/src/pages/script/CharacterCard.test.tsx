import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Character } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeCandidate, makeEpisode, makeJob } from '../../test/factories';
import { renderAt } from '../../test/render';
import { EventsContext } from '../../ws/EventsContext';
import { initialEvents } from '../../ws/events';
import { ScriptPage } from '../ScriptPage';

const UPLOAD = makeCandidate({ id: 19, engine: 'upload', url: '/media/p/up.png', params: { source: 'chen.png' } });
const DRAWN = makeCandidate({ id: 46, engine: 'h3_image', url: '/media/p/ai.png', params: { visual_prompt: 'smiling mechanic' } });
const CHEN: Character = {
  id: 6, project_id: 1, name: '陈师傅', description: '汽修师傅', visual_prompt: 'lean mechanic', core_features: '',
  voice: null, portrait_candidate_id: 19, portrait_url: '/media/p/up.png', portraits: [UPLOAD, DRAWN],
  design_options: [], active_jobs: [],
};
const ROUTES = {
  'GET /api/episodes/5': makeEpisode({ id: 5, scene_blocks: [{ id: 1, order: 0, location_id: null, text: '修车厂' }] }),
  'GET /api/projects/demo/locations': [],
  'GET /api/projects/demo/style': null,
};
const AT = { route: '/projects/demo/episodes/5/script', path: '/projects/:slug/episodes/:episodeId/script' };

function setup(chen: Character, extra: Record<string, unknown> = {}) {
  const calls = installFetch({ ...ROUTES, 'GET /api/projects/demo/characters': [chen], ...extra });
  renderAt(<ScriptPage />, AT);
  return { calls, user: userEvent.setup() };
}

const posts = (calls: { method: string; url: string; body: unknown }[]) => calls.filter((c) => c.method !== 'GET');

describe('角色卡', () => {
  it('显示全部定妆照候选，选定的高亮；点另一张调用 use 切换角色', async () => {
    const { calls, user } = setup(CHEN, { 'POST /api/characters/6/portraits/46/use': { ...CHEN, portrait_candidate_id: 46 } });
    const card = await screen.findByTestId('character-6');
    const current = within(card).getByRole('listitem', { name: '定妆照 #19（当前）' });
    expect(current.getAttribute('aria-pressed')).toBe('true');
    expect(current.className).toContain('is-selected');
    await user.click(current); // 点当前那张不发请求
    await user.click(within(card).getByRole('listitem', { name: '定妆照 #46' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ method: 'POST', url: '/api/characters/6/portraits/46/use' });
  });

  it('抽卡按选的张数提交；进行中的抽卡显示占位和进度', async () => {
    const running = { ...CHEN, active_jobs: [{ id: 80, type: 'image.portrait', status: 'running' as const }, { id: 81, type: 'image.portrait', status: 'queued' as const }] };
    installFetch({ ...ROUTES, 'GET /api/projects/demo/characters': [running], 'POST /api/characters/6/portraits/draw': { job_ids: [82] } });
    renderAt(
      <EventsContext.Provider value={{ ...initialEvents, progress: { 80: { value: 5, max: 20 } } }}>
        <ScriptPage />
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
    await user.click(within(rec).getByRole('button', { name: '添加角色' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[0]).toMatchObject({ url: '/api/episodes/5/characters/recommend', body: { count: 3 } });
    expect(posts(calls)[1]).toMatchObject({
      url: '/api/projects/demo/characters',
      body: { name: '前台小李', description: '修车厂前台', visual_prompt: 'young clerk', core_features: '工牌' },
    });
  });

  it('右栏分页：场景和风格在各自的页里', async () => {
    const { user } = setup(CHEN);
    expect(await screen.findByTestId('character-6')).toBeTruthy();
    await user.click(screen.getByRole('tab', { name: '风格' }));
    expect(screen.queryByTestId('character-6')).toBeNull();
    expect(screen.getByLabelText(/风格锚/)).toBeTruthy();
    expect(screen.getByRole('tab', { name: '场景（0）' })).toBeTruthy();
  });
});
