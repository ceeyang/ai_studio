import { type FormEvent, useEffect, useState } from 'react';
import type { DialogueLine, KmReview } from '../../api/types';

export interface DialogueEditorProps {
  dialogue: DialogueLine[];
  onSave(lines: DialogueLine[]): void;
  saving?: boolean;
  error?: string | null;
}

/**
 * 对白编辑：中文台词和高棉语字幕/配音稿。高棉语多半是 AI 译稿（km_review = ai_draft），
 * 发布前要请柬籍母语者校对，校对过的勾"母语已校对"。改中文会让视频过期（视频提示词里有台词）。
 */
export function DialogueEditor({ dialogue, onSave, saving = false, error = null }: DialogueEditorProps) {
  const key = JSON.stringify(dialogue);
  const [lines, setLines] = useState<DialogueLine[]>(dialogue);
  useEffect(() => {
    setLines(JSON.parse(key) as DialogueLine[]);
  }, [key]);

  const base = JSON.parse(key) as DialogueLine[];
  const dirty = JSON.stringify(lines) !== key;
  const zhChanged = lines.some((l, i) => l.zh !== base[i]?.zh);
  const set = (i: number, patch: Partial<DialogueLine>) =>
    setLines((prev) => prev.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (dirty) onSave(lines);
  };

  return (
    <form className="fields dialogue-editor" onSubmit={submit}>
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
