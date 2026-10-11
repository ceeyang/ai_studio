// 剧本体检：分镜好了以后、花首帧 / 视频额度之前，看一眼开头抓不抓人、结尾有没有卡点。
// 规则检查（第一句台词在第几秒、前 15 秒几个镜头、超过 10 秒的镜头、结尾、总时长）不调 LLM，打开就有；
// 「开始体检 / 重新体检」提交 script.review 任务，文本模型按开场钩子、冲突与爽点密度、结尾卡点、节奏打分并给改写建议。
// 默认收起，一行摘要：几项要注意、AI 平均分。
import { useState } from 'react';
import { errorText } from '../../api/client';
import { useRunScriptReview, useScriptReview } from '../../api/queries';
import type { ReviewDimension, ReviewSeverity, ScriptReview } from '../../api/types';

export const DIMENSION_LABEL: Record<ReviewDimension, string> = {
  hook: '开场钩子（前 15 秒）',
  conflict: '冲突与爽点密度',
  ending: '结尾卡点',
  pacing: '节奏',
};
const DIMENSIONS: ReviewDimension[] = ['hook', 'conflict', 'ending', 'pacing'];
const SEVERITY_LABEL: Record<ReviewSeverity, string> = { ok: '通过', info: '提示', warn: '注意', error: '严重' };

function scoreTone(n: number): string {
  return n >= 8 ? 'ok' : n >= 6 ? 'info' : n >= 4 ? 'warn' : 'error';
}

/** 收起时的一行摘要 */
export function reviewSummary(d: ScriptReview | undefined): string {
  if (!d) return '加载中…';
  const bad = d.rules.findings.filter((f) => f.severity === 'warn' || f.severity === 'error').length;
  const parts = [bad > 0 ? `规则检查 ${bad} 项要注意` : '规则检查都通过'];
  const r = d.review;
  if (r) {
    const avg = DIMENSIONS.reduce((s, k) => s + r[k].score, 0) / DIMENSIONS.length;
    parts.push(`AI 体检平均 ${avg.toFixed(1)} 分${r.stale ? '（分镜改过了）' : ''}`);
  }
  const st = d.job?.status;
  if (st === 'queued' || st === 'running') parts.push('AI 体检中…');
  return parts.join(' · ');
}

export function ScriptReviewPanel({ episodeId }: { episodeId: number }) {
  const [open, setOpen] = useState(false);
  const q = useScriptReview(episodeId);
  const run = useRunScriptReview(episodeId);
  const d = q.data;
  const st = d?.job?.status;
  const active = st === 'queued' || st === 'running';
  const review = d?.review ?? null;
  // 最近一次任务失败、且它不是现在这份结果的来源：显示失败原因
  const failed = d?.job && st === 'failed' && d.job.id !== review?.job_id ? d.job : null;

  return (
    <div className={`block review-box${open ? ' is-open' : ''}`} data-testid="script-review">
      <button type="button" className="block-row" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="block-no">剧本体检</span>
        <span className="block-sum">{q.error ? '没取到体检结果' : reviewSummary(d)}</span>
        <span className="chev" aria-hidden="true" />
      </button>
      <div className="block-edit" hidden={!open}>
        {q.error && <p className="error">{errorText(q.error)}</p>}
        {d && (
          <>
            <h4 className="review-h">
              规则检查
              <span className="muted">
                {' '}
                全集 {d.rules.total_seconds} 秒 · {d.rules.shot_count} 个镜头 · 不调模型，按分镜现算
              </span>
            </h4>
            <ul className="review-list" aria-label="规则检查">
              {d.rules.findings.map((f) => (
                <li key={f.key} className={`rv rv-${f.severity}`}>
                  <span className="rv-tag">{SEVERITY_LABEL[f.severity]}</span>
                  <span>
                    {f.title}
                    {f.detail && <span className="muted">：{f.detail}</span>}
                  </span>
                </li>
              ))}
            </ul>

            <h4 className="review-h">AI 体检</h4>
            {active && <p className="muted">AI 体检{st === 'queued' ? '排队中' : '中'}…（约半分钟到一分钟）</p>}
            {st === 'waiting_input' && <p className="warn">AI 体检在等人工粘贴模型输出：到任务页处理，或在设置里给「剧本体检」选一个文本模型</p>}
            {failed && <p className="error">上次体检失败：{failed.error || '未知原因'}</p>}
            {review ? (
              <>
                {review.stale && <p className="warn">体检之后分镜或段落改过了，结果可能过时，建议重新体检</p>}
                {review.summary && <p>{review.summary}</p>}
                <ul className="review-list" aria-label="AI 打分">
                  {DIMENSIONS.map((k) => (
                    <li key={k} className={`rv rv-${scoreTone(review[k].score)}`}>
                      <span className="rv-tag">{review[k].score}/10</span>
                      <span>
                        {DIMENSION_LABEL[k]}
                        <span className="muted">：{review[k].reason}</span>
                      </span>
                    </li>
                  ))}
                </ul>
                {review.suggestions.length > 0 && (
                  <ol className="review-sugs" aria-label="改写建议">
                    {review.suggestions.map((s, i) => (
                      <li key={i}>
                        <b>{s.ref}</b> <span className="muted">{DIMENSION_LABEL[s.dimension]}</span>
                        {s.problem && <div className="muted">{s.problem}</div>}
                        <div>→ {s.rewrite}</div>
                      </li>
                    ))}
                  </ol>
                )}
              </>
            ) : (
              !active && <p className="muted">还没做过 AI 体检。体检用文本模型给开场、冲突、结尾、节奏打分并给改写建议，不花出图额度。</p>
            )}
            {run.error && <p className="error">{errorText(run.error)}</p>}
            <div className="review-actions">
              <button type="button" className="sm" disabled={active || run.isPending} onClick={() => run.mutate()}>
                {review ? '重新体检' : '开始体检'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
