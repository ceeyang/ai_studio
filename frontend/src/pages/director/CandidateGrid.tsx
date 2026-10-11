import { useState } from 'react';
import { ApiError, detailText, errorText } from '../../api/client';
import type { Slot } from '../../api/types';
import { type MediaItem, useMediaGroup } from '../../components/MediaViewer';
import { engineLabel, fmtTime } from '../../format';
import { SLOT_LABEL } from './slots';
import { qcIssues } from './shotStatus';

export interface CandidateGridProps {
  slot: Slot;
  onSelect(candidateId: number): void;
  selecting?: boolean;
  /** 镜头号（S01）：看大图的标题「S01 首帧候选」 */
  shotCode?: string;
}

/** 从 Content-Disposition 头里取 filename="..."；没有就用调用方给的兜底名。 */
function filenameFrom(header: string | null, fallback: string): string {
  const m = header ? /filename="?([^";]+)"?/i.exec(header) : null;
  return m?.[1] ?? fallback;
}

/**
 * 导出到 ComfyUI 不能用裸的 `<a href download>`：后端缺节点、没配 ComfyUI、连不上 ComfyUI 时
 * 分别回 400/503/502，裸链接点了什么都不会发生。改成按钮：fetch 成功就用 Blob 触发下载，
 * 失败就读 detail 就地显示（比如缺失的节点名）。
 */
async function exportComfyWorkflow(candidateId: number, fallbackName: string): Promise<void> {
  const res = await fetch(`/api/candidates/${candidateId}/comfy-workflow`);
  if (!res.ok) {
    const text = await res.text();
    let detail: unknown = res.statusText;
    if (text) {
      try {
        const data = JSON.parse(text);
        detail = data !== null && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : data;
      } catch {
        detail = text;
      }
    }
    throw new ApiError(res.status, detailText(detail));
  }
  const blob = await res.blob();
  const filename = filenameFrom(res.headers.get('Content-Disposition'), fallbackName);
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function ExportComfyButton({ candidateId, engine }: { candidateId: number; engine: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onClick = async () => {
    setBusy(true);
    setError(null);
    try {
      await exportComfyWorkflow(candidateId, `${engine}_${candidateId}.json`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button type="button" className="ghost sm" disabled={busy} onClick={onClick}>
        导出到 ComfyUI
      </button>
      {error && <span className="error">导出失败：{error}</span>}
    </>
  );
}

export function CandidateGrid({ slot, onSelect, selecting = false, shotCode }: CandidateGridProps) {
  const isVideo = slot.kind === 'video';
  const list = [...slot.candidates].reverse(); // 新的在前
  const groupTitle = `${shotCode ? `${shotCode} ` : ''}${SLOT_LABEL[slot.kind] ?? slot.kind}候选`;
  // 点缩略图在当前页看大图 / 播视频，同一格的候选一组左右切换；弹窗里也能直接「选定」
  const items: MediaItem[] = list.map((c) => ({
    url: c.url,
    kind: isVideo ? 'video' : 'image',
    alt: `候选 #${c.id}`,
    title: `${groupTitle} · ${engineLabel(c.engine)} · ${fmtTime(c.created_at)}`,
    current: c.id === slot.selected_candidate_id,
  }));
  const view = useMediaGroup(items, {
    title: groupTitle,
    actions: (_item, i) => {
      const c = list[i];
      if (!c || c.id === slot.selected_candidate_id) return null;
      return (
        <button type="button" className="sm" disabled={selecting} onClick={() => onSelect(c.id)}>
          选定
        </button>
      );
    },
  });
  if (slot.candidates.length === 0) return <p className="muted">还没有候选</p>;
  return (
    <div className="cands">
      {list.map((c, i) => {
        const selected = c.id === slot.selected_candidate_id;
        const seed = c.params.seed;
        return (
          <figure key={c.id} className={selected ? 'cand selected' : 'cand'} data-testid={`candidate-${c.id}`}>
            {isVideo ? (
              <button type="button" className="media-thumb is-video" aria-label={`播放候选 #${c.id}`} onClick={() => view(i)}>
                <video src={c.url} preload="metadata" muted aria-hidden="true" />
              </button>
            ) : (
              <button type="button" className="media-thumb" aria-label={`看大图：候选 #${c.id}`} onClick={() => view(i)}>
                <img src={c.url} alt={`候选 #${c.id}`} loading="lazy" />
              </button>
            )}
            <figcaption>
              <span>{engineLabel(c.engine)}</span>
              {c.engine === 'asset' && typeof c.params.from === 'string' && <span className="muted">来自 {c.params.from}</span>}
              <span className="muted">{fmtTime(c.created_at)}</span>
              {isVideo && qcIssues(c).length > 0 && (
                <>
                  {qcIssues(c).map((q) => (
                    <span key={q.label} className="badge warn" title={q.tip}>
                      {q.label}
                    </span>
                  ))}
                  {/* 说明直接写出来（悬停提示在触屏上看不到，也容易被忽略） */}
                  <span className="cand-warn" role="note">
                    {c.note || qcIssues(c).map((q) => q.tip).join('；')}
                  </span>
                </>
              )}
              {selected ? (
                <span className="badge ok">已选定</span>
              ) : (
                <button type="button" className="sm" disabled={selecting} onClick={() => onSelect(c.id)}>
                  选定
                </button>
              )}
              {typeof c.params.workflow === 'string' && <ExportComfyButton candidateId={c.id} engine={c.engine} />}
              <details className="cand-adv">
                <summary>高级</summary>
                <p className="muted">
                  #{c.id} · {c.engine}
                  {typeof seed === 'number' ? ` · seed ${seed}` : ''}
                </p>
                <pre>{JSON.stringify(c.params, null, 2)}</pre>
              </details>
            </figcaption>
          </figure>
        );
      })}
    </div>
  );
}
