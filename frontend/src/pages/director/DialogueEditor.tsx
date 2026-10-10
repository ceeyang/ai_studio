import { type FormEvent, useEffect, useRef, useState } from 'react';
import type { DialogueLine, KmReview } from '../../api/types';

export interface DialogueEditorProps {
  dialogue: DialogueLine[];
  onSave(lines: DialogueLine[]): void;
  saving?: boolean;
  error?: string | null;
  /** 有没有未保存的修改变化时通知父组件（抽屉关闭前确认用） */
  onDirtyChange?: (dirty: boolean) => void;
}

/**
 * 对白编辑：中文台词和高棉语字幕/配音稿。高棉语多半是 AI 译稿（km_review = ai_draft），
 * 发布前要请柬籍母语者校对，校对过的勾"母语已校对"。改中文会让视频过期（视频提示词里有台词）。
 */
/** 按高棉语时长改写过的句子：原中文和两边的实测时长。 */
export function fitNote(d: DialogueLine): string | null {
  const orig = (d.zh_original ?? '').trim();
  if (!orig) return null;
  const parts = [orig === d.zh.trim() ? '原句未改' : `原中文：${orig}`];
  if (typeof d.km_seconds === 'number' && typeof d.zh_seconds === 'number') {
    const ratio = typeof d.fit_ratio === 'number' ? `（${d.fit_ratio.toFixed(2)}）` : '';
    parts.push(`高棉语 ${d.km_seconds.toFixed(2)}s / 中文 ${d.zh_seconds.toFixed(2)}s${ratio}`);
  }
  return parts.join(' · ');
}

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/**
 * 服务器的对白变了（翻译、改写任务写回）时按句按字段合并：用户没动过的字段（当前值等于旧的服务器值）
 * 换成新值，动过的保留。句数变了没法逐句对上，就整段保留用户的版本。
 */
export function mergeDialogue(current: DialogueLine[], oldBase: DialogueLine[], next: DialogueLine[]): DialogueLine[] {
  if (current.length !== oldBase.length || next.length !== oldBase.length) return current;
  return next.map((n, i) => {
    const cur = current[i] as unknown as Record<string, unknown>;
    const old = oldBase[i] as unknown as Record<string, unknown>;
    const out: Record<string, unknown> = { ...n };
    for (const k of new Set([...Object.keys(cur), ...Object.keys(old)])) {
      if (!same(cur[k], old[k])) {
        if (cur[k] === undefined) delete out[k];
        else out[k] = cur[k];
      }
    }
    return out as unknown as DialogueLine;
  });
}

export function DialogueEditor({ dialogue, onSave, saving = false, error = null, onDirtyChange }: DialogueEditorProps) {
  const key = JSON.stringify(dialogue);
  const [lines, setLines] = useState<DialogueLine[]>(dialogue);
  // 正在改的时候任务把对白更新了：合并后提示，可以一键载入最新
  const [updated, setUpdated] = useState(false);
  const baseRef = useRef(key);
  const linesRef = useRef(lines);
  linesRef.current = lines;
  useEffect(() => {
    const oldKey = baseRef.current;
    if (oldKey === key) return;
    baseRef.current = key;
    const next = JSON.parse(key) as DialogueLine[];
    const cur = linesRef.current;
    if (JSON.stringify(cur) === oldKey) {
      setLines(next);
      setUpdated(false);
      return;
    }
    const merged = mergeDialogue(cur, JSON.parse(oldKey) as DialogueLine[], next);
    setLines(merged);
    setUpdated(JSON.stringify(merged) !== key);
  }, [key]);

  const base = JSON.parse(key) as DialogueLine[];
  const dirty = JSON.stringify(lines) !== key;
  const dirtyCb = useRef(onDirtyChange);
  dirtyCb.current = onDirtyChange;
  const lastDirty = useRef(false);
  useEffect(() => {
    if (dirty === lastDirty.current) return;
    lastDirty.current = dirty;
    dirtyCb.current?.(dirty);
  }, [dirty]);
  const loadLatest = () => {
    setLines(JSON.parse(key) as DialogueLine[]);
    setUpdated(false);
  };
  const zhChanged = lines.some((l, i) => l.zh !== base[i]?.zh);
  const set = (i: number, patch: Partial<DialogueLine>) =>
    setLines((prev) => prev.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (dirty) onSave(lines);
  };

  return (
    <form className="fields dialogue-editor" onSubmit={submit}>
      {updated && dirty && (
        <p className="notice dialogue-updated" role="status">
          <span>对白已被任务更新</span>
          <span aria-hidden="true">·</span>
          <button type="button" className="link" onClick={loadLatest}>
            载入最新
          </button>
        </p>
      )}
      {lines.map((d, i) => {
        const n = i + 1;
        const review: KmReview | null = d.km_review ?? null;
        return (
          <fieldset key={i} className="dialogue-line">
            <legend>
              <span className="t">
                {d.start.toFixed(1)}–{d.end.toFixed(1)}s
              </span>
              {d.speaker || '（未指定）'}
              {(d.km ?? '').trim() !== '' && review !== 'checked' && <span className="badge stale">AI 译稿·待母语校对</span>}
            </legend>
            <label className="field">
              <span>中文</span>
              <input aria-label={`第 ${n} 句中文`} value={d.zh} onChange={(e) => set(i, { zh: e.target.value })} />
            </label>
            {fitNote(d) && (
              <p className="muted fit-note" data-testid={`fit-note-${n}`}>
                {fitNote(d)}
              </p>
            )}
            <label className="field">
              <span>高棉语</span>
              <textarea
                aria-label={`第 ${n} 句高棉语`}
                rows={2}
                lang="km"
                value={d.km ?? ''}
                onChange={(e) => set(i, { km: e.target.value })}
              />
            </label>
            <label className="check">
              <input
                type="checkbox"
                aria-label={`第 ${n} 句母语已校对`}
                checked={review === 'checked'}
                disabled={(d.km ?? '').trim() === ''}
                onChange={(e) => set(i, { km_review: e.target.checked ? 'checked' : 'ai_draft' })}
              />{' '}
              母语已校对
            </label>
          </fieldset>
        );
      })}
      <div className="row">
        <button type="submit" className={dirty ? 'primary' : undefined} disabled={!dirty || saving}>
          {saving ? '保存中…' : '保存对白'}
        </button>
        {dirty && <span className="muted">有未保存的修改</span>}
        {zhChanged && <span className="muted">改了中文台词：视频会标记为过期，需要重出才会念新台词</span>}
        {error && <span className="error">{error}</span>}
      </div>
    </form>
  );
}
