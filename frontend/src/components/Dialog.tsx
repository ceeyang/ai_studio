import { type ReactNode, useEffect, useId, useRef, useState } from 'react';

interface DialogProps {
  title: string;
  /** 标题下面的一行说明 */
  subtitle?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  /** 返回 true 表示有未保存的内容：Esc、点遮罩、点关闭时先在底部问「放弃修改？」 */
  guard?: () => boolean;
  /** 提交中：Esc、遮罩、关闭按钮都不关 */
  busy?: boolean;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// 打开着的弹窗，后打开的在最后：键盘事件（Esc、Tab）只归最上面那个
const stack: symbol[] = [];

function focusables(box: HTMLElement): HTMLElement[] {
  return Array.from(box.querySelectorAll<HTMLElement>(FOCUSABLE));
}

/**
 * 居中模态框：Esc 或点遮罩关闭；打开时聚焦（优先 data-autofocus，其次第一个输入框，再次第一个可聚焦元素），
 * Tab 只在弹窗里循环，关闭后焦点还给原来的元素。叠了多层时 Esc 只关最上面一层。
 */
export function Dialog({ title, subtitle, onClose, children, wide = false, guard, busy = false }: DialogProps) {
  const titleId = useId();
  const box = useRef<HTMLDivElement>(null);
  const [asking, setAsking] = useState(false);
  const latest = useRef({ onClose, guard, busy, asking });
  latest.current = { onClose, guard, busy, asking };

  /** 用户想关：提交中不理；有未保存内容先问；否则直接关。 */
  const requestClose = useRef(() => {
    const { onClose: close, guard: g, busy: b } = latest.current;
    if (b) return;
    if (g?.()) setAsking(true);
    else close();
  }).current;

  useEffect(() => {
    const me = Symbol('dialog');
    stack.push(me);
    const prev = document.activeElement as HTMLElement | null;
    const el = box.current;
    if (el) {
      const first =
        el.querySelector<HTMLElement>('[data-autofocus]') ??
        el.querySelector<HTMLElement>('input:not([disabled]), textarea:not([disabled]), select:not([disabled])') ??
        focusables(el).find((n) => !n.closest('.modal-head')) ??
        el;
      first.focus();
    }
    const onKey = (e: KeyboardEvent) => {
      if (stack[stack.length - 1] !== me) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        if (latest.current.asking) setAsking(false);
        else requestClose();
        return;
      }
      if (e.key === 'Tab' && box.current) {
        const list = focusables(box.current);
        if (list.length === 0) {
          e.preventDefault();
          return;
        }
        const head = list[0];
        const tail = list[list.length - 1];
        const active = document.activeElement as HTMLElement | null;
        const inside = !!active && box.current.contains(active);
        if (e.shiftKey && (!inside || active === head || active === box.current)) {
          e.preventDefault();
          tail.focus();
        } else if (!e.shiftKey && (!inside || active === tail)) {
          e.preventDefault();
          head.focus();
        }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      const i = stack.indexOf(me);
      if (i >= 0) stack.splice(i, 1);
      prev?.focus?.();
    };
  }, [requestClose]);

  // 问「放弃修改？」时把焦点放到「继续编辑」上：回车默认不丢内容
  const keepRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (asking) keepRef.current?.focus();
  }, [asking]);

  return (
    <div className="modal-scrim" onMouseDown={(e) => e.target === e.currentTarget && requestClose()}>
      <div
        ref={box}
        className={`modal${wide ? ' wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-busy={busy || undefined}
        tabIndex={-1}
      >
        <header className="modal-head">
          <div>
            <h3 id={titleId}>{title}</h3>
            {subtitle && <p className="muted">{subtitle}</p>}
          </div>
          <button type="button" className="ghost icon-btn" aria-label="关闭" disabled={busy} onClick={requestClose}>
            <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
              <path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </header>
        {children}
        {asking && (
          <div className="modal-foot modal-discard" role="alert">
            <span className="warn">放弃修改？</span>
            <button type="button" className="danger" onClick={() => latest.current.onClose()}>
              放弃
            </button>
            <button type="button" ref={keepRef} onClick={() => setAsking(false)}>
              继续编辑
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
