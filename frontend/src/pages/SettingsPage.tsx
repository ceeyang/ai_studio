import { errorText } from '../api/client';
import { useConfig, useDoctor, useEngines } from '../api/queries';
import type { DoctorCheck, EngineRow } from '../api/types';
import { engineLabel } from '../format';
import { ApiServicesPanel } from './settings/ApiServicesPanel';
import { BrowserPanel } from './settings/BrowserPanel';
import { DefaultsPanel } from './settings/DefaultsPanel';

const ICON: Record<DoctorCheck['status'], string> = { ok: '√', warn: '!', fail: '×' };

function summary(rows: DoctorCheck[]): string {
  const fails = rows.filter((r) => r.status === 'fail').length;
  const warns = rows.filter((r) => r.status === 'warn').length;
  if (!fails && !warns) return '全部通过';
  return [fails ? `${fails} 项失败` : '', warns ? `${warns} 项警告` : ''].filter(Boolean).join('，');
}

function capsText(kind: 'image' | 'video', e: EngineRow): string {
  const c = e.caps;
  const sizes = Object.keys((c.sizes as Record<string, unknown> | undefined) ?? {}).join(' / ');
  const api = e.runtime === 'api' ? `${e.model ? ` · 模型 ${e.model}` : ''} · 没有种子，每次结果不同 · 按次计费 · 不占显卡` : '';
  if (kind === 'video') {
    const v = `${c.has_audio ? '有声' : '无声'} · ${c.fps} fps · 默认 ${c.default_seconds} 秒 / 最长 ${c.max_seconds} 秒 · 参考图上限 ${c.max_refs}`;
    return e.runtime === 'api' ? `首帧图生视频 · ${v} · 画幅 ${sizes}${api}` : v;
  }
  const base = `参考图上限 ${c.max_refs} · ${c.supports_negative ? '支持' : '不支持'}负面提示词 · 画幅 ${sizes}`;
  if (e.runtime === 'api') return `${base}${api}`;
  return e.runtime === 'browser' ? `${base}（写进提示词）· 没有种子，每次结果不同 · 不占显卡` : base;
}

export function SettingsPage() {
  const doctor = useDoctor();
  const engines = useEngines();
  const config = useConfig();
  const rows = doctor.data ?? [];
  const hasBrowserEngine = (engines.data?.image ?? []).some((e) => e.runtime === 'browser');
  const engineRows: [string, 'image' | 'video', EngineRow][] = [
    ...(engines.data?.image ?? []).map((e): [string, 'image', EngineRow] => ['图像', 'image', e]),
    ...(engines.data?.video ?? []).map((e): [string, 'video', EngineRow] => ['视频', 'video', e]),
  ];

  return (
    <div className="page">
      <header className="page-head">
        <span className="eyebrow">05 · 设置</span>
        <h2>设置</h2>
      </header>
      <div className="settings-layout">
        <DefaultsPanel />

        <ApiServicesPanel />

        <section className="panel area-doctor">
          <div className="page-head">
            <h2>环境检查</h2>
            <div className="row">
              <button type="button" disabled={doctor.isFetching} onClick={() => void doctor.refetch()}>
                {doctor.isFetching ? '检查中…（最长十几秒）' : '重新检查'}
              </button>
            </div>
          </div>
          {doctor.error && <p className="error">{errorText(doctor.error)}</p>}
          {doctor.data && (
            <p>
              <span data-testid="doctor-summary" className={`doctor-summary ${rows.some((r) => r.status !== 'ok') ? 'bad' : 'ok'}`}>
                {summary(rows)}
              </span>
            </p>
          )}
          <table className="table doctor-table">
            <thead>
              <tr>
                <th />
                <th>分组</th>
                <th>项目</th>
                <th>详情</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className={`row-${r.status}`}>
                  <td>
                    <span className={`status-icon status-${r.status}`}>{ICON[r.status]}</span>
                  </td>
                  <td>{r.group}</td>
                  <td>{r.name}</td>
                  <td>{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        {hasBrowserEngine && <BrowserPanel />}

        <section className="panel area-engines">
          <h2>引擎</h2>
          {engines.error && <p className="error">{errorText(engines.error)}</p>}
          <table className="table engine-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>类型</th>
                <th>模型族</th>
                <th>能力</th>
              </tr>
            </thead>
            <tbody>
              {engineRows.map(([label, kind, e]) => (
                <tr key={e.name}>
                  <td>
                    {engineLabel(e)}
                    <span className="engine-id">{e.name}</span>
                  </td>
                  <td>{label}</td>
                  <td>
                    {e.family}
                    {e.runtime === 'browser' && <span className="pill">浏览器</span>}
                    {e.runtime === 'runninghub' && <span className="pill">云端</span>}
                    {e.runtime === 'api' && <span className="pill">API</span>}
                  </td>
                  <td>
                    {capsText(kind, e)}
                    {e.ready === false && e.note && <small className="error engine-note">{e.note}</small>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="panel area-config">
          <h2>当前配置（只读）</h2>
          <p className="muted">修改请编辑仓库根目录的 config.yaml 后重启服务。密钥只在环境变量里，这里看不到。</p>
          {config.error && <p className="error">{errorText(config.error)}</p>}
          <pre data-testid="config">{config.data ? JSON.stringify(config.data, null, 2) : '加载中…'}</pre>
        </section>
      </div>
    </div>
  );
}
