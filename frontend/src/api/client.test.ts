import { describe, expect, it, vi } from 'vitest';
import { installFetch, json } from '../test/fetch';
import { ApiError, api, detailText, enc } from './client';

describe('api client', () => {
  it('GET 返回解析后的 JSON', async () => {
    installFetch({ 'GET /api/health': { ok: true } });
    expect(await api.get('/api/health')).toEqual({ ok: true });
  });

  it('POST 默认带空对象 body 和 JSON 头', async () => {
    const calls = installFetch({ 'POST /api/queue/pause': { paused: true } });
    await api.post('/api/queue/pause');
    expect(calls[0]).toMatchObject({ method: 'POST', body: {} });
    expect(calls[0].headers['Content-Type']).toBe('application/json');
  });

  it('后端的 detail 变成带状态码的中文错误', async () => {
    installFetch({ 'POST /api/projects': () => json({ detail: '项目 demo 已存在' }, 409) });
    const err = await api.post('/api/projects', { title: 'demo' }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).message).toBe('项目 demo 已存在（HTTP 409）');
  });

  it('422 校验错误列出字段', () => {
    expect(detailText([{ loc: ['body', 'title'], msg: 'Field required' }])).toBe('title：Field required');
  });

  it('204 没有内容时返回 null', async () => {
    installFetch({ 'DELETE /api/shots/3': () => new Response(null, { status: 204 }) });
    expect(await api.del('/api/shots/3')).toBeNull();
  });

  it('连不上服务时给出提示', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const err = await api.get('/api/health').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(0);
    expect((err as ApiError).message).toContain('studio-server');
  });

  it('enc 编码中文 slug', () => {
    expect(enc('提词员')).toBe('%E6%8F%90%E8%AF%8D%E5%91%98');
  });
});
