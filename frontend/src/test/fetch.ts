import { vi } from 'vitest';

export interface FetchCall {
  method: string;
  url: string;
  body: unknown;
  headers: Record<string, string>;
}

export function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/**
 * 用一张「方法 路径」→ 返回值 的表替换全局 fetch，返回调用记录。
 * 路径先按带查询串精确匹配，再按去掉查询串匹配；值可以是数据、Response，或 (call) => 数据 | Response。
 */
export function installFetch(routes: Record<string, unknown>): FetchCall[] {
  const calls: FetchCall[] = [];
  const impl = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url =
      typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
    const method = (init?.method ?? 'GET').toUpperCase();
    const raw = init?.body;
    const call: FetchCall = {
      method,
      url,
      body: typeof raw === 'string' ? JSON.parse(raw) : undefined,
      headers: (init?.headers ?? {}) as Record<string, string>,
    };
    calls.push(call);
    const path = url.replace(/^https?:\/\/[^/]+/, '');
    const key = [`${method} ${path}`, `${method} ${path.split('?')[0]}`].find((k) => k in routes);
    if (key === undefined) return json({ detail: `测试里没有模拟 ${method} ${path}` }, 404);
    const route = routes[key];
    const value =
      typeof route === 'function' ? await (route as (c: FetchCall) => unknown)(call) : route;
    return value instanceof Response ? value : json(value ?? null);
  };
  vi.stubGlobal('fetch', vi.fn(impl));
  return calls;
}
