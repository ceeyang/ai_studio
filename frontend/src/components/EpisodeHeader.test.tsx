import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFetch } from '../test/fetch';
import { makeEpisode, makeProject, makeShot } from '../test/factories';
import { renderAt } from '../test/render';
import { EpisodeHeader, EpisodeLoadError } from './EpisodeHeader';

const EPISODES = [
  { id: 5, project_id: 1, number: 3, title: '第三', source_text: '', target_seconds: 30, status: 'draft' },
  { id: 3, project_id: 1, number: 1, title: '第一', source_text: '', target_seconds: 30, status: 'draft' },
  { id: 4, project_id: 1, number: 2, title: '提词器', source_text: '', target_seconds: 30, status: 'draft' },
];
const ROUTES = {
  'GET /api/projects/demo': makeProject({ title: '深夜直播' }),
  'GET /api/projects/demo/episodes': EPISODES,
  'GET /api/projects/demo/characters': [
    { id: 8, project_id: 1, name: '林夏', description: '', visual_prompt: '', core_features: '', voice: null, portrait_url: '/media/p.png' },
  ],
  'GET /api/episodes/4/exports': [],
};
const EP = makeEpisode({
  id: 4,
  number: 2,
  title: '提词器',
  scene_blocks: [{ id: 1, order: 0, location_id: null, text: 'a' }],
  shots: [makeShot({ id: 1, cast: [{ character_id: 8, variation_id: null }] }), makeShot({ id: 2, code: 'S02' })],
});

describe('EpisodeHeader', () => {
  it('面包屑：项目名链接到项目详情，后面是第几集和标题', async () => {
    installFetch(ROUTES);
    renderAt(<EpisodeHeader slug="demo" ep={EP} page="director" title="导演台" />);
    const crumbs = screen.getByRole('navigation', { name: '面包屑' });
    const proj = await within(crumbs).findByRole('link', { name: '深夜直播' });
    expect(proj.getAttribute('href')).toBe('/projects/demo');
    expect(crumbs.textContent).toContain('第 2 集 提词器');
    expect(screen.getByRole('heading', { level: 2, name: '导演台' })).toBeTruthy();
  });

  it('上一集 / 下一集按集数排，停在同一个阶段页；第一集没有上一集', async () => {
    installFetch(ROUTES);
    renderAt(<EpisodeHeader slug="demo" ep={EP} page="director" title="导演台" />);
    expect((await screen.findByRole('link', { name: /上一集/ })).getAttribute('href')).toBe('/projects/demo/episodes/3/director');
    expect(screen.getByRole('link', { name: /下一集/ }).getAttribute('href')).toBe('/projects/demo/episodes/5/director');
  });

  it('第一集：上一集不可点', async () => {
    installFetch(ROUTES);
    renderAt(<EpisodeHeader slug="demo" ep={{ ...EP, id: 3, number: 1 }} page="cast" title="角色" />);
    expect((await screen.findByRole('link', { name: /下一集/ })).getAttribute('href')).toBe('/projects/demo/episodes/4/cast');
    expect(screen.queryByRole('link', { name: /上一集/ })).toBeNull();
    expect(screen.getByText(/上一集/).closest('[aria-disabled="true"]')).toBeTruthy();
  });

  it('阶段进度六段，当前页对应的段标出来；下一步在别的页面时是右侧唯一的主按钮（链接）', async () => {
    installFetch(ROUTES);
    const { container } = renderAt(<EpisodeHeader slug="demo" ep={EP} page="cast" title="角色" />);
    const stepper = screen.getByRole('list', { name: '制作进度' });
    const items = within(stepper).getAllByRole('listitem');
    expect(items.map((li) => li.querySelector('.step-label')?.textContent)).toEqual(['剧本', '角色', '分镜', '首帧', '视频', '导出']);
    expect(within(stepper).getByRole('link', { name: /角色/ }).getAttribute('aria-current')).toBe('step');
    await waitFor(() => expect(within(stepper).getByRole('link', { name: /角色/ }).textContent).toContain('1/1 有定妆照'));
    expect(within(stepper).getByRole('link', { name: /剧本/ }).textContent).toContain('已完成');
    const next = screen.getByRole('link', { name: '下一步：生成首帧（2 镜缺首帧）' });
    expect(next.getAttribute('href')).toBe('/projects/demo/episodes/4/director');
    expect(container.querySelectorAll('.primary')).toHaveLength(1);
  });

  it('加载失败：给出回到项目的链接', () => {
    renderAt(<EpisodeLoadError slug="demo" error={new Error('网络断了')} />);
    expect(screen.getByText(/网络断了/)).toBeTruthy();
    expect(screen.getByRole('link', { name: '回到项目' }).getAttribute('href')).toBe('/projects/demo');
  });

  it('下一步就在本页：不是链接而是提示，点了滚到并聚焦本页标了 data-next-action 的按钮', async () => {
    installFetch(ROUTES);
    const { container } = renderAt(
      <>
        <EpisodeHeader slug="demo" ep={EP} page="director" title="导演台" />
        <button type="button" className="primary" data-next-action>
          生成首帧（全部）
        </button>
      </>,
    );
    const user = userEvent.setup();
    const hint = await screen.findByRole('button', { name: /下一步：生成首帧（2 镜缺首帧）/ });
    expect(screen.queryByRole('link', { name: /下一步/ })).toBeNull();
    expect(hint.className).not.toContain('primary');
    // 本页只有那个动作按钮是主按钮
    expect(container.querySelectorAll('.primary')).toHaveLength(1);
    await user.click(hint);
    await waitFor(() => expect(document.activeElement?.textContent).toBe('生成首帧（全部）'));
  });
});
