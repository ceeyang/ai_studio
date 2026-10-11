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
  it('首页有「从一个点子开始」，去 /start；空状态里也有', async () => {
    installFetch({ 'GET /api/projects': [], 'GET /api/engines': ENGINES });
    renderAt(<ProjectsPage />, { route: '/', path: '/' });
    const empty = (await screen.findByText(/还没有项目/)).closest('section')!;
    const links = screen.getAllByRole('link', { name: '从一个点子开始' });
    expect(links).toHaveLength(2);
    expect(links.every((a) => a.getAttribute('href') === '/start')).toBe(true);
    expect(within(empty).getByRole('link', { name: '从一个点子开始' })).toBeTruthy();
    await userEvent.setup().click(links[0]);
    await waitFor(() => expect(where()).toBe('/start'));
  });

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

  it('新建项目：不动预设也用默认预设推荐的引擎；手动改过的引擎换预设时不覆盖', async () => {
    const preset = (key: string, image: string | null, video: string | null) => ({
      key, label: key, description: '', negative: '', image_engines: [], video_engines: [],
      has_storyboard_guidance: false, image_engine: image, video_engine: video,
    });
    const calls = installFetch({
      'GET /api/projects': [],
      'GET /api/engines': ENGINES,
      'GET /api/style-presets': [preset('live_action', 'h3_image', 'h3_video'), preset('anime', 'h3_image', 'wan22_i2v')],
      'POST /api/projects': makeProject({ id: 2, slug: '写实', title: '写实' }),
    });
    renderAt(<ProjectsPage />, { route: '/', path: '/' });
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole('button', { name: '新建项目' }))[0]);
    await screen.findByRole('option', { name: 'Z-Image 快速' });
    // 默认预设「真人写实」推荐 h3_image：没点预设也预选它（以前要切一下预设才填）
    await waitFor(() => expect((screen.getByLabelText('图像引擎') as HTMLSelectElement).value).toBe('h3_image'));
    // 用户手动改成 zimage，再换预设：手动选的不被覆盖；没手动改的视频引擎跟着新预设走
    await user.selectOptions(screen.getByLabelText('图像引擎'), 'zimage');
    await user.click(within(screen.getByRole('group', { name: '风格预设' })).getByRole('radio', { name: /日系动漫/ }));
    expect((screen.getByLabelText('图像引擎') as HTMLSelectElement).value).toBe('zimage');
    expect((screen.getByLabelText('视频引擎') as HTMLSelectElement).value).toBe('wan22_i2v');
    await user.type(screen.getByLabelText('项目标题'), '写实');
    await user.click(screen.getByRole('button', { name: '创建项目' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({ image_engine: 'zimage', video_engine: 'wan22_i2v', style_preset: 'anime' });
  });

  it('新建项目：设置页明确设了图片引擎、且在预设推荐的引擎里时用设置的', async () => {
    const calls = installFetch({
      'GET /api/projects': [],
      'GET /api/engines': ENGINES,
      'GET /api/settings': {
        image_engine: { value: 'zimage', effective: 'zimage', fallback: 'zimage', source: 'settings' },
        video_engine: { value: null, effective: 'h3_video', fallback: 'h3_video', source: 'default' },
        steps: [],
      },
      'GET /api/style-presets': [{
        key: 'live_action', label: '真人写实', description: '', negative: '', image_engines: ['h3_image', 'zimage'],
        video_engines: ['h3_video'], has_storyboard_guidance: false, image_engine: 'h3_image', video_engine: 'h3_video',
      }],
    });
    renderAt(<ProjectsPage />, { route: '/', path: '/' });
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole('button', { name: '新建项目' }))[0]);
    await screen.findByRole('option', { name: 'Z-Image 快速' });
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/api/style-presets'))).toBe(true));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/api/settings'))).toBe(true));
    await new Promise((r) => setTimeout(r, 20));
    expect((screen.getByLabelText('图像引擎') as HTMLSelectElement).value).toBe('zimage');
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

  it('项目设置：镜头节奏和自动重拍，只发改了的字段；节奏改回跟随视频引擎发 0', async () => {
    const calls = installFetch({
      'GET /api/projects': [makeProject({ shot_seconds: 4, auto_retry_video: false })],
      'GET /api/projects/demo/overview': { episode_count: 0, cover_url: null, episodes: [] },
      'PATCH /api/projects/demo': makeProject({ shot_seconds: 3, auto_retry_video: true }),
    });
    renderAt(<ProjectsPage />, { route: '/', path: '/' });
    const user = userEvent.setup();
    const card = await screen.findByTestId('project-demo');
    await user.click(within(card).getByRole('button', { name: '更多操作 demo' }));
    await user.click(screen.getByRole('menuitem', { name: '项目设置' }));
    const dialog = screen.getByRole('dialog', { name: '项目设置' });
    const pace = within(dialog).getByRole('combobox', { name: '镜头节奏' }) as HTMLSelectElement;
    expect(pace.value).toBe('4');
    expect(within(dialog).getAllByRole('button').filter((b) => b.classList.contains('primary'))).toHaveLength(1);
    await user.selectOptions(pace, '3');
    await user.click(within(dialog).getByRole('checkbox', { name: /自动重拍一次/ }));
    await user.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ shot_seconds: 3, auto_retry_video: true });
  });

  it('项目设置：节奏改回「跟随视频引擎」发 0', async () => {
    const calls = installFetch({
      'GET /api/projects': [makeProject({ shot_seconds: 4 })],
      'GET /api/projects/demo/overview': { episode_count: 0, cover_url: null, episodes: [] },
      'PATCH /api/projects/demo': makeProject(),
    });
    renderAt(<ProjectsPage />, { route: '/', path: '/' });
    const user = userEvent.setup();
    const card = await screen.findByTestId('project-demo');
    await user.click(within(card).getByRole('button', { name: '更多操作 demo' }));
    await user.click(screen.getByRole('menuitem', { name: '项目设置' }));
    const dialog = screen.getByRole('dialog', { name: '项目设置' });
    await user.selectOptions(within(dialog).getByRole('combobox', { name: '镜头节奏' }), '0');
    await user.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ shot_seconds: 0 }));
  });

  it('新建项目：说明首帧引擎要能吃参考图；选了不吃参考图的引擎再提醒一句；预设推荐的定妆照引擎写出来', async () => {
    installFetch({
      'GET /api/projects': [],
      'GET /api/engines': {
        image: [
          { name: 'h3_image', family: 'h3', caps: { max_refs: 9 } },
          { name: 'krea2', family: 'krea2', caps: { max_refs: 0 } },
        ],
        video: ENGINES.video,
      },
      'GET /api/style-presets': [{
        key: 'live_action', label: '真人写实', description: '', negative: '', image_engines: ['h3_image', 'krea2'],
        video_engines: ['h3_video'], portrait_engines: ['krea2'], has_storyboard_guidance: false,
        image_engine: 'h3_image', video_engine: 'h3_video', portrait_engine: 'krea2',
      }],
    });
    renderAt(<ProjectsPage />, { route: '/', path: '/' });
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole('button', { name: '新建项目' }))[0]);
    const hint = await screen.findByTestId('engine-hint');
    expect(hint.textContent).toContain('首帧引擎要能吃参考图才能锁脸、用场景图');
    await waitFor(() => expect(screen.getByTestId('engine-hint').textContent).toContain('定妆照默认用 Krea2 人像（这个风格预设推荐的'));
    expect(screen.getByTestId('engine-hint').textContent).not.toContain('不吃参考图');
    await user.selectOptions(screen.getByLabelText('图像引擎'), 'krea2');
    expect(screen.getByTestId('engine-hint').textContent).toContain('不吃参考图');
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

  it('新建剧集：AI 写梗概填入空框，框里有字后变成「AI 续写」只往后追加；出错显示原因不丢字；写的时候能停', async () => {
    const replies: (unknown | (() => unknown))[] = [
      { text: '订婚宴上，林夏当众撕了婚书。', mode: 'new' },
      { text: '未婚夫冷笑：你撕的是假的。', mode: 'continue' },
      new Response(JSON.stringify({ detail: 'AI 写梗概失败：连不上 fake' }), { status: 502 }),
      () => new Promise(() => {}),
    ];
    const calls = installFetch({
      'GET /api/projects': [makeProject()],
      'GET /api/engines': ENGINES,
      'GET /api/projects/demo': makeProject({ style_preset: 'guofeng' }),
      'GET /api/projects/demo/episodes': [
        { id: 3, project_id: 1, number: 1, title: '旧的一集', source_text: 'x', target_seconds: 60, status: 'draft' },
      ],
      'GET /api/projects/demo/overview': { episode_count: 1, cover_url: null, episodes: [] },
      'GET /api/projects/demo/characters': [],
      'GET /api/projects/demo/locations': [],
      'GET /api/projects/demo/style': null,
      'POST /api/projects/demo/episode-idea': () => {
        const r = replies.shift();
        return typeof r === 'function' ? (r as () => unknown)() : r;
      },
    });
    renderAt(<ProjectsPage />, { route: '/projects/demo', path: '/projects/:slug' });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '新建剧集' }));
    const box = screen.getByLabelText('原文或梗概') as HTMLTextAreaElement;
    const dialog = screen.getByRole('dialog', { name: '新建剧集 · 第 2 集' });
    expect(await within(dialog).findByText(/风格预设（国风漫画）/)).toBeTruthy();
    const aiBtn = screen.getByRole('button', { name: 'AI 写梗概' }) as HTMLButtonElement;
    expect(aiBtn.classList.contains('primary')).toBe(false);
    await user.type(screen.getByLabelText('本集标题'), '撕婚书');
    await user.click(aiBtn);
    await waitFor(() => expect(box.value).toBe('订婚宴上，林夏当众撕了婚书。'));
    const ideaCalls = () => calls.filter((c) => c.url.endsWith('/episode-idea'));
    expect(ideaCalls()[0].body).toEqual({ title: '撕婚书', target_seconds: 30, number: 2, text: '' });
    // 框里有字：按钮变「AI 续写」，结果接在后面（先换行），不覆盖
    await user.click(screen.getByRole('button', { name: 'AI 续写' }));
    await waitFor(() => expect(box.value).toBe('订婚宴上，林夏当众撕了婚书。\n未婚夫冷笑：你撕的是假的。'));
    expect(ideaCalls()[1].body).toMatchObject({ text: '订婚宴上，林夏当众撕了婚书。' });
    // 出错：对话框里显示原因，已有文字不丢
    await user.click(screen.getByRole('button', { name: 'AI 续写' }));
    expect(await screen.findByText(/AI 写梗概失败：连不上 fake/)).toBeTruthy();
    expect(box.value).toBe('订婚宴上，林夏当众撕了婚书。\n未婚夫冷笑：你撕的是假的。');
    // 写的时候显示「正在写…」并禁用，可以停
    await user.click(screen.getByRole('button', { name: 'AI 续写' }));
    const busy = await screen.findByRole('button', { name: '正在写…' });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText(/AI 写梗概失败/)).toBeNull();
    expect((screen.getByRole('button', { name: '创建剧集' }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole('button', { name: '停止' }));
    expect(screen.getByRole('button', { name: 'AI 续写' })).toBeTruthy();
    expect(box.value).toBe('订婚宴上，林夏当众撕了婚书。\n未婚夫冷笑：你撕的是假的。');
  });

  it('新建剧集：第一集标题和框都空时 AI 写梗概不能点', async () => {
    installFetch({
      'GET /api/projects': [makeProject()],
      'GET /api/engines': ENGINES,
      'GET /api/projects/demo': makeProject(),
      'GET /api/projects/demo/episodes': [],
      'GET /api/projects/demo/overview': { episode_count: 0, cover_url: null, episodes: [] },
      'GET /api/projects/demo/characters': [],
      'GET /api/projects/demo/locations': [],
      'GET /api/projects/demo/style': null,
    });
    renderAt(<ProjectsPage />, { route: '/projects/demo', path: '/projects/:slug' });
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole('button', { name: '新建剧集' }))[0]);
    const btn = screen.getByRole('button', { name: 'AI 写梗概' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    await user.type(screen.getByLabelText('原文或梗概'), '女主重生');
    expect((screen.getByRole('button', { name: 'AI 续写' }) as HTMLButtonElement).disabled).toBe(false);
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
