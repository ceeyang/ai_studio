// 调用后端 HTTP API 的薄封装：统一把失败变成带中文说明和状态码的 ApiError。

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** URL 路径里的 slug 一律编码（slug 可以是中文）。 */
export const enc = encodeURIComponent;

interface ValidationItem {
  loc?: unknown[];
  msg?: string;
}

/** 后端 detail 可能是字符串（我们自己的错误）或 FastAPI 的 422 校验列表。 */
export function detailText(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: ValidationItem) => {
        const loc = Array.isArray(d?.loc) ? d.loc.filter((x) => x !== 'body').join('.') : '';
        const msg = d?.msg ?? JSON.stringify(d);
        return loc ? `${loc}：${msg}` : msg;
      })
      .join('；');
  }
  return JSON.stringify(detail);
}

/** body 是 FormData 时原样发送（multipart，浏览器自己带 boundary），否则按 JSON 发。 */
export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  const form = body instanceof FormData;
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined || form ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : form ? body : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, '连不上工作台服务，确认 uv run studio-server 正在运行');
  }
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const detail =
      data !== null && typeof data === 'object' && 'detail' in data
        ? (data as { detail: unknown }).detail
        : data || res.statusText;
    throw new ApiError(res.status, `${detailText(detail)}（HTTP ${res.status}）`);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body: unknown = {}) => request<T>('POST', path, body),
  patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
  del: (path: string) => request<null>('DELETE', path),
};

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
