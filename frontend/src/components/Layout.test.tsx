import { cleanup, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { installFetch } from '../test/fetch';
import { renderAt } from '../test/render';
import { Layout } from './Layout';

beforeEach(() => {
  localStorage.clear();
  installFetch({ 'GET /api/queue': { paused: false, counts: { queued: 0, running: 0, waiting_input: 0 }, current_family: null }, 'GET /api/jobs': [] });
});

describe('Layout', () => {
  it('没打开过剧集时剧本、导演台不可点', () => {
    renderAt(<Layout />, { route: '/jobs' });
    const nav = screen.getByRole('navigation', { name: '阶段导航' });
    expect(nav.textContent).toContain('剧本');
    expect(screen.queryByRole('link', { name: '剧本' })).toBeNull();
    expect(screen.getByRole('link', { name: '任务' }).getAttribute('href')).toBe('/jobs');
  });

  it('在剧集页面时指向这一集，离开后仍记得', () => {
    renderAt(<Layout />, { route: '/projects/%E6%8F%90%E8%AF%8D%E5%91%98/episodes/4/director' });
    expect(screen.getByRole('link', { name: '剧本' }).getAttribute('href')).toBe(
      '/projects/%E6%8F%90%E8%AF%8D%E5%91%98/episodes/4/script',
    );
    cleanup();
    renderAt(<Layout />, { route: '/settings' });
    expect(screen.getByRole('link', { name: '导演台' }).getAttribute('href')).toBe(
      '/projects/%E6%8F%90%E8%AF%8D%E5%91%98/episodes/4/director',
    );
  });
});
