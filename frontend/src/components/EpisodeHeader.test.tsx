import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { installFetch } from '../test/fetch';
import { makeEpisode, makeProject } from '../test/factories';
import { renderAt } from '../test/render';
import { EpisodeHeader, EpisodeLoadError, nextActionProps } from './EpisodeHeader';

const EPISODES = [
  { id: 5, project_id: 1, number: 3, title: '第三', source_text: '', target_seconds: 30, status: 'draft' },
  { id: 3, project_id: 1, number: 1, title: '第一', source_text: '', target_seconds: 30, status: 'draft' },
  { id: 4, project_id: 1, number: 2, title: '提词器', source_text: '', target_seconds: 30, status: 'draft' },
];
const ROUTES = {
  'GET /api/projects/demo': makeProject({ title: '深夜直播' }),
  'GET /api/projects/demo/episodes': EPISODES,
};
const EP = makeEpisode({ id: 4, number: 2, title: '提词器' });

describe('EpisodeHeader', () => {
  it('面包屑：项目名链接到项目详情；标题是第几集和集名；只有面包屑和切集，没有进度条和主按钮', async () => {
    installFetch(ROUTES);
    const { container } = renderAt(<EpisodeHeader slug="demo" ep={EP} stage="keyframes" />);
    const crumbs = screen.getByRole('navigation', { name: '面包屑' });
    const proj = await within(crumbs).findByRole('link', { name: '深夜直播' });
    expect(proj.getAttribute('href')).toBe('/projects/demo');
    expect(screen.getByRole('heading', { level: 2, name: '第 2 集 提词器' })).toBeTruthy();
    expect(screen.queryByRole('list', { name: '制作进度' })).toBeNull();
    expect(container.querySelectorAll('.primary')).toHaveLength(0);
  });

  it('上一集 / 下一集按集数排，停在同一个阶段', async () => {
    installFetch(ROUTES);
    renderAt(<EpisodeHeader slug="demo" ep={EP} stage="keyframes" />);
    expect((await screen.findByRole('link', { name: /上一集/ })).getAttribute('href')).toBe('/projects/demo/episodes/3?stage=keyframes');
    expect(screen.getByRole('link', { name: /下一集/ }).getAttribute('href')).toBe('/projects/demo/episodes/5?stage=keyframes');
  });

  it('第一集：上一集不可点；没给阶段时切集不带 stage', async () => {
    installFetch(ROUTES);
    renderAt(<EpisodeHeader slug="demo" ep={{ ...EP, id: 3, number: 1 }} />);
    expect((await screen.findByRole('link', { name: /下一集/ })).getAttribute('href')).toBe('/projects/demo/episodes/4');
    expect(screen.queryByRole('link', { name: /上一集/ })).toBeNull();
    expect(screen.getByText(/上一集/).closest('[aria-disabled="true"]')).toBeTruthy();
  });

  it('加载失败：给出回到项目的链接', () => {
    renderAt(<EpisodeLoadError slug="demo" error={new Error('网络断了')} />);
    expect(screen.getByText(/网络断了/)).toBeTruthy();
    expect(screen.getByRole('link', { name: '回到项目' }).getAttribute('href')).toBe('/projects/demo');
  });

  it('nextActionProps：是下一步就打上 data-next-action，但不再是主按钮（全页唯一主按钮在阶段条）', () => {
    expect(nextActionProps(true)).toEqual({ 'data-next-action': true });
    expect(nextActionProps(false)).toEqual({});
  });
});
