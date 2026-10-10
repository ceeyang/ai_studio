import { type FormEvent, useContext, useEffect, useRef, useState } from 'react';
import { UNSAFE_DataRouterContext, useBlocker } from 'react-router-dom';
import { useConfirm } from './ConfirmDialog';

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
  /** 数字字段：取值范围（同时写进 input 的 min/max），超出或留空时不能保存 */
  min?: number;
  max?: number;
  /** 下拉框再加一项「其他（手填）」：选它（或当前值不在选项里）时下面出一个输入框手填 */
  customLabel?: string;
  placeholder?: string;
}

/** 字段值不合法时的提示；合法返回 null。目前只校验带 min/max 的数字字段。 */
export function fieldError(f: FieldSpec, v: string): string | null {
  if (f.kind !== 'number' || (f.min === undefined && f.max === undefined)) return null;
  if (v.trim() === '') return `${f.label}不能为空`;
  const n = Number(v);
  if (Number.isNaN(n) || (f.min !== undefined && n < f.min) || (f.max !== undefined && n > f.max)) {
    return `${f.label}要在 ${f.min ?? '-∞'}–${f.max ?? '∞'} 之间`;
  }
  return null;
}

/** 当前编辑状态：changed 只含改过的字段，valid 表示能不能保存 */
export interface FieldsState {
  values: FieldValues;
  changed: FieldValues;
  dirty: boolean;
  valid: boolean;
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
  /** 每次内容变化时把当前状态报给父组件（例如「保存并运行」要拿到没保存的改动） */
  onValuesChange?: (state: FieldsState) => void;
}

const CUSTOM = '__custom__';

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
  onValuesChange,
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
  const errors = fields.map((f) => fieldError(f, values[f.name] ?? '')).filter((x): x is string => !!x);
  const valid = errors.length === 0;
  // 手动切到「其他（手填）」的下拉字段；没切过时按当前值在不在选项里决定
  const [customMode, setCustomMode] = useState<Record<string, boolean>>({});
  const isCustom = (f: FieldSpec) => {
    const v = values[f.name] ?? '';
    return customMode[f.name] ?? (v !== '' && !(f.options ?? []).some((o) => o.value === v));
  };

  const valuesCb = useRef(onValuesChange);
  valuesCb.current = onValuesChange;
  const stateKey = JSON.stringify([values, changed, valid]);
  useEffect(() => {
    valuesCb.current?.({ values, changed, dirty, valid });
    // stateKey 概括了要报的内容
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stateKey]);

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
    if (dirty && valid) onSave(values, changed);
  };

  return (
    <form className="fields" onSubmit={submit}>
      {fields.map((f) => (
        <label key={f.name} className="field">
          <span>{f.label}</span>
          {f.kind === 'textarea' ? (
            <textarea rows={f.rows ?? 4} value={values[f.name] ?? ''} onChange={(e) => set(f.name, e.target.value)} />
          ) : f.kind === 'select' ? (
            <select
              value={f.customLabel && isCustom(f) ? CUSTOM : (values[f.name] ?? '')}
              onChange={(e) => {
                const v = e.target.value;
                if (v === CUSTOM) {
                  setCustomMode((m) => ({ ...m, [f.name]: true }));
                  set(f.name, '');
                } else {
                  if (f.customLabel) setCustomMode((m) => ({ ...m, [f.name]: false }));
                  set(f.name, v);
                }
              }}
            >
              {(f.options ?? []).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
              {f.customLabel && <option value={CUSTOM}>{f.customLabel}</option>}
            </select>
          ) : (
            <input
              type={f.kind === 'number' ? 'number' : 'text'}
              step={f.step}
              min={f.min}
              max={f.max}
              placeholder={f.placeholder}
              value={values[f.name] ?? ''}
              onChange={(e) => set(f.name, e.target.value)}
            />
          )}
        </label>
      )).flatMap((el, i) => {
        const f = fields[i];
        if (!(f.kind === 'select' && f.customLabel && isCustom(f))) return [el];
        // 手填框放在 label 外面：不和下拉框抢同一个标签
        return [
          el,
          <input
            key={`${f.name}-custom`}
            type="text"
            className="field-custom"
            aria-label={`${f.label}（手填）`}
            placeholder={f.placeholder}
            value={values[f.name] ?? ''}
            onChange={(e) => set(f.name, e.target.value)}
          />,
        ];
      })}
      <div className="row">
        <button type="submit" className={dirty && valid ? 'primary' : undefined} disabled={!dirty || !valid || saving}>
          {saving ? '保存中…' : saveLabel}
        </button>
        {dirty && <span className="muted">有未保存的修改</span>}
        {errors.map((m) => (
          <span key={m} className="error">
            {m}
          </span>
        ))}
        {error && <span className="error">{error}</span>}
      </div>
    </form>
  );
}

const LEAVE_PROMPT = '有未保存的修改，确定离开？';

// 正在编辑、还没保存的地方（每个 useUnsavedGuard(true) 一份）。react-router 一个路由只认最后注册的那个
// useBlocker，所以不能每个编辑框各挂一个拦截器：编辑框只往这里登记，全站由一个 LeaveGuard 统一拦。
const dirtySources = new Set<symbol>();

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

/**
 * 有未保存的修改时拦住离开：关页面/刷新走浏览器的 beforeunload；站内换页面（路径变了）由 LeaveGuard 问一句。
 * 只改查询串（开关镜头抽屉、换任务筛选）不算离开。
 */
export function useUnsavedGuard(dirty: boolean): void {
  useBeforeUnloadGuard(dirty);
  const [me] = useState(() => Symbol('unsaved'));
  useEffect(() => {
    if (!dirty) return;
    dirtySources.add(me);
    return () => {
      dirtySources.delete(me);
    };
  }, [dirty, me]);
}

function RouteLeaveGuard() {
  const confirm = useConfirm();
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => dirtySources.size > 0 && currentLocation.pathname !== nextLocation.pathname,
  );
  const asking = useRef(false);
  useEffect(() => {
    if (blocker.state !== 'blocked' || asking.current) return;
    asking.current = true;
    void confirm({
      title: LEAVE_PROMPT,
      body: '离开后，这一页上改过但没保存的内容会丢掉。',
      confirmText: '离开',
      cancelText: '留下',
      danger: true,
    }).then((ok) => {
      asking.current = false;
      if (ok) blocker.proceed();
      else blocker.reset();
    });
  }, [blocker, confirm]);
  return null;
}

/**
 * 全站唯一的站内跳转拦截器，挂在根路由里（ConfirmProvider 里面）。
 * useBlocker 只在数据路由（createBrowserRouter / createMemoryRouter + RouterProvider）下可用，
 * 普通 <MemoryRouter> 下什么也不做——一个组件一生里这件事不会变，条件渲染不会打乱 hooks 顺序。
 */
export function LeaveGuard() {
  const dataRouter = useContext(UNSAFE_DataRouterContext);
  return dataRouter ? <RouteLeaveGuard /> : null;
}
