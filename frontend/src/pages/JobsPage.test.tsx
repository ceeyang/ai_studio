import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Job } from '../api/types';
import { installFetch } from '../test/fetch';
import { makeJob } from '../test/factories';
import { renderAt } from '../test/render';
import { JobsPage } from './JobsPage';

const FAILED = makeJob({
  id: 3,
  type: 'video.shot',
  status: 'failed',
  error: 'ComfyUI 拒绝工作流：节点 7',
  started_at: '2026-10-08T10:00:00Z',
  finished_at: '2026-10-08T10:00:42Z',
  log: '[10:00:00] 视频 S01 · h3_video\n',
});

describe('JobsPage', () => {
  it('按状态筛选并记在地址栏；重试失败任务后跳到新任务', async () => {
    const calls = installFetch({
      'GET /api/jobs?limit=100': [FAILED],
      'GET /api/jobs?status=failed&limit=100': [FAILED],
      'GET /api/jobs/3': FAILED,
      'POST /api/jobs/3/retry': { job_id: 4 },
      'GET /api/jobs/4': makeJob({ id: 4, type: 'video.shot', status: 'queued' }),
    });
    renderAt(<JobsPage />, { route: '/jobs', path: '/jobs' });
    const user = userEvent.setup();
    await screen.findByRole('button', { name: '查看任务 #3' });
    expect(screen.getByRole('cell', { name: '42 秒' })).toBeTruthy();
    await user.selectOptions(screen.getByLabelText('状态筛选'), 'failed');
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/jobs?status=failed'));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/jobs?status=failed&limit=100')).toBe(true));
    await user.click(screen.getByRole('button', { name: '查看任务 #3' }));
    expect((await screen.findByTestId('job-status')).textContent).toBe('失败');
    expect(screen.getByTestId('job-log').textContent).toContain('视频 S01');
    await user.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/jobs?job=4'));
    await waitFor(() => expect(screen.getByTestId('job-status').textContent).toBe('排队中'));
  });

  it('等待输入的任务显示人工输入表单，也可以取消', async () => {
    const waiting = makeJob({
      id: 9,
      status: 'waiting_input',
      result: { input_request: { key: 'script_structure', task: 'script', system: 'S', user: 'U' } },
    });
    const calls = installFetch({
      'GET /api/jobs': [waiting],
      'GET /api/jobs/9': waiting,
      'POST /api/jobs/9/cancel': { ok: true },
    });
    renderAt(<JobsPage />, { route: '/jobs?job=9', path: '/jobs' });
    const user = userEvent.setup();
    expect((await screen.findByTestId('input-key')).textContent).toBe('script_structure');
    expect(screen.getByTestId('job-status').textContent).toBe('等待输入');
    await user.click(screen.getByRole('button', { name: '取消' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/jobs/9/cancel')).toBe(true));
  });

  it('页头「04 · 任务」；列表写任务类型和所属对象、开始时间；# 是能用键盘点的按钮', async () => {
    const job = makeJob({
      ...FAILED,
      context: { label: '第 1 集 S01', url: '/projects/demo/episodes/4/director?shot=11', project_slug: 'demo', episode_id: 4, shot_id: 11, character_id: null },
    });
    installFetch({ 'GET /api/jobs': [job], 'GET /api/jobs/3': job });
    renderAt(<JobsPage />, { route: '/jobs', path: '/jobs' });
    const user = userEvent.setup();
    expect(screen.getByText('04 · 任务')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 2, name: '任务' })).toBeTruthy();
    const table = await screen.findByRole('table');
    expect(within(table).getAllByRole('columnheader').map((th) => th.textContent)).toEqual(['#', '任务', '状态', '耗时', '开始时间']);
    expect(await within(table).findByText('视频 · 第 1 集 S01')).toBeTruthy();
    const btn = within(table).getByRole('button', { name: '查看任务 #3' });
    btn.focus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/jobs?job=3'));
  });

  it('详情：打开所属对象；显卡 / 扩展 / 登录类错误给「去设置检查」；参数和结果收进调试信息，调用栈收进完整日志', async () => {
    const job: Job = makeJob({
      ...FAILED,
      model_family: 'h3',
      payload: { shot_id: 11 },
      result: { foo: 'bar' },
      log: '[10:00] 视频 S01\nTraceback (most recent call last):\n  File "a.py", line 3, in run\nRuntimeError: 连不上 ComfyUI\n',
      context: { label: '第 1 集 S01', url: '/projects/demo/episodes/4/director?shot=11', project_slug: 'demo', episode_id: 4, shot_id: 11, character_id: null },
    });
    installFetch({ 'GET /api/jobs': [job], 'GET /api/jobs/3': job });
    renderAt(<JobsPage />, { route: '/jobs?job=3', path: '/jobs' });
    const open = await screen.findByRole('link', { name: '打开所属对象' });
    expect(open.getAttribute('href')).toBe('/projects/demo/episodes/4/director?shot=11');
    expect(screen.getByRole('link', { name: '去设置检查' }).getAttribute('href')).toBe('/settings');
    const debug = screen.getByText('调试信息').closest('details')!;
    expect(debug.textContent).toContain('"shot_id": 11');
    expect(debug.textContent).toContain('"foo": "bar"');
    expect(debug.textContent).toContain('h3');
    const log = screen.getByTestId('job-log').textContent ?? '';
    expect(log).not.toContain('File "a.py"');
    expect(log).toContain('RuntimeError: 连不上 ComfyUI');
    expect(screen.getByText('完整日志').closest('details')!.textContent).toContain('File "a.py"');
  });

  it('普通错误不给去设置的链接；没有调用栈时没有完整日志折叠', async () => {
    const job = makeJob({ ...FAILED, error: '提示词为空' });
    installFetch({ 'GET /api/jobs': [job], 'GET /api/jobs/3': job });
    renderAt(<JobsPage />, { route: '/jobs?job=3', path: '/jobs' });
    expect(await screen.findByTestId('job-status')).toBeTruthy();
    expect(screen.queryByRole('link', { name: '去设置检查' })).toBeNull();
    expect(screen.queryByText('完整日志')).toBeNull();
  });

  it('加载更多：每次多拿 100 条', async () => {
    const many = Array.from({ length: 100 }, (_, i) => makeJob({ id: 1000 - i, status: 'done' }));
    const calls = installFetch({ 'GET /api/jobs?limit=100': many, 'GET /api/jobs?limit=200': [...many, makeJob({ id: 1, status: 'done' })] });
    renderAt(<JobsPage />, { route: '/jobs', path: '/jobs' });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '加载更多' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/jobs?limit=200')).toBe(true));
    expect(await screen.findByRole('button', { name: '查看任务 #1' })).toBeTruthy();
    // 不满一页：没有更多了
    expect(screen.queryByRole('button', { name: '加载更多' })).toBeNull();
  });

  it('失败筛选时可以重试全部失败：先确认写数量，逐个重试，报几个成功', async () => {
    const a = makeJob({ id: 3, status: 'failed', error: 'x' });
    const b = makeJob({ id: 5, status: 'failed', error: 'y' });
    const calls = installFetch({
      'GET /api/jobs?status=failed&limit=100': [a, b],
      'POST /api/jobs/3/retry': { job_id: 30 },
      'POST /api/jobs/5/retry': () => new Response(JSON.stringify({ detail: '任务已在重试' }), { status: 409 }),
    });
    renderAt(<JobsPage />, { route: '/jobs?status=failed', path: '/jobs' });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '重试全部失败（2）' }));
    const dlg = screen.getByRole('dialog', { name: '重试全部 2 个失败任务？' });
    await user.click(within(dlg).getByRole('button', { name: '全部重试' }));
    expect(await screen.findByText('已重新提交 1/2 个失败任务（1 个没成功：任务已在重试（HTTP 409））')).toBeTruthy();
    expect(calls.filter((c) => c.method === 'POST').map((c) => c.url)).toEqual(['/api/jobs/3/retry', '/api/jobs/5/retry']);
  });

  it('不是失败筛选时没有「重试全部失败」', async () => {
    installFetch({ 'GET /api/jobs': [FAILED] });
    renderAt(<JobsPage />, { route: '/jobs', path: '/jobs' });
    await screen.findByRole('button', { name: '查看任务 #3' });
    expect(screen.queryByRole('button', { name: /重试全部失败/ })).toBeNull();
  });
});
