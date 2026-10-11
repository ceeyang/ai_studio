// 「04 · 任务」：上面状态分段 + 搜索 + 类型 / 项目筛选（都记在地址栏），左边按日期分组的任务列表，右边任务详情
// （窄屏时详情变成从右侧滑出的抽屉）。地址 /jobs?job=293 可以直达、分享、刷新保持。
import { useQueryClient } from '@tanstack/react-query';
import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, errorText } from '../api/client';
import { keys, useJobs, useProjects, useQueue } from '../api/queries';
import type { Job } from '../api/types';
import { useConfirm } from '../components/ConfirmDialog';
import { fmtTime, jobStatusLabel } from '../format';
import { useEvents } from '../ws/EventsContext';
import { type DetailTab, JobDetail, isDetailTab } from './jobs/JobDetail';
import {
  DATE_GROUP_LABEL,
  STATUS_SEGMENTS,
  TYPE_GROUPS,
  errorBrief,
  fmtSeconds,
  groupByDate,
  jobEngine,
  jobTitle,
  matchesSearch,
  relTime,
  secondsBetween,
  typeGroupOf,
} from './jobs/jobList';
import { ThinProgress } from './jobs/ThinProgress';
import { useNow } from './jobs/useNow';

/** 一页多少条；「加载更多」每次再多拿这么多 */
const PAGE = 100;

/** 列表一行的耗时：运行中实时走秒，排队中写排了多久，结束了写总耗时 */
function rowDuration(j: Job, now: number): string {
  if (j.status === 'running') return `已运行 ${fmtSeconds(secondsBetween(j.started_at, null, now) ?? 0)}`;
  if (j.status === 'queued') {
    const s = secondsBetween(j.created_at, null, now);
    return s === null ? '' : `排队 ${fmtSeconds(s)}`;
  }
  const s = secondsBetween(j.started_at, j.finished_at, now);
  return s === null || !j.finished_at ? '' : fmtSeconds(s);
}

function JobRow({
  j,
  now,
  selected,
  active,
  onPick,
}: {
  j: Job;
  now: number;
  selected: boolean;
  active: boolean;
  onPick: () => void;
}) {
  const { progress } = useEvents();
  const engine = jobEngine(j);
  const dur = rowDuration(j, now);
  const brief = j.status === 'failed' ? errorBrief(j.error) : '';
  return (
    <div
      role="option"
      id={`job-row-${j.id}`}
      aria-selected={selected}
      className={`job-row is-${j.status}${active ? ' is-active' : ''}`}
      onClick={onPick}
    >
      <span className={`type-dot t-${typeGroupOf(j.type)}`} aria-hidden="true" />
      <div className="jr-main">
        <div className="jr-title">{jobTitle(j)}</div>
        <div className="jr-meta">
          <span className="mono">#{j.id}</span>
          {j.context?.project_slug && <span>{j.context.project_slug}</span>}
          {engine && <span>{engine}</span>}
          <time dateTime={j.created_at ?? undefined} title={fmtTime(j.created_at)}>
            {relTime(j.created_at, now)}
          </time>
          {dur && <span className="jr-dur">{dur}</span>}
        </div>
        {brief && (
          <div className="jr-error" title={j.error ?? undefined} data-testid="row-error">
            {brief}
          </div>
        )}
        {j.status === 'running' && <ThinProgress p={progress[j.id]} label={`任务 #${j.id} 进度`} />}
      </div>
      <span className={`pill job-${j.status}`}>{jobStatusLabel(j.status)}</span>
    </div>
  );
}

function ListSkeleton() {
  return (
    <div className="job-list-skeleton" data-testid="list-skeleton" aria-hidden="true">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="job-row is-skeleton">
          <span className="skeleton dot" />
          <div className="jr-main">
            <span className="skeleton w-60" />
            <span className="skeleton w-40" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function JobsPage() {
  const [params, setParams] = useSearchParams();
  const status = params.get('status') ?? '';
  const type = params.get('type') ?? '';
  const project = params.get('project') ?? '';
  const q = params.get('q') ?? '';
  const selectedId = Number(params.get('job')) || null;
  const tabParam = params.get('tab');
  const tab: DetailTab = isDetailTab(tabParam) ? tabParam : 'overview';

  const [limit, setLimit] = useState(PAGE);
  const list = useJobs(status, limit, true, project);
  // 状态分段上的数量：用不筛状态的最近一页算；排队 / 运行 / 等待输入在不筛项目时用队列的准确数
  const all = useJobs('', PAGE, false, project);
  const queue = useQueue();
  const projects = useProjects();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [retrying, setRetrying] = useState(false);
  const [retryMsg, setRetryMsg] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<number | null>(selectedId);
  const listRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const loaded = list.data ?? [];
  const typeGroup = TYPE_GROUPS.find((g) => g.value === type);
  const rows = useMemo(
    () => loaded.filter((j) => (!typeGroup || typeGroup.types.includes(j.type)) && matchesSearch(j, q)),
    [loaded, typeGroup, q],
  );
  const hasRunning = rows.some((j) => j.status === 'running' || j.status === 'queued');
  const now = useNow(true, hasRunning ? 1000 : 30_000);
  const groups = useMemo(() => groupByDate(rows, now), [rows, now]);
  const failed = rows.filter((j) => j.status === 'failed');
  const hasMore = loaded.length >= limit;
  const clientFiltered = !!typeGroup || !!q.trim();
  const anyFilter = !!(status || type || project || q);

  const counts = useMemo(() => {
    const c: Record<string, number> = { '': all.data?.length ?? 0 };
    for (const j of all.data ?? []) c[j.status] = (c[j.status] ?? 0) + 1;
    const qc2 = queue.data?.counts;
    if (qc2 && !project) {
      c.queued = qc2.queued;
      c.running = qc2.running;
      c.waiting_input = qc2.waiting_input;
    }
    return c;
  }, [all.data, queue.data, project]);

  const update = (patch: Record<string, string | null>, replace = false) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === '') next.delete(k);
      else next.set(k, v);
    }
    setParams(next, { replace });
  };

  const pick = (id: number) => {
    setActiveId(id);
    update({ job: String(id) });
  };

  const closeDetail = () => {
    update({ job: null, tab: null });
    listRef.current?.focus();
  };

  // 选中的任务变了（地址栏直达、重试跳到新任务）：键盘光标跟过去
  useEffect(() => {
    if (selectedId) setActiveId(selectedId);
  }, [selectedId]);

  // 键盘光标移动时滚到看得见
  useEffect(() => {
    if (activeId === null) return;
    document.getElementById(`job-row-${activeId}`)?.scrollIntoView?.({ block: 'nearest' });
  }, [activeId]);

  // 按 / 聚焦搜索框（不在输入框里时）
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.closest('input, textarea, select, [contenteditable="true"]') || t.isContentEditable)) return;
      e.preventDefault();
      searchRef.current?.focus();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const onListKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (rows.length === 0) return;
    const idx = rows.findIndex((j) => j.id === activeId);
    let next = -1;
    if (e.key === 'ArrowDown') next = idx < 0 ? 0 : Math.min(rows.length - 1, idx + 1);
    else if (e.key === 'ArrowUp') next = idx < 0 ? 0 : Math.max(0, idx - 1);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = rows.length - 1;
    else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (idx >= 0) pick(rows[idx].id);
      return;
    } else return;
    e.preventDefault();
    setActiveId(rows[next].id);
  };

  const setStatus = (s: string) => {
    setLimit(PAGE);
    setRetryMsg(null);
    update({ status: s });
  };

  /** 逐个重试列表里（已加载的）失败任务：一个没成功不影响后面的，最后说成功了几个 */
  const retryAllFailed = async () => {
    const targets = failed;
    const ok = await confirm({
      title: `重试全部 ${targets.length} 个失败任务？`,
      body: '每个任务按原来的参数重新排队，已有的结果不会被删。',
      confirmText: '全部重试',
    });
    if (!ok) return;
    setRetrying(true);
    setRetryMsg(null);
    let done = 0;
    const errors: string[] = [];
    for (const j of targets) {
      try {
        await api.post<{ job_id: number }>(`/api/jobs/${j.id}/retry`, {});
        done += 1;
      } catch (err) {
        errors.push(errorText(err));
      }
    }
    setRetrying(false);
    void qc.invalidateQueries({ queryKey: ['jobs'] });
    void qc.invalidateQueries({ queryKey: keys.queue });
    const failNote = errors.length > 0 ? `（${errors.length} 个没成功：${errors[0]}）` : '';
    setRetryMsg(`已重新提交 ${done}/${targets.length} 个失败任务${failNote}`);
  };

  const countText = (s: string): string | null => {
    const n = counts[s];
    if (!n) return null;
    return (s === '' || s === 'failed' || s === 'done' || s === 'canceled') && n >= PAGE ? `${PAGE}+` : String(n);
  };

  const projectOptions = projects.data ?? [];
  const activeDesc = activeId !== null && rows.some((j) => j.id === activeId) ? `job-row-${activeId}` : undefined;

  return (
    <div className={`page jobs-page${selectedId ? ' has-detail' : ''}`}>
      <header className="page-head jobs-head">
        <span className="eyebrow">04 · 任务</span>
        <h2>任务</h2>
        {status === 'failed' && failed.length > 0 && (
          <button type="button" className="jobs-retry-all" disabled={retrying} onClick={() => void retryAllFailed()}>
            {retrying ? '重试中…' : `重试全部失败（${failed.length}）`}
          </button>
        )}
      </header>

      <div className="jobs-filters">
        <div className="seg" role="group" aria-label="状态筛选">
          {STATUS_SEGMENTS.filter((s) => s.value !== 'waiting_input' || counts.waiting_input || status === 'waiting_input').map(
            (s) => {
              const n = countText(s.value);
              return (
                <button
                  key={s.value || 'all'}
                  type="button"
                  className={`seg-item${s.value ? ` s-${s.value}` : ''}`}
                  aria-pressed={status === s.value}
                  onClick={() => setStatus(s.value)}
                >
                  {s.label}
                  {n && (
                    <span className="seg-count" aria-label={`${n} 个`}>
                      {n}
                    </span>
                  )}
                </button>
              );
            },
          )}
        </div>
        <div className="jobs-filter-row">
          <label className="search-box">
            <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
              <circle cx="9" cy="9" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
              <path d="m13.2 13.2 3.3 3.3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
            <input
              ref={searchRef}
              type="search"
              aria-label="搜索任务"
              placeholder="搜索任务名、项目、镜头号、#编号"
              value={q}
              onChange={(e) => update({ q: e.target.value }, true)}
              onKeyDown={(e) => {
                if (e.key === 'Escape' && q) {
                  e.stopPropagation();
                  update({ q: null }, true);
                } else if (e.key === 'ArrowDown') {
                  e.preventDefault();
                  listRef.current?.focus();
                  if (rows[0] && activeDesc === undefined) setActiveId(rows[0].id);
                }
              }}
            />
            <kbd aria-hidden="true">/</kbd>
          </label>
          <select aria-label="类型筛选" value={type} onChange={(e) => update({ type: e.target.value })}>
            <option value="">全部类型</option>
            {TYPE_GROUPS.map((g) => (
              <option key={g.value} value={g.value}>
                {g.label}
              </option>
            ))}
          </select>
          <select
            aria-label="项目筛选"
            value={project}
            onChange={(e) => {
              setLimit(PAGE);
              update({ project: e.target.value });
            }}
          >
            <option value="">全部项目</option>
            {project && !projectOptions.some((p) => p.slug === project) && <option value={project}>{project}</option>}
            {projectOptions.map((p) => (
              <option key={p.slug} value={p.slug}>
                {p.title || p.slug}
              </option>
            ))}
          </select>
          {anyFilter && (
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setLimit(PAGE);
                update({ status: null, type: null, project: null, q: null });
              }}
            >
              清除筛选
            </button>
          )}
        </div>
      </div>

      {retryMsg && (
        <p className="notice" role="status">
          {retryMsg}
        </p>
      )}

      <div className="jobs-layout">
        <section className="panel jobs-list-panel" aria-label="任务列表区">
          {list.error ? (
            <div className="jobs-state">
              <p className="error">任务列表加载失败：{errorText(list.error)}</p>
              <button type="button" onClick={() => void list.refetch()}>
                重新加载
              </button>
            </div>
          ) : list.isLoading ? (
            <ListSkeleton />
          ) : rows.length === 0 ? (
            <div className="jobs-state empty-state" data-testid="jobs-empty">
              <strong>{anyFilter ? '没有符合筛选的任务' : '还没有任务'}</strong>
              <span className="muted">
                {anyFilter
                  ? clientFiltered && hasMore
                    ? `只在已加载的 ${loaded.length} 条里找了：可以加载更多再看。`
                    : '换个状态、类型或关键词试试。'
                  : '在剧集工作台里生成剧本、分镜、首帧或视频后，任务会出现在这里。'}
              </span>
              {anyFilter && (
                <button
                  type="button"
                  className="ghost"
                  onClick={() => update({ status: null, type: null, project: null, q: null })}
                >
                  清除筛选
                </button>
              )}
            </div>
          ) : (
            <div
              ref={listRef}
              className="job-list"
              role="listbox"
              aria-label="任务列表"
              tabIndex={0}
              aria-activedescendant={activeDesc}
              onKeyDown={onListKey}
            >
              {groups.map((g) => (
                <div key={g.group} role="group" aria-labelledby={`jobs-group-${g.group}`} className="job-group">
                  <div className="job-group-head" id={`jobs-group-${g.group}`} role="presentation">
                    {DATE_GROUP_LABEL[g.group]}
                    <span className="muted">{g.jobs.length}</span>
                  </div>
                  {g.jobs.map((j) => (
                    <JobRow
                      key={j.id}
                      j={j}
                      now={now}
                      selected={j.id === selectedId}
                      active={j.id === activeId}
                      onPick={() => pick(j.id)}
                    />
                  ))}
                </div>
              ))}
            </div>
          )}
          {(hasMore || (clientFiltered && rows.length > 0)) && !list.error && (
            <div className="jobs-more">
              {clientFiltered && (
                <span className="muted small">
                  在已加载的 {loaded.length} 条里筛出 {rows.length} 条
                </span>
              )}
              {hasMore && (
                <button
                  type="button"
                  className="ghost"
                  disabled={list.isPlaceholderData}
                  onClick={() => setLimit((n) => n + PAGE)}
                >
                  {list.isPlaceholderData ? '加载中…' : '加载更多'}
                </button>
              )}
            </div>
          )}
        </section>

        {selectedId ? (
          <>
            <div className="jd-backdrop" aria-hidden="true" onClick={closeDetail} />
            <div className="jd-wrap">
              <JobDetail
                key={selectedId}
                id={selectedId}
                tab={tab}
                onTab={(t) => update({ tab: t === 'overview' ? null : t }, true)}
                onRetried={(nid) => update({ job: String(nid), status: null, tab: null })}
                onClose={closeDetail}
              />
            </div>
          </>
        ) : (
          <section className="panel jobs-detail-empty" aria-label="任务详情">
            <strong>选一个任务查看详情</strong>
            <span className="muted">
              点左侧任意一行，或在列表里用 <kbd>↑</kbd> <kbd>↓</kbd> 移动、<kbd>Enter</kbd> 打开；<kbd>/</kbd> 搜索。
            </span>
          </section>
        )}
      </div>
    </div>
  );
}

