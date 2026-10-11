import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Job } from '../api/types';
import { installFetch, json } from '../test/fetch';
import { makeJob, makeProject } from '../test/factories';
import { renderAt } from '../test/render';
import type { EventsState } from '../ws/events';
import { EventsContext } from '../ws/EventsContext';
import { JobsPage } from './JobsPage';

const CTX = { label: '第 1 集 S01', url: '/projects/demo/episodes/4/director?shot=11', project_slug: 'demo', episode_id: 4, shot_id: 11, character_id: null };

const FAILED = makeJob({
  id: 3,
  type: 'video.shot',
  status: 'failed',
  error: 'ComfyUI 拒绝工作流：节点 7',
  started_at: '2026-10-08T10:00:00Z',
  finished_at: '2026-10-08T10:00:42Z',
  log: '[10:00:00] 视频 S01 · h3_video\n',
});

const ago = (ms: number) => new Date(Date.now() - ms).toISOString();

function rowIds() {
  return within(screen.getByRole('listbox', { name: '任务列表' }))
    .getAllByRole('option')
    .map((o) => o.id);
}

function location() {
  return screen.getByTestId('location').textContent;
}

function renderPage(route: string, events?: EventsState) {
  const ui = events ? (
    <EventsContext.Provider value={events}>
      <JobsPage />
    </EventsContext.Provider>
  ) : (
    <JobsPage />
  );
  return renderAt(ui, { route, path: '/jobs' });
}

describe('JobsPage · 列表与筛选', () => {
  it('页头「03 · 任务」；状态分段带数量，点了写进地址栏；重试失败任务后跳到新任务', async () => {
    const done = makeJob({ id: 2, status: 'done' });
    const calls = installFetch({
      'GET /api/jobs?limit=100': [FAILED, done],
      'GET /api/jobs?status=failed&limit=100': [FAILED],
      'GET /api/jobs/3': FAILED,
      'POST /api/jobs/3/retry': { job_id: 4 },
      'GET /api/jobs/4': makeJob({ id: 4, type: 'video.shot', status: 'queued' }),
    });
    renderPage('/jobs');
    const user = userEvent.setup();
    expect(screen.getByText('03 · 任务')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 2, name: '任务' })).toBeTruthy();
    await screen.findByRole('option', { name: /#3/ });
    const seg = screen.getByRole('group', { name: '状态筛选' });
    const failedBtn = within(seg).getByRole('button', { name: /失败/ });
    expect(failedBtn.textContent).toBe('失败1');
    expect(within(seg).getByRole('button', { name: /全部/ }).getAttribute('aria-pressed')).toBe('true');
    await user.click(failedBtn);
    await waitFor(() => expect(location()).toBe('/jobs?status=failed'));
    expect(failedBtn.getAttribute('aria-pressed')).toBe('true');
    await waitFor(() => expect(calls.some((c) => c.url === '/api/jobs?status=failed&limit=100')).toBe(true));
    await user.click(screen.getByRole('option', { name: /#3/ }));
    expect((await screen.findByTestId('job-status')).textContent).toBe('失败');
    await user.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(location()).toBe('/jobs?job=4'));
    await waitFor(() => expect(screen.getByTestId('job-status').textContent).toBe('排队中'));
  });

  it('搜索写进地址栏（q），能按 #编号、镜头号筛；类型筛选按步骤分组', async () => {
    const shot = makeJob({ id: 11, type: 'video.shot', status: 'done', context: { ...CTX, label: '第 1 集 S07' } });
    const kf = makeJob({ id: 12, type: 'image.continue_frame', status: 'done', context: { ...CTX, label: '第 1 集 S08' } });
    const script = makeJob({ id: 13, type: 'script', status: 'done' });
    installFetch({ 'GET /api/jobs': [shot, kf, script] });
    renderPage('/jobs');
    const user = userEvent.setup();
    await screen.findByRole('option', { name: /#13/ });
    await user.type(screen.getByRole('searchbox', { name: '搜索任务' }), 'S07');
    await waitFor(() => expect(location()).toBe('/jobs?q=S07'));
    expect(rowIds()).toEqual(['job-row-11']);
    await user.clear(screen.getByRole('searchbox', { name: '搜索任务' }));
    await user.type(screen.getByRole('searchbox', { name: '搜索任务' }), '#13');
    expect(rowIds()).toEqual(['job-row-13']);
    await user.clear(screen.getByRole('searchbox', { name: '搜索任务' }));
    await user.selectOptions(screen.getByLabelText('类型筛选'), 'keyframe');
    await waitFor(() => expect(location()).toBe('/jobs?type=keyframe'));
    // 「接上一镜尾帧」也算首帧
    expect(rowIds()).toEqual(['job-row-12']);
    expect(screen.getByText('在已加载的 3 条里筛出 1 条')).toBeTruthy();
  });

  it('地址栏直达筛选；项目筛选走后端；没有结果时给空态和「清除筛选」', async () => {
    const calls = installFetch({
      'GET /api/projects': [makeProject({ slug: 'demo', title: '演示' }), makeProject({ id: 2, slug: 'other', title: '另一个' })],
      'GET /api/jobs?project=demo&limit=100': [makeJob({ id: 5, type: 'script', status: 'done' })],
      'GET /api/jobs?status=failed&project=demo&limit=100': [],
    });
    renderPage('/jobs?status=failed&project=demo');
    const user = userEvent.setup();
    expect(await screen.findByTestId('jobs-empty')).toBeTruthy();
    expect(screen.getByText('没有符合筛选的任务')).toBeTruthy();
    await waitFor(() => expect((screen.getByLabelText('项目筛选') as HTMLSelectElement).value).toBe('demo'));
    expect(calls.some((c) => c.url === '/api/jobs?status=failed&project=demo&limit=100')).toBe(true);
    await user.click(within(screen.getByTestId('jobs-empty')).getByRole('button', { name: '清除筛选' }));
    await waitFor(() => expect(location()).toBe('/jobs'));
  });

  it('没有任何任务：空态说明任务从哪儿来', async () => {
    installFetch({ 'GET /api/jobs': [] });
    renderPage('/jobs');
    expect((await screen.findByTestId('jobs-empty')).textContent).toContain('还没有任务');
    expect(screen.queryByRole('button', { name: '清除筛选' })).toBeNull();
  });

  it('加载中显示骨架；加载失败显示错误和「重新加载」', async () => {
    let fail = true;
    installFetch({
      'GET /api/jobs': () => (fail ? json({ detail: '数据库忙' }, 500) : [FAILED]),
    });
    renderPage('/jobs');
    expect(screen.getByTestId('list-skeleton')).toBeTruthy();
    expect(await screen.findByText(/任务列表加载失败/)).toBeTruthy();
    fail = false;
    await userEvent.setup().click(screen.getByRole('button', { name: '重新加载' }));
    expect(await screen.findByRole('option', { name: /#3/ })).toBeTruthy();
  });

  it('按日期分组：今天 / 昨天 / 更早', async () => {
    const today = makeJob({ id: 30, status: 'done', created_at: ago(60_000) });
    const old = makeJob({ id: 10, status: 'done', created_at: '2020-01-01T10:00:00Z' });
    const y = new Date();
    y.setDate(y.getDate() - 1);
    y.setHours(12, 0, 0, 0);
    const yesterday = makeJob({ id: 20, status: 'done', created_at: y.toISOString() });
    installFetch({ 'GET /api/jobs': [today, yesterday, old] });
    renderPage('/jobs');
    await screen.findByRole('option', { name: /#30/ });
    const groups = screen.getAllByRole('group').filter((g) => g.classList.contains('job-group'));
    expect(groups.map((g) => g.getAttribute('aria-labelledby'))).toEqual(['jobs-group-today', 'jobs-group-yesterday', 'jobs-group-earlier']);
    expect(within(groups[0]).getByRole('option').textContent).toContain('1 分钟前');
    expect(within(groups[1]).getByRole('option').textContent).toContain('昨天 12:00');
    // 相对时间悬停看绝对时间
    expect(within(groups[2]).getByRole('option').querySelector('time')!.getAttribute('title')).toMatch(/2020/);
  });

  it('失败行只显示一行简短原因（截断），悬停看全文', async () => {
    const long = `显存不足，模型加载失败：${'很长的原因'.repeat(30)}\nTraceback 第二行不该出现`;
    installFetch({ 'GET /api/jobs': [makeJob({ ...FAILED, error: long })] });
    renderPage('/jobs');
    const err = await screen.findByTestId('row-error');
    expect(err.textContent!.length).toBeLessThanOrEqual(80);
    expect(err.textContent!.endsWith('…')).toBe(true);
    expect(err.textContent).not.toContain('第二行');
    expect(err.getAttribute('title')).toBe(long);
  });

  it('运行中：有进度按进度画细进度条，没有就是不定进度；耗时实时显示', async () => {
    const a = makeJob({ id: 7, type: 'video.shot', status: 'running', started_at: ago(65_000) });
    const b = makeJob({ id: 8, type: 'script', status: 'running', started_at: ago(5_000) });
    installFetch({ 'GET /api/jobs': [a, b] });
    renderPage('/jobs', { connected: true, everConnected: true, progress: { 7: { value: 12, max: 30 } } });
    const bar = await screen.findByRole('progressbar', { name: '任务 #7 进度' });
    expect(bar.getAttribute('aria-valuenow')).toBe('12');
    expect(bar.getAttribute('aria-valuemax')).toBe('30');
    const ind = screen.getByRole('progressbar', { name: '任务 #8 进度' });
    expect(ind.className).toContain('is-indeterminate');
    expect(ind.getAttribute('aria-valuenow')).toBeNull();
    expect(screen.getByRole('option', { name: /#7/ }).textContent).toMatch(/已运行 1 分 \d+ 秒/);
  });

  it('键盘：列表里 ↑↓ 移动、Enter 打开详情', async () => {
    installFetch({
      'GET /api/jobs': [makeJob({ id: 3, status: 'done' }), makeJob({ id: 2, status: 'done' }), makeJob({ id: 1, status: 'done' })],
      'GET /api/jobs/2': makeJob({ id: 2, status: 'done' }),
    });
    renderPage('/jobs');
    const user = userEvent.setup();
    const list = await screen.findByRole('listbox', { name: '任务列表' });
    list.focus();
    await user.keyboard('{ArrowDown}');
    expect(list.getAttribute('aria-activedescendant')).toBe('job-row-3');
    await user.keyboard('{ArrowDown}');
    expect(list.getAttribute('aria-activedescendant')).toBe('job-row-2');
    await user.keyboard('{ArrowDown}{ArrowDown}{ArrowUp}');
    expect(list.getAttribute('aria-activedescendant')).toBe('job-row-2');
    await user.keyboard('{Enter}');
    await waitFor(() => expect(location()).toBe('/jobs?job=2'));
    expect(screen.getByRole('option', { name: /#2/ }).getAttribute('aria-selected')).toBe('true');
  });

  it('加载更多：每次多拿 100 条', async () => {
    const many = Array.from({ length: 100 }, (_, i) => makeJob({ id: 1000 - i, status: 'done' }));
    const calls = installFetch({ 'GET /api/jobs?limit=100': many, 'GET /api/jobs?limit=200': [...many, makeJob({ id: 1, status: 'done' })] });
    renderPage('/jobs');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '加载更多' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/jobs?limit=200')).toBe(true));
    await waitFor(() => expect(rowIds()).toContain('job-row-1'));
    expect(screen.queryByRole('button', { name: '加载更多' })).toBeNull();
  });

  it('失败筛选时可以重试全部失败：先确认写数量，逐个重试，报几个成功', async () => {
    const a = makeJob({ id: 3, status: 'failed', error: 'x' });
    const b = makeJob({ id: 5, status: 'failed', error: 'y' });
    const calls = installFetch({
      'GET /api/jobs?status=failed&limit=100': [a, b],
      'GET /api/jobs?limit=100': [a, b],
      'POST /api/jobs/3/retry': { job_id: 30 },
      'POST /api/jobs/5/retry': () => new Response(JSON.stringify({ detail: '任务已在重试' }), { status: 409 }),
    });
    renderPage('/jobs?status=failed');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '重试全部失败（2）' }));
    const dlg = screen.getByRole('dialog', { name: '重试全部 2 个失败任务？' });
    await user.click(within(dlg).getByRole('button', { name: '全部重试' }));
    expect(await screen.findByText('已重新提交 1/2 个失败任务（1 个没成功：任务已在重试（HTTP 409））')).toBeTruthy();
    expect(calls.filter((c) => c.method === 'POST').map((c) => c.url)).toEqual(['/api/jobs/3/retry', '/api/jobs/5/retry']);
  });

  it('不是失败筛选时没有「重试全部失败」', async () => {
    installFetch({ 'GET /api/jobs': [FAILED] });
    renderPage('/jobs');
    await screen.findByRole('option', { name: /#3/ });
    expect(screen.queryByRole('button', { name: /重试全部失败/ })).toBeNull();
  });
});

describe('JobsPage · 详情', () => {
  it('没选任务时右侧提示怎么选；选了能关掉（地址栏去掉 job）', async () => {
    installFetch({ 'GET /api/jobs': [FAILED], 'GET /api/jobs/3': FAILED });
    renderPage('/jobs');
    const user = userEvent.setup();
    expect(await screen.findByText('选一个任务查看详情')).toBeTruthy();
    await user.click(await screen.findByRole('option', { name: /#3/ }));
    await screen.findByTestId('job-status');
    await user.click(screen.getByRole('button', { name: '关闭详情' }));
    await waitFor(() => expect(location()).toBe('/jobs'));
  });

  it('失败：醒目的错误卡片——第一行加粗，后面的和调用栈收进「技术细节」；环境类错误给「去设置检查」；所属对象链接', async () => {
    const job: Job = makeJob({
      ...FAILED,
      error: '连不上 ComfyUI\nConnectError: 127.0.0.1:8188',
      log: '[10:00] 视频 S01\nTraceback (most recent call last):\n  File "a.py", line 3, in run\nRuntimeError: 连不上 ComfyUI\n',
      context: CTX,
    });
    installFetch({ 'GET /api/jobs': [job], 'GET /api/jobs/3': job });
    renderPage('/jobs?job=3');
    const card = await screen.findByRole('region', { name: '失败原因' });
    expect(within(card).getByTestId('job-error-head').tagName).toBe('STRONG');
    expect(within(card).getByTestId('job-error-head').textContent).toBe('连不上 ComfyUI');
    const more = within(card).getByText('技术细节').closest('details')!;
    expect(more.textContent).toContain('ConnectError: 127.0.0.1:8188');
    expect(more.textContent).toContain('File "a.py"');
    expect(within(card).getByRole('button', { name: '复制错误信息' })).toBeTruthy();
    expect(within(card).getByRole('link', { name: '去设置检查' }).getAttribute('href')).toBe('/settings');
    expect(screen.getByRole('link', { name: '打开所属对象' }).getAttribute('href')).toBe(CTX.url);
  });

  it('普通错误不给去设置的链接；没有更多细节时没有「技术细节」', async () => {
    const job = makeJob({ ...FAILED, error: '提示词为空' });
    installFetch({ 'GET /api/jobs': [job], 'GET /api/jobs/3': job });
    renderPage('/jobs?job=3');
    expect(await screen.findByRole('region', { name: '失败原因' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: '去设置检查' })).toBeNull();
    expect(screen.queryByText('技术细节')).toBeNull();
  });

  it('页签：概览 / 日志 / 调用明细 / 调试信息，切换写进地址栏，←→ 键切换', async () => {
    const job: Job = makeJob({
      ...FAILED,
      model_family: 'h3',
      payload: { shot_id: 11 },
      result: { foo: 'bar' },
      log: '[10:00] 视频 S01\nTraceback (most recent call last):\n  File "a.py", line 3, in run\nRuntimeError: 连不上 ComfyUI\n',
    });
    installFetch({ 'GET /api/jobs': [job], 'GET /api/jobs/3': job, 'GET /api/jobs/3/traces': { job_id: 3, traces: [] } });
    renderPage('/jobs?job=3');
    const user = userEvent.setup();
    const tabs = await screen.findByRole('tablist', { name: '任务详情页签' });
    expect(within(tabs).getAllByRole('tab').map((t) => t.textContent)).toEqual(['概览', '日志', '调用明细', '调试信息']);
    expect(within(tabs).getByRole('tab', { name: '概览' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('list', { name: '时间线' }).textContent).toContain('耗时 42 秒');

    await user.click(within(tabs).getByRole('tab', { name: '日志' }));
    await waitFor(() => expect(location()).toBe('/jobs?job=3&tab=log'));
    const log = screen.getByTestId('job-log');
    expect(log.textContent).not.toContain('File "a.py"');
    expect(log.textContent).toContain('RuntimeError: 连不上 ComfyUI');
    expect(log.querySelector('.log-line')!.getAttribute('data-n')).toBe('1');
    await user.click(screen.getByLabelText('显示调用栈'));
    expect(screen.getByTestId('job-log').textContent).toContain('File "a.py"');
    await user.type(screen.getByRole('searchbox', { name: '搜索日志' }), 'runtime');
    expect(screen.getByTestId('job-log').querySelectorAll('.log-line')).toHaveLength(1);
    expect(screen.getByTestId('job-log').querySelector('mark')!.textContent).toBe('Runtime');
    expect(screen.getByRole('button', { name: '复制日志' })).toBeTruthy();

    within(tabs).getByRole('tab', { name: '日志' }).focus();
    await user.keyboard('{ArrowRight}');
    expect(within(tabs).getByRole('tab', { name: '调用明细' }).getAttribute('aria-selected')).toBe('true');
    expect(await screen.findByTestId('trace-empty')).toBeTruthy();
    await user.keyboard('{ArrowRight}');
    const panel = screen.getByRole('tabpanel');
    expect(panel.textContent).toContain('"shot_id": 11');
    expect(panel.textContent).toContain('"foo": "bar"');
    expect(panel.textContent).toContain('h3');
    expect(within(panel).getByRole('button', { name: '复制参数（payload）' })).toBeTruthy();
    await waitFor(() => expect(location()).toBe('/jobs?job=3&tab=debug'));
  });

  it('运行中：时间线实时走秒、显示进度；运行中的日志可以暂停跟随', async () => {
    const job = makeJob({ id: 7, type: 'video.shot', status: 'running', created_at: ago(90_000), started_at: ago(30_000), log: 'a\nb\n' });
    installFetch({ 'GET /api/jobs': [job], 'GET /api/jobs/7': job });
    renderPage('/jobs?job=7&tab=log', { connected: true, everConnected: true, progress: { 7: { value: 3, max: 10 } } });
    const user = userEvent.setup();
    const follow = await screen.findByRole('button', { name: '跟随最新' });
    expect(follow.getAttribute('aria-pressed')).toBe('true');
    await user.click(follow);
    expect(screen.getByRole('button', { name: '已暂停跟随' }).getAttribute('aria-pressed')).toBe('false');
    await user.click(screen.getByRole('tab', { name: '概览' }));
    const timeline = screen.getByRole('list', { name: '时间线' });
    expect(timeline.textContent).toMatch(/已运行 3\d 秒/);
    expect(timeline.textContent).toContain('3/10');
    expect(within(timeline).getByRole('progressbar').getAttribute('aria-valuenow')).toBe('3');
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
    renderPage('/jobs?job=9');
    const user = userEvent.setup();
    expect((await screen.findByTestId('input-key')).textContent).toBe('script_structure');
    expect(screen.getByTestId('job-status').textContent).toBe('等待输入');
    await user.click(screen.getByRole('button', { name: '取消' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/jobs/9/cancel')).toBe(true));
  });

  it('取消运行中的任务先确认', async () => {
    const job = makeJob({ id: 7, status: 'running', started_at: ago(10_000) });
    const calls = installFetch({ 'GET /api/jobs': [job], 'GET /api/jobs/7': job, 'POST /api/jobs/7/cancel': { ok: true } });
    renderPage('/jobs?job=7');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '取消' }));
    const dlg = await screen.findByRole('dialog');
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    await user.click(within(dlg).getByRole('button', { name: '取消任务' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/jobs/7/cancel')).toBe(true));
  });

  it.each([
    ['failed', '重试'],
    ['canceled', '重试'],
    ['done', '打开所属对象'],
    ['running', null],
    ['waiting_input', '提交结果'],
  ] as const)('全页只有一个 .primary（%s → %s）', async (status, primary) => {
    const job = makeJob({
      id: 3,
      status,
      error: status === 'failed' ? '失败了' : null,
      context: CTX,
      result: status === 'waiting_input' ? { input_request: { key: 'k', task: 'script', system: 'S', user: 'U' } } : null,
    });
    installFetch({ 'GET /api/jobs': [job], 'GET /api/jobs/3': job, 'GET /api/jobs?status=failed&limit=100': [job] });
    const { container } = renderPage(status === 'failed' ? '/jobs?job=3&status=failed' : '/jobs?job=3');
    await screen.findByTestId('job-status');
    const primaries = Array.from(container.ownerDocument.querySelectorAll('.primary'));
    if (primary === null) expect(primaries).toHaveLength(0);
    else {
      expect(primaries).toHaveLength(1);
      expect(primaries[0].textContent).toBe(primary);
    }
  });

  it('找不到任务时详情里写明', async () => {
    installFetch({ 'GET /api/jobs': [], 'GET /api/jobs/99': () => json({ detail: '任务不存在' }, 404) });
    renderPage('/jobs?job=99');
    expect(await screen.findByText(/任务不存在/)).toBeTruthy();
  });
});
