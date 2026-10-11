// 任务详情：头部（标题、状态、所属对象、主要操作）+ 失败原因卡片 + 页签（概览 / 日志 / 调用明细 / 调试信息）。
import { type KeyboardEvent, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { errorText } from '../../api/client';
import { useCancelJob, useJob, useRetryJob } from '../../api/queries';
import type { Job } from '../../api/types';
import { useConfirm } from '../../components/ConfirmDialog';
import { ACTIVE_STATUSES, fmtTime, jobStatusLabel, jobTypeLabel } from '../../format';
import { useEvents } from '../../ws/EventsContext';
import { CopyButton } from './CopyButton';
import { JobLog } from './JobLog';
import { JobTraces } from './JobTraces';
import { fmtSeconds, jobEngine, jobTitle, relTime, secondsBetween, typeGroupOf } from './jobList';
import { ManualInputForm } from './ManualInputForm';
import { ThinProgress } from './ThinProgress';
import { useNow } from './useNow';

/** 这类错误多半是环境问题（显卡服务、浏览器扩展、网站登录）：给个去设置页检查的入口 */
const ENV_ERROR = /comfyui|扩展|登录/i;
const TRACE_HEAD = 'Traceback (most recent call last)';

export type DetailTab = 'overview' | 'log' | 'traces' | 'debug';
export const DETAIL_TABS: { value: DetailTab; label: string }[] = [
  { value: 'overview', label: '概览' },
  { value: 'log', label: '日志' },
  { value: 'traces', label: '调用明细' },
  { value: 'debug', label: '调试信息' },
];

export function isDetailTab(v: string | null): v is DetailTab {
  return DETAIL_TABS.some((t) => t.value === v);
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  const text = JSON.stringify(value, null, 2);
  return (
    <div className="json-block">
      <div className="json-block-head">
        <h5>{title}</h5>
        <CopyButton text={text} what={title} />
      </div>
      <pre className="json">{text}</pre>
    </div>
  );
}

/** 失败原因：中文的第一行加粗当标题；后面几行和日志里的调用栈收进「技术细节」，可展开、可复制 */
function ErrorCard({ j }: { j: Job }) {
  const err = j.error ?? '';
  const lines = err.split('\n');
  const head = lines.find((l) => l.trim())?.trim() ?? '任务失败';
  const rest = lines.slice(lines.findIndex((l) => l.trim()) + 1).join('\n').trim();
  const at = j.log.indexOf(TRACE_HEAD);
  const trace = at >= 0 ? j.log.slice(at).trim() : '';
  const details = [rest, trace].filter(Boolean).join('\n\n');
  return (
    <section className="jd-error" aria-label="失败原因">
      <div className="jd-error-head">
        <span className="jd-error-icon" aria-hidden="true" />
        <div className="jd-error-text">
          <span className="jd-error-label">失败原因</span>
          <strong data-testid="job-error-head">{head}</strong>
        </div>
        <CopyButton text={[err, trace].filter(Boolean).join('\n\n')} what="错误信息" />
      </div>
      {ENV_ERROR.test(err) && (
        <p className="jd-error-hint">
          看起来是环境问题（显卡服务、浏览器扩展或网站登录）。<Link to="/settings">去设置检查</Link>
        </p>
      )}
      {details && (
        <details className="jd-error-more">
          <summary>技术细节</summary>
          <pre>{details}</pre>
        </details>
      )}
    </section>
  );
}

function Timeline({ j, now }: { j: Job; now: number }) {
  const { progress } = useEvents();
  const p = progress[j.id];
  const waited = secondsBetween(j.created_at, j.started_at ?? (j.status === 'queued' ? null : j.finished_at), now);
  const ran = j.started_at ? secondsBetween(j.started_at, j.finished_at, now) : null;
  const running = j.status === 'running';
  const ended = !!j.finished_at;
  const endLabel = j.status === 'failed' ? '失败' : j.status === 'canceled' ? '取消' : j.status === 'done' ? '完成' : '结束';
  return (
    <ol className="jd-timeline" aria-label="时间线">
      <li className="is-done">
        <span className="tl-dot" aria-hidden="true" />
        <span className="tl-label">创建</span>
        <time dateTime={j.created_at ?? undefined}>{fmtTime(j.created_at)}</time>
      </li>
      <li className={j.started_at ? 'is-done' : j.status === 'queued' ? 'is-current' : 'is-pending'}>
        <span className="tl-dot" aria-hidden="true" />
        <span className="tl-label">开始</span>
        <time dateTime={j.started_at ?? undefined}>{j.started_at ? fmtTime(j.started_at) : j.status === 'queued' ? '排队中' : '—'}</time>
        {waited !== null && (j.started_at || j.status === 'queued') && (
          <span className="tl-note">排队 {fmtSeconds(waited)}</span>
        )}
      </li>
      <li className={ended ? `is-done tl-${j.status}` : running ? 'is-current' : 'is-pending'}>
        <span className="tl-dot" aria-hidden="true" />
        <span className="tl-label">{ended ? endLabel : running ? '运行中' : '结束'}</span>
        <time dateTime={j.finished_at ?? undefined}>{ended ? fmtTime(j.finished_at) : '—'}</time>
        {ran !== null && (
          <span className="tl-note" data-testid="job-elapsed">
            {ended ? '耗时' : '已运行'} {fmtSeconds(ran)}
          </span>
        )}
        {running && (
          <div className="tl-progress">
            <ThinProgress p={p} />
            {p && p.max > 0 && (
              <span className="muted small">{p.value >= p.max ? '采样完成，解码保存中…' : `${p.value}/${p.max}`}</span>
            )}
          </div>
        )}
      </li>
    </ol>
  );
}

function Overview({ j, now, onTab }: { j: Job; now: number; onTab: (t: DetailTab) => void }) {
  const req = j.result?.input_request;
  const tail = j.log.trim().split('\n').slice(-6).join('\n');
  const engine = jobEngine(j);
  return (
    <div className="jd-overview">
      <Timeline j={j} now={now} />
      {j.status === 'waiting_input' && req && <ManualInputForm key={`${j.id}:${req.key}:${req.error ?? ''}`} job={j} />}
      <dl className="jd-facts">
        <dt>类型</dt>
        <dd>{jobTypeLabel(j.type)}</dd>
        {j.context?.project_slug && (
          <>
            <dt>项目</dt>
            <dd>{j.context.project_slug}</dd>
          </>
        )}
        {j.context?.label && (
          <>
            <dt>对象</dt>
            <dd>{j.context.label}</dd>
          </>
        )}
        {engine && (
          <>
            <dt>引擎 / 模型</dt>
            <dd>{engine}</dd>
          </>
        )}
        {j.depends_on.length > 0 && (
          <>
            <dt>依赖</dt>
            <dd>
              {j.depends_on.map((d, i) => (
                <span key={d}>
                  {i > 0 && '、'}
                  <Link to={`/jobs?job=${d}`}>#{d}</Link>
                </span>
              ))}
            </dd>
          </>
        )}
      </dl>
      <div className="jd-tail">
        <div className="jd-tail-head">
          <h5>最近日志</h5>
          <button type="button" className="link" onClick={() => onTab('log')}>
            查看完整日志
          </button>
        </div>
        <pre className="log is-tail">{tail || (ACTIVE_STATUSES.includes(j.status) ? '等待日志输出…' : '（没有日志）')}</pre>
      </div>
    </div>
  );
}

function Debug({ j }: { j: Job }) {
  const req = j.result?.input_request;
  return (
    <div className="jd-debug">
      <dl className="debug-list">
        <dt>任务编号</dt>
        <dd>#{j.id}</dd>
        <dt>任务类型</dt>
        <dd>{j.type}</dd>
        {j.model_family && (
          <>
            <dt>模型族</dt>
            <dd>{j.model_family}</dd>
          </>
        )}
        <dt>用显卡</dt>
        <dd>{j.gpu ? '是' : '否'}</dd>
        <dt>优先级</dt>
        <dd>{j.priority}</dd>
        {j.depends_on.length > 0 && (
          <>
            <dt>依赖</dt>
            <dd>#{j.depends_on.join(', #')}</dd>
          </>
        )}
      </dl>
      <JsonBlock title="参数（payload）" value={j.payload} />
      {j.result && !req && <JsonBlock title="结果" value={j.result} />}
    </div>
  );
}

export interface JobDetailProps {
  id: number;
  tab: DetailTab;
  onTab: (t: DetailTab) => void;
  onRetried: (newId: number) => void;
  onClose: () => void;
}

export function JobDetail({ id, tab, onTab, onRetried, onClose }: JobDetailProps) {
  const job = useJob(id);
  const cancel = useCancelJob();
  const retry = useRetryJob();
  const confirm = useConfirm();
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const rootRef = useRef<HTMLElement>(null);
  const j = job.data;
  const live = j?.status === 'running' || j?.status === 'queued';
  const now = useNow(!!live);

  // 窄屏下详情是抽屉：打开时把焦点移进去，Esc 关掉（宽屏不抢列表的焦点，方便继续 ↑↓）
  const loaded = !!j;
  useEffect(() => {
    if (loaded && window.matchMedia?.('(max-width: 1279px)').matches) rootRef.current?.focus();
  }, [loaded]);

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape' && !(e.target as HTMLElement).closest('input, textarea, select, [role="dialog"]')) {
      e.stopPropagation();
      onClose();
    }
  };

  const head = (title: string) => (
    <div className="jd-head">
      <div className="jd-title">
        <h3>{title}</h3>
      </div>
      <button type="button" className="ghost icon jd-close" aria-label="关闭详情" onClick={onClose}>
        ×
      </button>
    </div>
  );

  if (job.isLoading) {
    return (
      <section className="panel job-detail" aria-label="任务详情" aria-busy="true">
        {head(`任务 #${id}`)}
        <div className="skeleton-stack" data-testid="detail-skeleton">
          <span className="skeleton w-60" />
          <span className="skeleton w-40" />
          <span className="skeleton h-80" />
        </div>
      </section>
    );
  }
  if (job.error || !j) {
    return (
      <section className="panel job-detail" aria-label="任务详情" onKeyDown={onKeyDown}>
        {head(`任务 #${id}`)}
        <p className="error">{job.error ? errorText(job.error) : `找不到任务 ${id}`}</p>
      </section>
    );
  }

  const active = ACTIVE_STATUSES.includes(j.status);
  const canRetry = j.status === 'failed' || j.status === 'canceled';
  const ctxUrl = j.context?.url;
  const engine = jobEngine(j);

  const doCancel = async () => {
    if (j.status === 'running') {
      const ran = j.started_at ? `已运行 ${fmtSeconds(secondsBetween(j.started_at, null) ?? 0)}` : '还没开始运行';
      const ok = await confirm({
        title: `取消任务 #${j.id}？`,
        body: `${jobTypeLabel(j.type)}任务${ran}，取消后这次的进度会丢掉。`,
        confirmText: '取消任务',
        cancelText: '继续运行',
        danger: true,
      });
      if (!ok) return;
    }
    cancel.mutate(j.id);
  };

  const onTabKey = (e: KeyboardEvent, i: number) => {
    const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const next = (i + d + DETAIL_TABS.length) % DETAIL_TABS.length;
    onTab(DETAIL_TABS[next].value);
    tabRefs.current[next]?.focus();
  };

  return (
    <section
      ref={rootRef}
      tabIndex={-1}
      className={`panel job-detail is-${j.status}`}
      aria-label="任务详情"
      onKeyDown={onKeyDown}
    >
      <div className="jd-head">
        <div className="jd-title">
          <span className={`type-dot t-${typeGroupOf(j.type)}`} aria-hidden="true" />
          <h3>{jobTitle(j)}</h3>
          <span data-testid="job-status" className={`pill job-${j.status}`}>
            {jobStatusLabel(j.status)}
          </span>
        </div>
        <button type="button" className="ghost icon jd-close" aria-label="关闭详情" onClick={onClose}>
          ×
        </button>
      </div>
      <p className="jd-sub">
        <span className="mono">#{j.id}</span>
        {j.context?.project_slug && <span>{j.context.project_slug}</span>}
        {engine && <span>{engine}</span>}
        <time dateTime={j.created_at ?? undefined} title={fmtTime(j.created_at)}>
          创建于 {relTime(j.created_at, now)}
        </time>
      </p>
      <div className="jd-actions">
        {canRetry && (
          <button
            type="button"
            className="primary"
            disabled={retry.isPending}
            onClick={() => retry.mutate(j.id, { onSuccess: (r) => onRetried(r.job_id) })}
          >
            {retry.isPending ? '重试中…' : '重试'}
          </button>
        )}
        {ctxUrl && (
          <Link className={j.status === 'done' ? 'btn primary' : 'btn'} to={ctxUrl}>
            打开所属对象
          </Link>
        )}
        {active && (
          <button type="button" className="danger" disabled={cancel.isPending} onClick={() => void doCancel()}>
            取消
          </button>
        )}
      </div>
      {cancel.data && !cancel.data.ok && <p className="warn">没能取消（任务可能已经结束）</p>}
      {(cancel.error || retry.error) && <p className="error">{errorText(cancel.error ?? retry.error)}</p>}
      {j.status === 'failed' && j.error && <ErrorCard j={j} />}

      <div className="jd-tabs" role="tablist" aria-label="任务详情页签">
        {DETAIL_TABS.map((t, i) => (
          <button
            key={t.value}
            ref={(el) => {
              tabRefs.current[i] = el;
            }}
            type="button"
            role="tab"
            id={`jd-tab-${t.value}`}
            aria-selected={tab === t.value}
            aria-controls={`jd-panel-${t.value}`}
            tabIndex={tab === t.value ? 0 : -1}
            onClick={() => onTab(t.value)}
            onKeyDown={(e) => onTabKey(e, i)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="jd-panel" role="tabpanel" id={`jd-panel-${tab}`} aria-labelledby={`jd-tab-${tab}`}>
        {tab === 'overview' && <Overview j={j} now={now} onTab={onTab} />}
        {tab === 'log' && <JobLog log={j.log ?? ''} active={active} />}
        {tab === 'traces' && <JobTraces jobId={j.id} active={active} heading={false} />}
        {tab === 'debug' && <Debug j={j} />}
      </div>
    </section>
  );
}
