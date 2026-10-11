import { type FormEvent, type ReactNode, useContext, useEffect, useRef, useState } from 'react';
import { UNSAFE_DataRouterContext, useBlocker } from 'react-router-dom';
import { useConfirm } from './ConfirmDialog';
import { confirmDiscard } from './confirmDiscard';

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
  /** 收进表单末尾的「高级」折叠里（如英文提示词） */
  advanced?: boolean;
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
  /** 「高级」折叠一开始就展开 */
  advancedOpen?: boolean;
  /** 某个字段后面再放点东西（如视频引擎下面的引擎建议），键是字段名 */
  extras?: Record<string, ReactNode>;
  /** 从外面改草稿（不保存）：seq 变了就把 values 写进当前编辑内容（如引擎建议的「用这个」） */
  assign?: { values: FieldValues; seq: number } | null;
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
  advancedOpen = false,
  extras,
  assign,
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

  // 外面要改草稿：只在 seq 变了时写一次（之后用户还能接着改）
  const assignSeq = assign?.seq;
  const assignRef = useRef(assign);
  assignRef.current = assign;
  useEffect(() => {
    const a = assignRef.current;
    if (assignSeq === undefined || !a) return;
    setValues((prev) => ({ ...prev, ...a.values }));
  }, [assignSeq]);

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

  /** 一个字段：label + 输入控件；下拉选了「其他（手填）」时后面再跟一个手填框 */
  const renderField = (f: FieldSpec) => [
    <label key={f.name} className="field">
      <span>{f.label}</span>
      {f.kind === 'textarea' ? (
        <textarea name={f.name} rows={f.rows ?? 4} value={values[f.name] ?? ''} onChange={(e) => set(f.name, e.target.value)} />
      ) : f.kind === 'select' ? (
        <select
          name={f.name}
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
          name={f.name}
          type={f.kind === 'number' ? 'number' : 'text'}
          step={f.step}
          min={f.min}
          max={f.max}
          placeholder={f.placeholder}
          value={values[f.name] ?? ''}
          onChange={(e) => set(f.name, e.target.value)}
        />
      )}
    </label>,
    // 手填框放在 label 外面：不和下拉框抢同一个标签
    ...(f.kind === 'select' && f.customLabel && isCustom(f)
      ? [
          <input
            key={`${f.name}-custom`}
            type="text"
            className="field-custom"
            aria-label={`${f.label}（手填）`}
            placeholder={f.placeholder}
            value={values[f.name] ?? ''}
            onChange={(e) => set(f.name, e.target.value)}
          />,
        ]
      : []),
    ...(extras?.[f.name] ? [<div key={`${f.name}-extra`} className="field-extra">{extras[f.name]}</div>] : []),
  ];
  const basic = fields.filter((f) => !f.advanced);
  const advanced = fields.filter((f) => f.advanced);

  return (
    <form className="fields" onSubmit={submit}>
      {basic.flatMap(renderField)}
      {advanced.length > 0 && (
        <details className="fields-advanced" open={advancedOpen || undefined}>
          <summary>高级</summary>
          {advanced.flatMap(renderField)}
        </details>
      )}
      <div className="row">
        <button type="submit" className={dirty && valid ? 'soft' : undefined} disabled={!dirty || !valid || saving}>
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

/** 这次站内跳转（同一路径、只改查询串）会不会让这个抽屉卸载、丢掉没保存的修改 */
export type DrawerLoss = (from: URLSearchParams, to: URLSearchParams) => boolean;

// 开着、有没保存修改的抽屉（单集工作台的镜头 / 角色抽屉）。和 dirtySources 分开：开关抽屉、只换查询串
// 平时不拦；只有这次跳转会让有修改的抽屉卸载时（如浏览器后退把 ?stage= 换掉）才拦。
const dirtyDrawers = new Map<symbol, DrawerLoss>();

/** 抽屉的脏标记（登记在全站拦截器里）：dirty 读、set 写；卸载时自动清掉 */
export interface DrawerDirty {
  readonly dirty: boolean;
  set(dirty: boolean): void;
}

/**
 * 登记一个抽屉的「没保存」状态。loses 判断某次只改查询串的跳转会不会让抽屉卸载；会的话 LeaveGuard 先问。
 * 站内自己已经问过的路径（关抽屉、切阶段的检查）确认后先 set(false)，免得再问一次。
 */
export function useDrawerDirty(loses: DrawerLoss): DrawerDirty {
  const lossRef = useRef(loses);
  lossRef.current = loses;
  const [handle] = useState(() => {
    const me = Symbol('drawer');
    const check: DrawerLoss = (from, to) => lossRef.current(from, to);
    return {
      me,
      get dirty() {
        return dirtyDrawers.has(me);
      },
      set(d: boolean) {
        if (d) dirtyDrawers.set(me, check);
        else dirtyDrawers.delete(me);
      },
    };
  });
  useEffect(() => () => handle.set(false), [handle]);
  return handle;
}

type BlockKind = 'leave' | 'drawer';

/** 这次跳转要不要拦、按哪种问：换页面看 dirtySources；同一页只改查询串看有修改的抽屉会不会被卸载 */
function blockKind(from: { pathname: string; search: string }, to: { pathname: string; search: string }): BlockKind | null {
  if (from.pathname !== to.pathname) return dirtySources.size > 0 || dirtyDrawers.size > 0 ? 'leave' : null;
  if (dirtyDrawers.size === 0 || from.search === to.search) return null;
  const a = new URLSearchParams(from.search);
  const b = new URLSearchParams(to.search);
  for (const loses of dirtyDrawers.values()) if (loses(a, b)) return 'drawer';
  return null;
}

function RouteLeaveGuard() {
  const confirm = useConfirm();
  const kind = useRef<BlockKind>('leave');
  const blocker = useBlocker(({ currentLocation, nextLocation }) => {
    const k = blockKind(currentLocation, nextLocation);
    if (k) kind.current = k;
    return k != null;
  });
  const asking = useRef(false);
  useEffect(() => {
    if (blocker.state !== 'blocked' || asking.current) return;
    asking.current = true;
    const ask =
      kind.current === 'drawer'
        ? confirmDiscard(confirm, '抽屉里', '换走')
        : confirm({
            title: LEAVE_PROMPT,
            body: '离开后，这一页上改过但没保存的内容会丢掉。',
            confirmText: '离开',
            cancelText: '留下',
            danger: true,
          });
    void ask.then((ok) => {
      asking.current = false;
      if (ok) {
        // 放弃了：抽屉的脏标记清掉，跳过去之后各阶段不会再问一次
        dirtyDrawers.clear();
        blocker.proceed();
      } else blocker.reset();
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
