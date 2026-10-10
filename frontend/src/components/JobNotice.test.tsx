import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { installFetch } from '../test/fetch';
import { makeJob } from '../test/factories';
import { renderAt } from '../test/render';
import { useJobNotice } from './JobNotice';

function Probe({ ids, text = '出首帧' }: { ids: number[]; text?: string }) {
  const notice = useJobNotice();
  return (
    <div>
      <button type="button" onClick={() => notice.track(ids, text)}>提交</button>
      <button type="button" onClick={() => notice.error('剧本还没跑完（HTTP 400）')}>出错</button>
      <button type="button" onClick={() => notice.info('已加入队列', { action: { label: '去看看', to: '/jobs' } })}>提示</button>
      <div data-testid="notice">{notice.view}</div>
    </div>
  );
}

describe('useJobNotice', () => {
  it('全部完成：显示「完成：<text>」，6 秒后自动消失', async () => {
    installFetch({
      'GET /api/jobs/5': makeJob({ id: 5, type: 'image.keyframe', status: 'done', result: {} }),
      'GET /api/jobs/6': makeJob({ id: 6, type: 'image.keyframe', status: 'done', result: {} }),
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderAt(<Probe ids={[5, 6]} />);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await user.click(screen.getByRole('button', { name: '提交' }));
    expect(await screen.findByText('完成：出首帧')).toBeTruthy();
    await act(async () => {
      vi.advanceTimersByTime(6100);
    });
    expect(screen.queryByText('完成：出首帧')).toBeNull();
  });

  it('有失败：显示第一条错误和「查看任务」链接，用错误样式、能手动关掉', async () => {
    installFetch({
      'GET /api/jobs/5': makeJob({ id: 5, type: 'image.keyframe', status: 'done', result: {} }),
      'GET /api/jobs/6': makeJob({ id: 6, type: 'image.keyframe', status: 'failed', error: 'ComfyUI 连不上' }),
    });
    renderAt(<Probe ids={[5, 6]} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '提交' }));
    const msg = await screen.findByText(/失败：ComfyUI 连不上/);
    expect(msg.closest('.error')).toBeTruthy();
    expect(screen.getByRole('link', { name: '查看任务' }).getAttribute('href')).toBe('/jobs?job=6');
    await user.click(screen.getByRole('button', { name: '关闭提示' }));
    expect(screen.queryByText(/失败：/)).toBeNull();
  });

  it('失败原因是多行（例如报错堆栈）：只显示第一行，完整内容放在悬停提示里', async () => {
    const full = 'ComfyUI 出错\nTraceback (most recent call last):\n  File "x.py", line 1';
    installFetch({ 'GET /api/jobs/6': makeJob({ id: 6, type: 'image.keyframe', status: 'failed', error: full }) });
    renderAt(<Probe ids={[6]} />);
    await userEvent.setup().click(screen.getByRole('button', { name: '提交' }));
    const msg = await screen.findByText('失败：ComfyUI 出错');
    expect(msg.textContent).not.toContain('Traceback');
    expect(msg.getAttribute('title')).toBe(full);
  });

  it('全部完成但都因输入没变被跳过：说明已经是最新的', async () => {
    installFetch({
      'GET /api/jobs/5': makeJob({ id: 5, type: 'image.keyframe', status: 'done', result: { skipped: true } }),
    });
    renderAt(<Probe ids={[5]} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '提交' }));
    expect(await screen.findByText('已跳过：输入没变，已经是最新的')).toBeTruthy();
  });

  it('有任务在等人工输入：提示去填写', async () => {
    installFetch({
      'GET /api/jobs/7': makeJob({ id: 7, type: 'script', status: 'waiting_input' }),
    });
    renderAt(<Probe ids={[7]} text="跑剧本" />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '提交' }));
    expect(await screen.findByText(/在等人工输入/)).toBeTruthy();
    expect(screen.getByRole('link', { name: '去填写' }).getAttribute('href')).toBe('/jobs?job=7');
  });

  it('还在跑时显示进行中；error / info 直接显示', async () => {
    installFetch({ 'GET /api/jobs/5': makeJob({ id: 5, status: 'running' }) });
    renderAt(<Probe ids={[5]} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '提交' }));
    expect(await screen.findByText(/出首帧.*进行中/)).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '出错' }));
    expect(screen.getByText('剧本还没跑完（HTTP 400）').closest('.error')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '提示' }));
    expect(screen.getByText('已加入队列')).toBeTruthy();
    expect(screen.getByRole('link', { name: '去看看' }).getAttribute('href')).toBe('/jobs');
  });
});
