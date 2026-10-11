import { type FormEvent, useEffect, useState } from 'react';
import { errorText } from '../../api/client';
import { useAigc, usePutAigc } from '../../api/queries';
import type { Aigc } from '../../api/types';
import { useUnsavedGuard } from '../../components/EditableFields';

export const DEFAULT_AIGC: Aigc = { label_text: '本片由 AI 辅助制作', producer: 'AI Studio' };

/** 项目的 AI 生成内容标识设置：画面角标文字、出品方（写进成片元数据）。整个项目共用，留空保存恢复默认值。 */
export function AigcPanel({ slug }: { slug: string }) {
  const q = useAigc(slug);
  const put = usePutAigc(slug);
  const key = JSON.stringify(q.data ?? DEFAULT_AIGC);
  const [form, setForm] = useState<Aigc>(DEFAULT_AIGC);
  useEffect(() => {
    setForm(JSON.parse(key) as Aigc);
  }, [key]);

  const dirty = JSON.stringify(form) !== key;
  useUnsavedGuard(dirty);

  if (q.isLoading) return <p className="loading">加载 AI 标识设置…</p>;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (dirty) put.mutate(form);
  };

  return (
    <form className="fields aigc-settings" onSubmit={submit}>
      <div className="row">
        <label className="inline-field grow">
          <span>角标文字</span>
          <input
            aria-label="角标文字"
            maxLength={40}
            placeholder={DEFAULT_AIGC.label_text}
            value={form.label_text}
            onChange={(e) => setForm((f) => ({ ...f, label_text: e.target.value }))}
          />
        </label>
        <label className="inline-field grow">
          <span>出品方</span>
          <input
            aria-label="出品方"
            maxLength={100}
            placeholder={DEFAULT_AIGC.producer}
            value={form.producer}
            onChange={(e) => setForm((f) => ({ ...f, producer: e.target.value }))}
          />
        </label>
      </div>
      <div className="row">
        <button type="submit" className={dirty ? 'soft' : undefined} disabled={!dirty || put.isPending}>
          {put.isPending ? '保存中…' : '保存标识设置'}
        </button>
        {dirty && <span className="muted">有未保存的修改</span>}
        {put.error && <span className="error">{errorText(put.error)}</span>}
      </div>
    </form>
  );
}
