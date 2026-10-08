import { screen, waitFor } from '@testing-library/react';
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
    await screen.findByRole('option', { name: 'zimage' });
    expect((screen.getByLabelText('图像引擎') as HTMLSelectElement).value).toBe('zimage');
    expect((screen.getByLabelText('视频引擎') as HTMLSelectElement).value).toBe('h3_video');
    expect((screen.getByRole('button', { name: '新建项目' }) as HTMLButtonElement).disabled).toBe(true);
    await user.type(screen.getByLabelText('项目标题'), '冒烟');
    await user.click(screen.getByRole('button', { name: '新建项目' }));
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
    await screen.findByRole('option', { name: 'zimage' });
    await user.type(screen.getByLabelText('项目标题'), 'demo');
    await user.click(screen.getByRole('button', { name: '新建项目' }));
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
      'POST /api/projects/demo/episodes': {
        id: 5, project_id: 1, number: 2, title: '', source_text: '深夜的直播间', target_seconds: 10, status: 'draft',
      },
    });
    renderAt(<ProjectsPage />, { route: '/projects/demo', path: '/projects/:slug' });
    const user = userEvent.setup();
    expect(await screen.findByText(/旧的一集/)).toBeTruthy();
    expect(screen.getByText(/已分镜/)).toBeTruthy();
    expect(screen.getByRole('link', { name: '导演台' }).getAttribute('href')).toBe('/projects/demo/episodes/3/director');
    const btn = screen.getByRole('button', { name: '新建剧集' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    await user.type(screen.getByLabelText('原文或梗概'), '深夜的直播间');
    const secs = screen.getByLabelText('目标时长（秒）');
    await user.clear(secs);
    await user.type(secs, '10');
    await user.click(btn);
    await waitFor(() => expect(where()).toBe('/projects/demo/episodes/5/script'));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({
      title: '',
      source_text: '深夜的直播间',
      target_seconds: 10,
    });
  });
});
