import { useQueries } from '@tanstack/react-query';
import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { keys } from '../api/queries';
import type { Job } from '../api/types';

export interface NoticeAction {
  label: string;
  to: string;
}

export interface JobNotice {
  /** 放在按钮旁边的提示条；没有提示时是 null */
  view: ReactNode;
  /** 提交了任务后调用：跟踪这些任务，结束时把提示改写成结果 */
  track: (jobIds: number[], text: string) => void;
  error: (msg: string) => void;
  info: (msg: string, opts?: { action?: NoticeAction }) => void;
  clear: () => void;
}

type State =
  | null
  | { kind: 'track'; ids: number[]; text: string; v: number }
  | { kind: 'error'; msg: string; v: number }
  | { kind: 'info'; msg: string; action?: NoticeAction; v: number };

/** 跟踪中的任务汇总成一条提示：tone 决定样式，autoHide 的 6 秒后自动消失。 */
interface Outcome {
  tone: 'pending' | 'ok' | 'error';
  text: string;
  /** 悬停提示：失败时放完整的错误（text 里只有第一行） */
  title?: string;
  action?: NoticeAction;
  autoHide: boolean;
}

const ENDED = new Set(['done', 'failed', 'canceled', 'waiting_input']);
const SUCCESS_MS = 6000;

function jobLink(id: number): string {
  return `/jobs?job=${id}`;
}

/** 错误可能是多行的堆栈：提示条里只放第一行非空的 */
function firstLine(s: string | null | undefined): string {
  return (s ?? '').split(/\r?\n/).map((l) => l.trim()).find(Boolean) ?? '';
}

export function summarize(jobs: (Job | undefined)[], text: string): Outcome {
  const loaded = jobs.filter((j): j is Job => !!j);
  if (loaded.length < jobs.length || loaded.some((j) => !ENDED.has(j.status))) {
    const finished = loaded.filter((j) => ENDED.has(j.status)).length;
    const progress = jobs.length > 1 ? `（${finished}/${jobs.length} 完成）` : '';
    return { tone: 'pending', text: `${text}：已提交，进行中${progress}`, autoHide: false };
  }
  const failed = loaded.find((j) => j.status === 'failed');
  if (failed) {
    return {
      tone: 'error',
      text: `失败：${firstLine(failed.error) || '没有错误信息'}`,
      title: failed.error || undefined,
      action: { label: '查看任务', to: jobLink(failed.id) },
      autoHide: false,
    };
  }
  const waiting = loaded.find((j) => j.status === 'waiting_input');
  if (waiting) {
    return {
      tone: 'pending',
      text: `${text}：在等人工输入`,
      action: { label: '去填写', to: jobLink(waiting.id) },
      autoHide: false,
    };
  }
  const done = loaded.filter((j) => j.status === 'done');
  if (done.length < loaded.length) {
    return { tone: 'ok', text: done.length ? `部分已取消：${text}` : `已取消：${text}`, autoHide: true };
  }
  const skipped = done.filter((j) => j.result?.skipped === true).length;
  if (skipped === done.length) {
    return { tone: 'ok', text: '已跳过：输入没变，勾选强制重跑可重出', autoHide: true };
  }
  if (skipped > 0) {
    return { tone: 'ok', text: `完成：${text}（${skipped} 个输入没变已跳过，勾选强制重跑可重出）`, autoHide: true };
  }
  return { tone: 'ok', text: `完成：${text}`, autoHide: true };
}

/**
 * 提交任务后的就地提示：track() 后订阅这些任务（和任务页共用 ['job', id] 缓存，WS 事件会让它刷新），
 * 全部结束时改写成结果——完成/跳过 6 秒后消失，失败带「查看任务」链接、要手动关。
 */
export function useJobNotice(): JobNotice {
  const [state, setState] = useState<State>(null);
  const ids = state?.kind === 'track' ? state.ids : [];

  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: keys.job(id),
      queryFn: () => api.get<Job>(`/api/jobs/${id}`),
      // WS 断开时的兜底：还没结束就 5 秒拉一次
      refetchInterval: (q: { state: { data?: Job } }) => (q.state.data && ENDED.has(q.state.data.status) ? false : 5000),
    })),
  });
  const jobs = results.map((r) => r.data);
  const jobsKey = jobs.map((j) => (j ? `${j.id}:${j.status}` : '-')).join(',');

  const outcome: Outcome | null = useMemo(() => {
    if (!state) return null;
    if (state.kind === 'error') return { tone: 'error', text: state.msg, autoHide: false };
    if (state.kind === 'info') return { tone: 'ok', text: state.msg, action: state.action, autoHide: true };
    return summarize(jobs, state.text);
    // jobsKey 概括了 jobs 里会影响提示的部分
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, jobsKey]);

  const clear = useCallback(() => setState(null), []);
  const track = useCallback((jobIds: number[], text: string) => setState((s) => ({ kind: 'track', ids: jobIds, text, v: (s?.v ?? 0) + 1 })), []);
  const error = useCallback((msg: string) => setState((s) => ({ kind: 'error', msg, v: (s?.v ?? 0) + 1 })), []);
  const info = useCallback(
    (msg: string, opts?: { action?: NoticeAction }) => setState((s) => ({ kind: 'info', msg, action: opts?.action, v: (s?.v ?? 0) + 1 })),
    [],
  );

  const autoHide = outcome?.autoHide ?? false;
  const version = state?.v;
  useEffect(() => {
    if (!autoHide) return;
    const t = setTimeout(() => setState((s) => (s?.v === version ? null : s)), SUCCESS_MS);
    return () => clearTimeout(t);
  }, [autoHide, version]);

  let view: ReactNode = null;
  if (outcome) {
    const action = outcome.action && <Link to={outcome.action.to}>{outcome.action.label}</Link>;
    view =
      outcome.tone === 'error' ? (
        <div className="error banner job-notice" role="alert">
          <span title={outcome.title}>{outcome.text}</span>
          {action && <span aria-hidden="true">·</span>}
          {action}
          <button type="button" className="ghost icon-btn" aria-label="关闭提示" onClick={clear}>
            ×
          </button>
        </div>
      ) : (
        <p className={`notice job-notice${outcome.tone === 'pending' ? ' is-pending' : ''}`} role="status">
          <span>{outcome.text}</span>
          {action && <span aria-hidden="true">·</span>}
          {action}
        </p>
      );
  }

  return { view, track, error, info, clear };
}
