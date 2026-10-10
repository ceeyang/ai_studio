import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Outlet, RouterProvider, createMemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import type { Character, ExportRow, Slot } from '../api/types';
import { ConfirmProvider } from '../components/ConfirmDialog';
import { LeaveGuard } from '../components/EditableFields';
import { installFetch } from '../test/fetch';
import { makeCandidate, makeEpisode, makeProject, makeShot, makeSlot } from '../test/factories';
import { newQueryClient } from '../test/render';
import { episodeRoutes } from './EpisodeWorkspace';

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname + loc.search}</div>;
}

/** 和 App 一样的数据路由，只挂单集工作台和旧页面的重定向 */
function renderRoute(route: string) {
  const router = createMemoryRouter(
    [
      {
        element: (
          <ConfirmProvider>
            <Outlet />
            <LocationProbe />
            <LeaveGuard />
          </ConfirmProvider>
        ),
        children: [...episodeRoutes, { path: '*', element: null }],
      },
    ],
    { initialEntries: [route] },
  );
  return render(
    <QueryClientProvider client={newQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

function char(id: number, name: string, portrait: boolean): Character {
  return {
    id, project_id: 1, name, description: '', visual_prompt: 'look', core_features: '', voice: null,
    portrait_candidate_id: portrait ? id * 10 : null, portrait_url: portrait ? `/media/p${id}.png` : null,
  };
}

function chosen(id: number, kind: string): Slot {
  return makeSlot({ id, kind, selected_candidate_id: id * 10, candidates: [makeCandidate({ id: id * 10, slot_id: id })] });
}

const BLOCKS = [
  { id: 21, order: 0, location_id: 3, text: '直播间里，林夏念稿。' },
  { id: 22, order: 1, location_id: null, text: '老周推门进来。' },
];
// 林夏有定妆照、老周没有，两人都出场：下一步在角色阶段
const EP = makeEpisode({
  id: 4,
  number: 2,
  title: '提词器',
  source_text: '深夜的直播间\n第二行',
  scene_blocks: BLOCKS,
  shots: [
    makeShot({ id: 11, code: 'S01', scene_block_id: 21, cast: [{ character_id: 8, variation_id: null }] }),
    makeShot({ id: 12, code: 'S02', order: 1, scene_block_id: 22, cast: [{ character_id: 9, variation_id: null }] }),
  ],
});
const CHARS = [char(8, '林夏', true), char(9, '老周', false)];

function routes(extra: Record<string, unknown> = {}) {
  return {
    'GET /api/projects/demo': makeProject({ title: '深夜直播' }),
    'GET /api/projects/demo/episodes': [
      { id: 4, project_id: 1, number: 2, title: '提词器', source_text: '', target_seconds: 30, status: 'draft' },
      { id: 5, project_id: 1, number: 3, title: '第三', source_text: '', target_seconds: 30, status: 'draft' },
    ],
    'GET /api/episodes/4': EP,
    'GET /api/episodes/4/exports': [] as ExportRow[],
    'GET /api/projects/demo/characters': CHARS,
    'GET /api/projects/demo/locations': [
      { id: 3, project_id: 1, name: '直播间', time_of_day: '夜', atmosphere: '', visual_prompt: '' },
    ],
    'GET /api/projects/demo/style': null,
    'GET /api/engines': { image: [], video: [] },
    'GET /api/jobs': [],
    ...extra,
  };
}

const where = () => screen.getByTestId('location').textContent;
const stageBar = () => screen.getByRole('list', { name: '制作阶段' });

beforeEach(() => localStorage.clear());

describe('EpisodeWorkspace', () => {
  it('没有 ?stage 时停在下一步所在的阶段，并写进地址；页头是面包屑和切集', async () => {
    installFetch(routes());
    renderRoute('/projects/demo/episodes/4');
    await waitFor(() => expect(where()).toBe('/projects/demo/episodes/4?stage=cast'));
    expect(within(stageBar()).getByRole('button', { name: /角色/ }).getAttribute('aria-current')).toBe('step');
    expect(screen.getByRole('heading', { level: 2, name: '第 2 集 提词器' })).toBeTruthy();
    expect((await screen.findByRole('link', { name: /下一集/ })).getAttribute('href')).toBe('/projects/demo/episodes/5?stage=cast');
  });

  it('全部完成时默认停在成片', async () => {
    const done = {
      ...EP,
      shots: EP.shots.map((s, i) => ({ ...s, cast: [{ character_id: 8, variation_id: null }], slots: [chosen(i * 2 + 1, 'first_frame'), chosen(i * 2 + 2, 'video')] })),
    };
    const exp: ExportRow = { id: 1, episode_id: 4, variant: '9:16', file_path: 'x/1.mp4', url: '/media/1.mp4', settings: {}, is_final: true, created_at: '2026-10-08T10:00:00' };
    installFetch(routes({ 'GET /api/episodes/4': done, 'GET /api/episodes/4/exports': [exp] }));
    renderRoute('/projects/demo/episodes/4');
    await waitFor(() => expect(where()).toBe('/projects/demo/episodes/4?stage=export'));
    expect(screen.getByText('五段都完成了')).toBeTruthy();
  });

  it('阶段条五段可点切换；切走再切回，剧本里没保存的修改还在，也不弹确认', async () => {
    installFetch(routes());
    const { container } = renderRoute('/projects/demo/episodes/4?stage=script');
    const user = userEvent.setup();
    const bar = await screen.findByRole('list', { name: '制作阶段' });
    expect(within(bar).getAllByRole('button').map((b) => b.querySelector('.step-label')?.textContent)).toEqual(['剧本', '角色', '首帧', '视频', '成片']);
    await user.click(await screen.findByRole('button', { name: /原文 \/ 梗概/ }));
    const title = screen.getByLabelText('本集标题') as HTMLInputElement;
    await user.clear(title);
    await user.type(title, '新标题');
    await user.click(within(bar).getByRole('button', { name: /首帧/ }));
    expect(where()).toBe('/projects/demo/episodes/4?stage=keyframes');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(await screen.findByRole('button', { name: '打开 S02' })).toBeTruthy();
    // 剧本阶段藏起来但还挂着
    expect(container.querySelector('[data-stage-panel="script"]')?.hasAttribute('hidden')).toBe(true);
    await user.click(within(bar).getByRole('button', { name: /剧本/ }));
    expect((screen.getByLabelText('本集标题') as HTMLInputElement).value).toBe('新标题');
  });

  it('「下一步」是全页唯一的主按钮；下一步在别的阶段时点它切过去', async () => {
    installFetch(routes());
    const { container } = renderRoute('/projects/demo/episodes/4?stage=script');
    const next = await screen.findByRole('button', { name: '下一步：抽定妆照（1 个角色缺定妆照）' });
    expect(next.className).toContain('primary');
    expect(container.querySelectorAll('.primary')).toHaveLength(1);
    await userEvent.setup().click(next);
    expect(where()).toBe('/projects/demo/episodes/4?stage=cast');
    await waitFor(() => expect(screen.getByTestId('character-9')).toBeTruthy());
    expect(container.querySelectorAll('.primary')).toHaveLength(1);
  });

  it('下一步就在当前阶段：点它触发阶段里的动作按钮（生成分镜）', async () => {
    const noShots = { ...EP, shots: [] };
    const calls = installFetch(
      routes({ 'GET /api/episodes/4': noShots, 'POST /api/episodes/4/run': { job_ids: [9] }, 'GET /api/jobs/9': { id: 9, status: 'running' } }),
    );
    renderRoute('/projects/demo/episodes/4');
    await waitFor(() => expect(where()).toBe('/projects/demo/episodes/4?stage=script'));
    const next = await screen.findByRole('button', { name: '下一步：生成分镜' });
    await waitFor(() => expect(document.querySelector('[data-next-action]')?.textContent).toBe('生成分镜'));
    await userEvent.setup().click(next);
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ stage: 'storyboard', force: false }));
  });

  it('旧页面地址重定向到工作台对应阶段，保留 ?shot=（镜头抽屉照样打开）', async () => {
    installFetch(routes());
    renderRoute('/projects/demo/episodes/4/director?shot=12');
    await waitFor(() => expect(where()).toBe('/projects/demo/episodes/4?stage=keyframes&shot=12'));
    expect(await screen.findByRole('dialog', { name: '镜头 S02 详情' })).toBeTruthy();
  });

  it.each([
    ['script', 'script'],
    ['cast', 'cast'],
    ['export', 'export'],
  ])('旧的 /%s 重定向到 ?stage=%s', async (page, stage) => {
    installFetch(routes());
    renderRoute(`/projects/demo/episodes/4/${page}`);
    await waitFor(() => expect(where()).toBe(`/projects/demo/episodes/4?stage=${stage}`));
  });

  it('离开首帧 / 视频阶段时去掉 ?shot=，镜头抽屉随之关掉（藏起来的阶段里不留抽屉和它的 Esc 监听）；离开角色阶段去掉 ?char=', async () => {
    installFetch(routes());
    renderRoute('/projects/demo/episodes/4?stage=keyframes&shot=12');
    const user = userEvent.setup();
    expect(await screen.findByRole('dialog', { name: '镜头 S02 详情' })).toBeTruthy();
    // 首帧 → 视频：同一块内容，抽屉保留
    await user.click(within(stageBar()).getByRole('button', { name: /视频/ }));
    expect(where()).toBe('/projects/demo/episodes/4?stage=videos&shot=12');
    expect(screen.getByRole('dialog', { name: '镜头 S02 详情' })).toBeTruthy();
    await user.click(within(stageBar()).getByRole('button', { name: /剧本/ }));
    expect(where()).toBe('/projects/demo/episodes/4?stage=script');
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
    // 角色
    const rail = screen.getByRole('complementary', { name: '剧本大纲与角色' });
    await user.click(await within(rail).findByRole('button', { name: /^老周/ }));
    expect(where()).toBe('/projects/demo/episodes/4?stage=cast&char=9');
    await user.click(within(stageBar()).getByRole('button', { name: /成片/ }));
    expect(where()).toBe('/projects/demo/episodes/4?stage=export');
  });

  it('下一步事件只发给当前阶段那块内容：下一步在剧本阶段时，藏着的角色阶段不切页签', async () => {
    const noShots = { ...EP, shots: [] };
    installFetch(
      routes({ 'GET /api/episodes/4': noShots, 'POST /api/episodes/4/run': { job_ids: [9] }, 'GET /api/jobs/9': { id: 9, status: 'running' } }),
    );
    renderRoute('/projects/demo/episodes/4?stage=cast');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('tab', { name: '风格' }));
    await user.click(within(stageBar()).getByRole('button', { name: /剧本/ }));
    await user.click(await screen.findByRole('button', { name: '下一步：生成分镜' }));
    await waitFor(() => expect(document.querySelector('[data-stage-panel="cast"] [role="tab"][aria-selected="true"]')?.textContent).toBe('风格'));
  });

  it('角色阶段（三视图、推荐角色、造型方案都在）和成片阶段（片尾卡改了没保存）里，看得见的主按钮都只有阶段条的「下一步」', async () => {
    const visiblePrimaries = () =>
      Array.from(document.querySelectorAll('.primary')).filter((el) => !el.closest('[hidden]'));
    const turnaround = { look: 'full body', default_engine: 'h3_image', sheets: [], cells: [] };
    const opts = [{ label: '老练款', visual_prompt: 'buzz cut', core_features: '寸头' }];
    installFetch(
      routes({
        'GET /api/projects/demo/characters': [
          { ...CHARS[0], turnaround, design_options: opts, portraits: [], active_jobs: [] },
          { ...CHARS[1], turnaround, design_options: opts, portraits: [], active_jobs: [] },
        ],
        'GET /api/jobs/90': {
          id: 90, type: 'character.recommend', status: 'done',
          result: { characters: [{ name: '前台小李', description: '', visual_prompt: 'young clerk', core_features: '' }] },
        },
        'GET /api/projects/demo/end-card': { seconds: 3, background: '#000000', lines: [{ text: '片尾', font: 'Khmer UI', size: 60, bold: false }] },
      }),
    );
    renderRoute('/projects/demo/episodes/4?stage=cast&recommend=90');
    const user = userEvent.setup();
    expect(await screen.findByTestId('recommend-前台小李')).toBeTruthy();
    await waitFor(() => expect(screen.getAllByRole('region', { name: /的三视图/ })).toHaveLength(2));
    expect(screen.getAllByRole('button', { name: '用这个提示词抽卡' }).length).toBeGreaterThan(0);
    await waitFor(() => expect(visiblePrimaries().map((el) => el.textContent)).toEqual(['下一步：抽定妆照（1 个角色缺定妆照）']));

    await user.click(within(stageBar()).getByRole('button', { name: /成片/ }));
    await user.click(await screen.findByLabelText('片尾卡'));
    const line = await screen.findByLabelText('第 1 行文字');
    await user.type(line, '！');
    expect(visiblePrimaries()).toHaveLength(1);
    expect(visiblePrimaries()[0].classList.contains('stage-next')).toBe(true);
  });

  it('只有 ?shot= 没有 stage：停在首帧阶段', async () => {
    installFetch(routes());
    renderRoute('/projects/demo/episodes/4?shot=11');
    await waitFor(() => expect(where()).toBe('/projects/demo/episodes/4?stage=keyframes&shot=11'));
  });

  it('左栏：原文一行摘要和段落列表，点段落切到剧本阶段', async () => {
    installFetch(routes());
    renderRoute('/projects/demo/episodes/4?stage=keyframes');
    const rail = await screen.findByRole('complementary', { name: '剧本大纲与角色' });
    expect(within(rail).getByTestId('rail-source').textContent).toContain('深夜的直播间');
    expect(within(rail).getByTestId('rail-source').textContent).not.toContain('第二行');
    const block = await within(rail).findByRole('button', { name: /段落 1 · 直播间/ });
    expect(within(rail).getByRole('button', { name: /段落 2 · 未指定场景/ })).toBeTruthy();
    await userEvent.setup().click(block);
    expect(where()).toBe('/projects/demo/episodes/4?stage=script');
    expect(await screen.findByTestId('block-21')).toBeTruthy();
  });

  it('左栏角色：头像或首字、名字、定妆状态；点角色切到角色阶段并带上 ?char=', async () => {
    installFetch(routes());
    renderRoute('/projects/demo/episodes/4?stage=keyframes');
    const rail = await screen.findByRole('complementary', { name: '剧本大纲与角色' });
    const lin = await within(rail).findByRole('button', { name: /^林夏/ });
    expect(lin.textContent).toContain('已定妆');
    expect(lin.querySelector('img')?.getAttribute('src')).toBe('/media/p8.png');
    const zhou = within(rail).getByRole('button', { name: /^老周/ });
    expect(zhou.textContent).toContain('缺定妆照');
    expect(zhou.querySelector('.rail-avatar')?.textContent).toBe('老');
    await userEvent.setup().click(zhou);
    expect(where()).toBe('/projects/demo/episodes/4?stage=cast&char=9');
    expect(await screen.findByTestId('character-9')).toBeTruthy();
  });

  it('左栏可以收起，记在本机，下次打开还是收起的', async () => {
    installFetch(routes());
    const first = renderRoute('/projects/demo/episodes/4?stage=script');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '收起左栏' }));
    expect(screen.queryByRole('heading', { name: '剧本大纲' })).toBeNull();
    expect(screen.getByRole('button', { name: '展开左栏' })).toBeTruthy();
    first.unmount();
    renderRoute('/projects/demo/episodes/4?stage=script');
    expect(await screen.findByRole('button', { name: '展开左栏' })).toBeTruthy();
  });

  describe('首帧确认门（首帧 / 视频阶段的下一步）', () => {
    const posts = <T extends { method: string }>(calls: T[]) => calls.filter((c) => c.method === 'POST');
    const staleKf = (id: number): Slot =>
      makeSlot({ id, kind: 'first_frame', selected_candidate_id: id * 10, stale: true, candidates: [makeCandidate({ id: id * 10 })] });

    it('有缺少 / 过期的首帧：下一步只补这些（缺的不强制、过期的强制），不会去出视频；卡片用普通按钮', async () => {
      const ep = { ...EP, shots: [EP.shots[0], { ...EP.shots[1], slots: [staleKf(3)] }] };
      const calls = installFetch(routes({ 'GET /api/episodes/4': ep, 'POST /api/episodes/4/run': { job_ids: [51] }, 'GET /api/jobs/51': { id: 51, status: 'queued' } }));
      const { container } = renderRoute('/projects/demo/episodes/4?stage=keyframes');
      const next = await screen.findByRole('button', { name: '下一步：生成缺少/过期的首帧（2 张，耗时未知）' });
      await screen.findByTestId('shot-S01');
      expect(Array.from(container.querySelectorAll('.primary')).filter((el) => !el.closest('[hidden]'))).toEqual([next]);
      await userEvent.setup().click(next);
      await waitFor(() => expect(posts(calls)).toHaveLength(2));
      expect(posts(calls).map((c) => c.body)).toEqual([
        { stage: 'keyframes', candidates: 1, force: false, shots: ['11'] },
        { stage: 'keyframes', candidates: 1, force: true, shots: ['12'] },
      ]);
      expect(where()).toBe('/projects/demo/episodes/4?stage=keyframes');
    });

    it('首帧全部就绪：下一步「确认首帧，生成全部视频」，点了切到视频阶段并提交视频', async () => {
      const ep = { ...EP, shots: EP.shots.map((s, i) => ({ ...s, slots: [chosen(i + 1, 'first_frame')] })) };
      const calls = installFetch(routes({ 'GET /api/episodes/4': ep, 'POST /api/episodes/4/run': { job_ids: [52] }, 'GET /api/jobs/52': { id: 52, status: 'queued' } }));
      renderRoute('/projects/demo/episodes/4?stage=keyframes');
      await userEvent.setup().click(await screen.findByRole('button', { name: '下一步：确认首帧，生成全部视频（2 镜，耗时未知）' }));
      await waitFor(() => expect(posts(calls)).toHaveLength(1));
      expect(posts(calls)[0].body).toEqual({ stage: 'videos', candidates: 1, force: false, shots: ['11', '12'] });
      expect(where()).toBe('/projects/demo/episodes/4?stage=videos');
      expect(within(stageBar()).getByRole('button', { name: /视频/ }).getAttribute('aria-current')).toBe('step');
    });

    it('视频也都就绪：下一步「去成片」', async () => {
      const ep = { ...EP, shots: EP.shots.map((s, i) => ({ ...s, slots: [chosen(i * 2 + 1, 'first_frame'), chosen(i * 2 + 2, 'video')] })) };
      installFetch(routes({ 'GET /api/episodes/4': ep }));
      renderRoute('/projects/demo/episodes/4?stage=videos');
      await userEvent.setup().click(await screen.findByRole('button', { name: '下一步：去成片' }));
      expect(where()).toBe('/projects/demo/episodes/4?stage=export');
    });

    it('勾选了镜头：下一步变「对选中的 N 镜生成…」；有的已有首帧时缺的生成、有的重拍（强制），确认里写清楚，提交后清掉勾选', async () => {
      const ep = { ...EP, shots: [{ ...EP.shots[0], slots: [chosen(1, 'first_frame')] }, EP.shots[1]] };
      const calls = installFetch(routes({ 'GET /api/episodes/4': ep, 'POST /api/episodes/4/run': { job_ids: [53] }, 'GET /api/jobs/53': { id: 53, status: 'queued' } }));
      renderRoute('/projects/demo/episodes/4?stage=keyframes');
      const user = userEvent.setup();
      await user.click(await screen.findByRole('checkbox', { name: '选择 S01' }));
      await user.click(screen.getByRole('checkbox', { name: '选择 S02' }));
      await user.click(screen.getByRole('button', { name: '下一步：对选中的 2 镜生成…' }));
      const dlg = await screen.findByRole('dialog', { name: '对选中的 2 镜生成 / 重拍首帧？' });
      expect(dlg.textContent).toContain('1 镜还没有首帧，生成；1 镜已有首帧，重拍');
      await user.click(within(dlg).getByRole('button', { name: '继续提交' }));
      await waitFor(() => expect(posts(calls)).toHaveLength(2));
      expect(posts(calls).map((c) => c.body)).toEqual([
        { stage: 'keyframes', candidates: 1, force: false, shots: ['12'] },
        { stage: 'keyframes', candidates: 1, force: true, shots: ['11'] },
      ]);
      await waitFor(() => expect((screen.getByRole('checkbox', { name: '选择 S01' }) as HTMLInputElement).checked).toBe(false));
      expect(screen.getByRole('button', { name: /^下一步：生成缺少\/过期的首帧/ })).toBeTruthy();
    });

    it('勾选的都已有首帧：下一步「对选中的 N 镜重拍…」，强制', async () => {
      const ep = { ...EP, shots: EP.shots.map((s, i) => ({ ...s, slots: [chosen(i + 1, 'first_frame')] })) };
      const calls = installFetch(routes({ 'GET /api/episodes/4': ep, 'POST /api/episodes/4/run': { job_ids: [54] }, 'GET /api/jobs/54': { id: 54, status: 'queued' } }));
      renderRoute('/projects/demo/episodes/4?stage=keyframes');
      const user = userEvent.setup();
      await user.click(await screen.findByRole('checkbox', { name: '选择第 1 段的全部镜头' }));
      await user.click(screen.getByRole('button', { name: '下一步：对选中的 1 镜重拍…' }));
      await user.click(within(await screen.findByRole('dialog', { name: '对选中的 1 镜重拍首帧？' })).getByRole('button', { name: '继续提交' }));
      await waitFor(() => expect(posts(calls)).toHaveLength(1));
      expect(posts(calls)[0].body).toEqual({ stage: 'keyframes', candidates: 1, force: true, shots: ['11'] });
    });

    it('视频阶段卡片「先出首帧」：切回首帧阶段并打开这个镜头', async () => {
      installFetch(routes());
      renderRoute('/projects/demo/episodes/4?stage=videos');
      await userEvent.setup().click(await screen.findByRole('button', { name: '先出首帧 S02' }));
      expect(where()).toBe('/projects/demo/episodes/4?stage=keyframes&shot=12');
      expect(await screen.findByRole('dialog', { name: '镜头 S02 详情' })).toBeTruthy();
    });

    it('首帧都在生成中：下一步禁用，写明在等', async () => {
      const busy = { active_jobs: [{ id: 70, type: 'image.keyframe', status: 'running' as const }] };
      const ep = { ...EP, shots: EP.shots.map((s) => ({ ...s, ...busy })) };
      installFetch(routes({ 'GET /api/episodes/4': ep }));
      renderRoute('/projects/demo/episodes/4?stage=keyframes');
      const next = await screen.findByRole('button', { name: '下一步：等首帧生成完（2 镜生成中）' });
      expect((next as HTMLButtonElement).disabled).toBe(true);
    });
  });
});
