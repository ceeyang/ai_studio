import { errorText } from '../api/client';
import { useConfig, useDoctor, useEngines } from '../api/queries';
import type { DoctorCheck, EngineRow } from '../api/types';

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
  if (kind === 'video') {
    return `${c.has_audio ? '有声' : '无声'} · ${c.fps} fps · 默认 ${c.default_seconds} 秒 / 最长 ${c.max_seconds} 秒 · 参考图上限 ${c.max_refs}`;
  }
  return `参考图上限 ${c.max_refs} · ${c.supports_negative ? '支持' : '不支持'}负面提示词 · 画幅 ${sizes}`;
}

export function SettingsPage() {
  const doctor = useDoctor();
  const engines = useEngines();
  const config = useConfig();
  const rows = doctor.data ?? [];
  const engineRows: [string, 'image' | 'video', EngineRow][] = [
    ...(engines.data?.image ?? []).map((e): [string, 'image', EngineRow] => ['图像', 'image', e]),
    ...(engines.data?.video ?? []).map((e): [string, 'video', EngineRow] => ['视频', 'video', e]),
  ];

  return (
    <div className="page">
      <section className="panel">
        <div className="page-head">
          <h2>环境检查（doctor）</h2>
          <button type="button" disabled={doctor.isFetching} onClick={() => void doctor.refetch()}>
            {doctor.isFetching ? '检查中…（最长十几秒）' : '重新检查'}
          </button>
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

      <section className="panel">
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
                <td>{e.name}</td>
                <td>{label}</td>
                <td>{e.family}</td>
                <td>{capsText(kind, e)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <h2>当前配置（只读）</h2>
        <p className="muted">修改请编辑仓库根目录的 config.yaml 后重启服务。密钥只在环境变量里，这里看不到。</p>
        {config.error && <p className="error">{errorText(config.error)}</p>}
        <pre data-testid="config">{config.data ? JSON.stringify(config.data, null, 2) : '加载中…'}</pre>
      </section>
    </div>
  );
}
