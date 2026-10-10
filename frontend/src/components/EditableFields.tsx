import { type FormEvent, useContext, useEffect, useRef, useState } from 'react';
import { UNSAFE_DataRouterContext, useBlocker } from 'react-router-dom';

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
  /** 有没有未保存的修改变化时通知父组件（用来挂 useUnsavedGuard、弹窗 guard 等） */
  onDirtyChange?: (dirty: boolean) => void;
}

export function changedValues(values: FieldValues, initial: FieldValues): FieldValues {
  const out: FieldValues = {};
  for (const k of Object.keys(values)) {
    if (values[k] !== initial[k]) out[k] = values[k];
  }
  return out;
}

/**
 * 服务器内容变了时按字段合并：用户没动过的字段（当前值等于旧的服务器值）换成新值，动过的保留。
 * 这样 WebSocket 触发的重新拉取不会冲掉正在编辑的内容，别的字段又能跟上服务器。
 */
export function mergeFieldValues(values: FieldValues, oldBase: FieldValues, next: FieldValues): FieldValues {
  const out: FieldValues = { ...next };
  for (const k of Object.keys(values)) {
    if (values[k] !== oldBase[k]) out[k] = values[k];
  }
  return out;
}

export function EditableFields({
  fields,
  initial,
  onSave,
  saving = false,
  error = null,
  saveLabel = '保存',
  onDirtyChange,
}: EditableFieldsProps) {
  // 用内容做比较，而不是对象身份：WebSocket 触发的重新拉取若内容没变，不会冲掉正在编辑的内容
  const initialKey = JSON.stringify(initial);
  const [values, setValues] = useState<FieldValues>(initial);
  const baseRef = useRef<FieldValues>(initial);
  useEffect(() => {
    const next = JSON.parse(initialKey) as FieldValues;
    const oldBase = baseRef.current;
    baseRef.current = next;
    setValues((prev) => mergeFieldValues(prev, oldBase, next));
  }, [initialKey]);

  const base = JSON.parse(initialKey) as FieldValues;
  const changed = changedValues(values, base);
  const dirty = Object.keys(changed).length > 0;

  const dirtyCb = useRef(onDirtyChange);
  dirtyCb.current = onDirtyChange;
  const lastDirty = useRef(false);
  useEffect(() => {
    if (dirty === lastDirty.current) return;
    lastDirty.current = dirty;
    dirtyCb.current?.(dirty);
  }, [dirty]);
  // 带着未保存内容被卸载：告诉父组件已经不脏了，免得它一直拦着离开
  useEffect(
    () => () => {
      if (lastDirty.current) dirtyCb.current?.(false);
    },
    [],
  );

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
        <button type="submit" className={dirty ? "primary" : undefined} disabled={!dirty || saving}>
          {saving ? '保存中…' : saveLabel}
        </button>
        {dirty && <span className="muted">有未保存的修改</span>}
        {error && <span className="error">{error}</span>}
      </div>
    </form>
  );
}

const LEAVE_PROMPT = '有未保存的修改，确定离开？';

function useBeforeUnloadGuard(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [dirty]);
}

function useRouteLeaveGuard(dirty: boolean) {
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    if (window.confirm(LEAVE_PROMPT)) blocker.proceed();
    else blocker.reset();
  }, [blocker]);
}

/**
 * 有未保存的修改时拦住离开：关页面/刷新走浏览器的 beforeunload；站内跳转走 react-router 的 useBlocker 并问一句。
 * useBlocker 只在数据路由（createBrowserRouter + RouterProvider）下可用，BrowserRouter 下调用会抛错，
 * 所以先看有没有数据路由上下文——一个组件一生里这件事不会变，条件调用不会打乱 hooks 顺序。
 */
export function useUnsavedGuard(dirty: boolean): void {
  const dataRouter = useContext(UNSAFE_DataRouterContext);
  useBeforeUnloadGuard(dirty);
  // eslint-disable-next-line react-hooks/rules-of-hooks
  if (dataRouter) useRouteLeaveGuard(dirty);
}
