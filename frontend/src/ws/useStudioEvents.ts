import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useReducer } from 'react';
import { keys } from '../api/queries';
import type { QueueState } from '../api/types';
import { type EventsState, type WsEvent, eventsReducer, initialEvents, invalidationsFor } from './events';

export type WebSocketCtor = new (url: string) => WebSocket;

export interface StudioEventsOptions {
  url?: string;
  WebSocketImpl?: WebSocketCtor;
  retryMs?: number;
}

// 开发态下 Vite 的 ws 代理一断线就在终端打 "ws proxy error: read ECONNRESET"
// （本机的火绒/AdGuard 等 WFP 网络过滤驱动会 RST 掉刚写完就关的回环连接，无害但吵）；
// 所以开发态让浏览器直连后端，绕开代理。后端地址可用 VITE_BACKEND_WS 覆盖，默认 127.0.0.1:8700。
const DEFAULT_BACKEND_WS = 'ws://127.0.0.1:8700';

/** 开发态直连后端，生产态（FastAPI 同源托管前端）用当前页面的 host。 */
export function defaultWsUrl(dev: boolean = import.meta.env.DEV): string {
  if (dev) {
    const backend = (import.meta.env.VITE_BACKEND_WS as string | undefined) || DEFAULT_BACKEND_WS;
    return `${backend}/api/ws`;
  }
  const { protocol, host } = window.location;
  return `${protocol === 'https:' ? 'wss' : 'ws'}://${host}/api/ws`;
}

export function useStudioEvents({ url, WebSocketImpl, retryMs = 2000 }: StudioEventsOptions = {}): EventsState {
  const qc = useQueryClient();
  const [state, dispatch] = useReducer(eventsReducer, initialEvents);

  useEffect(() => {
    const Impl: WebSocketCtor = WebSocketImpl ?? WebSocket;
    const target = url ?? defaultWsUrl();
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let dropped = false;

    const connect = () => {
      ws = new Impl(target);
      ws.onopen = () => {
        dispatch({ type: 'connected' });
        // 断线期间的事件已经丢了：重连成功后把所有查询重新拉一遍
        if (dropped) void qc.invalidateQueries();
      };
      ws.onmessage = (m: MessageEvent) => {
        let ev: WsEvent;
        try {
          ev = JSON.parse(String(m.data)) as WsEvent;
        } catch {
          return;
        }
        dispatch(ev);
        if (ev.type === 'queue') {
          qc.setQueryData<QueueState>(keys.queue, {
            paused: ev.paused,
            counts: ev.counts,
            current_family: ev.current_family,
          });
        }
        for (const queryKey of invalidationsFor(ev)) void qc.invalidateQueries({ queryKey });
      };
      ws.onclose = () => {
        dispatch({ type: 'disconnected' });
        if (stopped) return;
        dropped = true;
        timer = setTimeout(connect, retryMs);
      };
    };

    connect();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
    };
  }, [qc, url, WebSocketImpl, retryMs]);

  return state;
}
