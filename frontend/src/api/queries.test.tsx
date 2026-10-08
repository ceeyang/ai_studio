import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { installFetch } from '../test/fetch';
import { makeCandidate, makeEpisode, makeJob } from '../test/factories';
import { newQueryClient } from '../test/render';
import { keys, useEpisodes, useJobs, useProvideInput, useRunStage, useSelectCandidate } from './queries';

function wrapper(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

describe('queries', () => {
  it('中文 slug 在路径里会被编码', async () => {
    const calls = installFetch({ 'GET /api/projects/%E6%8F%90%E8%AF%8D%E5%91%98/episodes': [] });
    const { result } = renderHook(() => useEpisodes('提词员'), { wrapper: wrapper(newQueryClient()) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(calls[0].url).toBe('/api/projects/%E6%8F%90%E8%AF%8D%E5%91%98/episodes');
  });

  it('useJobs 按状态过滤，空状态表示全部', async () => {
    const calls = installFetch({ 'GET /api/jobs': [makeJob()] });
    const qc = newQueryClient();
    const a = renderHook(() => useJobs('waiting_input'), { wrapper: wrapper(qc) });
    const b = renderHook(() => useJobs('', 5), { wrapper: wrapper(qc) });
    await waitFor(() => expect(a.result.current.isSuccess && b.result.current.isSuccess).toBe(true));
    expect(calls.map((c) => c.url).sort()).toEqual(['/api/jobs?limit=5', '/api/jobs?status=waiting_input&limit=100']);
  });

  it('useRunStage 提交阶段并让任务列表失效', async () => {
    const calls = installFetch({ 'POST /api/episodes/3/run': { job_ids: [7, 8] } });
    const qc = newQueryClient();
    qc.setQueryData(keys.jobs('', 100), []);
    const { result } = renderHook(() => useRunStage(3), { wrapper: wrapper(qc) });
    const out = await result.current.mutateAsync({ stage: 'keyframes', shots: ['11'], candidates: 2, force: false });
    expect(out.job_ids).toEqual([7, 8]);
    expect(calls[0]).toMatchObject({
      method: 'POST',
      url: '/api/episodes/3/run',
      body: { stage: 'keyframes', shots: ['11'], candidates: 2, force: false },
    });
    expect(qc.getQueryState(keys.jobs('', 100))?.isInvalidated).toBe(true);
  });

  it('选定候选后这一集的数据失效', async () => {
    installFetch({ 'POST /api/candidates/12/select': makeCandidate({ id: 12 }) });
    const qc = newQueryClient();
    qc.setQueryData(keys.episode(4), makeEpisode({ id: 4 }));
    const { result } = renderHook(() => useSelectCandidate(4), { wrapper: wrapper(qc) });
    await result.current.mutateAsync(12);
    expect(qc.getQueryState(keys.episode(4))?.isInvalidated).toBe(true);
  });

  it('提交人工输入后用返回的任务更新缓存', async () => {
    const calls = installFetch({ 'POST /api/jobs/9/input': makeJob({ id: 9, status: 'queued' }) });
    const qc = newQueryClient();
    const { result } = renderHook(() => useProvideInput(), { wrapper: wrapper(qc) });
    await result.current.mutateAsync({ id: 9, text: '{"a":1}' });
    expect(calls[0].body).toEqual({ text: '{"a":1}' });
    expect(qc.getQueryData(keys.job(9))).toMatchObject({ id: 9, status: 'queued' });
  });
});
