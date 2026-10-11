// 看图 / 看视频弹窗：项目里点任何图片、视频都在当前页打开，不再开新标签。
// 同一组（同一个候选网格、同一排定妆照……）左右切换；图片默认适应窗口，可切到原始大小拖动查看；
// 视频带控制条自动播放，切换或关闭时暂停；底部信息条写标题、尺寸 / 时长、是否已选定，调用方可以放操作按钮。
//
// 用法：组件里 `const view = useMediaGroup(items, { title, actions })`，缩略图 onClick={() => view(i)}；
// 应用根部挂一个 <MediaViewerHost />（没挂时第一次打开会自己挂一个）。
import { type PointerEvent, type ReactNode, useCallback, useEffect, useId, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { type Root, createRoot } from 'react-dom/client';
import { Dialog } from './Dialog';

export type MediaKind = 'image' | 'video';

export interface MediaItem {
  url: string;
  /** 不写时按扩展名猜（.mp4 / .webm / .mov / .m4v 算视频） */
  kind?: MediaKind;
  alt?: string;
  /** 信息条第一行，比如「S01 首帧候选 · Gemini 网页 · 2026/10/11 21:42:40」 */
  title?: string;
  /** 信息条下面的补充说明 */
  caption?: ReactNode;
  /** 是不是当前选定的那张：信息条里标出来 */
  current?: boolean;
  /** 选定标记的字，默认「已选定」 */
  currentLabel?: string;
  /** 已知的尺寸（图片加载完会用实际尺寸） */
  width?: number | null;
  height?: number | null;
}

export type MediaActions = (item: MediaItem, index: number) => ReactNode;

export interface MediaViewerOptions {
  /** 弹窗标题（读屏念的名字），比如「S01 首帧候选」 */
  title: string;
  /** 信息条右边的操作按钮（比如「选定」） */
  actions?: MediaActions;
}

const VIDEO_EXT = /\.(mp4|webm|mov|m4v)(?:[?#]|$)/i;

export function mediaKind(item: Pick<MediaItem, 'url' | 'kind'>): MediaKind {
  return item.kind ?? (VIDEO_EXT.test(item.url) ? 'video' : 'image');
}

/** 时长：「8.0 秒」「1:05」 */
export function fmtDuration(sec: number): string {
  if (!Number.isFinite(sec) || sec <= 0) return '';
  if (sec < 60) return `${sec.toFixed(1)} 秒`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

function pauseVideo(v: HTMLVideoElement | null) {
  if (!v) return;
  try {
    v.pause();
  } catch {
    // jsdom 等不支持播放的环境
  }
}

interface Meta {
  w?: number;
  h?: number;
  duration?: number;
}

/**
 * 受控的看图弹窗：items / index 由调用方管。一般不直接用，用 useMediaGroup；
 * 需要自己管状态时（比如老的 Lightbox）也可以直接渲染。
 */
export function MediaViewer({
  title,
  items,
  index,
  onIndex,
  onClose,
  actions,
}: {
  title: string;
  items: MediaItem[];
  index: number;
  onIndex: (i: number) => void;
  onClose: () => void;
  actions?: MediaActions;
}) {
  const n = items.length;
  const i = Math.min(Math.max(index, 0), Math.max(n - 1, 0));
  const item = items[i] as MediaItem | undefined;
  const url = item?.url ?? '';
  const kind = item ? mediaKind(item) : 'image';
  const [fit, setFit] = useState(true);
  const [meta, setMeta] = useState<Meta>({});
  const viewport = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number; moved: boolean } | null>(null);
  const moved = useRef(false);
  const latest = useRef({ i, n, onIndex });
  latest.current = { i, n, onIndex };

  // 换一张：回到适应窗口，尺寸重新量
  useEffect(() => {
    setFit(true);
    setMeta({});
  }, [url]);

  // 视频：换一条或关掉时先暂停（卸载前停下来，别在后台接着出声）
  useEffect(() => {
    const v = video.current;
    return () => pauseVideo(v);
  }, [url]);

  // 打开时锁住页面滚动
  useEffect(() => {
    const body = document.body;
    const prev = body.style.overflow;
    body.style.overflow = 'hidden';
    return () => {
      body.style.overflow = prev;
    };
  }, []);

  // 预加载左右两张图片，切换时不白屏
  useEffect(() => {
    if (n < 2) return;
    for (const j of new Set([(i + 1) % n, (i - 1 + n) % n])) {
      const it = items[j];
      if (j !== i && it && mediaKind(it) === 'image') {
        const img = new Image();
        img.src = it.url;
      }
    }
  }, [i, n, items]);

  // ← → 切换：只在焦点在这个弹窗里时（上面叠着确认框时不动）；输入框、视频控制条里的方向键留给它们自己
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      const dlg = viewport.current?.closest('[role="dialog"]');
      const active = document.activeElement;
      if (!dlg || !active || !dlg.contains(active)) return;
      if (active.matches('input, textarea, select, video, [contenteditable="true"]')) return;
      const { i: cur, n: total, onIndex: go } = latest.current;
      if (total < 2) return;
      e.preventDefault();
      go(e.key === 'ArrowLeft' ? (cur - 1 + total) % total : (cur + 1) % total);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  if (!item) return null;

  const go = (d: -1 | 1) => onIndex((i + d + n) % n);
  const w = meta.w ?? item.width ?? undefined;
  const h = meta.h ?? item.height ?? undefined;
  const facts = [w && h ? `${w}×${h}` : '', meta.duration ? `时长 ${fmtDuration(meta.duration)}` : ''].filter(Boolean);
  const alt = item.alt ?? item.title ?? title;

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    moved.current = false;
    if (fit || kind !== 'image' || e.button !== 0) return;
    const el = viewport.current;
    if (!el) return;
    drag.current = { x: e.clientX, y: e.clientY, left: el.scrollLeft, top: el.scrollTop, moved: false };
    el.setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    const el = viewport.current;
    if (!d || !el) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) {
      d.moved = true;
      moved.current = true;
    }
    el.scrollLeft = d.left - dx;
    el.scrollTop = d.top - dy;
  };
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    drag.current = null;
    viewport.current?.releasePointerCapture?.(e.pointerId);
  };

  return (
    <Dialog
      title={title}
      subtitle={n > 1 ? `${i + 1} / ${n}` : undefined}
      onClose={onClose}
      className="media-viewer"
      scrimClassName="media-viewer-scrim"
    >
      <div className="mv-stage">
        {n > 1 && (
          <button type="button" className="ghost mv-nav is-prev" aria-label="上一张" title="上一张（←）" onClick={() => go(-1)}>
            ‹
          </button>
        )}
        <div
          ref={viewport}
          className={`mv-viewport ${kind === 'image' && !fit ? 'is-actual' : 'is-fit'}`}
          role="group"
          aria-label={kind === 'video' ? '视频' : fit ? '图片（适应窗口）' : '图片（原始大小，可拖动查看）'}
          tabIndex={0}
          data-autofocus
          data-testid="media-viewer-stage"
          onClick={(e) => {
            // 适应窗口时点图片外面的黑边 = 点遮罩，关掉
            if (e.target === e.currentTarget && fit) onClose();
          }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          {kind === 'video' ? (
            <video
              key={url}
              ref={video}
              src={url}
              controls
              autoPlay
              playsInline
              aria-label={alt}
              onLoadedMetadata={(e) => {
                const v = e.currentTarget;
                setMeta({ w: v.videoWidth || undefined, h: v.videoHeight || undefined, duration: v.duration || undefined });
              }}
            />
          ) : (
            <img
              key={url}
              src={url}
              alt={alt}
              draggable={false}
              title={fit ? '点击看原始大小' : '点击适应窗口'}
              onLoad={(e) => {
                const im = e.currentTarget;
                if (im.naturalWidth && im.naturalHeight) setMeta({ w: im.naturalWidth, h: im.naturalHeight });
              }}
              onClick={() => {
                if (moved.current) return; // 拖动查看，不是点击
                setFit((f) => !f);
              }}
            />
          )}
        </div>
        {n > 1 && (
          <button type="button" className="ghost mv-nav is-next" aria-label="下一张" title="下一张（→）" onClick={() => go(1)}>
            ›
          </button>
        )}
      </div>
      <div className="mv-info">
        <div className="mv-meta">
          <div className="mv-line">
            {item.title && <strong className="mv-title">{item.title}</strong>}
            {item.current && <span className="badge ok">{item.currentLabel ?? '已选定'}</span>}
            {facts.length > 0 && <span className="muted mv-facts">{facts.join(' · ')}</span>}
          </div>
          {item.caption && <div className="mv-caption">{item.caption}</div>}
        </div>
        <div className="mv-actions">
          {kind === 'image' && (
            <button type="button" className="sm ghost" aria-pressed={!fit} onClick={() => setFit((f) => !f)}>
              {fit ? '原始大小' : '适应窗口'}
            </button>
          )}
          {actions?.(item, i)}
          <a className="mv-newtab" href={url} target="_blank" rel="noreferrer">
            在新标签打开
          </a>
        </div>
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 全局状态：同一时间只开一个看图弹窗。打开它的组件重新渲染时把最新的 items / actions 同步进来
// （比如在弹窗里点了「选定」，网格刷新后弹窗里的「已选定」也跟着变）。

interface OpenState extends MediaViewerOptions {
  group: string;
  items: MediaItem[];
  index: number;
}

let state: OpenState | null = null;
const listeners = new Set<() => void>();

function setState(next: OpenState | null) {
  state = next;
  listeners.forEach((f) => f());
}

function subscribe(f: () => void) {
  listeners.add(f);
  return () => {
    listeners.delete(f);
  };
}

const snapshot = () => state;

// 挂着的 MediaViewerHost，先挂的那个负责渲染（挂了多个也只出一个弹窗）
let hosts: symbol[] = [];
let fallbackRoot: Root | null = null;
const primaryHost = () => hosts[0] ?? null;

/** 没挂 MediaViewerHost 时（单独渲染的组件）自己在 body 下挂一个 */
function ensureHost() {
  if (hosts.length > 0 || fallbackRoot || typeof document === 'undefined') return;
  const el = document.createElement('div');
  el.setAttribute('data-media-viewer-host', '');
  document.body.appendChild(el);
  fallbackRoot = createRoot(el);
  fallbackRoot.render(<MediaViewerHost />);
}

/** 关掉当前的看图弹窗（测试清理也用） */
export function closeMediaViewer() {
  if (state) setState(null);
}

/** 直接打开一组（不需要跟着组件刷新时用）；一般用 useMediaGroup */
export function openMediaViewer(items: MediaItem[], index: number, opts: MediaViewerOptions, group = 'adhoc') {
  if (items.length === 0) return;
  ensureHost();
  setState({ group, items, index: Math.min(Math.max(index, 0), items.length - 1), ...opts });
}

/** 挂在应用根部：渲染当前打开的看图弹窗 */
export function MediaViewerHost() {
  const cur = useSyncExternalStore(subscribe, snapshot, snapshot);
  const [me] = useState(() => Symbol('media-viewer-host'));
  const primary = useSyncExternalStore(subscribe, primaryHost, primaryHost);
  useEffect(() => {
    hosts = [...hosts, me];
    listeners.forEach((f) => f());
    return () => {
      hosts = hosts.filter((h) => h !== me);
      listeners.forEach((f) => f());
    };
  }, [me]);
  if (!cur || primary !== me || typeof document === 'undefined') return null;
  return createPortal(
    <MediaViewer
      title={cur.title}
      items={cur.items}
      index={cur.index}
      actions={cur.actions}
      onIndex={(i) => state && setState({ ...state, index: i })}
      onClose={() => setState(null)}
    />,
    document.body,
  );
}

/**
 * 一组可以左右切换的图片 / 视频：返回 open(index)。组件每次渲染都把最新的 items、actions 同步给打开着的弹窗；
 * 组件卸载（比如抽屉关了）时弹窗跟着关。
 */
export function useMediaGroup(items: MediaItem[], opts: MediaViewerOptions): (index: number) => void {
  const group = useId();
  const latest = useRef({ items, opts });
  latest.current = { items, opts };

  useEffect(() => {
    if (state?.group !== group) return;
    if (items.length === 0) {
      setState(null);
      return;
    }
    setState({ ...state, title: opts.title, actions: opts.actions, items, index: Math.min(state.index, items.length - 1) });
  });

  useEffect(
    () => () => {
      if (state?.group === group) setState(null);
    },
    [group],
  );

  return useCallback(
    (index: number) => {
      const { items: list, opts: o } = latest.current;
      openMediaViewer(list, index, o, group);
    },
    [group],
  );
}
