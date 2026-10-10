import { type CSSProperties, type KeyboardEvent, type ReactNode, useEffect, useId, useRef, useState } from 'react';

export interface MenuItem {
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  /** 删除这类操作：红字 */
  danger?: boolean;
}

interface MenuProps {
  /** 触发按钮和菜单的无障碍名，如「更多操作 S01」 */
  label: string;
  items: MenuItem[];
  /** 触发按钮上显示的字，默认「⋯」 */
  children?: ReactNode;
  disabled?: boolean;
}

const MENU_GAP = 4;
/** 估一个菜单高度：离窗口底部不够就往上弹 */
const ITEM_H = 34;

/**
 * 「⋯」弹出菜单：aria-haspopup，打开时焦点落到第一个能点的项；上下键循环、Home/End 到头尾，
 * Esc 关闭并把焦点还给按钮，点外面或滚动页面也关。菜单用 fixed 定位，不会被表格裁掉。
 */
export function Menu({ label, items, children = '⋯', disabled = false }: MenuProps) {
  const [pos, setPos] = useState<CSSProperties | null>(null);
  const open = pos !== null;
  const trigger = useRef<HTMLButtonElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const enabled = () => Array.from(box.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? []);

  const show = () => {
    const r = trigger.current?.getBoundingClientRect();
    if (!r) return;
    const right = Math.max(window.innerWidth - r.right, 8);
    const up = r.bottom + MENU_GAP + items.length * ITEM_H + 8 > window.innerHeight && r.top > items.length * ITEM_H;
    setPos(up ? { right, bottom: window.innerHeight - r.top + MENU_GAP } : { right, top: r.bottom + MENU_GAP });
  };
  const close = (refocus: boolean) => {
    setPos(null);
    if (refocus) trigger.current?.focus();
  };

  // 打开后把焦点放到第一个能点的项
  useEffect(() => {
    if (open) enabled()[0]?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (box.current?.contains(t) || trigger.current?.contains(t)) return;
      setPos(null);
    };
    const onScroll = () => setPos(null);
    document.addEventListener('mousedown', onDown);
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onScroll);
    return () => {
      document.removeEventListener('mousedown', onDown);
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onScroll);
    };
  }, [open]);

  const onTriggerKey = (e: KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!open) show();
    }
  };

  const onMenuKey = (e: KeyboardEvent) => {
    const list = enabled();
    const i = list.indexOf(document.activeElement as HTMLButtonElement);
    let next: HTMLButtonElement | undefined;
    if (e.key === 'ArrowDown') next = list[(i + 1) % list.length];
    else if (e.key === 'ArrowUp') next = list[(i - 1 + list.length) % list.length];
    else if (e.key === 'Home') next = list[0];
    else if (e.key === 'End') next = list[list.length - 1];
    else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close(true);
      return;
    } else if (e.key === 'Tab') {
      close(false);
      return;
    } else return;
    e.preventDefault();
    next?.focus();
  };

  return (
    <span className="menu-wrap">
      <button
        ref={trigger}
        type="button"
        className="sm ghost icon menu-trigger"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        disabled={disabled}
        onClick={() => (open ? close(false) : show())}
        onKeyDown={onTriggerKey}
      >
        {children}
      </button>
      {open && (
        <div ref={box} id={menuId} className="menu" role="menu" aria-label={label} style={pos} onKeyDown={onMenuKey}>
          {items.map((it) => (
            <button
              key={it.label}
              type="button"
              role="menuitem"
              tabIndex={-1}
              className={it.danger ? 'menu-item danger' : 'menu-item'}
              disabled={it.disabled}
              onClick={() => {
                close(true);
                it.onSelect();
              }}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}
