import { type FormEvent, useEffect, useState } from 'react';
import { errorText } from '../../api/client';
import { useEndCard, usePutEndCard } from '../../api/queries';
import type { EndCard, EndCardLine } from '../../api/types';
import { useUnsavedGuard } from '../../components/EditableFields';

const EMPTY: EndCard = { seconds: 3, background: '#0b1a33', lines: [] };
const NEW_LINE: EndCardLine = { text: '', font: null, size: 48, bold: false };

/** 项目片尾卡：每行文字、字体、字号（1080×1920 上的像素）、粗细。导出时勾"片尾卡"接在成片末尾。 */
export function EndCardPanel({ slug }: { slug: string }) {
  const q = useEndCard(slug);
  const put = usePutEndCard(slug);
  const key = JSON.stringify(q.data ?? EMPTY);
  const [card, setCard] = useState<EndCard>(EMPTY);
  useEffect(() => {
    setCard(JSON.parse(key) as EndCard);
  }, [key]);

  const dirty = JSON.stringify(card) !== key;
  // 改了没保存：换页面先问（和其他编辑框一样走全局拦截）
  useUnsavedGuard(dirty);

  if (q.isLoading) return <p className="loading">加载片尾卡…</p>;
  const setLine = (i: number, patch: Partial<EndCardLine>) =>
    setCard((c) => ({ ...c, lines: c.lines.map((l, j) => (j === i ? { ...l, ...patch } : l)) }));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (dirty) put.mutate(card);
  };

  return (
    <form className="fields end-card" onSubmit={submit}>
      <div className="row">
        <label>
          时长（秒）{' '}
          <input
            aria-label="片尾卡时长"
            type="number"
            min={1}
            max={10}
            step={0.5}
            value={card.seconds}
            onChange={(e) => setCard((c) => ({ ...c, seconds: Number(e.target.value) }))}
          />
        </label>
        <label>
          底色{' '}
          <input
            aria-label="片尾卡底色"
            type="color"
            value={card.background}
            onChange={(e) => setCard((c) => ({ ...c, background: e.target.value }))}
          />
        </label>
      </div>
      {card.lines.length === 0 && <p className="muted">还没有内容。空行只占高度，可以用来分组。</p>}
      {card.lines.map((l, i) => {
        const n = i + 1;
        return (
          <div key={i} className="row end-card-line">
            <input aria-label={`第 ${n} 行文字`} className="grow" placeholder="（空行）" value={l.text} onChange={(e) => setLine(i, { text: e.target.value })} />
            <input
              aria-label={`第 ${n} 行字体`}
              placeholder="默认字体"
              value={l.font ?? ''}
              onChange={(e) => setLine(i, { font: e.target.value === '' ? null : e.target.value })}
            />
            <input
              aria-label={`第 ${n} 行字号`}
              type="number"
              min={8}
              max={240}
              value={l.size}
              onChange={(e) => setLine(i, { size: Number(e.target.value) })}
            />
            <label className="check">
              <input type="checkbox" aria-label={`第 ${n} 行粗体`} checked={l.bold} onChange={(e) => setLine(i, { bold: e.target.checked })} /> 粗
            </label>
            <button
              type="button"
              className="ghost"
              aria-label={`删除第 ${n} 行`}
              onClick={() => setCard((c) => ({ ...c, lines: c.lines.filter((_, j) => j !== i) }))}
            >
              删除
            </button>
          </div>
        );
      })}
      <div className="row">
        <button type="button" className="ghost" onClick={() => setCard((c) => ({ ...c, lines: [...c.lines, { ...NEW_LINE }] }))}>
          加一行
        </button>
        <button type="submit" className={dirty ? 'soft' : undefined} disabled={!dirty || put.isPending}>
          {put.isPending ? '保存中…' : '保存片尾卡'}
        </button>
        {dirty && <span className="muted">有未保存的修改</span>}
        {put.error && <span className="error">{errorText(put.error)}</span>}
      </div>
    </form>
  );
}
