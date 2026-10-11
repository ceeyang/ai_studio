import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { JobTrace } from '../../api/types';
import { makeJob } from '../../test/factories';
import { installFetch } from '../../test/fetch';
import { renderAt } from '../../test/render';
import { JobsPage } from '../JobsPage';
import { ConversationLine, JobTraces, mediaUrl, traceSummary } from './JobTraces';

const LLM: JobTrace = {
  id: 1,
  seq: 1,
  kind: 'llm',
  title: 'storyboard',
  created_at: '2026-10-11T10:00:00Z',
  data: {
    provider: 'claude',
    model: 'claude',
    step: 'storyboard',
    attempt: 1,
    max_attempts: 3,
    seconds: 18.62,
    status: 'invalid',
    system: '你是分镜师 SYSTEM',
    user: '第 1 集原文 USER',
    reply: '{"shots": []}',
    error: '至少要 3 个镜头',
    followups: [],
    usage: { input_tokens: 1200, output_tokens: 300 },
  },
};

const ENGINE: JobTrace = {
  id: 2,
  seq: 2,
  kind: 'engine',
  title: '出图 · h3_image · 首帧 S03',
  created_at: '2026-10-11T10:01:00Z',
  data: {
    engine: 'h3_image',
    runner: 'comfyui',
    output_kind: 'image',
    what: '首帧 S03',
    status: 'ok',
    prompt: 'ANCHOR a woman in a studio',
    negative: 'blurry',
    negative_sent: false,
    refs: [
      { path: 'demo/media/location/a.png', label: '车间', role: '场景图', sent: true },
      { path: 'demo/media/portrait/b.png', label: '林夏 定妆照', role: '定妆照', sent: true },
    ],
    params: { aspect: '9:16', seed: 12345, steps: 20, size: [768, 1344] },
    workflow: { '5': { class_type: 'CLIPTextEncode', inputs: { text: 'ANCHOR a woman' } } },
    workflow_file: 'demo/runs/7/workflow.api.json',
    outputs: ['demo/runs/7/out_1.png'],
    candidate_ids: [42],
    candidate_files: ['demo/media/first_frame/c.png'],
    seconds: 31.2,
  },
};

const FFMPEG: JobTrace = {
  id: 3,
  seq: 3,
  kind: 'ffmpeg',
  title: 'ffmpeg · final.mp4',
  created_at: null,
  data: { argv: ['ffmpeg', '-y', '-i', 'a.mp4', '-filter_complex', '[0:v]scale=1:1[v]', 'final.mp4'], filter_complex: '[0:v]scale=1:1[v]', status: 'ok', seconds: 2.5 },
};

afterEach(() => vi.unstubAllGlobals());

describe('JobTraces', () => {
  it('每条一行摘要', () => {
    expect(traceSummary(LLM)).toBe('LLM · storyboard · claude · 第 1 次 · 18.6s');
    expect(traceSummary(ENGINE)).toBe('出图 · h3_image · 首帧 S03 · 9:16 · seed 12345 · 耗时 31.2s');
    expect(traceSummary(FFMPEG)).toBe('ffmpeg · final.mp4 · 2.5s');
    expect(mediaUrl('demo/media/a b.png')).toBe('/media/demo/media/a%20b.png');
    expect(mediaUrl('demo/runs/7/out.png')).toBeNull();
  });

  it('按顺序列出调用；点开看提示词、原始回复、校验错误；复制按钮写剪贴板', async () => {
    installFetch({ 'GET /api/jobs/7/traces': { job_id: 7, traces: [LLM, ENGINE, FFMPEG] } });
    const writeText = vi.fn().mockResolvedValue(undefined);
    renderAt(<JobTraces jobId={7} active={false} />);
    const user = userEvent.setup();
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const listEl = await screen.findByRole('list', { name: '调用明细' });
    const rows = within(listEl).getAllByRole('listitem').filter((li) => li.classList.contains('trace-row'));
    expect(rows.map((r) => r.querySelector('.trace-summary')?.textContent)).toEqual([
      'LLM · storyboard · claude · 第 1 次 · 18.6s',
      '出图 · h3_image · 首帧 S03 · 9:16 · seed 12345 · 耗时 31.2s',
      'ffmpeg · final.mp4 · 2.5s',
    ]);
    expect(within(rows[0]).getByText('输出不合格')).toBeTruthy();

    await user.click(screen.getByText('LLM · storyboard · claude · 第 1 次 · 18.6s'));
    expect(within(rows[0]).getByText('你是分镜师 SYSTEM')).toBeTruthy();
    expect(within(rows[0]).getByText('第 1 集原文 USER')).toBeTruthy();
    expect(within(rows[0]).getByText('{"shots": []}')).toBeTruthy();
    expect(within(rows[0]).getByText('校验错误')).toBeTruthy();
    expect(within(rows[0]).getByText('至少要 3 个镜头')).toBeTruthy();
    await user.click(within(rows[0]).getByRole('button', { name: '复制System 提示词' }));
    expect(writeText).toHaveBeenCalledWith('你是分镜师 SYSTEM');
    expect(await within(rows[0]).findByText('已复制')).toBeTruthy();
  });

  it('引擎调用：最终提示词、负面词、参考图缩略图（能点开看大图）、参数和 workflow', async () => {
    installFetch({ 'GET /api/jobs/7/traces': { job_id: 7, traces: [ENGINE] } });
    renderAt(<JobTraces jobId={7} active={false} />);
    const user = userEvent.setup();
    await user.click(await screen.findByText(/出图 · h3_image · 首帧 S03/));
    expect(screen.getByText('ANCHOR a woman in a studio')).toBeTruthy();
    expect(screen.getByText('负面词（这个引擎不吃负面词，没有发送）')).toBeTruthy();
    expect(screen.getByText('参考图（按发送顺序，2 张）')).toBeTruthy();
    const thumb = screen.getByRole('img', { name: '1. 场景图 · 车间' });
    expect(thumb.getAttribute('src')).toBe('/media/demo/media/location/a.png');
    expect(screen.getByText('12345')).toBeTruthy();
    expect(screen.getByText('#42')).toBeTruthy();
    // 任务目录里的产物不能直接打开：只写路径
    expect(screen.getByText('demo/runs/7/out_1.png')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '看大图：2. 定妆照 · 林夏 定妆照' }));
    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).getByRole('img', { name: '2. 定妆照 · 林夏 定妆照' })).toBeTruthy();
    expect(screen.getByText('workflow JSON', { selector: 'summary' })).toBeTruthy();
  });

  it('旧任务没有明细时写明；进行中的任务写还没有', async () => {
    installFetch({ 'GET /api/jobs/5/traces': { job_id: 5, traces: [] } });
    renderAt(<JobTraces jobId={5} active={false} />);
    expect((await screen.findByTestId('trace-empty')).textContent).toBe('这个任务是旧版本跑的，没有调用明细。');
  });

  it('任务详情在日志下面显示调用明细，调试信息还在', async () => {
    const job = makeJob({ id: 7, type: 'storyboard', status: 'done', log: '[10:00:00] 开始\n' });
    installFetch({
      'GET /api/jobs': [job],
      'GET /api/jobs/7': job,
      'GET /api/jobs/7/traces': { job_id: 7, traces: [LLM] },
    });
    renderAt(<JobsPage />, { route: '/jobs?job=7', path: '/jobs' });
    await screen.findByRole('list', { name: '调用明细' });
    const headings = screen.getAllByRole('heading', { level: 4 }).map((h) => h.textContent);
    expect(headings.indexOf('调用明细')).toBeGreaterThan(headings.indexOf('日志'));
    expect(screen.getByText('调试信息')).toBeTruthy();
    await waitFor(() => expect(screen.getByText('LLM · storyboard · claude · 第 1 次 · 18.6s')).toBeTruthy());
  });
});

describe('调用明细里的网页会话', () => {
  it('续接：「续接会话 <链接>」，链接在新标签页打开', () => {
    renderAt(<ConversationLine conv={{ mode: 'continue', url: 'https://chatgpt.com/c/abc', reason: null, site: 'chatgpt', scope: 'shot:11', turns: 3 }} />);
    const line = screen.getByTestId('trace-conversation');
    expect(line.textContent).toBe('续接会话 https://chatgpt.com/c/abc');
    const a = within(line).getByRole('link');
    expect(a.getAttribute('href')).toBe('https://chatgpt.com/c/abc');
    expect(a.getAttribute('target')).toBe('_blank');
  });

  it('新开：有原因时括号里写；url 空时只写文字；没有 conversation 不显示', () => {
    const { unmount } = renderAt(
      <ConversationLine conv={{ mode: 'new', url: null, reason: '用户勾了新开对话', site: 'gemini', scope: 'character:6', turns: 0 }} />,
    );
    const line = screen.getByTestId('trace-conversation');
    expect(line.textContent).toBe('新开会话（用户勾了新开对话）');
    expect(within(line).queryByRole('link')).toBeNull();
    unmount();
    renderAt(<ConversationLine conv={undefined} />);
    expect(screen.queryByTestId('trace-conversation')).toBeNull();
  });

  it('引擎调用的明细里显示会话行', async () => {
    installFetch({
      'GET /api/jobs/7/traces': {
        job_id: 7,
        traces: [{ ...ENGINE, data: { ...ENGINE.data, conversation: { mode: 'new', url: 'https://gemini.google.com/app/x', reason: '超过 20 轮', site: 'gemini', scope: 'shot:3', turns: 21 } } }],
      },
    });
    renderAt(<JobTraces jobId={7} active={false} />);
    const line = await screen.findByTestId('trace-conversation');
    expect(line.textContent).toBe('新开会话 https://gemini.google.com/app/x（超过 20 轮）');
  });
});
