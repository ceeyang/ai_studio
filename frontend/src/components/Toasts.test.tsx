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
    renderAt(<Toasts />, { route: '/projects/demo/episodes/1?stage=keyframes' });
    act(() => {
      emitJobSettled(makeJob({ id: 4, type: 'video.shot', status: 'failed' }));
      emitJobSettled(makeJob({ id: 5, type: 'video.shot', status: 'failed' }));
    });
    expect(await screen.findByText(/S05 视频失败/)).toBeTruthy();
    expect(screen.queryByText(/S04/)).toBeNull();
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
