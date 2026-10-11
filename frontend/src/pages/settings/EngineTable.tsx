import { useState } from 'react';
import type { DoctorCheck, EngineRow } from '../../api/types';
import { engineLabel } from '../../format';

/** 设置页「引擎」：按类型（图像 / 视频）和来源（本地 / 网页 / API / 云端）分类查看；
 *  现在用不了的（没配 Key、ComfyUI 连不上、缺节点或模型、扩展没连上或没登录）灰掉、写原因、排在后面。 */

type Kind = 'image' | 'video';
type Source = 'comfyui' | 'browser' | 'api' | 'runninghub';

const KIND_LABEL: Record<Kind, string> = { image: '图像', video: '视频' };
const SOURCE_LABEL: Record<Source, string> = {
  comfyui: '本地 ComfyUI',
  browser: '网页（浏览器扩展）',
  api: 'API',
  runninghub: '云端 RunningHub',
};
const SOURCES: Source[] = ['comfyui', 'browser', 'api', 'runninghub'];

export function sourceOf(e: EngineRow): Source {
  return (e.runtime as Source | undefined) ?? 'comfyui';
}

/** 用不了的原因（整句，直接显示）：后端 preflight 的 note 优先，再看环境检查里这个引擎、ComfyUI 服务、浏览器扩展的失败项。 */
export function unavailableReason(e: EngineRow, doctor: DoctorCheck[]): string | null {
  if (e.ready === false) return e.note || '现在不能用';
  const fail = (r: DoctorCheck) => r.status === 'fail';
  const src = sourceOf(e);
  if (src === 'comfyui') {
    const svc = doctor.find((r) => r.group === 'ComfyUI' && r.name === '服务' && fail(r));
    if (svc) return `不能用：ComfyUI 连不上（${svc.detail}）`;
    const own = doctor.filter((r) => r.group === '引擎' && fail(r) && (r.name === e.name || r.name.startsWith(`${e.name} `)));
    if (own.length) return `不能用：${own.map((r) => r.detail).join('；')}`;
  }
  if (src === 'browser') {
    const ext = doctor.find((r) => r.group === '浏览器' && r.name === '扩展连接' && fail(r));
    if (ext) return `不能用：浏览器扩展没连上（${ext.detail}）`;
    // 同一网站的出图、出视频合在一行：「Gemini 登录（gemini_image、gemini_video）」
    const login = doctor.find((r) => r.group === '浏览器' && fail(r) && new RegExp(`[(（、]${e.name}[)）、]`).test(r.name));
    if (login) return `不能用：${login.name} ${login.detail}`;
  }
  return null;
}

/** 视频时长怎么控制（后端 VideoCaps.duration_control / allowed_seconds，网页引擎是实测的）。 */
export function durationText(c: Record<string, unknown>): string {
  const allowed = ((c.allowed_seconds as number[] | undefined) ?? []).join(' / ');
  switch (c.duration_control) {
    case 'fixed':
      return `时长不能指定，总是约 ${c.default_seconds} 秒`;
    case 'prompt':
      return `时长写进提示词${allowed ? `（只认 ${allowed} 秒）` : ''}，不一定照做；不指定时约 ${c.default_seconds} 秒`;
    default:
      return allowed ? `时长可选 ${allowed} 秒` : `默认 ${c.default_seconds} 秒 / 最长 ${c.max_seconds} 秒`;
  }
}

function capsText(kind: Kind, e: EngineRow): string {
  const c = e.caps;
  const sizes = Object.keys((c.sizes as Record<string, unknown> | undefined) ?? {}).join(' / ');
  const api = e.runtime === 'api' ? `${e.model ? ` · 模型 ${e.model}` : ''} · 没有种子，每次结果不同 · 按次计费 · 不占显卡` : '';
  if (kind === 'video') {
    const v = `${c.has_audio ? '有声' : '无声'} · ${c.fps} fps · ${durationText(c)} · 参考图上限 ${c.max_refs}`;
    if (e.runtime === 'browser') return `首帧图生视频 · ${v} · 画幅 ${sizes} · 没有种子，每次结果不同 · 不占显卡`;
    return e.runtime === 'api' ? `首帧图生视频 · ${v} · 画幅 ${sizes}${api}` : v;
  }
  const base = `参考图上限 ${c.max_refs} · ${c.supports_negative ? '支持' : '不支持'}负面提示词 · 画幅 ${sizes}`;
  if (e.runtime === 'api') return `${base}${api}`;
  return e.runtime === 'browser' ? `${base}（写进提示词）· 没有种子，每次结果不同 · 不占显卡` : base;
}

interface Row {
  kind: Kind;
  e: EngineRow;
  reason: string | null;
}

export function EngineTable({ image, video, doctor }: { image: EngineRow[]; video: EngineRow[]; doctor: DoctorCheck[] }) {
  const [kind, setKind] = useState<Kind | 'all'>('all');
  const [onlyReady, setOnlyReady] = useState(false);
  const all: Row[] = [
    ...image.map((e): Row => ({ kind: 'image', e, reason: unavailableReason(e, doctor) })),
    ...video.map((e): Row => ({ kind: 'video', e, reason: unavailableReason(e, doctor) })),
  ];
  const shown = all.filter((r) => (kind === 'all' || r.kind === kind) && (!onlyReady || !r.reason));
  const readyCount = (k: Kind | 'all') => all.filter((r) => (k === 'all' || r.kind === k) && !r.reason).length;
  const total = (k: Kind | 'all') => all.filter((r) => k === 'all' || r.kind === k).length;

  return (
    <>
      <div className="engine-filters">
        <div className="tabs engine-tabs" role="tablist" aria-label="引擎类型">
          {(['all', 'image', 'video'] as const).map((k) => (
            <button
              key={k}
              type="button"
              role="tab"
              aria-selected={kind === k}
              onClick={() => setKind(k)}
            >
              {k === 'all' ? '全部' : KIND_LABEL[k]}
              <span className="muted small">{` ${readyCount(k)}/${total(k)}`}</span>
            </button>
          ))}
        </div>
        <label className="row small">
          <input type="checkbox" checked={onlyReady} onChange={(ev) => setOnlyReady(ev.target.checked)} />
          只看能用的
        </label>
      </div>
      <table className="table engine-table">
        <thead>
          <tr>
            <th>名称</th>
            <th>类型</th>
            <th>模型族</th>
            <th>能力</th>
          </tr>
        </thead>
        {SOURCES.map((src) => {
          // 同一来源里能用的在前，用不了的灰掉排在后面
          const rows = shown
            .filter((r) => sourceOf(r.e) === src)
            .sort((a, b) => Number(!!a.reason) - Number(!!b.reason));
          if (!rows.length) return null;
          const ok = rows.filter((r) => !r.reason).length;
          return (
            <tbody key={src} data-testid={`engine-group-${src}`}>
              <tr className="engine-group">
                <th colSpan={4}>
                  {SOURCE_LABEL[src]}
                  <span className="muted small">{` · ${ok} 个能用 / 共 ${rows.length} 个`}</span>
                </th>
              </tr>
              {rows.map(({ kind: k, e, reason }) => (
                <tr key={e.name} className={reason ? 'engine-off' : undefined} aria-disabled={reason ? true : undefined}>
                  <td>
                    {engineLabel(e)}
                    <span className="engine-id">{e.name}</span>
                  </td>
                  <td>{KIND_LABEL[k]}</td>
                  <td>
                    {e.family}
                    {e.runtime === 'browser' && <span className="pill">浏览器</span>}
                    {e.runtime === 'runninghub' && <span className="pill">云端</span>}
                    {e.runtime === 'api' && <span className="pill">API</span>}
                  </td>
                  <td>
                    {capsText(k, e)}
                    {reason && <small className="engine-note">{reason}</small>}
                  </td>
                </tr>
              ))}
            </tbody>
          );
        })}
        {!shown.length && (
          <tbody>
            <tr>
              <td colSpan={4} className="muted">
                没有符合条件的引擎
              </td>
            </tr>
          </tbody>
        )}
      </table>
    </>
  );
}
