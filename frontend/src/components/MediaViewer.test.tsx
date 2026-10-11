import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { type MediaItem, MediaViewerHost, fmtDuration, mediaKind, openMediaViewer, useMediaGroup } from './MediaViewer';

const PICS: MediaItem[] = [
  { url: '/media/a.png', alt: '图 A', title: 'S01 首帧候选 · Gemini 网页 · 2026/10/11 21:42:40', current: true },
  { url: '/media/b.png', alt: '图 B', title: 'S01 首帧候选 · Z-Image 快速' },
  { url: '/media/c.mp4', alt: '视频 C', title: 'S01 视频候选' },
];

/** 一排缩略图 + 「选定」：选定后网格刷新，弹窗里的标记跟着变 */
function Grid({ items = PICS, onPick }: { items?: MediaItem[]; onPick?: (i: number) => void }) {
  const [sel, setSel] = useState(0);
  const list = items.map((it, i) => ({ ...it, current: i === sel }));
  const view = useMediaGroup(list, {
    title: 'S01 首帧候选',
    actions: (_item, i) =>
      i === sel ? null : (
        <button
          type="button"
          className="sm"
          onClick={() => {
            setSel(i);
            onPick?.(i);
          }}
        >
          选定
        </button>
      ),
  });
  return (
    <>
      {list.map((it, i) => (
        <button key={it.url} type="button" onClick={() => view(i)}>
          {`看 ${it.alt}`}
        </button>
      ))}
      <MediaViewerHost />
    </>
  );
}

const dialog = () => screen.getByRole('dialog', { name: 'S01 首帧候选' });
const shownSrc = () => {
  const d = dialog();
  return (d.querySelector('.mv-viewport img, .mv-viewport video') as HTMLImageElement | HTMLVideoElement).getAttribute('src');
};

let pause: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
});
afterEach(() => {
  pause.mockRestore();
  document.body.style.overflow = '';
});

describe('MediaViewer', () => {
  it('点缩略图在当前页打开：模态、标题、序号、信息条，锁住页面滚动，不开新标签', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    render(<Grid />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '看 图 B' }));
    const d = dialog();
    expect(d.getAttribute('aria-modal')).toBe('true');
    expect(within(d).getByText('2 / 3')).toBeTruthy();
    expect(shownSrc()).toBe('/media/b.png');
    expect(within(d).getByText('S01 首帧候选 · Z-Image 快速')).toBeTruthy();
    expect(within(d).queryByText('已选定')).toBeNull();
    expect(document.body.style.overflow).toBe('hidden');
    // 备用：在新标签打开是个普通链接
    expect(within(d).getByRole('link', { name: '在新标签打开' }).getAttribute('href')).toBe('/media/b.png');
    expect(open).not.toHaveBeenCalled();
    open.mockRestore();
  });

  it('左右键、两侧按钮切换，首尾循环；切过去显示已选定', async () => {
    render(<Grid />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '看 图 B' }));
    await user.keyboard('{ArrowLeft}');
    expect(shownSrc()).toBe('/media/a.png');
    expect(within(dialog()).getByText('1 / 3')).toBeTruthy();
    expect(within(dialog()).getByText('已选定')).toBeTruthy();
    await user.keyboard('{ArrowLeft}');
    expect(shownSrc()).toBe('/media/c.mp4');
    expect(dialog().querySelector('video')).toBeTruthy();
    await user.click(within(dialog()).getByRole('button', { name: '下一张' }));
    expect(shownSrc()).toBe('/media/a.png');
    await user.click(within(dialog()).getByRole('button', { name: '上一张' }));
    expect(shownSrc()).toBe('/media/c.mp4');
  });

  it('Esc 关闭，焦点回到点开它的缩略图，页面滚动恢复', async () => {
    render(<Grid />);
    const user = userEvent.setup();
    const thumb = screen.getByRole('button', { name: '看 图 A' });
    await user.click(thumb);
    expect(dialog().contains(document.activeElement)).toBe(true);
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(document.activeElement).toBe(thumb);
    expect(document.body.style.overflow).toBe('');
  });

  it('Tab 只在弹窗里循环（focus trap）', async () => {
    render(<Grid />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '看 图 A' }));
    for (let k = 0; k < 10; k += 1) {
      await user.tab();
      expect(dialog().contains(document.activeElement)).toBe(true);
    }
  });

  it('点图片外面的黑边（遮罩）关闭', async () => {
    render(<Grid />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '看 图 A' }));
    await user.click(screen.getByTestId('media-viewer-stage'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('视频自动播放带控制条、显示时长；切走或关闭时暂停', async () => {
    render(<Grid />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '看 视频 C' }));
    const v = dialog().querySelector('video') as HTMLVideoElement;
    expect(v.controls).toBe(true);
    expect(v.autoplay).toBe(true);
    Object.defineProperty(v, 'duration', { value: 8, configurable: true });
    Object.defineProperty(v, 'videoWidth', { value: 720, configurable: true });
    Object.defineProperty(v, 'videoHeight', { value: 1280, configurable: true });
    fireEvent.loadedMetadata(v);
    expect(within(dialog()).getByText('720×1280 · 时长 8.0 秒')).toBeTruthy();
    expect(pause).not.toHaveBeenCalled();
    await user.keyboard('{ArrowRight}');
    expect(pause).toHaveBeenCalledTimes(1);
    await user.keyboard('{ArrowLeft}');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(pause).toHaveBeenCalledTimes(2);
  });

  it('适应窗口 / 原始大小切换（按钮或点图片），显示图片尺寸', async () => {
    render(<Grid />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '看 图 A' }));
    const stage = screen.getByTestId('media-viewer-stage');
    expect(stage.className).toContain('is-fit');
    const img = stage.querySelector('img') as HTMLImageElement;
    Object.defineProperty(img, 'naturalWidth', { value: 1080, configurable: true });
    Object.defineProperty(img, 'naturalHeight', { value: 1920, configurable: true });
    fireEvent.load(img);
    expect(within(dialog()).getByText('1080×1920')).toBeTruthy();
    await user.click(within(dialog()).getByRole('button', { name: '原始大小' }));
    expect(stage.className).toContain('is-actual');
    expect(within(dialog()).getByRole('button', { name: '适应窗口' }).getAttribute('aria-pressed')).toBe('true');
    await user.click(img);
    expect(stage.className).toContain('is-fit');
    // 换一张回到适应窗口
    await user.click(img);
    await user.keyboard('{ArrowRight}');
    expect(screen.getByTestId('media-viewer-stage').className).toContain('is-fit');
  });

  it('调用方的操作按钮：点「选定」调回调，网格刷新后弹窗里标成已选定', async () => {
    const onPick = vi.fn();
    render(<Grid onPick={onPick} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '看 图 B' }));
    await user.click(within(dialog()).getByRole('button', { name: '选定' }));
    expect(onPick).toHaveBeenCalledWith(1);
    expect(within(dialog()).getByText('已选定')).toBeTruthy();
    expect(within(dialog()).queryByRole('button', { name: '选定' })).toBeNull();
  });

  it('打开它的组件卸载时弹窗跟着关', async () => {
    function Wrap() {
      const [on, setOn] = useState(true);
      return (
        <>
          <button type="button" onClick={() => setOn(false)}>
            收起
          </button>
          {on && <Grid />}
          <MediaViewerHost />
        </>
      );
    }
    render(<Wrap />);
    const user = userEvent.setup();
    await user.click(screen.getAllByRole('button', { name: '看 图 A' })[0]);
    expect(screen.getByRole('dialog')).toBeTruthy();
    act(() => screen.getByRole('button', { name: '收起' }).click());
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('没挂 MediaViewerHost 时自己挂一个', async () => {
    render(<span>空</span>);
    act(() => openMediaViewer([{ url: '/media/x.png', alt: 'X' }], 0, { title: '单张' }));
    expect(await screen.findByRole('dialog', { name: '单张' })).toBeTruthy();
  });

  it('工具函数：按扩展名猜类型、时长格式', () => {
    expect(mediaKind({ url: '/media/a.MP4' })).toBe('video');
    expect(mediaKind({ url: '/media/a.webm?v=2' })).toBe('video');
    expect(mediaKind({ url: '/media/a.png' })).toBe('image');
    expect(mediaKind({ url: '/x', kind: 'video' })).toBe('video');
    expect(fmtDuration(8)).toBe('8.0 秒');
    expect(fmtDuration(65)).toBe('1:05');
    expect(fmtDuration(NaN)).toBe('');
  });
});
