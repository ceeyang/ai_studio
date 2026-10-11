import { cleanup, screen, waitFor, within } from '@testing-library/react';
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
    const queued = await screen.findByRole('link', { name: '另有 3 个排队' });
    expect(queued.getAttribute('href')).toBe('/jobs?status=queued');
    expect(screen.getByRole('link', { name: '等待输入 1' }).getAttribute('href')).toBe('/jobs?status=waiting_input');
    expect(screen.queryByText(/实时连接已断开/)).toBeNull();
  });

  it('点运行中那一块跳到这个任务的详情（/jobs?job=ID）', async () => {
    installFetch({
      'GET /api/queue': QUEUE,
      'GET /api/jobs': [makeJob({ id: 293, type: 'video.shot', status: 'running', context: { label: '第 1 集 S01', url: null, project_slug: 'demo', episode_id: 1, shot_id: 1, character_id: null } })],
    });
    renderBar({ connected: true, everConnected: true, progress: {} });
    const user = userEvent.setup();
    const link = await screen.findByTestId('taskbar-current-link');
    expect(link.getAttribute('href')).toBe('/jobs?job=293');
    expect(link.textContent).toContain('运行中 #293 视频');
    expect(link.textContent).toContain('第 1 集 S01');
    await user.click(link);
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/jobs?job=293'));
  });

  it('「取消」按钮单独可点，不触发跳转', async () => {
    installFetch({ 'GET /api/queue': QUEUE, 'GET /api/jobs': [makeJob({ id: 7, status: 'running' })] });
    renderBar({ connected: true, everConnected: true, progress: {} });
    const user = userEvent.setup();
    const current = await screen.findByTestId('taskbar-current');
    const before = screen.getByTestId('location').textContent;
    const cancelBtn = within(current).getByRole('button', { name: '取消' });
    expect(cancelBtn.closest('a')).toBeNull();
    await user.click(cancelBtn);
    expect(await screen.findByRole('dialog')).toBeTruthy();
    expect(screen.getByTestId('location').textContent).toBe(before);
  });

  it('多个运行中：写「另有 N 个运行中」，点了跳到筛选为运行中的列表', async () => {
    installFetch({
      'GET /api/queue': { ...QUEUE, counts: { queued: 0, running: 2, waiting_input: 0 } },
      'GET /api/jobs': [makeJob({ id: 7, status: 'running' }), makeJob({ id: 8, status: 'running' })],
    });
    renderBar({ connected: true, everConnected: true, progress: {} });
    const user = userEvent.setup();
    const more = await screen.findByRole('link', { name: '另有 1 个运行中' });
    await user.click(more);
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/jobs?status=running'));
  });

  it('没有运行中、只有排队：写「排队 N」', async () => {
    installFetch({ 'GET /api/queue': { ...QUEUE, counts: { queued: 2, running: 0, waiting_input: 0 } }, 'GET /api/jobs': [] });
    renderBar({ connected: true, everConnected: true, progress: {} });
    expect((await screen.findByRole('link', { name: '排队 2' })).getAttribute('href')).toBe('/jobs?status=queued');
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

  it('ComfyUI 或浏览器扩展不可用：任务条下面一行黄色横幅，原因放悬停提示，可去设置、可关掉', async () => {
    sessionStorage.clear();
    installFetch({
      'GET /api/queue': { ...QUEUE, counts: { queued: 0, running: 0, waiting_input: 0 } },
      'GET /api/jobs': [],
      'GET /api/system/health': {
        comfyui: { ok: false, message: '连不上 127.0.0.1:8188（超时）', detail: 'ComfyError: ConnectTimeout: timed out' },
        browser_ext: { ok: false, message: '浏览器扩展没有连上，ChatGPT / Gemini 出图会失败；到设置页看安装步骤' },
      },
    });
    renderBar({ connected: true, everConnected: true, progress: {} });
    const banner = await screen.findByTestId('health-banner');
    const line = banner.querySelector('.health-msgs')!;
    expect(line.textContent).toBe('ComfyUI 没连上，本机出图、出视频会失败 · 浏览器扩展没连上，网页出图会失败');
    // 英文原文不在横幅正文里，只在悬停提示里
    expect(banner.textContent).not.toContain('timed out');
    expect(line.getAttribute('title')).toContain('连不上 127.0.0.1:8188（超时）');
    expect(line.getAttribute('title')).toContain('ConnectTimeout: timed out');
    expect(within(banner).getByRole('link', { name: '去设置' }).getAttribute('href')).toBe('/settings');
    const user = userEvent.setup();
    await user.click(within(banner).getByRole('button', { name: '关闭提醒' }));
    expect(screen.queryByTestId('health-banner')).toBeNull();
  });

  it('关掉横幅记在 sessionStorage：刷新（重新挂载）后同样的问题不再提示；换了问题会重新出现', async () => {
    sessionStorage.clear();
    const down = { ok: false, message: '连不上 127.0.0.1:8188（拒绝连接）' };
    installFetch({
      'GET /api/queue': { ...QUEUE, counts: { queued: 0, running: 0, waiting_input: 0 } },
      'GET /api/jobs': [],
      'GET /api/system/health': { comfyui: down, browser_ext: { ok: true, message: '' } },
    });
    const first = renderBar({ connected: true, everConnected: true, progress: {} });
    const user = userEvent.setup();
    await user.click(within(await screen.findByTestId('health-banner')).getByRole('button', { name: '关闭提醒' }));
    first.unmount();
    renderBar({ connected: true, everConnected: true, progress: {} });
    await screen.findByText('没有运行中的任务');
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId('health-banner')).toBeNull();
    cleanup();
    installFetch({
      'GET /api/queue': { ...QUEUE, counts: { queued: 0, running: 0, waiting_input: 0 } },
      'GET /api/jobs': [],
      'GET /api/system/health': { comfyui: down, browser_ext: { ok: false, message: '没连上' } },
    });
    renderBar({ connected: true, everConnected: true, progress: {} });
    expect(await screen.findByTestId('health-banner')).toBeTruthy();
    sessionStorage.clear();
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
