import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { errorText } from '../api/client';
import { useCancelJob, useJob, useJobs, useRetryJob } from '../api/queries';
import { ACTIVE_STATUSES, fmtDuration, fmtTime, jobStatusLabel, jobTypeLabel } from '../format';
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

  return (
    <section className="panel">
      <div className="page-head">
        <h3>
          任务 #{j.id} · {jobTypeLabel(j.type)}
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
      {(j.model_family || j.depends_on.length > 0) && (
        <p className="muted job-extra">
          {j.model_family ? `模型族 ${j.model_family}` : ''}
          {j.model_family && j.depends_on.length > 0 ? ' · ' : ''}
          {j.depends_on.length > 0 ? `依赖 #${j.depends_on.join(', #')}` : ''}
        </p>
      )}
      <div className="row">
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
        </>
      )}
      {j.status === 'waiting_input' && req && <ManualInputForm key={`${j.id}:${req.key}:${req.error ?? ''}`} job={j} />}
      <h4>日志</h4>
      <pre className="log" ref={logRef} data-testid="job-log">
        {log || '（还没有日志）'}
      </pre>
      <details>
        <summary>参数（payload）</summary>
        <pre>{JSON.stringify(j.payload, null, 2)}</pre>
      </details>
      {j.result && !req && (
        <details>
          <summary>结果</summary>
          <pre>{JSON.stringify(j.result, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}

export function JobsPage() {
  const [params, setParams] = useSearchParams();
  const status = params.get('status') ?? '';
  const selectedId = Number(params.get('job')) || null;
  const jobs = useJobs(status);

  const update = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === '') next.delete(k);
      else next.set(k, v);
    }
    setParams(next);
  };

  return (
    <div className="split jobs">
      <section className="panel">
        <div className="page-head">
          <h2>任务</h2>
          <select aria-label="状态筛选" value={status} onChange={(e) => update({ status: e.target.value })}>
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        {jobs.error && <p className="error">{errorText(jobs.error)}</p>}
        {jobs.data && jobs.data.length === 0 && <p className="muted">没有任务。</p>}
        <table className="table jobs-table">
          <thead>
            <tr>
              <th>#</th>
              <th>类型</th>
              <th>状态</th>
              <th>耗时</th>
            </tr>
          </thead>
          <tbody>
            {(jobs.data ?? []).map((j) => (
              <tr
                key={j.id}
                className={`clickable${j.id === selectedId ? ' selected' : ''}`}
                aria-selected={j.id === selectedId}
                onClick={() => update({ job: String(j.id) })}
              >
                <td>{j.id}</td>
                <td>{jobTypeLabel(j.type)}</td>
                <td>
                  <span className={`pill job-${j.status}`}>{jobStatusLabel(j.status)}</span>
                  {j.error && <div className="error">{j.error.split('\n')[0].slice(0, 60)}</div>}
                </td>
                <td>{fmtDuration(j.started_at, j.finished_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {selectedId ? (
        <JobDetail key={selectedId} id={selectedId} onRetried={(nid) => update({ job: String(nid), status: null })} />
      ) : (
        <section className="panel empty">点左侧的任务查看日志和详情。</section>
      )}
    </div>
  );
}
