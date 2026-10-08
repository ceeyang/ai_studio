import type { ShotDetail } from '../../api/types';
import { selectedOf, slotMark, slotOf } from './slots';

export type GenStage = 'keyframes' | 'videos';

export interface ShotTableProps {
  shots: ShotDetail[];
  selected: ReadonlySet<number>;
  busy?: boolean;
  /** 生成任务（首帧/视频）提交后、还没拿到响应期间为 true：生成按钮禁用，防止重复点击提交两次。 */
  generating?: boolean;
  onToggle(id: number): void;
  onToggleAll(): void;
  onOpen(id: number): void;
  onMove(id: number, delta: -1 | 1): void;
  onSplit(id: number): void;
  onInsertAfter(id: number): void;
  onDelete(id: number): void;
  onGenerate(id: number, stage: GenStage): void;
}

const STALE_TIP = '提示词、引擎或参考图改过，这个结果是按旧输入生成的，需要重新生成';

function MediaCell({ shot, kind }: { shot: ShotDetail; kind: 'first_frame' | 'video' }) {
  const slot = slotOf(shot, kind);
  const mark = slotMark(slot);
  const cand = selectedOf(slot);
  const label = kind === 'first_frame' ? '首帧' : '视频';
  if (mark === 'none' || !cand) return <span className="media-none">未生成</span>;
  return (
    <div className={mark === 'stale' ? 'media-cell is-stale' : 'media-cell'}>
      {kind === 'video' ? (
        <video className="thumb" src={cand.url} muted preload="metadata" aria-label={`${shot.code} ${label}`} />
      ) : (
        <img className="thumb" src={cand.url} alt={`${shot.code} ${label}`} loading="lazy" />
      )}
      {mark === 'ok' && <span className="mark-ok" title="最新：按当前输入生成" aria-hidden="true" />}
      {mark === 'stale' && (
        <span className="badge stale" title={STALE_TIP}>
          已过期
        </span>
      )}
    </div>
  );
}

export function ShotTable(props: ShotTableProps) {
  const { shots, selected, busy = false, generating = false } = props;
  const allChecked = shots.length > 0 && shots.every((s) => selected.has(s.id));
  return (
    <div className="shots-wrap">
      <table className="table shots">
      <thead>
        <tr>
          <th>
            <input type="checkbox" aria-label="全选" checked={allChecked} onChange={props.onToggleAll} />
          </th>
          <th>编号</th>
          <th>时长</th>
          <th>首帧</th>
          <th>视频</th>
          <th>动作 / 对白</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        {shots.map((s, i) => (
          <tr key={s.id} data-testid={`shot-${s.code}`} className={selected.has(s.id) ? 'selected' : undefined}>
            <td>
              <input
                type="checkbox"
                aria-label={`选择 ${s.code}`}
                checked={selected.has(s.id)}
                onChange={() => props.onToggle(s.id)}
              />
            </td>
            <td className="code-cell">
              <button type="button" className="link" aria-label={`打开 ${s.code}`} onClick={() => props.onOpen(s.id)}>
                {s.code}
              </button>
            </td>
            <td className="num">{s.duration.toFixed(1)}s</td>
            <td>
              <MediaCell shot={s} kind="first_frame" />
            </td>
            <td>
              <MediaCell shot={s} kind="video" />
            </td>
            <td className="text-cell">
              <div className="action-text">{s.action || <span className="muted">（没有动作描述）</span>}</div>
              {s.dialogue.length > 0 && (
                <div className="dialogue">
                  {s.dialogue.map((d, k) => (
                    <div key={k} className="dialogue-line">{`${d.speaker}：${d.zh}`}</div>
                  ))}
                </div>
              )}
            </td>
            <td>
              <div className="actions">
                <div className="btn-group">
                  <button
                    type="button"
                    className="sm soft"
                    aria-label={`生成首帧 ${s.code}`}
                    disabled={generating}
                    onClick={() => props.onGenerate(s.id, 'keyframes')}
                  >
                    生成首帧
                  </button>
                  <button
                    type="button"
                    className="sm soft"
                    aria-label={`生成视频 ${s.code}`}
                    disabled={generating}
                    onClick={() => props.onGenerate(s.id, 'videos')}
                  >
                    生成视频
                  </button>
                </div>
                <div className="btn-group">
                  <button type="button" className="sm icon" aria-label={`上移 ${s.code}`} disabled={busy || i === 0} onClick={() => props.onMove(s.id, -1)}>
                    ↑
                  </button>
                  <button
                    type="button"
                    className="sm icon"
                    aria-label={`下移 ${s.code}`}
                    disabled={busy || i === shots.length - 1}
                    onClick={() => props.onMove(s.id, 1)}
                  >
                    ↓
                  </button>
                  <button type="button" className="sm ghost" aria-label={`拆分 ${s.code}`} disabled={busy} onClick={() => props.onSplit(s.id)}>
                    拆分
                  </button>
                  <button type="button" className="sm ghost" aria-label={`在 ${s.code} 后插入`} disabled={busy} onClick={() => props.onInsertAfter(s.id)}>
                    插入
                  </button>
                  <button type="button" className="sm danger" aria-label={`删除 ${s.code}`} disabled={busy} onClick={() => props.onDelete(s.id)}>
                    删除
                  </button>
                </div>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}
