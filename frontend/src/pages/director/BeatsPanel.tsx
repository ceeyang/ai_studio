import { type FormEvent, useEffect, useState } from 'react';
import type { Beat, EngineRow, ShotDetail } from '../../api/types';
import { engineLabel } from '../../format';
import { generationUnit, wasteOf, wasteText } from './beats';

/**
 * 固定时长视频引擎的浪费提醒（设计文档 2026-10-12 固定时长引擎 §3.6）：引擎每次固定出 10 秒、这一镜只用 3 秒时，
 * 提示会浪费几秒，并给「并进下一镜做成多拍镜头」（同一段落、合起来不超过一次生成时才能并）。
 * next：同一集里的下一镜（不知道时 undefined，按钮照样给，后端再拦）。
 */
export function WasteNotice({
  shot,
  engine,
  next,
  merging,
  error,
  onMerge,
}: {
  shot: ShotDetail;
  engine: EngineRow | undefined;
  next: ShotDetail | null | undefined;
  merging: boolean;
  error: string | null;
  onMerge(): void;
}) {
  const w = wasteOf(engine?.caps, shot.duration, shot.beats);
  if (!w) return null;
  const unit = generationUnit(engine?.caps) ?? w.unit;
  let blocked: string | null = null;
  if (next === null) blocked = '这是最后一镜，没有下一镜可以并进来';
  else if (next && next.scene_block_id !== shot.scene_block_id) blocked = `${next.code} 在另一个段落（换了场景），不能并`;
  else if (next && shot.duration + next.duration > unit + 0.2)
    blocked = `和 ${next.code} 加起来 ${Math.round((shot.duration + next.duration) * 10) / 10} 秒，超过一次生成的 ${unit} 秒`;
  const name = engine ? engineLabel(engine) : '';
  return (
    <section className="shot-waste" aria-label="固定时长浪费提醒">
      <p className="warn small" data-testid="waste-notice">
        {name ? `${name}：` : ''}
        {wasteText(engine?.caps, shot.duration, w.unit, w.waste)}。
        {blocked ? `（${blocked}）` : ''}
      </p>
      <button
        type="button"
        className="sm soft"
        disabled={merging || !!blocked}
        title={blocked ?? '和下一镜合成一个多拍镜头：一次生成里按时间戳切两拍，用满这次生成'}
        onClick={onMerge}
      >
        {merging ? '合并中…' : '并进下一镜做成多拍镜头'}
      </button>
      {error && <p className="error">{error}</p>}
    </section>
  );
}

const num = (v: string) => (v.trim() === '' ? Number.NaN : Number(v));

/**
 * 多拍镜头的拍：每拍的起止秒数、景别、运镜、动作（英文，直接拼进发给视频引擎的时间戳提示词）。
 * 保存走 PATCH beats（后端按镜头时长校验：2–4 拍、每拍至少 1.5 秒、首尾相接、覆盖整个镜头）；
 * 「改回普通镜头」清空 beats。
 */
export function BeatsEditor({
  shot,
  saving,
  error,
  onSave,
  onDirtyChange,
}: {
  shot: ShotDetail;
  saving: boolean;
  error: string | null;
  onSave(beats: Beat[] | null): void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const key = JSON.stringify(shot.beats ?? []);
  const [rows, setRows] = useState<Beat[]>(shot.beats ?? []);
  useEffect(() => setRows(JSON.parse(key) as Beat[]), [key]);
  const dirty = JSON.stringify(rows) !== key;
  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange]);
  if (!shot.beats?.length) return null;
  const set = (i: number, patch: Partial<Beat>) => setRows((prev) => prev.map((b, j) => (j === i ? { ...b, ...patch } : b)));
  const bad = rows.some((b) => Number.isNaN(b.start) || Number.isNaN(b.end));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (dirty && !bad) onSave(rows);
  };
  return (
    <section aria-label="多拍镜头">
      <h4>多拍镜头（{shot.beats.length} 拍）</h4>
      <p className="muted small">
        一次生成里按时间戳切这几拍（视频提示词逐拍写、每拍重复人物外观，台词放进对应时间段）。拍与拍首尾相接、覆盖整个镜头 {shot.duration} 秒，每拍至少 1.5 秒（建议 2–4 秒）。
      </p>
      <form className="fields beats-editor" onSubmit={submit}>
        {rows.map((b, i) => {
          const n = i + 1;
          return (
            <fieldset key={i} className="beat-row" data-testid={`beat-${n}`}>
              <legend>第 {n} 拍</legend>
              <label className="field">
                <span>开始（秒）</span>
                <input
                  aria-label={`第 ${n} 拍开始`}
                  type="number"
                  step="0.1"
                  value={Number.isNaN(b.start) ? '' : b.start}
                  onChange={(e) => set(i, { start: num(e.target.value) })}
                />
              </label>
              <label className="field">
                <span>结束（秒）</span>
                <input
                  aria-label={`第 ${n} 拍结束`}
                  type="number"
                  step="0.1"
                  value={Number.isNaN(b.end) ? '' : b.end}
                  onChange={(e) => set(i, { end: num(e.target.value) })}
                />
              </label>
              <label className="field">
                <span>景别</span>
                <input aria-label={`第 ${n} 拍景别`} value={b.shot_size} onChange={(e) => set(i, { shot_size: e.target.value })} />
              </label>
              <label className="field">
                <span>运镜</span>
                <input aria-label={`第 ${n} 拍运镜`} value={b.camera_move} onChange={(e) => set(i, { camera_move: e.target.value })} />
              </label>
              <label className="field">
                <span>动作（英文，发给视频引擎）</span>
                <textarea aria-label={`第 ${n} 拍动作`} rows={2} value={b.action} onChange={(e) => set(i, { action: e.target.value })} />
              </label>
            </fieldset>
          );
        })}
        {error && <p className="error">{error}</p>}
        <div className="row">
          <button type="submit" className="sm primary" disabled={!dirty || bad || saving}>
            {saving ? '保存中…' : '保存拍'}
          </button>
          <button type="button" className="sm ghost" disabled={saving} onClick={() => onSave(null)}>
            改回普通镜头
          </button>
        </div>
      </form>
    </section>
  );
}
