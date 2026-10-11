import { useId, useState, type ReactNode } from 'react';

// 设置页每一块都能展开 / 折叠：点标题切换，状态按块记在本机（localStorage；读写不了时只在这次打开的页面里记）。
const key = (id: string) => `studio:settings-fold:${id}`;

function readOpen(id: string, fallback: boolean): boolean {
  try {
    const v = window.localStorage.getItem(key(id));
    return v === null ? fallback : v === '1';
  } catch {
    return fallback;
  }
}

function writeOpen(id: string, open: boolean): void {
  try {
    window.localStorage.setItem(key(id), open ? '1' : '0');
  } catch {
    // 存不了（隐私模式等）：这次打开的页面里照样折叠
  }
}

interface Props {
  /** 记住展开状态用的键 */
  id: string;
  title: ReactNode;
  className: string;
  /** 折叠时也看得到的一句摘要，比如「已配置 1 / 3」 */
  meta?: ReactNode;
  /** 标题右边的按钮（保存、重新检查），折叠时也在 */
  actions?: ReactNode;
  defaultOpen?: boolean;
  /** 外面要求展开（比如从别处跳过来定位到里面的一项）：不管记住的状态，打开页面时先展开 */
  forceOpen?: boolean;
  ariaLabel?: string;
  testId?: string;
  children: ReactNode;
}

export function FoldPanel({ id, title, className, meta, actions, defaultOpen = true, forceOpen = false, ariaLabel, testId, children }: Props) {
  // forceOpen 只决定第一次打开时展开，之后用户照样能收起
  const [open, setOpen] = useState(() => forceOpen || readOpen(id, defaultOpen));
  const bodyId = useId();
  const toggle = () => {
    setOpen(!open);
    writeOpen(id, !open);
  };
  return (
    <section className={`panel fold-panel ${className}${open ? '' : ' folded'}`} aria-label={ariaLabel} data-testid={testId}>
      <div className="fold-head">
        <h2>
          <button type="button" className="fold-toggle" aria-expanded={open} aria-controls={bodyId} onClick={toggle}>
            <span className="fold-chev" aria-hidden="true" />
            {title}
          </button>
        </h2>
        {meta && <span className="muted small fold-meta">{meta}</span>}
        {actions && <div className="row fold-actions">{actions}</div>}
      </div>
      <div id={bodyId} className="fold-body" hidden={!open}>
        {children}
      </div>
    </section>
  );
}
