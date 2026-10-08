import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFetch } from '../test/fetch';
import { makeJob } from '../test/factories';
import { renderAt } from '../test/render';
import type { EventsState } from '../ws/events';
import { EventsContext } from '../ws/EventsContext';
import { TaskBar } from './TaskBar';

const QUEUE = { paused: false, counts: { queued: 3, running: 1, waiting_input: 1 }, current_family: 'h3' };

function renderBar(events: EventsState) {
  return renderAt(
    <EventsContext.Provider value={events}>
      <TaskBar />
    </EventsContext.Provider>,
  );
}

describe('TaskBar', () => {
  it('显示运行中的任务、进度、排队数和等待输入的入口', async () => {
    installFetch({ 'GET /api/queue': QUEUE, 'GET /api/jobs': [makeJob({ id: 7, type: 'video.shot', status: 'running' })] });
    renderBar({ connected: true, everConnected: true, progress: { 7: { value: 12, max: 30 } } });
    const current = await screen.findByTestId('taskbar-current');
    expect(current.textContent).toContain('#7');
    expect(current.textContent).toContain('视频');
    expect(current.textContent).toContain('12/30');
    expect(await screen.findByText('排队 3')).toBeTruthy();
    expect(screen.getByRole('link', { name: '等待输入 1' }).getAttribute('href')).toBe('/jobs?status=waiting_input');
    expect(screen.queryByText(/实时连接已断开/)).toBeNull();
  });

  it('暂停队列后显示"恢复队列"；可以取消当前任务', async () => {
    const calls = installFetch({
      'GET /api/queue': QUEUE,
      'GET /api/jobs': [makeJob({ id: 7, status: 'running' })],
      'POST /api/queue/pause': { ...QUEUE, paused: true },
      'POST /api/jobs/7/cancel': { ok: true },
    });
    renderBar({ connected: true, everConnected: true, progress: {} });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '暂停队列' }));
    expect(await screen.findByRole('button', { name: '恢复队列' })).toBeTruthy();
    expect(screen.getByText('队列已暂停')).toBeTruthy();
    await user.click(within(screen.getByTestId('taskbar-current')).getByRole('button', { name: '取消' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/jobs/7/cancel')).toBe(true));
  });

  it('断线时提示正在重连；没有运行中的任务时说明', async () => {
    installFetch({ 'GET /api/queue': { ...QUEUE, counts: { queued: 0, running: 0, waiting_input: 0 } }, 'GET /api/jobs': [] });
    renderBar({ connected: false, everConnected: true, progress: {} });
    expect(screen.getByText('实时连接已断开，正在重连…')).toBeTruthy();
    expect(await screen.findByText('没有运行中的任务')).toBeTruthy();
    expect(screen.queryByRole('link', { name: /等待输入/ })).toBeNull();
  });

  it('首次加载、WS 还没连上时，不提示"断线重连"（没连过不算断线）', async () => {
    installFetch({ 'GET /api/queue': { ...QUEUE, counts: { queued: 0, running: 0, waiting_input: 0 } }, 'GET /api/jobs': [] });
    renderBar({ connected: false, everConnected: false, progress: {} });
    expect(await screen.findByText('没有运行中的任务')).toBeTruthy();
    expect(screen.queryByText(/实时连接已断开/)).toBeNull();
  });
});
