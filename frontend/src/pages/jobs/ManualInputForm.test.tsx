import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { installFetch, json } from '../../test/fetch';
import { makeJob } from '../../test/factories';
import { renderAt } from '../../test/render';
import { ManualInputForm } from './ManualInputForm';

const WAITING = makeJob({
  id: 9,
  status: 'waiting_input',
  result: { input_request: { key: 'script_structure', task: 'script', system: '你是编剧', user: '梗概：深夜的直播间' } },
});

describe('ManualInputForm', () => {
  it('显示 key 和两段提示词；粘贴后提交，成功后清空', async () => {
    const calls = installFetch({ 'POST /api/jobs/9/input': makeJob({ id: 9, status: 'queued' }) });
    const onSubmitted = vi.fn();
    renderAt(<ManualInputForm job={WAITING} onSubmitted={onSubmitted} />);
    const user = userEvent.setup();
    expect(screen.getByTestId('input-key').textContent).toBe('script_structure');
    expect((screen.getByLabelText('系统提示词（system）') as HTMLTextAreaElement).value).toBe('你是编剧');
    expect((screen.getByLabelText('用户提示词（user）') as HTMLTextAreaElement).value).toBe('梗概：深夜的直播间');
    const btn = screen.getByRole('button', { name: '提交结果' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    const box = screen.getByLabelText('模型输出') as HTMLTextAreaElement;
    await user.click(box);
    await user.paste('{"scenes": []}');
    await user.click(btn);
    await waitFor(() => expect(onSubmitted).toHaveBeenCalledWith(expect.objectContaining({ id: 9, status: 'queued' })));
    expect(calls[0]).toMatchObject({ method: 'POST', url: '/api/jobs/9/input', body: { text: '{"scenes": []}' } });
    expect(box.value).toBe('');
  });

  it('上次粘贴不合格时显示原因', () => {
    const job = makeJob({
      ...WAITING,
      result: { input_request: { key: 'script_structure', task: 'script', system: 's', user: 'u', error: '缺少字段 scenes' } },
    });
    renderAt(<ManualInputForm job={job} />);
    expect(screen.getByText('上次粘贴的结果不合格：缺少字段 scenes')).toBeTruthy();
  });

  it('提交被后端拒绝时显示原因', async () => {
    installFetch({ 'POST /api/jobs/9/input': () => json({ detail: '任务 9 不在等待输入状态（当前 queued）' }, 400) });
    renderAt(<ManualInputForm job={WAITING} />);
    const user = userEvent.setup();
    await user.click(screen.getByLabelText('模型输出'));
    await user.paste('{}');
    await user.click(screen.getByRole('button', { name: '提交结果' }));
    expect(await screen.findByText('任务 9 不在等待输入状态（当前 queued）（HTTP 400）')).toBeTruthy();
  });

  // Controller ruling：两阶段场景——任务停在 waiting_input，粘贴提交后任务又回到 waiting_input，
  // 但带着一个*新*的 input_request.error（比如后端拒绝了刚粘贴的 JSON）。JobsPage 按
  // `任务id:key:error` 换 key，这时会整体重新挂载一个干净的表单（用 unmount + 重新 render 模拟），
  // 要断言：输入框是空的，显示的是新错误，不是旧错误。
  it('两阶段：提交后任务带着新的 input_request.error 回到 waiting_input，新请求是干净的输入框', async () => {
    const OLD = makeJob({
      id: 9,
      status: 'waiting_input',
      result: {
        input_request: {
          key: 'script_structure',
          task: 'script',
          system: '你是编剧',
          user: '梗概：深夜的直播间',
          error: '旧错误：JSON 解析失败',
        },
      },
    });
    const NEW = makeJob({
      id: 9,
      status: 'waiting_input',
      result: {
        input_request: {
          key: 'script_structure',
          task: 'script',
          system: '你是编剧',
          user: '梗概：深夜的直播间',
          error: '新错误：缺少字段 scenes',
        },
      },
    });
    installFetch({ 'POST /api/jobs/9/input': NEW });
    const onSubmitted = vi.fn();
    const { unmount } = renderAt(<ManualInputForm job={OLD} onSubmitted={onSubmitted} />);
    const user = userEvent.setup();
    expect(screen.getByText('上次粘贴的结果不合格：旧错误：JSON 解析失败')).toBeTruthy();
    await user.click(screen.getByLabelText('模型输出'));
    await user.paste('{"bad": true}');
    await user.click(screen.getByRole('button', { name: '提交结果' }));
    await waitFor(() => expect(onSubmitted).toHaveBeenCalledWith(expect.objectContaining({ id: 9, status: 'waiting_input' })));
    // JobsPage 会因为 req.error 变了而换 key，整体重新挂载一个新的表单实例
    unmount();

    renderAt(<ManualInputForm job={NEW} />);
    expect((screen.getByLabelText('模型输出') as HTMLTextAreaElement).value).toBe('');
    expect(screen.getByText('上次粘贴的结果不合格：新错误：缺少字段 scenes')).toBeTruthy();
    expect(screen.queryByText('上次粘贴的结果不合格：旧错误：JSON 解析失败')).toBeNull();
  });
});
