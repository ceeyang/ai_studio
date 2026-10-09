// 角色三视图：LLM 先写 9 格视角描述（可改），再逐格出图（定妆照锁脸）。
// 首帧和视频打包参考图时，按镜头景别 / 机位从这里挑最匹配的一格。
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { errorText } from '../../api/client';
import {
  useGenerateTurnaround,
  useJob,
  usePatchTurnaroundPlan,
  usePlanTurnaround,
  useSelectTurnaround,
} from '../../api/queries';
import type { Character, JobBrief, TurnaroundCell, TurnaroundView } from '../../api/types';
import { useEvents } from '../../ws/EventsContext';

function CellView({
  c,
  cell,
  job,
  canGenerate,
  busy,
  onGenerate,
  onSave,
  onSelect,
}: {
  c: Character;
  cell: TurnaroundCell;
  job: JobBrief | undefined;
  canGenerate: boolean;
  busy: boolean;
  onGenerate: () => void;
  onSave: (text: string) => void;
  onSelect: (candidateId: number) => void;
}) {
  const { progress } = useEvents();
  const [text, setText] = useState(cell.description);
  useEffect(() => setText(cell.description), [cell.description]);
  const dirty = text.trim() !== cell.description.trim();
  const has = cell.url != null;
  const pr = job ? progress[job.id] : undefined;
  const state = job ? null : cell.stale ? { cls: 'stale', text: '过期' } : has ? { cls: 'ok', text: '已生成' } : { cls: 'none', text: '未生成' };

  return (
    <div className={`turn-cell${cell.stale ? ' is-stale' : ''}`} data-testid={`turn-${cell.view}`}>
      <div className="turn-cell-head">
        <strong>{cell.label}</strong>
        {state && <span className={`badge ${state.cls}`}>{state.text}</span>}
      </div>
      <div className="turn-media">
        {job ? (
          <div className="turn-pending" data-testid="turn-pending">
            <span className="spinner" aria-hidden="true" />
            <span>{job.status === 'running' ? '生成中' : '排队中'}</span>
            {pr && pr.max > 0 && <span className="muted">{Math.round((pr.value / pr.max) * 100)}%</span>}
          </div>
        ) : has ? (
          <a href={cell.url!} target="_blank" rel="noreferrer" title="打开原图">
            <img src={cell.url!} alt={`${c.name} ${cell.label}`} loading="lazy" />
          </a>
        ) : (
          <div className="turn-empty">{cell.description ? '还没出图' : '还没有描述'}</div>
        )}
      </div>
      {cell.candidates.length > 1 && (
        <div className="turn-cands" role="group" aria-label={`${cell.label} 的候选`}>
          {cell.candidates.map((k) => {
            const selected = k.id === cell.selected_candidate_id;
            return (
              <button
                key={k.id}
                type="button"
                className={`turn-cand${selected ? ' is-selected' : ''}`}
                aria-pressed={selected}
                aria-label={`${cell.label} 候选 #${k.id}${selected ? '（当前）' : ''}`}
                title={selected ? '当前选定' : '选这一张'}
                disabled={busy}
                onClick={() => {
                  if (!selected) onSelect(k.id);
                }}
              >
                <img src={k.url} alt="" loading="lazy" />
              </button>
            );
          })}
        </div>
      )}
      <textarea
        rows={3}
        aria-label={`${cell.label} 视角描述`}
        placeholder="视角描述（英文），先点“生成视角描述”"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="turn-actions">
        {dirty && (
          <button type="button" className="sm" disabled={busy} onClick={() => onSave(text.trim())}>
            保存描述
          </button>
        )}
        <button
          type="button"
          className="sm"
          disabled={busy || !!job || !canGenerate || !cell.description.trim() || dirty}
          title={dirty ? '先保存描述' : undefined}
          onClick={onGenerate}
        >
          {has ? '重新生成' : '生成'}
        </button>
      </div>
    </div>
  );
}

export function TurnaroundPanel({ c, slug }: { c: Character; slug: string }) {
  const plan = usePlanTurnaround(slug);
  const patch = usePatchTurnaroundPlan(slug);
  const gen = useGenerateTurnaround(slug);
  const select = useSelectTurnaround(slug);
  const [planJob, setPlanJob] = useState<number | null>(null);
  const job = useJob(planJob);
  const cells = c.turnaround ?? [];
  const jobs = c.active_jobs ?? [];
  const planning = jobs.some((j) => j.type === 'character.turnaround_plan');
  const cellJob = (view: TurnaroundView) => jobs.find((j) => j.type === 'image.turnaround' && j.view === view);
  const hasPlan = cells.some((x) => x.description.trim());
  const allPlanned = cells.length > 0 && cells.every((x) => x.description.trim());
  const hasPortrait = c.portrait_candidate_id != null;
  const running = jobs.filter((j) => j.type === 'image.turnaround').length;
  const planFailed = job.data && (job.data.status === 'failed' || job.data.status === 'canceled');
  const waitingInput = job.data?.status === 'waiting_input';
  const error = [plan.error, patch.error, gen.error, select.error].find(Boolean);
  const busy = patch.isPending || gen.isPending || select.isPending;

  return (
    <section className="turnaround" aria-label={`${c.name} 的三视图`}>
      <div className="turn-head">
        <h5>三视图</h5>
        <p className="muted small">
          9 个视角逐格出图，定妆照锁脸；出场镜头的首帧和视频按景别 / 机位带上最匹配的一格。
        </p>
        <div className="row">
          <button
            type="button"
            disabled={plan.isPending || planning || !c.visual_prompt.trim()}
            title={c.visual_prompt.trim() ? '让 LLM 按外貌提示词写 9 格视角描述（会覆盖现有描述）' : '先写外貌提示词'}
            onClick={() => plan.mutate(c.id, { onSuccess: (r) => setPlanJob(r.job_ids[0]) })}
          >
            {planning ? '写描述中…' : hasPlan ? '重写视角描述' : '生成视角描述'}
          </button>
          <button
            type="button"
            className="primary"
            disabled={busy || !allPlanned || !hasPortrait}
            title={!allPlanned ? '9 格都要有视角描述' : undefined}
            onClick={() => gen.mutate({ id: c.id })}
          >
            {running > 0 ? `全部生成（${running} 格进行中）` : '全部生成'}
          </button>
        </div>
      </div>
      {!hasPortrait && <p className="notice">先选定定妆照：三视图用它锁脸。</p>}
      {planFailed && (
        <p className="error">
          生成视角描述失败：{job.data?.error ?? job.data?.status} · <Link to={`/jobs?job=${planJob}`}>查看任务</Link>
        </p>
      )}
      {waitingInput && (
        <p className="notice">
          视角描述在等人工输入 · <Link to={`/jobs?job=${planJob}`}>去填写</Link>
        </p>
      )}
      <div className="turn-grid">
        {cells.map((cell) => (
          <CellView
            key={cell.view}
            c={c}
            cell={cell}
            job={cellJob(cell.view)}
            canGenerate={hasPortrait}
            busy={busy}
            onGenerate={() => gen.mutate({ id: c.id, views: [cell.view] })}
            onSave={(text) => patch.mutate({ id: c.id, views: { [cell.view]: text } })}
            onSelect={(candidateId) => select.mutate(candidateId)}
          />
        ))}
      </div>
      {error && <p className="error">{errorText(error)}</p>}
    </section>
  );
}
