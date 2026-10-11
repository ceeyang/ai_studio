// 角色阶段「设定」里每个场景卡的场景图（B）：出图、上传、换选定、生成中 / 失败。
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Location } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeCandidate, makeEpisode } from '../../test/factories';
import { renderAt } from '../../test/render';
import { CastStage } from './CastStage';
import { LocationImages, hasEnglishPrompt } from './LocationImages';

const LOC: Location = {
  id: 3, project_id: 1, name: '修车行', time_of_day: '夜', atmosphere: '压抑', visual_prompt: 'a cramped auto repair garage',
  image_candidate_id: 41, image_url: '/media/demo/media/location/a.png',
  images: [
    makeCandidate({ id: 41, url: '/media/demo/media/location/a.png', engine: 'zimage' }),
    makeCandidate({ id: 42, url: '/media/demo/media/location/b.png', engine: 'upload' }),
  ],
  active_jobs: [],
  last_failure: null,
};

describe('LocationImages', () => {
  it('候选条：当前那张标「当前」，另一张可以设为场景图', async () => {
    const calls = installFetch({ 'GET /api/engines': { image: [], video: [] }, 'POST /api/locations/3/images/42/use': { ...LOC, image_candidate_id: 42 } });
    renderAt(<LocationImages loc={LOC} slug="demo" />);
    const user = userEvent.setup();
    const strip = screen.getByRole('list', { name: '修车行 的场景图候选' });
    expect(within(strip).getByText('当前')).toBeTruthy();
    expect(within(strip).getByText('上传')).toBeTruthy();
    await user.click(within(strip).getByRole('button', { name: '把场景图 #42 设为场景图' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/locations/3/images/42/use')).toBe(true));
  });

  it('出场景图：按张数提交；上传走 multipart', async () => {
    const calls = installFetch({
      'GET /api/engines': { image: [{ name: 'zimage', family: 'zimage', caps: {} }], video: [] },
      'POST /api/locations/3/images/draw': { job_ids: [1, 2, 3] },
      'POST /api/locations/3/image': LOC,
    });
    renderAt(<LocationImages loc={{ ...LOC, images: [], image_url: null, image_candidate_id: null }} slug="demo" />);
    const user = userEvent.setup();
    expect(screen.getByText(/还没有场景图/)).toBeTruthy();
    await user.selectOptions(screen.getByLabelText('修车行 场景图张数'), '3');
    await user.click(screen.getByRole('button', { name: '出场景图' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ count: 3 }));
    await user.upload(screen.getByLabelText('上传 修车行 场景图'), new File(['png'], 'g.png', { type: 'image/png' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/locations/3/image')).toBe(true));
  });

  it('生成中显示进度卡，失败显示原因；没有英文提示词时不能出图', () => {
    installFetch({ 'GET /api/engines': { image: [], video: [] } });
    const { unmount } = renderAt(
      <LocationImages loc={{ ...LOC, active_jobs: [{ id: 7, type: 'image.location', status: 'running' }] }} slug="demo" />,
    );
    expect(screen.getByTestId('location-drawing-7').textContent).toContain('生成中');
    expect(screen.getByRole('button', { name: '出场景图（1 张进行中）' })).toBeTruthy();
    unmount();
    renderAt(
      <LocationImages
        loc={{ ...LOC, visual_prompt: '狭窄的修车行', last_failure: { id: 9, type: 'image.location', status: 'failed', error: 'ComfyUI 连不上' } }}
        slug="demo"
      />,
    );
    expect(screen.getByText('出图失败：ComfyUI 连不上')).toBeTruthy();
    expect((screen.getByRole('button', { name: '出场景图' }) as HTMLButtonElement).disabled).toBe(true);
    expect(hasEnglishPrompt({ ...LOC, visual_prompt: '' })).toBe(false);
  });

  it('角色阶段「设定」里每个场景卡都有场景图，折叠标题数有场景图的场景', async () => {
    installFetch({
      'GET /api/episodes/4': makeEpisode({ id: 4 }),
      'GET /api/projects/demo/locations': [LOC, { ...LOC, id: 5, name: '天台', images: [], image_url: null, image_candidate_id: null }],
      'GET /api/projects/demo/characters': [],
      'GET /api/projects/demo/style': null,
      'GET /api/engines': { image: [], video: [] },
    });
    renderAt(<CastStage />, { route: '/projects/demo/episodes/4?stage=cast', path: '/projects/:slug/episodes/:episodeId' });
    const fold = await screen.findByRole('button', { name: /^设定/ });
    expect(fold.textContent).toContain('场景 2 个（1 个有场景图）');
    expect(within(screen.getByTestId('location-3')).getByRole('img', { name: '修车行 场景图 1', hidden: true })).toBeTruthy();
    expect(within(screen.getByTestId('location-5')).getByText(/还没有场景图/)).toBeTruthy();
  });

  it('设定改了：选定的场景图标「已过期」，提示重出；折叠标题也数过期的', async () => {
    installFetch({
      'GET /api/episodes/4': makeEpisode({ id: 4 }),
      'GET /api/projects/demo/locations': [{ ...LOC, image_stale: true }],
      'GET /api/projects/demo/characters': [],
      'GET /api/projects/demo/style': null,
      'GET /api/engines': { image: [], video: [] },
    });
    renderAt(<CastStage />, { route: '/projects/demo/episodes/4?stage=cast&settings=1', path: '/projects/:slug/episodes/:episodeId' });
    const fold = await screen.findByRole('button', { name: /^设定/ });
    // ?settings=1：一进来就展开（首帧阶段「去生成场景图」的链接）
    expect(fold.getAttribute('aria-expanded')).toBe('true');
    expect(fold.textContent).toContain('1 个场景图已过期');
    const stale = within(screen.getByTestId('location-3')).getByTestId('location-image-stale');
    expect(stale.textContent).toContain('已过期');
    expect(stale.textContent).toContain('重出');
  });

  it('没过期时不标', () => {
    installFetch({ 'GET /api/engines': { image: [], video: [] } });
    renderAt(<LocationImages loc={{ ...LOC, image_stale: false }} slug="demo" />);
    expect(screen.queryByTestId('location-image-stale')).toBeNull();
  });
});
