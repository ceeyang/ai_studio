import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFetch, json } from '../test/fetch';
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

  it('采样进度到头（value>=max）但任务还在跑：显示"解码保存中"而不是卡在 20/20', async () => {
    installFetch({ 'GET /api/queue': QUEUE, 'GET /api/jobs': [makeJob({ id: 7, type: 'video.shot', status: 'running' })] });
    renderBar({ connected: true, everConnected: true, progress: { 7: { value: 20, max: 20 } } });
    const current = await screen.findByTestId('taskbar-current');
    expect(current.textContent).toContain('采样完成，解码保存中…');
    expect(current.textContent).not.toContain('20/20');
    const bar = within(current).getByRole('progressbar') as HTMLProgressElement;
    expect(bar.value).toBe(bar.max);
    expect(bar.className).toContain('is-decoding');
  });

  it('暂停队列后显示"恢复队列"；可以取消当前任务', async () => {
    const calls = installFetch({
      'GET /api/queue': QUEUE,
      'GET /api/jobs': [makeJob({ id: 7, status: 'running', started_at: '2026-10-08T10:00:00Z' })],
      'POST /api/queue/pause': { ...QUEUE, paused: true },
      'POST /api/jobs/7/cancel': { ok: true },
    });
    renderBar({ connected: true, everConnected: true, progress: {} });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '暂停队列' }));
    expect(await screen.findByRole('button', { name: '恢复队列' })).toBeTruthy();
    expect(screen.getByText('队列已暂停')).toBeTruthy();
    await user.click(within(screen.getByTestId('taskbar-current')).getByRole('button', { name: '取消' }));
    // 先确认，正文写已经跑了多久
    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent).toContain('已运行');
    expect(calls.some((c) => c.url === '/api/jobs/7/cancel')).toBe(false);
    await user.click(within(dialog).getByRole('button', { name: '取消任务' }));
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

  it('点"释放显存"：调后端接口，用返回的前后显存数字弹提示', async () => {
    const calls = installFetch({
      'GET /api/queue': { ...QUEUE, counts: { queued: 0, running: 0, waiting_input: 0 } },
      'GET /api/jobs': [],
      'POST /api/queue/free-gpu': {
        before: { vram_free_gb: 1.3, ram_free_gb: 10, process_rss_gb: null },
        after: { vram_free_gb: 14.5, ram_free_gb: 40, process_rss_gb: null },
      },
    });
    renderBar({ connected: true, everConnected: true, progress: {} });
    const user = userEvent.setup();
    const btn = (await screen.findByRole('button', { name: '释放显存' })) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    await user.click(btn);
    const status = await screen.findByRole('status');
    expect(status.textContent).toContain('已释放：显存空闲 1.3 → 14.5 GB');
    expect(calls.some((c) => c.method === 'POST' && c.url === '/api/queue/free-gpu')).toBe(true);
  });

  it('有 GPU 任务在跑时，"释放显存"按钮是禁用的', async () => {
    installFetch({
      'GET /api/queue': QUEUE,
      'GET /api/jobs': [makeJob({ id: 9, type: 'video.shot', status: 'running', gpu: true })],
    });
    renderBar({ connected: true, everConnected: true, progress: {} });
    await screen.findByTestId('taskbar-current'); // 等任务数据到位，gpuBusy 才会算出来
    const btn = screen.getByRole('button', { name: '释放显存' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('释放失败时弹出错误信息', async () => {
    installFetch({
      'GET /api/queue': { ...QUEUE, counts: { queued: 0, running: 0, waiting_input: 0 } },
      'GET /api/jobs': [],
      'POST /api/queue/free-gpu': () => json({ detail: '有任务正在用显卡，等它结束再释放' }, 409),
    });
    renderBar({ connected: true, everConnected: true, progress: {} });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '释放显存' }));
    const status = await screen.findByRole('status');
    expect(status.textContent).toContain('有任务正在用显卡，等它结束再释放');
  });

  it('后端自动释放显存后推来的队列事件：弹"已自动释放显存"提示', async () => {
    installFetch({
      'GET /api/queue': { ...QUEUE, counts: { queued: 0, running: 0, waiting_input: 0 } },
      'GET /api/jobs': [],
    });
    renderBar({ connected: true, everConnected: true, progress: {}, gpuFreedAt: Date.now() });
    const status = await screen.findByRole('status');
    expect(status.textContent).toContain('已自动释放显存');
  });

  it('取消确认框里选"继续运行"就不取消', async () => {
    const calls = installFetch({ 'GET /api/queue': QUEUE, 'GET /api/jobs': [makeJob({ id: 7, status: 'running' })] });
    renderBar({ connected: true, everConnected: true, progress: {} });
    const user = userEvent.setup();
    await user.click(within(await screen.findByTestId('taskbar-current')).getByRole('button', { name: '取消' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '继续运行' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  });

  it('没在跑、只有等待输入的任务：写「N 个任务在等你输入」；排队 0 不显示', async () => {
    installFetch({ 'GET /api/queue': { ...QUEUE, counts: { queued: 0, running: 0, waiting_input: 2 } }, 'GET /api/jobs': [] });
    renderBar({ connected: true, everConnected: true, progress: {} });
    const link = await screen.findByRole('link', { name: '2 个任务在等你输入' });
    expect(link.getAttribute('href')).toBe('/jobs?status=waiting_input');
    expect(screen.queryByText('没有运行中的任务')).toBeNull();
    expect(screen.queryByText(/排队/)).toBeNull();
  });

  it('ComfyUI 或浏览器扩展不可用：任务条下面出黄色横幅，可去设置、可关掉', async () => {
    installFetch({
      'GET /api/queue': { ...QUEUE, counts: { queued: 0, running: 0, waiting_input: 0 } },
      'GET /api/jobs': [],
      'GET /api/system/health': {
        comfyui: { ok: false, message: 'ComfyUI 没启动（127.0.0.1:8188 连不上）' },
        browser_ext: { ok: true, message: '已连接' },
      },
    });
    renderBar({ connected: true, everConnected: true, progress: {} });
    const banner = await screen.findByTestId('health-banner');
    expect(banner.textContent).toContain('ComfyUI 没启动');
    expect(banner.textContent).not.toContain('已连接');
    expect(within(banner).getByRole('link', { name: '去设置' }).getAttribute('href')).toBe('/settings');
    const user = userEvent.setup();
    await user.click(within(banner).getByRole('button', { name: '关闭提醒' }));
    expect(screen.queryByTestId('health-banner')).toBeNull();
  });

  it('都正常时不显示横幅', async () => {
    installFetch({
      'GET /api/queue': { ...QUEUE, counts: { queued: 0, running: 0, waiting_input: 0 } },
      'GET /api/jobs': [],
      'GET /api/system/health': { comfyui: { ok: true, message: '' }, browser_ext: { ok: true, message: '' } },
    });
    renderBar({ connected: true, everConnected: true, progress: {} });
    await screen.findByText('没有运行中的任务');
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId('health-banner')).toBeNull();
  });
});
