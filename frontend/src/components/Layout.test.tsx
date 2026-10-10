import { cleanup, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { installFetch } from '../test/fetch';
import { makeProject } from '../test/factories';
import { renderAt } from '../test/render';
import { Layout } from './Layout';

const SLUG = '提词员';
const ENC = encodeURIComponent(SLUG);
const BASE_ROUTES = {
  'GET /api/queue': { paused: false, counts: { queued: 0, running: 0, waiting_input: 0 }, current_family: null },
  'GET /api/jobs': [],
  [`GET /api/projects/${ENC}`]: makeProject({ slug: SLUG, title: '深夜直播' }),
  [`GET /api/projects/${ENC}/episodes`]: [
    { id: 4, project_id: 1, number: 2, title: '第二集', source_text: '', target_seconds: 30, status: 'draft' },
    { id: 3, project_id: 1, number: 1, title: '第一集', source_text: '', target_seconds: 30, status: 'draft' },
  ],
  'GET /api/projects/empty': makeProject({ slug: 'empty', title: '空项目' }),
  'GET /api/projects/empty/episodes': [],
};

function nav() {
  return screen.getByRole('navigation', { name: '阶段导航' });
}

beforeEach(() => {
  localStorage.clear();
  installFetch(BASE_ROUTES);
});

const WS = `/projects/${ENC}/episodes`;

describe('Layout', () => {
  it('制作流程只有项目中心和当前集；没打开过剧集时当前集不可点但能聚焦，并写明原因', () => {
    renderAt(<Layout />, { route: '/jobs' });
    const links = within(nav()).getAllByRole('link');
    expect(links.map((a) => a.textContent?.replace(/\d+$/, ''))).toEqual(['项目中心', '当前集', '任务', '设置']);
    const item = within(nav()).getByRole('link', { name: '当前集' });
    expect(item.getAttribute('aria-disabled')).toBe('true');
    expect(item.getAttribute('href')).toBeNull();
    expect(item.tabIndex).toBe(0);
    expect(item.getAttribute('aria-describedby')).toBeTruthy();
    expect(within(nav()).getByText('先在项目中心打开一集')).toBeTruthy();
    expect(within(nav()).getByRole('link', { name: '任务' }).getAttribute('href')).toBe('/jobs');
  });

  it('在单集工作台时当前集指向这一集的工作台，上方写明「项目 · 第 N 集」；离开后仍记得', async () => {
    renderAt(<Layout />, { route: `${WS}/4?stage=keyframes` });
    const cur = within(nav()).getByRole('link', { name: '当前集' });
    expect(cur.getAttribute('href')).toBe(`${WS}/4`);
    expect(cur.getAttribute('aria-current')).toBe('page');
    await waitFor(() => expect(screen.getByTestId('nav-context').textContent).toBe('深夜直播 · 第 2 集'));
    cleanup();
    renderAt(<Layout />, { route: '/settings' });
    expect(within(nav()).getByRole('link', { name: '当前集' }).getAttribute('href')).toBe(`${WS}/4`);
    await waitFor(() => expect(screen.getByTestId('nav-context').textContent).toBe('深夜直播 · 第 2 集'));
  });

  it('在项目详情页时当前集指向这个项目最近的一集（没打开过就取集数最大的）', async () => {
    localStorage.setItem('studio.lastEpisode', JSON.stringify({ slug: 'other', episodeId: 99 }));
    renderAt(<Layout />, { route: `/projects/${ENC}` });
    await waitFor(() => expect(within(nav()).getByRole('link', { name: '当前集' }).getAttribute('href')).toBe(`${WS}/4`));
    await waitFor(() => expect(screen.getByTestId('nav-context').textContent).toBe('深夜直播 · 第 2 集'));
  });

  it('项目详情页上次打开的是这个项目的另一集：指向那一集', async () => {
    localStorage.setItem('studio.lastEpisode', JSON.stringify({ slug: SLUG, episodeId: 3 }));
    renderAt(<Layout />, { route: `/projects/${ENC}` });
    await waitFor(() => expect(within(nav()).getByRole('link', { name: '当前集' }).getAttribute('href')).toBe(`${WS}/3`));
  });

  it('项目还没有剧集：当前集不可点，写明先新建剧集', async () => {
    renderAt(<Layout />, { route: '/projects/empty' });
    expect(await within(nav()).findByText('这个项目还没有剧集，先新建一集')).toBeTruthy();
    expect(within(nav()).getByRole('link', { name: '当前集' }).getAttribute('aria-disabled')).toBe('true');
  });
});
