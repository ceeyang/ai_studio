import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFetch } from '../test/fetch';
import { makeProject } from '../test/factories';
import { renderAt } from '../test/render';
import { ProjectsPage } from './ProjectsPage';

const ENGINES = {
  image: [
    { name: 'h3_image', family: 'h3', caps: {} },
    { name: 'zimage', family: 'zimage', caps: {} },
  ],
  video: [
    { name: 'h3_video', family: 'h3', caps: { has_audio: true } },
    { name: 'wan22_i2v', family: 'wan', caps: { has_audio: false } },
  ],
};

function where(): string {
  return decodeURIComponent(screen.getByTestId('location').textContent ?? '');
}

describe('ProjectsPage', () => {
  it('新建项目：默认选 zimage / h3_video，提交后跳到项目页', async () => {
    const calls = installFetch({
      'GET /api/projects': [],
      'GET /api/engines': ENGINES,
      'POST /api/projects': makeProject({ id: 2, slug: '冒烟', title: '冒烟' }),
    });
    renderAt(<ProjectsPage />, { route: '/', path: '/' });
    const user = userEvent.setup();
    const empty = (await screen.findByText(/还没有项目/)).closest('section')!;
    expect(screen.queryByLabelText('项目标题')).toBeNull();
    // 空状态里就有新建按钮
    await user.click(within(empty).getByRole('button', { name: '新建项目' }));
    const dialog = screen.getByRole('dialog', { name: '新建项目' });
    await screen.findByRole('option', { name: 'Z-Image 快速' });
    expect((screen.getByLabelText('图像引擎') as HTMLSelectElement).value).toBe('zimage');
    expect((screen.getByLabelText('视频引擎') as HTMLSelectElement).value).toBe('h3_video');
    expect(document.activeElement).toBe(screen.getByLabelText('项目标题'));
    expect((screen.getByRole('button', { name: '创建项目' }) as HTMLButtonElement).disabled).toBe(true);
    await user.type(screen.getByLabelText('项目标题'), '冒烟');
    expect(dialog.contains(screen.getByRole('button', { name: '创建项目' }))).toBe(true);
    await user.click(screen.getByRole('button', { name: '创建项目' }));
    await waitFor(() => expect(where()).toBe('/projects/冒烟'));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({
      title: '冒烟',
      aspect: '9:16',
      image_engine: 'zimage',
      video_engine: 'h3_video',
    });
  });

  it('新建项目失败时显示后端的原因', async () => {
    installFetch({
      'GET /api/projects': [],
      'GET /api/engines': ENGINES,
      'POST /api/projects': () => new Response(JSON.stringify({ detail: '项目 demo 已存在' }), { status: 409 }),
    });
    renderAt(<ProjectsPage />, { route: '/', path: '/' });
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole('button', { name: '新建项目' }))[0]);
    await screen.findByRole('option', { name: 'Z-Image 快速' });
    await user.type(screen.getByLabelText('项目标题'), 'demo');
    await user.click(screen.getByRole('button', { name: '创建项目' }));
    expect(await screen.findByText('项目 demo 已存在（HTTP 409）')).toBeTruthy();
  });

  it('列出剧集；新建剧集提交原文和目标时长，跳到剧本页', async () => {
    const calls = installFetch({
      'GET /api/projects': [makeProject()],
      'GET /api/engines': ENGINES,
      'GET /api/projects/demo': makeProject(),
      'GET /api/projects/demo/episodes': [
        { id: 3, project_id: 1, number: 1, title: '旧的一集', source_text: 'x', target_seconds: 60, status: 'storyboarded' },
      ],
      'GET /api/projects/demo/overview': {
        episode_count: 1,
        cover_url: '/media/demo/media/first_frame/a.png',
        episodes: [
          { id: 3, number: 1, title: '旧的一集', status: 'storyboarded', shot_count: 4, export_count: 2, cover_url: '/media/demo/media/first_frame/a.png' },
        ],
      },
      'GET /api/projects/demo/characters': [],
      'GET /api/projects/demo/locations': [],
      'GET /api/projects/demo/style': null,
      'POST /api/projects/demo/episodes': {
        id: 5, project_id: 1, number: 2, title: '', source_text: '深夜的直播间', target_seconds: 10, status: 'draft',
      },
    });
    renderAt(<ProjectsPage />, { route: '/projects/demo', path: '/projects/:slug' });
    const user = userEvent.setup();
    expect(await screen.findByText(/旧的一集/)).toBeTruthy();
    expect(screen.getByText(/已分镜/)).toBeTruthy();
    expect(screen.getByRole('link', { name: '去导演台' }).getAttribute('href')).toBe('/projects/demo/episodes/3/director');
    expect(screen.getByRole('link', { name: '去剧本' }).getAttribute('href')).toBe('/projects/demo/episodes/3/script');
    // 已经分镜的集：主按钮是去导演台
    expect(screen.getByRole('link', { name: '去导演台' }).classList.contains('primary')).toBe(true);
    expect(screen.getByRole('link', { name: '去剧本' }).classList.contains('primary')).toBe(false);
    expect(screen.getByRole('link', { name: /全部项目/ }).getAttribute('href')).toBe('/');
    expect(await screen.findByText('2 个成片')).toBeTruthy();
    expect(screen.getByText('4 个镜头')).toBeTruthy();
    expect(screen.queryByLabelText('原文或梗概')).toBeNull();
    await user.click(screen.getByRole('button', { name: '新建剧集' }));
    expect(screen.getByRole('dialog', { name: '新建剧集 · 第 2 集' })).toBeTruthy();
    const btn = screen.getByRole('button', { name: '创建剧集' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    // 默认 30 秒
    expect((screen.getByLabelText('目标时长（秒）') as HTMLInputElement).value).toBe('30');
    await user.type(screen.getByLabelText('原文或梗概'), '深夜的直播间');
    // 原文非空时按 Esc 先问放弃修改，不直接关
    await user.keyboard('{Escape}');
    expect(screen.getByText('放弃修改？')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '继续编辑' }));
    const secs = screen.getByLabelText('目标时长（秒）');
    expect(secs.getAttribute('min')).toBe('5');
    expect(secs.getAttribute('max')).toBe('3600');
    // 和剧本页一样 5–3600 秒：太短的不让建（不然剧本页什么都存不了）
    await user.clear(secs);
    await user.type(secs, '4');
    expect(btn.disabled).toBe(true);
    expect(screen.getByText('目标时长要在 5–3600 秒之间')).toBeTruthy();
    await user.clear(secs);
    await user.type(secs, '10');
    expect(screen.queryByText('目标时长要在 5–3600 秒之间')).toBeNull();
    await user.click(btn);
    await waitFor(() => expect(where()).toBe('/projects/demo/episodes/5/script'));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({
      title: '',
      source_text: '深夜的直播间',
      target_seconds: 10,
    });
  });

  it('项目总览：卡片带封面、集数和最新一集的导演台入口；没有首帧时用首字占位', async () => {
    installFetch({
      'GET /api/projects': [makeProject(), makeProject({ id: 2, slug: 'b', title: '夜班' })],
      'GET /api/projects/demo/overview': {
        episode_count: 2,
        cover_url: '/media/demo/c.png',
        episodes: [
          { id: 3, number: 1, title: '', status: 'exported', shot_count: 2, export_count: 1, cover_url: null },
          { id: 7, number: 2, title: '', status: 'scripted', shot_count: 0, export_count: 0, cover_url: '/media/demo/c.png' },
        ],
      },
      'GET /api/projects/b/overview': { episode_count: 0, cover_url: null, episodes: [] },
    });
    const { container } = renderAt(<ProjectsPage />, { route: '/', path: '/' });
    const card = await screen.findByTestId('project-demo');
    await waitFor(() => expect(card.querySelector('img')?.getAttribute('src')).toBe('/media/demo/c.png'));
    expect(card.textContent).toContain('2 集');
    expect(card.textContent).toContain('已出剧本');
    expect(screen.getByRole('link', { name: '导演台 · 第 2 集' }).getAttribute('href')).toBe('/projects/demo/episodes/7/director');
    const other = screen.getByTestId('project-b');
    expect(other.querySelector('img')).toBeNull();
    expect(other.querySelector('.cover-gen')?.textContent).toBe('夜');
    // 继续制作：没有上次记录时取最新一集
    const cont = screen.getByRole('region', { name: '继续制作' });
    expect(cont.textContent).toContain('第 2 集');
    // 这一集还没分镜：主按钮是剧本，不把人带到空的导演台
    expect(within(cont).getByRole('link', { name: '剧本' }).classList.contains('primary')).toBe(true);
    expect(within(cont).getByRole('link', { name: '打开导演台' }).classList.contains('primary')).toBe(false);
    expect(container.querySelector('.stat-tiles')?.textContent).toContain('剧集2');
  });

  it('新建项目对话框：Esc 关闭', async () => {
    installFetch({ 'GET /api/projects': [], 'GET /api/engines': ENGINES });
    renderAt(<ProjectsPage />, { route: '/', path: '/' });
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole('button', { name: '新建项目' }))[0]);
    expect(screen.getByRole('dialog')).toBeTruthy();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('项目卡：引擎写中文名；默认不显示已归档，底部可以展开「显示已归档（N）」', async () => {
    const calls = installFetch({
      'GET /api/projects?archived=1': [makeProject(), makeProject({ id: 2, slug: 'old', title: '旧剧', archived: true })],
      'GET /api/projects/demo/overview': { episode_count: 0, cover_url: null, episodes: [] },
      'GET /api/projects/old/overview': { episode_count: 0, cover_url: null, episodes: [] },
    });
    renderAt(<ProjectsPage />, { route: '/', path: '/' });
    const user = userEvent.setup();
    const card = await screen.findByTestId('project-demo');
    expect(card.textContent).toContain('Z-Image 快速');
    expect(card.textContent).toContain('H3 视频（有声）');
    expect(screen.queryByTestId('project-old')).toBeNull();
    expect(calls.some((c) => c.url === '/api/projects?archived=1')).toBe(true);
    await user.click(screen.getByRole('button', { name: '显示已归档（1）' }));
    expect((await screen.findByTestId('project-old')).textContent).toContain('已归档');
    await user.click(screen.getByRole('button', { name: '收起已归档' }));
    expect(screen.queryByTestId('project-old')).toBeNull();
  });

  it('项目卡「⋯」：改名提交新标题；归档先确认再提交', async () => {
    const calls = installFetch({
      'GET /api/projects?archived=1': [makeProject()],
      'GET /api/projects/demo/overview': { episode_count: 0, cover_url: null, episodes: [] },
      'PATCH /api/projects/demo': (c: { body: unknown }) => makeProject({ ...(c.body as object) }),
    });
    renderAt(<ProjectsPage />, { route: '/', path: '/' });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '更多操作 demo' }));
    await user.click(screen.getByRole('menuitem', { name: '改名' }));
    const dlg = screen.getByRole('dialog', { name: '项目改名' });
    const input = within(dlg).getByLabelText('项目标题');
    await user.clear(input);
    await user.type(input, '深夜直播');
    await user.click(within(dlg).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '项目改名' })).toBeNull());
    expect(calls.filter((c) => c.method === 'PATCH').map((c) => c.body)).toEqual([{ title: '深夜直播' }]);

    await user.click(screen.getByRole('button', { name: '更多操作 demo' }));
    await user.click(screen.getByRole('menuitem', { name: '归档' }));
    const confirmDlg = screen.getByRole('dialog', { name: '归档项目「demo」？' });
    await user.click(within(confirmDlg).getByRole('button', { name: '归档' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(2));
    expect(calls.filter((c) => c.method === 'PATCH')[1].body).toEqual({ archived: true });
  });

  it('项目详情：「⋯」菜单、目录名收进「高级」；剧集卡可以删除（写明镜头数），后端拒绝时说原因', async () => {
    const calls = installFetch({
      'GET /api/projects/demo': makeProject({ archived: true }),
      'GET /api/projects/demo/episodes': [
        { id: 3, project_id: 1, number: 1, title: '旧的一集', source_text: 'x', target_seconds: 60, status: 'storyboarded' },
      ],
      'GET /api/projects/demo/overview': {
        episode_count: 1, cover_url: null,
        episodes: [{ id: 3, number: 1, title: '旧的一集', status: 'storyboarded', shot_count: 4, export_count: 2, cover_url: null }],
      },
      'GET /api/projects/demo/characters': [],
      'GET /api/projects/demo/locations': [],
      'GET /api/projects/demo/style': null,
      'PATCH /api/projects/demo': makeProject({ archived: false }),
      'DELETE /api/episodes/3': () => new Response(JSON.stringify({ detail: '这一集还有排队或执行中的任务，先取消任务再删除' }), { status: 409 }),
    });
    renderAt(<ProjectsPage />, { route: '/projects/demo', path: '/projects/:slug' });
    const user = userEvent.setup();
    await screen.findByText(/旧的一集/);
    const adv = screen.getByText('高级').closest('details')!;
    expect(adv.textContent).toContain('demo');
    expect(screen.getByText('已归档')).toBeTruthy();
    expect(screen.getByText('Z-Image 快速')).toBeTruthy();
    // 归档了的项目：菜单里是「取消归档」
    await user.click(screen.getByRole('button', { name: '更多操作 demo' }));
    await user.click(screen.getByRole('menuitem', { name: '取消归档' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ archived: false });

    await user.click(screen.getByRole('button', { name: '更多操作 第 1 集' }));
    await user.click(screen.getByRole('menuitem', { name: '删除这一集' }));
    const dlg = screen.getByRole('dialog', { name: '删除第 1 集「旧的一集」？' });
    expect(dlg.textContent).toContain('4 个镜头');
    await user.click(within(dlg).getByRole('button', { name: '删除' }));
    expect(await screen.findByText('这一集还有排队或执行中的任务，先取消任务再删除（HTTP 409）')).toBeTruthy();
    expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/episodes/3')).toBe(true);
  });
});
