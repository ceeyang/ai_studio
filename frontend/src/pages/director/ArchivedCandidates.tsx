import { useState } from 'react';
import { errorText } from '../../api/client';
import { useArchivedCandidates, useRestoreArchived } from '../../api/queries';
import type { ArchivedCandidate } from '../../api/types';
import { engineLabel, fmtTime } from '../../format';
import { SLOT_LABEL } from './slots';

/** 能恢复到镜头上的种类：首帧回首帧槽位，视频回视频槽位 */
const KINDS = ['first_frame', 'video'];

export interface ArchivedCandidatesProps {
  episodeId: number;
  /** 恢复到哪个镜头 */
  shotId: number;
}

function snapshotText(a: ArchivedCandidate): string {
  const action = a.shot_snapshot?.action;
  return typeof action === 'string' ? action : '';
}

/**
 * 镜头抽屉里的「历史候选」：重跑分镜（或删镜头）删掉的镜头留下的首帧 / 视频，收着时不请求，
 * 展开后列出同一集的，每条可以「恢复到这个镜头」（这个镜头那一格还没选定时恢复的就是选定）。
 */
export function ArchivedCandidates({ episodeId, shotId }: ArchivedCandidatesProps) {
  const [open, setOpen] = useState(false);
  const q = useArchivedCandidates(episodeId, open);
  const restore = useRestoreArchived(episodeId);
  const rows = (q.data ?? []).filter((a) => KINDS.includes(a.kind));

  return (
    <section className="archived-cands" aria-label="历史候选区">
      <h4>
        <button type="button" className="ghost sm" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          历史候选
        </button>
      </h4>
      {open && (
        <>
          <p className="muted small">重新生成分镜或删镜头时，被删镜头的首帧和视频放在这里，可以恢复到当前镜头上。</p>
          {q.isLoading && <p className="muted small">正在读取…</p>}
          {q.error && <p className="error small">{errorText(q.error)}</p>}
          {q.data && rows.length === 0 && <p className="muted small">这一集没有历史候选。</p>}
          {rows.length > 0 && (
            <div className="cands" role="list" aria-label="历史候选">
              {rows.map((a) => (
                <figure key={a.id} role="listitem" className="cand">
                  {a.kind === 'video' ? (
                    <video src={a.url} controls preload="metadata" />
                  ) : (
                    <a href={a.url} target="_blank" rel="noreferrer">
                      <img src={a.url} alt={`历史候选 #${a.id}`} loading="lazy" />
                    </a>
                  )}
                  <figcaption>
                    <span>
                      {SLOT_LABEL[a.kind] ?? a.kind} · 原 {a.shot_code || '?'}
                    </span>
                    {a.was_selected && <span className="badge">当时选定</span>}
                    <span className="muted">{engineLabel(a.engine)}</span>
                    <span className="muted">{fmtTime(a.archived_at)} 移入</span>
                    {snapshotText(a) && <span className="muted small">{snapshotText(a)}</span>}
                    <button
                      type="button"
                      className="sm"
                      disabled={restore.isPending}
                      onClick={() => restore.mutate({ id: a.id, shotId })}
                    >
                      恢复到这个镜头
                    </button>
                  </figcaption>
                </figure>
              ))}
            </div>
          )}
          {restore.error && <p className="error small">{errorText(restore.error)}</p>}
        </>
      )}
    </section>
  );
}
