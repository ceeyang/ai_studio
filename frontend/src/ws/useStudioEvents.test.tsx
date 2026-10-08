import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { makeJob } from '../test/factories';
import { newQueryClient } from '../test/render';
import { defaultWsUrl, useStudioEvents, type WebSocketCtor } from './useStudioEvents';

class FakeWS {
  static all: FakeWS[] = [];
  onopen: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: ((e: CloseEvent) => void) | null = null;
  closed = false;

  constructor(public url: string) {
    FakeWS.all.push(this);
  }

  close() {
    this.closed = true;
  }

  open() {
    this.onopen?.(new Event('open'));
  }

  emit(data: unknown) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }));
  }

  drop() {
    this.onclose?.(new Event('close') as CloseEvent);
  }
}

function setup(retryMs = 1000) {
  FakeWS.all = [];
  const qc = newQueryClient();
  const spy = vi.spyOn(qc, 'invalidateQueries');
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  const hook = renderHook(
    () => useStudioEvents({ url: 'ws://test/api/ws', WebSocketImpl: FakeWS as unknown as WebSocketCtor, retryMs }),
    { wrapper },
  );
  const ws = () => FakeWS.all[FakeWS.all.length - 1];
  return { qc, spy, hook, ws };
}

describe('defaultWsUrl', () => {
  it('开发态直连后端，默认 127.0.0.1:8700', () => {
    expect(defaultWsUrl(true)).toBe('ws://127.0.0.1:8700/api/ws');
  });

  it('开发态可用 VITE_BACKEND_WS 覆盖后端地址', () => {
    vi.stubEnv('VITE_BACKEND_WS', 'ws://127.0.0.1:9999');
    expect(defaultWsUrl(true)).toBe('ws://127.0.0.1:9999/api/ws');
  });

  it('生产态走当前页面的 host（http -> ws）', () => {
    vi.stubGlobal('location', { protocol: 'http:', host: 'studio.local:8700' });
    expect(defaultWsUrl(false)).toBe('ws://studio.local:8700/api/ws');
  });

  it('生产态走当前页面的 host（https -> wss）', () => {
    vi.stubGlobal('location', { protocol: 'https:', host: 'studio.example.com' });
    expect(defaultWsUrl(false)).toBe('wss://studio.example.com/api/ws');
  });
});

describe('useStudioEvents', () => {
  it('连上后把队列状态写进缓存、进度写进状态', () => {
    const { qc, hook, ws } = setup();
    expect(ws().url).toBe('ws://test/api/ws');
    act(() => ws().open());
    expect(hook.result.current.connected).toBe(true);
    act(() =>
      ws().emit({ type: 'queue', paused: true, counts: { queued: 2, running: 1, waiting_input: 0 }, current_family: 'h3' }),
    );
    expect(qc.getQueryData(['queue'])).toEqual({
      paused: true,
      counts: { queued: 2, running: 1, waiting_input: 0 },
      current_family: 'h3',
    });
    act(() => ws().emit({ type: 'progress', job_id: 5, value: 3, max: 20 }));
    expect(hook.result.current.progress[5]).toEqual({ value: 3, max: 20 });
  });

  it('任务事件让对应查询失效；收到坏消息不崩', () => {
    const { spy, ws } = setup();
    act(() => ws().open());
    act(() => ws().emit({ type: 'job', job: makeJob({ id: 8, type: 'export.episode', status: 'done' }) }));
    expect(spy).toHaveBeenCalledWith({ queryKey: ['exports'] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ['job', 8] });
    act(() => ws().onmessage?.(new MessageEvent('message', { data: 'not json' })));
  });

  it('断线后自动重连，重连成功时全部刷新', () => {
    vi.useFakeTimers();
    const { spy, hook, ws } = setup(1000);
    act(() => ws().open());
    act(() => ws().drop());
    expect(hook.result.current.connected).toBe(false);
    expect(FakeWS.all).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(FakeWS.all).toHaveLength(2);
    spy.mockClear();
    act(() => ws().open());
    expect(hook.result.current.connected).toBe(true);
    expect(spy).toHaveBeenCalledWith();
  });

  it('卸载时关闭连接且不再重连', () => {
    vi.useFakeTimers();
    const { hook, ws } = setup(1000);
    const first = ws();
    hook.unmount();
    expect(first.closed).toBe(true);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(FakeWS.all).toHaveLength(1);
  });
});
