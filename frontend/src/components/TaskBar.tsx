import { Link } from 'react-router-dom';
import { useCancelJob, useJobs, usePauseQueue, useQueue, useResumeQueue } from '../api/queries';
import { jobTypeLabel } from '../format';
import { useEvents } from '../ws/EventsContext';

export function TaskBar() {
  const { connected, everConnected, progress } = useEvents();
  const queue = useQueue();
  const running = useJobs('running', 5);
  const pause = usePauseQueue();
  const resume = useResumeQueue();
  const cancel = useCancelJob();
  const q = queue.data;
  const jobs = running.data ?? [];
  const current = jobs[0];
  const p = current ? progress[current.id] : undefined;

  return (
    <header className="taskbar" aria-label="任务条">
      {everConnected && !connected && <span className="warn">实时连接已断开，正在重连…</span>}
      {current ? (
        <span className="current" data-testid="taskbar-current">
          运行中：#{current.id} {jobTypeLabel(current.type)}
          {p && (
            <>
              {' '}
              <progress value={p.value} max={p.max} /> {p.value}/{p.max}
            </>
          )}
          {jobs.length > 1 && <span className="muted">（另有 {jobs.length - 1} 个）</span>}{' '}
          <button type="button" disabled={cancel.isPending} onClick={() => cancel.mutate(current.id)}>
            取消
          </button>
        </span>
      ) : (
        <span className="muted">没有运行中的任务</span>
      )}
      {q && <span>排队 {q.counts.queued}</span>}
      {q && q.counts.waiting_input > 0 && (
        <Link to="/jobs?status=waiting_input">等待输入 {q.counts.waiting_input}</Link>
      )}
      {q &&
        (q.paused ? (
          <>
            <span className="warn">队列已暂停</span>
            <button type="button" disabled={resume.isPending} onClick={() => resume.mutate()}>
              恢复队列
            </button>
          </>
        ) : (
          <button type="button" disabled={pause.isPending} onClick={() => pause.mutate()}>
            暂停队列
          </button>
        ))}
      <Link to="/jobs" className="taskbar-all">
        全部任务
      </Link>
    </header>
  );
}
