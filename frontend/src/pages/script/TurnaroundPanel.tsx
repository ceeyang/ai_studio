// 角色三视图：按通行的角色设定图做——全身 4 视角（正面 / 3/4 / 侧面 / 背面）一张图一起出，
// 头像 3 视角（正脸 / 3/4 / 侧脸）一张图；默认 H3 + 定妆照锁脸。选定的设定图自动切成单个视角，
// 出场镜头的首帧和视频按景别 / 机位带上最匹配的一格。
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { errorText } from '../../api/client';
import {
  useCropTurnaround,
  useGenerateTurnaround,
  useJob,
  usePatchTurnaroundLook,
  useSelectTurnaroundSheet,
  useTurnaroundLook,
} from '../../api/queries';
import type { Character, JobBrief, TurnaroundCell, TurnaroundSheet } from '../../api/types';
import { useEvents } from '../../ws/EventsContext';

function SheetBlock({
  c,
  sheet,
  cells,
  job,
  canGenerate,
  busy,
  onGenerate,
  onSelect,
  onCrop,
}: {
  c: Character;
  sheet: TurnaroundSheet;
  cells: TurnaroundCell[];
  job: JobBrief | undefined;
  canGenerate: boolean;
  busy: boolean;
  onGenerate: () => void;
  onSelect: (candidateId: number) => void;
  onCrop: (method: 'auto' | 'equal') => void;
}) {
  const { progress } = useEvents();
  const has = sheet.url != null;
  const pr = job ? progress[job.id] : undefined;
  const state = job
    ? null
    : sheet.stale
      ? { cls: 'stale', text: '过期' }
      : has
        ? { cls: 'ok', text: '已生成' }
        : { cls: 'none', text: '未生成' };

  return (
    <div className={`turn-sheet${sheet.stale ? ' is-stale' : ''}`} data-testid={`sheet-${sheet.sheet}`}>
      <div className="turn-sheet-head">
        <strong>{sheet.label}</strong>
        {state && <span className={`badge ${state.cls}`}>{state.text}</span>}
        <div className="turn-actions">
          <button type="button" className="sm" disabled={busy || !!job || !canGenerate} onClick={onGenerate}>
            {has ? '重新生成' : '生成'}
          </button>
          <button
            type="button"
            className="sm"
            disabled={busy || !has}
            title="按人物重新切成单个视角（找不准时自动退回等宽）"
            onClick={() => onCrop('auto')}
          >
            重新切图
          </button>
          <button type="button" className="sm" disabled={busy || !has} title="不找人物，直接按等宽切" onClick={() => onCrop('equal')}>
            等宽切图
          </button>
        </div>
      </div>
      <div className={`turn-sheet-media is-${sheet.sheet}`}>
        {job ? (
          <div className="turn-pending" data-testid="sheet-pending">
            <span className="spinner" aria-hidden="true" />
            <span>{job.status === 'running' ? '生成中' : '排队中'}</span>
            {pr && pr.max > 0 && <span className="muted">{Math.round((pr.value / pr.max) * 100)}%</span>}
          </div>
        ) : has ? (
          <a href={sheet.url!} target="_blank" rel="noreferrer" title="打开原图">
            <img src={sheet.url!} alt={`${c.name} ${sheet.label}`} loading="lazy" />
          </a>
        ) : (
          <div className="turn-empty">还没出图</div>
        )}
      </div>
      {sheet.candidates.length > 1 && (
        <div className="turn-cands" role="group" aria-label={`${sheet.label} 的候选`}>
          {sheet.candidates.map((k) => {
            const selected = k.id === sheet.selected_candidate_id;
            return (
              <button
                key={k.id}
                type="button"
                className={`turn-cand is-${sheet.sheet}${selected ? ' is-selected' : ''}`}
                aria-pressed={selected}
                aria-label={`${sheet.label} 候选 #${k.id}${selected ? '（当前）' : ''}`}
                title={selected ? '当前选定' : '选这一张（会重新切图）'}
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
      {sheet.crop_method === 'equal' && (
        <p className="notice small">没按人物切准（人数不对或背景不干净），现在是等宽切的：可以换一张候选或重新切图。</p>
      )}
      <div className={`turn-cells is-${sheet.sheet}`}>
        {cells.map((cell) => (
          <figure
            key={cell.view}
            className={`turn-cell${cell.stale ? ' is-stale' : ''}`}
            data-testid={`turn-${cell.view}`}
          >
            <div className="turn-cell-media">
              {cell.url ? (
                <a href={cell.url} target="_blank" rel="noreferrer" title="打开切图">
                  <img src={cell.url} alt={`${c.name} ${cell.label}`} loading="lazy" />
                </a>
              ) : (
                <div className="turn-empty">—</div>
              )}
            </div>
            <figcaption>
              <span>{cell.label}</span>
              {cell.stale && <span className="badge stale">过期</span>}
            </figcaption>
          </figure>
        ))}
      </div>
    </div>
  );
}

export function TurnaroundPanel({ c, slug }: { c: Character; slug: string }) {
  const lookJob = useTurnaroundLook(slug);
  const patchLook = usePatchTurnaroundLook(slug);
  const gen = useGenerateTurnaround(slug);
  const select = useSelectTurnaroundSheet(slug);
  const crop = useCropTurnaround(slug);
  const t = c.turnaround;
  const [look, setLook] = useState(t?.look ?? '');
  useEffect(() => setLook(t?.look ?? ''), [t?.look]);
  const [lookJobId, setLookJobId] = useState<number | null>(null);
  const job = useJob(lookJobId);
  if (!t) return null;

  const jobs = c.active_jobs ?? [];
  const writing = jobs.some((j) => j.type === 'character.turnaround_look');
  const sheetJob = (key: string) => jobs.find((j) => j.type === 'image.turnaround' && j.sheet === key);
  const running = jobs.filter((j) => j.type === 'image.turnaround').length;
  const hasPortrait = c.portrait_candidate_id != null;
  const hasLook = !!(look.trim() || c.visual_prompt.trim());
  const canGenerate = hasPortrait && hasLook;
  const dirty = look.trim() !== t.look.trim();
  const lookFailed = job.data && (job.data.status === 'failed' || job.data.status === 'canceled');
  const waitingInput = job.data?.status === 'waiting_input';
  const error = [lookJob.error, patchLook.error, gen.error, select.error, crop.error].find(Boolean);
  const busy = patchLook.isPending || gen.isPending || select.isPending || crop.isPending;

  return (
    <section className="turnaround" aria-label={`${c.name} 的三视图`}>
      <div className="turn-head">
        <h5>三视图</h5>
        <p className="muted small">
          一张设定图出同一个人的正面、3/4、侧面、背面（另一张出正脸、3/4、侧脸），定妆照锁脸，自动切成单个视角；
          出场镜头的首帧和视频按景别 / 机位带上最匹配的一格。
        </p>
        <div className="row">
          <button
            type="button"
            className="primary"
            disabled={busy || !canGenerate}
            title={!hasPortrait ? '先选定定妆照' : undefined}
            onClick={() => gen.mutate({ id: c.id })}
          >
            {running > 0 ? `全部生成（${running} 张进行中）` : '全部生成'}
          </button>
        </div>
      </div>
      {!hasPortrait && <p className="notice">先选定定妆照：三视图用它锁脸。</p>}

      <div className="turn-look">
        <label htmlFor={`turn-look-${c.id}`}>全身描述</label>
        <textarea
          id={`turn-look-${c.id}`}
          rows={3}
          placeholder="从头到脚的英文描述（含下装和鞋）；空着就用外貌提示词"
          value={look}
          onChange={(e) => setLook(e.target.value)}
        />
        <div className="turn-actions">
          {dirty && (
            <button type="button" className="sm" disabled={busy} onClick={() => patchLook.mutate({ id: c.id, look: look.trim() })}>
              保存描述
            </button>
          )}
          <button
            type="button"
            className="sm"
            disabled={lookJob.isPending || writing || !c.visual_prompt.trim()}
            title={c.visual_prompt.trim() ? '让 LLM 按外貌提示词整理（会覆盖现在的描述）' : '先写外貌提示词'}
            onClick={() => lookJob.mutate(c.id, { onSuccess: (r) => setLookJobId(r.job_ids[0]) })}
          >
            {writing ? '整理中…' : t.look.trim() ? '重写全身描述' : 'AI 整理全身描述'}
          </button>
        </div>
      </div>
      {lookFailed && (
        <p className="error">
          整理全身描述失败：{job.data?.error ?? job.data?.status} · <Link to={`/jobs?job=${lookJobId}`}>查看任务</Link>
        </p>
      )}
      {waitingInput && (
        <p className="notice">
          全身描述在等人工输入 · <Link to={`/jobs?job=${lookJobId}`}>去填写</Link>
        </p>
      )}

      {t.sheets.map((sh) => (
        <SheetBlock
          key={sh.sheet}
          c={c}
          sheet={sh}
          cells={t.cells.filter((x) => x.sheet === sh.sheet)}
          job={sheetJob(sh.sheet)}
          canGenerate={canGenerate}
          busy={busy}
          onGenerate={() => gen.mutate({ id: c.id, sheets: [sh.sheet] })}
          onSelect={(candidateId) => select.mutate({ id: c.id, sheet: sh.sheet, candidateId })}
          onCrop={(method) => crop.mutate({ id: c.id, sheet: sh.sheet, method })}
        />
      ))}
      {error && <p className="error">{errorText(error)}</p>}
    </section>
  );
}
