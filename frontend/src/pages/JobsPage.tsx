import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, errorText } from '../api/client';
import { keys, useCancelJob, useJob, useJobs, useRetryJob } from '../api/queries';
import type { Job } from '../api/types';
import { useConfirm } from '../components/ConfirmDialog';
import { ACTIVE_STATUSES, fmtDuration, fmtShortTime, fmtTime, jobStatusLabel, jobTypeLabel } from '../format';
import { splitTraceback } from './jobs/log';
import { ManualInputForm } from './jobs/ManualInputForm';

const STATUS_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'waiting_input', label: '等待输入' },
  { value: 'running', label: '运行中' },
  { value: 'queued', label: '排队中' },
  { value: 'failed', label: '失败' },
  { value: 'done', label: '完成' },
  { value: 'canceled', label: '已取消' },
];

/** 一页多少条；「加载更多」每次再多拿这么多 */
const PAGE = 100;

/** 这类错误多半是环境问题（显卡服务、浏览器扩展、网站登录）：给个去设置页检查的入口 */
const ENV_ERROR = /comfyui|扩展|登录/i;

/** 列表和详情里的任务名：任务类型 + 它在说哪一集 / 哪个镜头 / 哪个角色 */
function jobTitle(j: Job): string {
  const label = j.context?.label;
  return label ? `${jobTypeLabel(j.type)} · ${label}` : jobTypeLabel(j.type);
}

function JobDetail({ id, onRetried }: { id: number; onRetried: (newId: number) => void }) {
  const job = useJob(id);
  const cancel = useCancelJob();
  const retry = useRetryJob();
  const logRef = useRef<HTMLPreElement>(null);
  const log = job.data?.log ?? '';
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [log]);

  if (job.isLoading) return <section className="panel loading">加载中…</section>;
  if (job.error || !job.data) {
    return <section className="panel error">{job.error ? errorText(job.error) : `找不到任务 ${id}`}</section>;
  }
  const j = job.data;
  const req = j.result?.input_request;
  const active = ACTIVE_STATUSES.includes(j.status);
  const { brief, hasTrace } = splitTraceback(log);
  const ctxUrl = j.context?.url;

  return (
    <section className="panel">
      <div className="page-head">
        <h3>
          任务 #{j.id} · {jobTitle(j)}
        </h3>
        <span data-testid="job-status" className={`pill job-${j.status}`}>
          {jobStatusLabel(j.status)}
        </span>
      </div>
      <dl className="job-meta">
        <div>
          <dt>创建</dt>
          <dd>{fmtTime(j.created_at)}</dd>
        </div>
        <div>
          <dt>开始</dt>
          <dd>{fmtTime(j.started_at)}</dd>
        </div>
        <div>
          <dt>结束</dt>
          <dd>{fmtTime(j.finished_at)}</dd>
        </div>
        <div>
          <dt>耗时</dt>
          <dd>{fmtDuration(j.started_at, j.finished_at)}</dd>
        </div>
      </dl>
      <div className="row">
        {ctxUrl && (
          <Link className="btn" to={ctxUrl}>
            打开所属对象
          </Link>
        )}
        {active && (
          <button type="button" className="danger" disabled={cancel.isPending} onClick={() => cancel.mutate(j.id)}>
            取消
          </button>
        )}
        {(j.status === 'failed' || j.status === 'canceled') && (
          <button
            type="button"
            className="primary"
            disabled={retry.isPending}
            onClick={() => retry.mutate(j.id, { onSuccess: (r) => onRetried(r.job_id) })}
          >
            重试
          </button>
        )}
      </div>
      {cancel.data && !cancel.data.ok && <p className="warn">没能取消（任务可能已经结束）</p>}
      {(cancel.error || retry.error) && <p className="error">{errorText(cancel.error ?? retry.error)}</p>}
      {j.error && (
        <>
          <h4>错误</h4>
          <pre className="error">{j.error}</pre>
          {ENV_ERROR.test(j.error) && (
            <p className="muted">
              看起来是环境问题（显卡服务、浏览器扩展或网站登录）。<Link to="/settings">去设置检查</Link>
            </p>
          )}
        </>
      )}
      {j.status === 'waiting_input' && req && <ManualInputForm key={`${j.id}:${req.key}:${req.error ?? ''}`} job={j} />}
      <h4>日志</h4>
      <pre className="log" ref={logRef} data-testid="job-log">
        {brief || '（还没有日志）'}
      </pre>
      {hasTrace && (
        <details>
          <summary>完整日志</summary>
          <pre className="log">{log}</pre>
        </details>
      )}
      <details>
        <summary>调试信息</summary>
        <dl className="debug-list">
          <dt>任务类型</dt>
          <dd>{j.type}</dd>
          {j.model_family && (
            <>
              <dt>模型族</dt>
              <dd>{j.model_family}</dd>
            </>
          )}
          {j.depends_on.length > 0 && (
            <>
              <dt>依赖</dt>
              <dd>#{j.depends_on.join(', #')}</dd>
            </>
          )}
        </dl>
        <h5>参数（payload）</h5>
        <pre>{JSON.stringify(j.payload, null, 2)}</pre>
        {j.result && !req && (
          <>
            <h5>结果</h5>
            <pre>{JSON.stringify(j.result, null, 2)}</pre>
          </>
        )}
      </details>
    </section>
  );
}

export function JobsPage() {
  const [params, setParams] = useSearchParams();
  const status = params.get('status') ?? '';
  const selectedId = Number(params.get('job')) || null;
  const [limit, setLimit] = useState(PAGE);
  const jobs = useJobs(status, limit, true);
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [retrying, setRetrying] = useState(false);
  const [retryMsg, setRetryMsg] = useState<string | null>(null);
  const rows = jobs.data ?? [];
  const failed = rows.filter((j) => j.status === 'failed');
  const hasMore = rows.length >= limit;

  const update = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === '') next.delete(k);
      else next.set(k, v);
    }
    setParams(next);
  };

  /** 逐个重试列表里（已加载的）失败任务：一个没成功不影响后面的，最后说成功了几个 */
  const retryAllFailed = async () => {
    const list = failed;
    const ok = await confirm({
      title: `重试全部 ${list.length} 个失败任务？`,
      body: '每个任务按原来的参数重新排队，已有的结果不会被删。',
      confirmText: '全部重试',
    });
    if (!ok) return;
    setRetrying(true);
    setRetryMsg(null);
    let done = 0;
    const errors: string[] = [];
    for (const j of list) {
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
    setRetryMsg(`已重新提交 ${done}/${list.length} 个失败任务${failNote}`);
  };

  return (
    <div className="page">
      <header className="page-head jobs-head">
        <span className="eyebrow">04 · 任务</span>
        <h2>任务</h2>
        <select
          aria-label="状态筛选"
          value={status}
          onChange={(e) => {
            setLimit(PAGE);
            setRetryMsg(null);
            update({ status: e.target.value });
          }}
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        {status === 'failed' && failed.length > 0 && (
          <button type="button" disabled={retrying} onClick={() => void retryAllFailed()}>
            {retrying ? '重试中…' : `重试全部失败（${failed.length}）`}
          </button>
        )}
      </header>
      {retryMsg && (
        <p className="notice" role="status">
          {retryMsg}
        </p>
      )}
      <div className="split jobs">
        <section className="panel">
          {jobs.error && <p className="error">{errorText(jobs.error)}</p>}
          {jobs.data && rows.length === 0 && <p className="muted">没有任务。</p>}
          <table className="table jobs-table">
            <thead>
              <tr>
                <th>#</th>
                <th>任务</th>
                <th>状态</th>
                <th>耗时</th>
                <th>开始时间</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((j) => (
                <tr
                  key={j.id}
                  className={`clickable${j.id === selectedId ? ' selected' : ''}`}
                  aria-selected={j.id === selectedId}
                  onClick={() => update({ job: String(j.id) })}
                >
                  <td>
                    {/* 整行能点；键盘用户用这个按钮 */}
                    <button
                      type="button"
                      className="link job-id"
                      aria-label={`查看任务 #${j.id}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        update({ job: String(j.id) });
                      }}
                    >
                      {j.id}
                    </button>
                  </td>
                  <td>{jobTitle(j)}</td>
                  <td>
                    <span className={`pill job-${j.status}`}>{jobStatusLabel(j.status)}</span>
                    {j.error && <div className="error">{j.error.split('\n')[0].slice(0, 60)}</div>}
                  </td>
                  <td>{fmtDuration(j.started_at, j.finished_at)}</td>
                  <td title={fmtTime(j.started_at)}>{fmtShortTime(j.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {hasMore && (
            <div className="row jobs-more">
              <button type="button" disabled={jobs.isPlaceholderData} onClick={() => setLimit((n) => n + PAGE)}>
                {jobs.isPlaceholderData ? '加载中…' : '加载更多'}
              </button>
            </div>
          )}
        </section>
        {selectedId ? (
          <JobDetail key={selectedId} id={selectedId} onRetried={(nid) => update({ job: String(nid), status: null })} />
        ) : (
          <section className="panel empty">点左侧的任务查看日志和详情。</section>
        )}
      </div>
    </div>
  );
}
