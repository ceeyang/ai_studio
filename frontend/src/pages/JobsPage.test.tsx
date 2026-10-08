import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
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
    await screen.findByRole('cell', { name: '3' });
    expect(screen.getByRole('cell', { name: '42 秒' })).toBeTruthy();
    await user.selectOptions(screen.getByLabelText('状态筛选'), 'failed');
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/jobs?status=failed'));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/jobs?status=failed&limit=100')).toBe(true));
    await user.click(screen.getByRole('cell', { name: '3' }));
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
});
