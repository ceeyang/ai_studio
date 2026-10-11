import { errorText } from '../api/client';
import { useConfig, useDoctor, useEngines } from '../api/queries';
import type { DoctorCheck } from '../api/types';
import { ApiServicesPanel } from './settings/ApiServicesPanel';
import { BrowserPanel } from './settings/BrowserPanel';
import { DefaultsPanel } from './settings/DefaultsPanel';
import { EngineTable } from './settings/EngineTable';
import { FoldPanel } from './settings/FoldPanel';

const ICON: Record<DoctorCheck['status'], string> = { ok: '√', warn: '!', fail: '×' };

function summary(rows: DoctorCheck[]): string {
  const fails = rows.filter((r) => r.status === 'fail').length;
  const warns = rows.filter((r) => r.status === 'warn').length;
  if (!fails && !warns) return '全部通过';
  return [fails ? `${fails} 项失败` : '', warns ? `${warns} 项警告` : ''].filter(Boolean).join('，');
}

export function SettingsPage() {
  const doctor = useDoctor();
  const engines = useEngines();
  const config = useConfig();
  const rows = doctor.data ?? [];
  const hasBrowserEngine = (engines.data?.image ?? []).some((e) => e.runtime === 'browser');

  return (
    <div className="page">
      <header className="page-head">
        <span className="eyebrow">05 · 设置</span>
        <h2>设置</h2>
      </header>
      <div className="settings-layout">
        <DefaultsPanel />

        <ApiServicesPanel />

        <FoldPanel
          id="doctor"
          className="area-doctor"
          title="环境检查"
          meta={
            doctor.data && (
              <span data-testid="doctor-summary" className={`doctor-summary ${rows.some((r) => r.status !== 'ok') ? 'bad' : 'ok'}`}>
                {summary(rows)}
              </span>
            )
          }
          actions={
            <button type="button" disabled={doctor.isFetching} onClick={() => void doctor.refetch()}>
              {doctor.isFetching ? '检查中…（最长十几秒）' : '重新检查'}
            </button>
          }
        >
          {doctor.error && <p className="error">{errorText(doctor.error)}</p>}
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
        </FoldPanel>

        {hasBrowserEngine && <BrowserPanel />}

        <FoldPanel id="engines" className="area-engines" title="引擎">
          {engines.error && <p className="error">{errorText(engines.error)}</p>}
          <EngineTable image={engines.data?.image ?? []} video={engines.data?.video ?? []} doctor={rows} />
        </FoldPanel>

        <FoldPanel id="config" className="area-config" title="当前配置（只读）" defaultOpen={false}>
          <p className="muted">修改请编辑仓库根目录的 config.yaml 后重启服务。密钥只在环境变量里，这里看不到。</p>
          {config.error && <p className="error">{errorText(config.error)}</p>}
          <pre data-testid="config">{config.data ? JSON.stringify(config.data, null, 2) : '加载中…'}</pre>
        </FoldPanel>
      </div>
    </div>
  );
}
