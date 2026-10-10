// 看大图：Esc 关闭、左右键切换、标出「当前」。放进 body 的 portal：角色卡有 container-type，
// 会成为 position: fixed 的包含块，留在卡片里遮罩就铺不满屏幕。portal 在 DOM 里排在确认框后面，
// 所以外面包一层 .lightbox-layer 把遮罩压低一级，从预览里点「设为当前造型」弹的确认框才盖得住它。
import { type ReactNode, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Dialog } from './Dialog';

export interface LightboxItem {
  url: string;
  alt: string;
  /** 是不是当前选定的那张 */
  current?: boolean;
  caption?: ReactNode;
}

export function Lightbox({
  title,
  items,
  index,
  onIndex,
  onClose,
  actions,
}: {
  title: string;
  items: LightboxItem[];
  index: number;
  onIndex: (i: number) => void;
  onClose: () => void;
  /** 当前这张下面的操作按钮 */
  actions?: (i: number) => ReactNode;
}) {
  const body = useRef<HTMLDivElement>(null);
  const n = items.length;
  const i = Math.min(Math.max(index, 0), n - 1);
  const latest = useRef({ i, n, onIndex });
  latest.current = { i, n, onIndex };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      // 只在焦点在这个预览里时切换：上面叠着确认框时不动
      const dlg = body.current?.closest('[role="dialog"]');
      if (!dlg || !dlg.contains(document.activeElement)) return;
      const { i: cur, n: total, onIndex: go } = latest.current;
      if (total < 2) return;
      e.preventDefault();
      go(e.key === 'ArrowLeft' ? (cur - 1 + total) % total : (cur + 1) % total);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  if (n === 0) return null;
  const item = items[i];
  return createPortal(
    <div className="lightbox-layer">
      <Dialog
        title={title}
        subtitle={n > 1 ? `${i + 1} / ${n} · 左右键切换` : undefined}
        onClose={onClose}
        wide
      >
        <div ref={body} className="lightbox">
          <div className="lightbox-stage">
            {n > 1 && (
              <button
                type='button'
                className='ghost lightbox-nav'
                aria-label='上一张'
                onClick={() => onIndex((i - 1 + n) % n)}
              >
                ‹
              </button>
            )}
            <figure className="lightbox-figure">
              <img src={item.url} alt={item.alt} />
              {item.current && <span className="pcard-current">当前</span>}
            </figure>
            {n > 1 && (
              <button
                type='button'
                className='ghost lightbox-nav'
                aria-label='下一张'
                onClick={() => onIndex((i + 1) % n)}
              >
                ›
              </button>
            )}
          </div>
          {(item.caption || actions) && (
            <div className="lightbox-foot">
              {item.caption && (
                <div className="lightbox-caption">{item.caption}</div>
              )}
              {actions?.(i)}
            </div>
          )}
        </div>
      </Dialog>
    </div>,
    document.body,
  );
}
