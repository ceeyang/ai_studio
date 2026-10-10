import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { Slot } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeCandidate, makeEpisode, makeShot, makeSlot } from '../../test/factories';
import { renderAt } from '../../test/render';
import type { GenStage } from '../director/shotStatus';
import { ShotStage } from './ShotStage';

const EP = makeEpisode({
  id: 4,
  shots: [makeShot({ id: 11, code: 'S01', duration: 5 }), makeShot({ id: 12, code: 'S02', order: 1, duration: 6 })],
});
const AT = { route: '/projects/demo/episodes/4?stage=keyframes', path: '/projects/:slug/episodes/:episodeId' };

function chosen(id: number, kind: string, stale = false): Slot {
  return makeSlot({ id, kind, selected_candidate_id: id * 10, stale, candidates: [makeCandidate({ id: id * 10 })] });
}

function routes(extra: Record<string, unknown> = {}) {
  return {
    'GET /api/episodes/4': EP,
    'GET /api/engines': { image: [], video: [] },
    'GET /api/projects/demo/locations': [],
    'GET /api/projects/demo/characters': [],
    'GET /api/jobs': [],
    ...extra,
  };
}

/** 工作台替身：勾选和候选数放在外面（和工作台一样） */
function Harness({ stage = 'keyframes', goStage = vi.fn() }: { stage?: GenStage; goStage?: (s: string, extra?: Record<string, string>) => void }) {
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [count, setCount] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  return <ShotStage work={{ stage, gate: null, selected, setSelected, count, setCount, submitting, setSubmitting, goStage }} />;
}

const posts = <T extends { method: string }>(calls: T[]) => calls.filter((c) => c.method === 'POST');

describe('ShotStage', () => {
  it('工具栏写镜头数和总时长；卡片已有选定结果是「重拍」并强制，还没出过的是「生成」不强制', async () => {
    const shots = [makeShot({ id: 11, code: 'S01', slots: [chosen(1, 'first_frame')] }), makeShot({ id: 12, code: 'S02', order: 1 })];
    const calls = installFetch(routes({ 'GET /api/episodes/4': makeEpisode({ id: 4, shots }), 'POST /api/episodes/4/run': { job_ids: [90] } }));
    renderAt(<Harness />, AT);
    const user = userEvent.setup();
    expect(await screen.findByText('2 个镜头，共 10.0 秒')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '重拍首帧 S01' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0].body).toEqual({ stage: 'keyframes', candidates: 1, force: true, shots: ['11'] });
    expect((await screen.findByRole('status')).textContent).toContain('重拍首帧 S01');
    await user.click(screen.getByRole('button', { name: '生成首帧 S02' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[1].body).toEqual({ stage: 'keyframes', candidates: 1, force: false, shots: ['12'] });
  });

  it('单镜候选数 4（超过 3 个任务）先确认耗时，取消就不提交', async () => {
    const calls = installFetch(routes());
    renderAt(<Harness />, AT);
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText('候选数'), '4');
    await user.click(screen.getByRole('button', { name: '生成首帧 S01' }));
    const dlg = await screen.findByRole('dialog', { name: '生成首帧？' });
    expect(dlg.textContent).toContain('将提交 4 个首帧任务，耗时未知');
    await user.click(within(dlg).getByRole('button', { name: '取消' }));
    expect(posts(calls)).toHaveLength(0);
  });

  it('视频阶段：失败的卡片「重试」强制提交视频', async () => {
    const shot = makeShot({
      id: 11, code: 'S01', slots: [chosen(1, 'first_frame'), chosen(2, 'video')],
      failures: [{ id: 80, type: 'video.shot', status: 'failed', error: '显存不足' }],
    });
    const calls = installFetch(routes({ 'GET /api/episodes/4': makeEpisode({ id: 4, shots: [shot] }), 'POST /api/episodes/4/run': { job_ids: [94] } }));
    renderAt(<Harness stage="videos" />, AT);
    await userEvent.setup().click(await screen.findByRole('button', { name: '重试视频 S01' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0].body).toEqual({ stage: 'videos', candidates: 1, force: true, shots: ['11'] });
  });

  it('视频阶段还没有首帧：「先出首帧」切回首帧阶段并打开这个镜头', async () => {
    const goStage = vi.fn();
    const calls = installFetch(routes());
    renderAt(<Harness stage="videos" goStage={goStage} />, AT);
    await userEvent.setup().click(await screen.findByRole('button', { name: '先出首帧 S02' }));
    expect(goStage).toHaveBeenCalledWith('keyframes', { shot: '12' });
    expect(posts(calls)).toHaveLength(0);
  });

  it('提交被后端拒绝时就地显示原因', async () => {
    installFetch(
      routes({ 'POST /api/episodes/4/run': () => new Response(JSON.stringify({ detail: 'ComfyUI 连不上' }), { status: 400 }) }),
    );
    renderAt(<Harness />, AT);
    await userEvent.setup().click(await screen.findByRole('button', { name: '生成首帧 S01' }));
    expect((await screen.findByRole('alert')).textContent).toContain('ComfyUI 连不上');
  });

  it('上移镜头时提交完整的新顺序', async () => {
    const calls = installFetch(routes({ 'POST /api/episodes/4/shots/reorder': [] }));
    renderAt(<Harness />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '更多操作 S02' }));
    await user.click(screen.getByRole('menuitem', { name: '上移' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/episodes/4/shots/reorder')).toBe(true));
    expect(calls.find((c) => c.url === '/api/episodes/4/shots/reorder')?.body).toEqual({ shot_ids: [12, 11] });
  });

  it('删除前用危险确认框问，取消就不删', async () => {
    const calls = installFetch(routes({ 'DELETE /api/shots/11': () => new Response(null, { status: 204 }) }));
    renderAt(<Harness />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '更多操作 S01' }));
    await user.click(screen.getByRole('menuitem', { name: '删除' }));
    const dlg = await screen.findByRole('dialog', { name: '删除镜头 S01？' });
    await user.click(within(dlg).getByRole('button', { name: '取消' }));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    await user.click(screen.getByRole('button', { name: '更多操作 S01' }));
    await user.click(screen.getByRole('menuitem', { name: '删除' }));
    await user.click(within(await screen.findByRole('dialog', { name: '删除镜头 S01？' })).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/shots/11')).toBe(true));
  });

  it('勾选后工具栏写「已选 N 镜」，可以清除', async () => {
    installFetch(routes());
    renderAt(<Harness />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('checkbox', { name: '选择 S01' }));
    expect(screen.getByText(/已选 1 镜/)).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '清除选择' }));
    expect(screen.queryByText(/已选 1 镜/)).toBeNull();
    expect((screen.getByRole('checkbox', { name: '选择 S01' }) as HTMLInputElement).checked).toBe(false);
  });

  it('没有分镜时引导去剧本阶段', async () => {
    installFetch(routes({ 'GET /api/episodes/4': makeEpisode({ id: 4, shots: [] }) }));
    renderAt(<Harness />, AT);
    expect((await screen.findByRole('link', { name: '去剧本阶段生成分镜' })).getAttribute('href')).toBe('/projects/demo/episodes/4?stage=script');
  });

  it('点卡片在地址栏记下镜头（保留 stage）并打开详情抽屉；抽屉和工具栏共用候选数', async () => {
    installFetch(routes());
    renderAt(<Harness />, AT);
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText('候选数'), '3');
    await user.click(screen.getByRole('button', { name: '打开 S02' }));
    expect(screen.getByTestId('location').textContent).toBe('/projects/demo/episodes/4?stage=keyframes&shot=12');
    const dlg = await screen.findByRole('dialog', { name: '镜头 S02 详情' });
    expect((within(dlg).getByLabelText('候选数') as HTMLSelectElement).value).toBe('3');
    await user.click(within(dlg).getByRole('button', { name: '关闭' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByTestId('location').textContent).toBe('/projects/demo/episodes/4?stage=keyframes');
  });

  it('抽屉里「保存并重拍」：已有选定首帧的镜头先保存再强制重拍', async () => {
    const fresh = makeShot({ id: 11, code: 'S01', image_prompt: 'old', slots: [chosen(1, 'first_frame')] });
    const calls = installFetch(
      routes({
        'GET /api/episodes/4': makeEpisode({ id: 4, shots: [fresh] }),
        'PATCH /api/shots/11': { ...fresh, image_prompt: 'new' },
        'POST /api/episodes/4/run': { job_ids: [93] },
      }),
    );
    renderAt(<Harness />, { ...AT, route: '/projects/demo/episodes/4?stage=keyframes&shot=11' });
    const user = userEvent.setup();
    const dlg = await screen.findByRole('dialog', { name: '镜头 S01 详情' });
    const ta = within(dlg).getByLabelText('图像提示词（英文）');
    await user.clear(ta);
    await user.type(ta, 'new');
    await user.click(within(dlg).getByRole('button', { name: '保存并重拍首帧' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0].body).toEqual({ stage: 'keyframes', candidates: 1, force: true, shots: ['11'] });
  });

  it('没有主按钮（全页唯一的主按钮在阶段条），也不打 data-next-action', async () => {
    installFetch(routes());
    const { container } = renderAt(<Harness />, AT);
    await screen.findByTestId('shot-S01');
    expect(container.querySelectorAll('.primary')).toHaveLength(0);
    expect(container.querySelectorAll('[data-next-action]')).toHaveLength(0);
  });

  it('剧集加载失败：显示原因和「回到项目」', async () => {
    installFetch({ 'GET /api/episodes/4': new Response(JSON.stringify({ detail: '剧集 4 不存在' }), { status: 404 }) });
    renderAt(<Harness />, AT);
    expect(await screen.findByText(/剧集 4 不存在/)).toBeTruthy();
    expect(screen.getByRole('link', { name: '回到项目' }).getAttribute('href')).toBe('/projects/demo');
  });
});
