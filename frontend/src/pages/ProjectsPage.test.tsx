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
      style_preset: 'live_action',
    });
  });

  it('新建项目：选风格预设会预选它推荐的引擎，用户还能改', async () => {
    const preset = (key: string, image: string | null, video: string | null) => ({
      key, label: key, description: '', negative: '', image_engines: [], video_engines: [],
      has_storyboard_guidance: false, image_engine: image, video_engine: video,
    });
    const calls = installFetch({
      'GET /api/projects': [],
      'GET /api/engines': ENGINES,
      'GET /api/style-presets': [preset('live_action', 'zimage', 'h3_video'), preset('anime', 'h3_image', 'wan22_i2v'),
        preset('commercial', 'gpt_image', null)],
      'POST /api/projects': makeProject({ id: 2, slug: '动漫', title: '动漫', style_preset: 'anime' }),
    });
    renderAt(<ProjectsPage />, { route: '/', path: '/' });
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole('button', { name: '新建项目' }))[0]);
    await screen.findByRole('option', { name: 'Z-Image 快速' });
    const group = screen.getByRole('group', { name: '风格预设' });
    expect((within(group).getByRole('radio', { name: /真人写实/ }) as HTMLInputElement).checked).toBe(true);
    expect(within(group).getAllByRole('radio')).toHaveLength(6);
    expect(within(group).getByText(/赛璐璐/)).toBeTruthy();
    // 推荐的引擎不可用（gpt_image 没启用）：不动原来的选择
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/api/style-presets'))).toBe(true));
    await user.click(within(group).getByRole('radio', { name: /商业广告/ }));
    expect((screen.getByLabelText('图像引擎') as HTMLSelectElement).value).toBe('zimage');
    await user.click(within(group).getByRole('radio', { name: /日系动漫/ }));
    expect((screen.getByLabelText('图像引擎') as HTMLSelectElement).value).toBe('h3_image');
    expect((screen.getByLabelText('视频引擎') as HTMLSelectElement).value).toBe('wan22_i2v');
    await user.selectOptions(screen.getByLabelText('视频引擎'), 'h3_video');
    await user.type(screen.getByLabelText('项目标题'), '动漫');
    await user.click(screen.getByRole('button', { name: '创建项目' }));
    await waitFor(() => expect(where()).toBe('/projects/动漫'));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({
      title: '动漫', aspect: '9:16', image_engine: 'h3_image', video_engine: 'h3_video', style_preset: 'anime',
    });
  });

  it('项目设置：改风格预设，提示要重新生成剧本才会按新预设写', async () => {
    const calls = installFetch({
      'GET /api/projects': [makeProject()],
      'GET /api/projects/demo/overview': { episode_count: 0, cover_url: null, episodes: [] },
      'PATCH /api/projects/demo': makeProject({ style_preset: 'guofeng' }),
    });
    renderAt(<ProjectsPage />, { route: '/', path: '/' });
    const user = userEvent.setup();
    const card = await screen.findByTestId('project-demo');
    expect(card.textContent).toContain('真人写实');
    await user.click(within(card).getByRole('button', { name: '更多操作 demo' }));
    await user.click(screen.getByRole('menuitem', { name: '项目设置' }));
    const dialog = screen.getByRole('dialog', { name: '项目设置' });
    const save = within(dialog).getByRole('button', { name: '保存' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(within(dialog).queryByRole('note')).toBeNull();
    await user.click(within(dialog).getByRole('radio', { name: /国风漫画/ }));
    expect(within(dialog).getByRole('note').textContent).toContain('重新生成剧本');
    await user.click(save);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ style_preset: 'guofeng' });
  });

  it('新建项目：引擎默认按设置页「新项目的图片/视频来源」；设置没加载完不能提交', async () => {
    let release: (v: unknown) => void = () => {};
    const gate = new Promise((r) => (release = r));
    installFetch({
      'GET /api/projects': [],
      'GET /api/engines': { image: [...ENGINES.image, { name: 'krea2', family: 'krea2', caps: {} }], video: ENGINES.video },
      'GET /api/settings': async () => {
        await gate;
        return {
          image_engine: { value: 'krea2', effective: 'krea2', fallback: 'zimage', source: 'settings' },
          video_engine: { value: null, effective: 'h3_video', fallback: 'h3_video', source: 'default' },
          steps: [],
        };
      },
    });
    renderAt(<ProjectsPage />, { route: '/', path: '/' });
    const user = userEvent.setup();
    const empty = (await screen.findByText(/还没有项目/)).closest('section')!;
    await user.click(within(empty).getByRole('button', { name: '新建项目' }));
    await user.type(screen.getByLabelText('项目标题'), '新片');
    expect((screen.getByRole('button', { name: '创建项目' }) as HTMLButtonElement).disabled).toBe(true);
    release(null);
    await waitFor(() => expect((screen.getByLabelText('图像引擎') as HTMLSelectElement).value).toBe('krea2'));
    expect((screen.getByLabelText('视频引擎') as HTMLSelectElement).value).toBe('h3_video');
    expect((screen.getByRole('button', { name: '创建项目' }) as HTMLButtonElement).disabled).toBe(false);
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

  it('列出剧集；新建剧集提交原文和目标时长，跳到这一集的工作台', async () => {
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
    // 每集一个入口：打开单集工作台（停在下一步所在的阶段）
    expect(screen.getByRole('link', { name: '打开第 1 集' }).getAttribute('href')).toBe('/projects/demo/episodes/3');
    expect(screen.queryByRole('link', { name: '去导演台' })).toBeNull();
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
    await waitFor(() => expect(where()).toBe('/projects/demo/episodes/5'));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({
      title: '',
      source_text: '深夜的直播间',
      target_seconds: 10,
    });
  });

  it('项目总览：卡片带封面、集数和最新一集的工作台入口；没有首帧时用首字占位', async () => {
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
    expect(screen.getByRole('link', { name: '工作台 · 第 2 集' }).getAttribute('href')).toBe('/projects/demo/episodes/7');
    const other = screen.getByTestId('project-b');
    expect(other.querySelector('img')).toBeNull();
    expect(other.querySelector('.cover-gen')?.textContent).toBe('夜');
    // 继续制作：没有上次记录时取最新一集
    const cont = screen.getByRole('region', { name: '继续制作' });
    expect(cont.textContent).toContain('第 2 集');
    // 一个入口：打开这一集的工作台（工作台自己停在下一步所在的阶段）
    const open = within(cont).getByRole('link', { name: '继续制作' });
    expect(open.getAttribute('href')).toBe('/projects/demo/episodes/7');
    expect(open.classList.contains('primary')).toBe(true);
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
