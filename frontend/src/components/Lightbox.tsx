// 看大图（老接口）：调用方自己管 index 的版本，内部就是全局的看图弹窗 MediaViewer——
// 适应窗口 / 原始大小、左右键切换、Esc 关闭、焦点回到原来的按钮。新代码用 useMediaGroup。
// portal 到 body：角色卡有 container-type，会成为 position: fixed 的包含块，留在卡片里遮罩铺不满屏幕；
// 层级按打开顺序（Dialog 管），从预览里点「设为当前造型」弹的确认框照样盖在上面。
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { MediaViewer } from './MediaViewer';

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
  if (items.length === 0) return null;
  return createPortal(
    <MediaViewer
      title={title}
      items={items.map((it) => ({ url: it.url, alt: it.alt, current: it.current, currentLabel: '当前', caption: it.caption }))}
      index={index}
      onIndex={onIndex}
      onClose={onClose}
      actions={actions ? (_item, i) => actions(i) : undefined}
    />,
    document.body,
  );
}
