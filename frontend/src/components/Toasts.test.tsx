import { act, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { installFetch } from '../test/fetch';
import { makeJob } from '../test/factories';
import { renderAt } from '../test/render';
import { emitJobSettled } from '../ws/events';
import { Toasts } from './Toasts';

const CTX = (id: number, url: string) => ({
  label: `第 1 集 S0${id}`,
  url,
  project_slug: 'demo',
  episode_id: 1,
  shot_id: id,
  character_id: null,
});

describe('Toasts', () => {
  it('生成任务完成：右下角提示「<对象> <任务名>完成 · 查看」，链接到对象页面；8 秒后消失', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    installFetch({
      'GET /api/jobs/3': { ...makeJob({ id: 3, type: 'image.keyframe', status: 'done' }), context: CTX(3, '/projects/demo/episodes/1/director?shot=3') },
    });
    renderAt(<Toasts />, { route: '/' });
    act(() => emitJobSettled(makeJob({ id: 3, type: 'image.keyframe', status: 'done' })));
    const toast = await screen.findByText(/第 1 集 S03 首帧完成/);
    const box = toast.closest('[data-testid="toast"]') as HTMLElement;
    // 后端给的还是旧页面地址：换成工作台地址
    expect(within(box).getByRole('link', { name: '查看' }).getAttribute('href')).toBe('/projects/demo/episodes/1?stage=keyframes&shot=3');
    await act(async () => {
      vi.advanceTimersByTime(8100);
    });
    expect(screen.queryByText(/S03 首帧完成/)).toBeNull();
  });

  it('失败也提示；已经在那个页面（工作台对应阶段）上就不弹', async () => {
    installFetch({
      'GET /api/jobs/4': { ...makeJob({ id: 4, type: 'video.shot', status: 'failed' }), context: CTX(4, '/projects/demo/episodes/1/director') },
      'GET /api/jobs/5': { ...makeJob({ id: 5, type: 'video.shot', status: 'failed' }), context: CTX(5, '/elsewhere') },
    });
    // 参数顺序不同也算同一处
    renderAt(<Toasts />, { route: '/projects/demo/episodes/1?shot=4&stage=videos' });
    act(() => {
      emitJobSettled(makeJob({ id: 4, type: 'video.shot', status: 'failed' }));
      emitJobSettled(makeJob({ id: 5, type: 'video.shot', status: 'failed' }));
    });
    expect(await screen.findByText(/S05 视频失败/)).toBeTruthy();
    expect(screen.queryByText(/S04/)).toBeNull();
  });

  it('按任务类型选阶段：在首帧阶段看着这一镜时，它的视频任务照样提示并链到视频阶段；角色任务链到角色阶段并带 char', async () => {
    installFetch({
      'GET /api/jobs/6': { ...makeJob({ id: 6, type: 'video.shot', status: 'done' }), context: CTX(6, '/projects/demo/episodes/1/director?shot=6') },
      'GET /api/jobs/7': {
        ...makeJob({ id: 7, type: 'image.portrait', status: 'done' }),
        context: { label: '林夏', url: '/projects/demo/episodes/1/cast', project_slug: 'demo', episode_id: 1, shot_id: null, character_id: 8 },
      },
    });
    renderAt(<Toasts />, { route: '/projects/demo/episodes/1?stage=keyframes&shot=6' });
    act(() => {
      emitJobSettled(makeJob({ id: 6, type: 'video.shot', status: 'done' }));
      emitJobSettled(makeJob({ id: 7, type: 'image.portrait', status: 'done' }));
    });
    const vid = (await screen.findByText(/S06 视频完成/)).closest('[data-testid="toast"]') as HTMLElement;
    expect(within(vid).getByRole('link', { name: '查看' }).getAttribute('href')).toBe('/projects/demo/episodes/1?stage=videos&shot=6');
    const por = (await screen.findByText(/林夏 定妆照完成/)).closest('[data-testid="toast"]') as HTMLElement;
    expect(within(por).getByRole('link', { name: '查看' }).getAttribute('href')).toBe('/projects/demo/episodes/1?stage=cast&char=8');
  });

  it('最多同时显示 3 条，新的挤掉旧的', async () => {
    const routes: Record<string, unknown> = {};
    for (const id of [1, 2, 3, 4]) {
      routes[`GET /api/jobs/${id}`] = { ...makeJob({ id, type: 'image.keyframe', status: 'done' }), context: CTX(id, `/p/${id}`) };
    }
    installFetch(routes);
    renderAt(<Toasts />);
    for (const id of [1, 2, 3, 4]) {
      act(() => emitJobSettled(makeJob({ id, type: 'image.keyframe', status: 'done' })));
      await screen.findByText(new RegExp(`S0${id} 首帧完成`));
    }
    await waitFor(() => expect(screen.getAllByTestId('toast')).toHaveLength(3));
    expect(screen.queryByText(/S01 首帧完成/)).toBeNull();
  });
});
