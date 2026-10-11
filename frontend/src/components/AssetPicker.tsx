// 资产选择器：从本项目 / 本集已有的图片里挑（角色定妆照和三视图、场景图、道具图、镜头的首帧和视频、
// 历史候选、已上传的构图参考）。按分类分页签、可搜索（名字 / 镜头号），多选受上限约束；视频要选取首帧还是尾帧。
import { type ReactNode, useMemo, useState } from 'react';
import { errorText } from '../api/client';
import { useEpisodeAssets } from '../api/queries';
import type { Asset, AssetKind, AssetPick } from '../api/types';
import { engineLabel, fmtTime } from '../format';
import { Dialog } from './Dialog';
import { useMediaGroup } from './MediaViewer';

export const ASSET_TABS: { kind: AssetKind; label: string; empty: string }[] = [
  { kind: 'character', label: '角色', empty: '还没有角色图：先在角色阶段抽定妆照或出三视图。' },
  { kind: 'location', label: '场景图', empty: '还没有场景图：在角色阶段的「设定」里出场景图。' },
  { kind: 'prop', label: '道具', empty: '还没有道具图：在角色阶段的「设定」里给道具出图或上传。' },
  { kind: 'shot', label: '镜头', empty: '这一集的镜头还没有首帧或视频。' },
  { kind: 'archived', label: '历史候选', empty: '这一集没有历史候选。' },
  { kind: 'shot_ref', label: '构图参考', empty: '这一集还没有上传过构图参考。' },
];

export interface AssetPickerProps {
  episodeId: number;
  title: string;
  subtitle?: ReactNode;
  /** 最多选几张；1 时点一下就换成这张 */
  max: number;
  confirmLabel?: string;
  /** 不让选的资产（比如当前镜头自己的首帧） */
  exclude?(a: Asset): boolean;
  /** 选中的资产下面额外显示的内容（比如画幅提示、裁切选项） */
  footer?(picked: Asset[]): ReactNode;
  busy?: boolean;
  error?: unknown;
  onConfirm(picks: AssetPick[], assets: Asset[]): void;
  onClose(): void;
}

function matches(a: Asset, q: string): boolean {
  if (!q) return true;
  const s = q.trim().toLowerCase();
  return a.label.toLowerCase().includes(s) || a.source.toLowerCase().includes(s);
}

function sizeText(a: Asset): string | null {
  return a.width && a.height ? `${a.width}×${a.height}` : null;
}

export function AssetPicker({
  episodeId,
  title,
  subtitle,
  max,
  confirmLabel = '确定',
  exclude,
  footer,
  busy = false,
  error,
  onConfirm,
  onClose,
}: AssetPickerProps) {
  const q = useEpisodeAssets(episodeId);
  const [tab, setTab] = useState<AssetKind | null>(null);
  const [search, setSearch] = useState('');
  const [picked, setPicked] = useState<string[]>([]);
  const [frames, setFrames] = useState<Record<string, 'first' | 'last'>>({});
  const all = useMemo(() => (q.data ?? []).filter((a) => !exclude?.(a)), [q.data, exclude]);
  const count = (k: AssetKind) => all.filter((a) => a.kind === k && matches(a, search)).length;
  // 默认打开第一个有图的分类
  const current: AssetKind = tab ?? ASSET_TABS.find((t) => all.some((a) => a.kind === t.kind))?.kind ?? 'character';
  const shown = all.filter((a) => a.kind === current && matches(a, search));
  const byId = new Map(all.map((a) => [a.id, a]));
  const chosen = picked.map((id) => byId.get(id)).filter((a): a is Asset => !!a);
  const full = max > 1 && picked.length >= max;
  const tabInfo = ASSET_TABS.find((t) => t.kind === current);

  const toggle = (a: Asset) => {
    setPicked((prev) => {
      if (prev.includes(a.id)) return prev.filter((x) => x !== a.id);
      if (max <= 1) return [a.id];
      if (prev.length >= max) return prev;
      return [...prev, a.id];
    });
  };

  // 缩略图点了是选中；右上角的放大镜在当前页看大图 / 播视频，弹窗里也能选
  const view = useMediaGroup(
    shown.map((a) => ({
      url: a.url || a.thumb_url,
      kind: a.media,
      alt: a.label,
      title: [a.label, a.engine ? engineLabel(a.engine) : '', a.created_at ? fmtTime(a.created_at) : ''].filter(Boolean).join(' · '),
      current: !!a.selected,
      currentLabel: '已选',
      width: a.width,
      height: a.height,
    })),
    {
      title: `${tabInfo?.label ?? ''}资产`,
      actions: (_item, i) => {
        const a = shown[i];
        if (!a) return null;
        const on = picked.includes(a.id);
        return (
          <button type="button" className="sm" aria-pressed={on} disabled={!on && full} onClick={() => toggle(a)}>
            {on ? '取消选择' : '选择'}
          </button>
        );
      },
    },
  );

  const confirm = () => {
    if (chosen.length === 0) return;
    onConfirm(
      chosen.map((a) => (a.media === 'video' ? { id: a.id, frame: frames[a.id] ?? 'last' } : { id: a.id })),
      chosen,
    );
  };

  return (
    <Dialog title={title} subtitle={subtitle} onClose={onClose} wide busy={busy}>
      <div className="asset-picker">
        <div className="asset-toolbar">
          <div className="tabs asset-tabs" role="tablist" aria-label="资产分类">
            {ASSET_TABS.map((t) => (
              <button key={t.kind} type="button" role="tab" aria-selected={current === t.kind} onClick={() => setTab(t.kind)}>
                {t.label}
                <span className="muted small">{` ${count(t.kind)}`}</span>
              </button>
            ))}
          </div>
          <input
            type="search"
            className="asset-search"
            placeholder="搜索名字或镜头号"
            aria-label="搜索资产"
            value={search}
            onChange={(ev) => setSearch(ev.target.value)}
          />
        </div>
        {q.isLoading && <p className="muted small">正在读取…</p>}
        {q.error && <p className="error small">{errorText(q.error)}</p>}
        {q.data && shown.length === 0 && (
          <p className="muted small">{search ? '没有符合搜索的图片。' : tabInfo?.empty}</p>
        )}
        {shown.length > 0 && (
          <div className="asset-grid" role="list" aria-label={`${tabInfo?.label ?? ''}资产`}>
            {shown.map((a, i) => {
              const on = picked.includes(a.id);
              const size = sizeText(a);
              return (
                <div key={a.id} role="listitem" className={`asset-card${on ? ' is-picked' : ''}`}>
                  <button
                    type="button"
                    className="asset-pick"
                    aria-pressed={on}
                    aria-label={`${on ? '取消选择' : '选择'} ${a.label}`}
                    disabled={!on && full}
                    title={!on && full ? `最多选 ${max} 张` : undefined}
                    onClick={() => toggle(a)}
                  >
                    {a.media === 'video' ? (
                      <video src={a.thumb_url} preload="metadata" muted aria-hidden="true" />
                    ) : (
                      <img src={a.thumb_url} alt={a.label} loading="lazy" />
                    )}
                    {a.selected && <span className="pcard-tag">已选</span>}
                    {a.media === 'video' && <span className="pcard-current">视频</span>}
                  </button>
                  <button
                    type="button"
                    className="ghost asset-zoom"
                    aria-label={`看大图：${a.label}`}
                    title={a.media === 'video' ? '放大播放' : '看大图'}
                    onClick={() => view(i)}
                  >
                    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
                      <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
                      <path d="M10.4 10.4L14 14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                    </svg>
                  </button>
                  <span className="asset-label" title={a.label}>
                    {a.label}
                  </span>
                  {size && <span className="muted small">{size}</span>}
                  {on && a.media === 'video' && (
                    <span className="asset-frame" role="radiogroup" aria-label={`${a.label} 取哪一帧`}>
                      {(['first', 'last'] as const).map((f) => (
                        <label key={f} className="small">
                          <input
                            type="radio"
                            name={`frame-${a.id}`}
                            checked={(frames[a.id] ?? 'last') === f}
                            onChange={() => setFrames((prev) => ({ ...prev, [a.id]: f }))}
                          />
                          {f === 'first' ? '取首帧' : '取尾帧'}
                        </label>
                      ))}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
        {chosen.length > 0 && footer?.(chosen)}
        {error ? <p className="error small">{errorText(error)}</p> : null}
        <div className="modal-foot">
          <span className="muted" data-testid="asset-picked-count">
            {max > 1 ? `已选 ${picked.length}/${max} 张` : picked.length ? `已选：${chosen[0]?.label ?? ''}` : '点一张图选中'}
            {full ? `（最多 ${max} 张）` : ''}
          </span>
          <button type="button" className="ghost" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button type="button" className="primary" disabled={busy || chosen.length === 0} onClick={confirm}>
            {busy ? '处理中…' : confirmLabel}
          </button>
        </div>
      </div>
    </Dialog>
  );
}

/** 画幅比例（9:16 → 0.5625）；写错时 null */
export function aspectRatio(aspect: string | undefined | null): number | null {
  const m = /^(\d+):(\d+)$/.exec(aspect ?? '');
  if (!m) return null;
  const w = Number(m[1]);
  const h = Number(m[2]);
  return w > 0 && h > 0 ? w / h : null;
}

/** 这张图和项目画幅差得多不多（尺寸不知道时按不差算） */
export function aspectMismatch(a: Pick<Asset, 'width' | 'height'>, aspect: string | undefined | null): boolean {
  const want = aspectRatio(aspect);
  if (!want || !a.width || !a.height) return false;
  return Math.abs(a.width / a.height - want) / want > 0.02;
}
