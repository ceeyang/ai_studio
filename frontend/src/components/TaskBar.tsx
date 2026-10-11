import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { errorText } from '../api/client';
import { useCancelJob, useFreeGpu, useJobs, usePauseQueue, useQueue, useResumeQueue } from '../api/queries';
import { fmtDuration, jobTypeLabel } from '../format';
import { useConfirm } from './ConfirmDialog';
import { HealthBanner } from './HealthBanner';
import { useEvents } from '../ws/EventsContext';
import { isDecoding } from '../ws/events';

function freeGpuNoticeText(before: number | null, after: number | null): string {
  return before !== null && after !== null
    ? `已释放：显存空闲 ${before.toFixed(1)} → ${after.toFixed(1)} GB`
    : '已释放显存';
}

export function TaskBar() {
  const { connected, everConnected, progress, gpuFreedAt } = useEvents();
  const queue = useQueue();
  const running = useJobs('running', 5);
  const pause = usePauseQueue();
  const resume = useResumeQueue();
  const cancel = useCancelJob();
  const freeGpu = useFreeGpu();
  const confirm = useConfirm();
  const [gpuNotice, setGpuNotice] = useState<string | null>(null);
  const q = queue.data;
  const jobs = running.data ?? [];
  const current = jobs[0];
  const p = current ? progress[current.id] : undefined;
  const gpuBusy = jobs.some((j) => j.gpu);
  // 没有进度事件时（LLM / 网页任务）显示实时耗时
  const [now, setNow] = useState(() => Date.now());
  const ticking = !!current && !p;
  useEffect(() => {
    if (!ticking) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [ticking]);

  // 队列空闲时后端会自动释放一次显存，并推一条带 gpu_idle_freed 标记的队列事件；
  // 这里只用它的时间戳触发提示，具体数值用户看不到也没关系。
  useEffect(() => {
    if (gpuFreedAt !== undefined) setGpuNotice('已自动释放显存');
  }, [gpuFreedAt]);

  useEffect(() => {
    if (!gpuNotice) return;
    const t = setTimeout(() => setGpuNotice(null), 6000);
    return () => clearTimeout(t);
  }, [gpuNotice]);

  const waiting = q?.counts.waiting_input ?? 0;

  // 取消会丢掉这次已经跑的进度：先确认，正文写已经跑了多久
  const handleCancel = async () => {
    if (!current) return;
    const id = current.id;
    const ran = current.started_at ? `已运行 ${fmtDuration(current.started_at, null)}` : '还没开始运行';
    const ok = await confirm({
      title: `取消任务 #${id}？`,
      body: `${jobTypeLabel(current.type)}任务${ran}，取消后这次的进度会丢掉。`,
      confirmText: '取消任务',
      cancelText: '继续运行',
      danger: true,
    });
    if (ok) cancel.mutate(id);
  };

  const handleFreeGpu = () => {
    freeGpu.mutate(undefined, {
      onSuccess: (r) => setGpuNotice(freeGpuNoticeText(r.before.vram_free_gb, r.after.vram_free_gb)),
      onError: (err) => setGpuNotice(errorText(err)),
    });
  };

  return (
    <>
      <header className="taskbar" aria-label="任务条">
        {everConnected && !connected && <span className="pill warn">实时连接已断开，正在重连…</span>}
        {current ? (
          <span className="current" data-testid="taskbar-current">
            {/* 整块可点：跳到这个任务的详情；「取消」是旁边单独的按钮，不触发跳转 */}
            <Link
              to={`/jobs?job=${current.id}`}
              className="current-link"
              title={`查看任务 #${current.id} 的详情`}
              data-testid="taskbar-current-link"
            >
              <span className="current-label">
                运行中 #{current.id} {jobTypeLabel(current.type)}
                {current.context?.label && <span className="current-ctx"> · {current.context.label}</span>}
              </span>
              {p &&
                (isDecoding(p) ? (
                  <>
                    <progress className="is-decoding" value={p.max} max={p.max} /> 采样完成，解码保存中…
                  </>
                ) : (
                  <>
                    <progress value={p.value} max={p.max} /> {p.value}/{p.max}
                  </>
                ))}
              {!p && current.started_at && <span className="current-elapsed">{fmtDuration(current.started_at, null, now)}</span>}
            </Link>
            <button type="button" className="ghost" disabled={cancel.isPending} onClick={() => void handleCancel()}>
              取消
            </button>
          </span>
        ) : waiting > 0 ? (
          // 没有在跑的，只剩等人工输入的：这时候卡住的是"人"，直接说清楚
          <Link to="/jobs?status=waiting_input" className="idle is-waiting">
            {waiting} 个任务在等你输入
          </Link>
        ) : (
          <span className="idle">没有运行中的任务</span>
        )}
        {jobs.length > 1 && (
          <Link to="/jobs?status=running" className="pill job-running taskbar-more">
            另有 {jobs.length - 1} 个运行中
          </Link>
        )}
        {q && q.counts.queued > 0 && (
          <Link to="/jobs?status=queued" className="pill job-queued" title="查看排队中的任务">
            {current ? `另有 ${q.counts.queued} 个排队` : `排队 ${q.counts.queued}`}
          </Link>
        )}
        {current && waiting > 0 && (
          <Link to="/jobs?status=waiting_input" className="pill job-waiting_input">等待输入 {waiting}</Link>
        )}
        {q &&
          (q.paused ? (
            <>
              <span className="pill warn">队列已暂停</span>
              <button type="button" className="soft" disabled={resume.isPending} onClick={() => resume.mutate()}>
                恢复队列
              </button>
            </>
          ) : (
            <button type="button" className="ghost" disabled={pause.isPending} onClick={() => pause.mutate()}>
              暂停队列
            </button>
          ))}
        <button
          type="button"
          className="ghost"
          disabled={gpuBusy || freeGpu.isPending}
          title={gpuBusy ? '有任务正在用显卡，等它结束再释放' : undefined}
          onClick={handleFreeGpu}
        >
          释放显存
        </button>
        {gpuNotice && (
          <span className="pill" role="status">
            {gpuNotice}
          </span>
        )}
        <Link to="/jobs" className="taskbar-all">
          全部任务
        </Link>
      </header>
      <HealthBanner />
    </>
  );
}
