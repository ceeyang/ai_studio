import { type FormEvent, useEffect, useState } from 'react';

export interface FieldOption {
  value: string;
  label: string;
}

export interface FieldSpec {
  name: string;
  label: string;
  kind?: 'text' | 'textarea' | 'number' | 'select';
  rows?: number;
  options?: FieldOption[];
  step?: string;
}

export type FieldValues = Record<string, string>;

export interface EditableFieldsProps {
  fields: FieldSpec[];
  initial: FieldValues;
  onSave: (values: FieldValues, changed: FieldValues) => void;
  saving?: boolean;
  error?: string | null;
  saveLabel?: string;
}

export function changedValues(values: FieldValues, initial: FieldValues): FieldValues {
  const out: FieldValues = {};
  for (const k of Object.keys(values)) {
    if (values[k] !== initial[k]) out[k] = values[k];
  }
  return out;
}

export function EditableFields({ fields, initial, onSave, saving = false, error = null, saveLabel = '保存' }: EditableFieldsProps) {
  // 用内容做比较，而不是对象身份：WebSocket 触发的重新拉取若内容没变，不会冲掉正在编辑的内容
  const initialKey = JSON.stringify(initial);
  const [values, setValues] = useState<FieldValues>(initial);
  useEffect(() => {
    setValues(JSON.parse(initialKey) as FieldValues);
  }, [initialKey]);

  const base = JSON.parse(initialKey) as FieldValues;
  const changed = changedValues(values, base);
  const dirty = Object.keys(changed).length > 0;
  const set = (name: string, v: string) => setValues((prev) => ({ ...prev, [name]: v }));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (dirty) onSave(values, changed);
  };

  return (
    <form className="fields" onSubmit={submit}>
      {fields.map((f) => (
        <label key={f.name} className="field">
          <span>{f.label}</span>
          {f.kind === 'textarea' ? (
            <textarea rows={f.rows ?? 4} value={values[f.name] ?? ''} onChange={(e) => set(f.name, e.target.value)} />
          ) : f.kind === 'select' ? (
            <select value={values[f.name] ?? ''} onChange={(e) => set(f.name, e.target.value)}>
              {(f.options ?? []).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          ) : (
            <input
              type={f.kind === 'number' ? 'number' : 'text'}
              step={f.step}
              value={values[f.name] ?? ''}
              onChange={(e) => set(f.name, e.target.value)}
            />
          )}
        </label>
      ))}
      <div className="row">
        <button type="submit" disabled={!dirty || saving}>
          {saving ? '保存中…' : saveLabel}
        </button>
        {dirty && <span className="muted">有未保存的修改</span>}
        {error && <span className="error">{error}</span>}
      </div>
    </form>
  );
}
