import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { api } from '../api/client';
import { keys } from '../api/queries';
import type { Job } from '../api/types';
import { jobTypeLabel } from '../format';
import { toWorkspaceUrl } from '../pages/workspace/urls';
import { subscribeJobSettled } from '../ws/events';

interface Toast {
  key: string;
  ok: boolean;
  text: string;
  to: string;
}

const MAX_TOASTS = 3;
const TOAST_MS = 8000;

/**
 * 右下角的完成提示：出图、出视频、定妆照、三视图、合成导出结束时弹一条「<对象> <任务名>完成/失败 · 查看」。
 * WS 事件里没有任务的上下文（哪一集哪个镜头），所以收到后再拉一次 /api/jobs/{id}。
 * 当前已经在那个页面上就不弹（结果就在眼前）。最多同时 3 条，每条 8 秒后消失。
 */
export function Toasts() {
  const qc = useQueryClient();
  const loc = useLocation();
  const here = useRef('');
  here.current = loc.pathname + loc.search;
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seen = useRef(new Set<string>());
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  useEffect(() => {
    let alive = true;
    const dismissLater = (key: string) => {
      timers.current.set(
        key,
        setTimeout(() => {
          timers.current.delete(key);
          setToasts((list) => list.filter((t) => t.key !== key));
        }, TOAST_MS),
      );
    };
    const off = subscribeJobSettled((ev) => {
      const key = `${ev.id}:${ev.status}`;
      if (seen.current.has(key)) return;
      seen.current.add(key);
      void qc
        .fetchQuery({ queryKey: keys.job(ev.id), queryFn: () => api.get<Job>(`/api/jobs/${ev.id}`), staleTime: 0 })
        .catch(() => ev)
        .then((job) => {
          if (!alive) return;
          const ctx = job.context ?? null;
          // 后端给的是旧的剧集页面地址（/director?shot= 等）：换成单集工作台的地址再比、再链
          const url = ctx?.url ? toWorkspaceUrl(ctx.url) : null;
          const to = url ?? `/jobs?job=${ev.id}`;
          if (url && url === here.current) return;
          const ok = ev.status === 'done';
          const text = `${ctx?.label ? `${ctx.label} ` : ''}${jobTypeLabel(ev.type)}${ok ? '完成' : '失败'}`;
          setToasts((list) => [...list, { key, ok, text, to }].slice(-MAX_TOASTS));
          dismissLater(key);
        });
    });
    const pending = timers.current;
    return () => {
      alive = false;
      off();
      for (const t of pending.values()) clearTimeout(t);
      pending.clear();
    };
  }, [qc]);

  if (toasts.length === 0) return null;
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.key} data-testid="toast" className={`toast ${t.ok ? 'is-ok' : 'is-fail'}`}>
          <span>{t.text}</span>
          <span aria-hidden="true">·</span>
          <Link to={t.to} onClick={() => setToasts((list) => list.filter((x) => x.key !== t.key))}>
            查看
          </Link>
          <button
            type="button"
            className="ghost icon-btn"
            aria-label="关闭"
            onClick={() => setToasts((list) => list.filter((x) => x.key !== t.key))}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
