import type { JobBrief, ShotDetail } from '../../api/types';
import { Menu } from '../../components/Menu';
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
  /** 失败格子上的「重试」；不给时当普通生成 */
  onRetry?(id: number, stage: GenStage): void;
}

export const STALE_TIP = '提示词、引擎或参考图改过，这个结果是按旧输入生成的，需要重新生成';

type CellKind = 'first_frame' | 'video';

/** 格子 → 出它的任务类型和生成阶段 */
export const CELL: Record<CellKind, { type: string; stage: GenStage; label: string }> = {
  first_frame: { type: 'image.keyframe', stage: 'keyframes', label: '首帧' },
  video: { type: 'video.shot', stage: 'videos', label: '视频' },
};

/** 这个镜头这一格正在排队/生成的任务 */
export function activeJob(shot: ShotDetail, stage: GenStage): JobBrief | undefined {
  const type = stage === 'keyframes' ? CELL.first_frame.type : CELL.video.type;
  return (shot.active_jobs ?? []).find((j) => j.type === type);
}

/** 这个镜头这一格最近一次失败（之后没出过新的） */
export function failedJob(shot: ShotDetail, stage: GenStage): JobBrief | undefined {
  const type = stage === 'keyframes' ? CELL.first_frame.type : CELL.video.type;
  return (shot.failures ?? []).find((j) => j.type === type);
}

/** 进行中任务的一句话：排队中 / 生成中 42% / 等待输入 */
export function activeText(j: JobBrief): string {
  if (j.status === 'queued') return '排队中';
  if (j.status === 'waiting_input') return '等待输入';
  return typeof j.progress === 'number' ? `生成中 ${Math.round(j.progress * 100)}%` : '生成中';
}

function MediaCell({
  shot,
  kind,
  onOpen,
  onRetry,
}: {
  shot: ShotDetail;
  kind: CellKind;
  onOpen(): void;
  onRetry(): void;
}) {
  const { stage, label } = CELL[kind];
  const slot = slotOf(shot, kind);
  const mark = slotMark(slot);
  const cand = selectedOf(slot);
  const active = activeJob(shot, stage);
  const failed = active ? undefined : failedJob(shot, stage);
  const fresh = slot?.new_candidates ?? 0;

  const status = active && (
<span className="media-busy">{activeText(active)}</span>
  );
  const failBadge = failed && <span className="badge fail">失败</span>;
  const failLine = failed && (
    <div className="media-fail">
      <span className="media-fail-msg" title={failed.error ?? undefined}>
        {failed.error || '没有错误信息'}
      </span>
      <button type="button" className="sm ghost" aria-label={`重试${label} ${shot.code}`} onClick={onRetry}>
        重试
      </button>
    </div>
  );

  if (mark === 'none' || !cand) {
    return (
      <div className="media-cell">
        <span className={`media-none${active ? ' is-busy' : ''}${failed ? ' is-failed' : ''}`}>
          {status ?? '未生成'}
          {failBadge}
        </span>
        {failLine}
      </div>
    );
  }
  return (
    <div className={`media-cell${mark === 'stale' ? ' is-stale' : ''}${active ? ' is-busy' : ''}`}>
      <div className="media-frame">
        <button type="button" className="thumb-btn" aria-label={`查看 ${shot.code} ${label}`} onClick={onOpen}>
          {kind === 'video' ? (
            <video className="thumb" src={cand.url} muted preload="metadata" aria-label={`${shot.code} ${label}`} />
          ) : (
            <img className="thumb" src={cand.url} alt={`${shot.code} ${label}`} loading="lazy" />
          )}
        </button>
        {mark === 'ok' && (
          <span className="mark-ok" title="最新：按当前输入生成">
            <span className="visually-hidden">已选定</span>
          </span>
        )}
        {mark === 'stale' && (
          <span className="badge stale" title={slot?.stale_reason || STALE_TIP}>
            已过期
          </span>
        )}
        {failBadge}
        {status}
        {fresh > 0 && (
          <button type="button" className="badge new-cands" onClick={onOpen}>
            {fresh} 张新候选
          </button>
        )}
      </div>
      {failLine}
    </div>
  );
}

export function ShotTable(props: ShotTableProps) {
  const { shots, selected, busy = false, generating = false } = props;
  const retry = props.onRetry ?? props.onGenerate;
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
          {shots.map((s, i) => {
            const kfBusy = !!activeJob(s, 'keyframes');
            const vidBusy = !!activeJob(s, 'videos');
            return (
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
                  <MediaCell
                    shot={s}
                    kind="first_frame"
                    onOpen={() => props.onOpen(s.id)}
                    onRetry={() => retry(s.id, 'keyframes')}
                  />
                </td>
                <td>
                  <MediaCell
                    shot={s}
                    kind="video"
                    onOpen={() => props.onOpen(s.id)}
                    onRetry={() => retry(s.id, 'videos')}
                  />
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
                    <button
                      type="button"
                      className="sm soft"
                      aria-label={`生成首帧 ${s.code}`}
                      disabled={generating || kfBusy}
                      onClick={() => props.onGenerate(s.id, 'keyframes')}
                    >
                      {kfBusy ? '生成中' : '生成首帧'}
                    </button>
                    <button
                      type="button"
                      className="sm soft"
                      aria-label={`生成视频 ${s.code}`}
                      disabled={generating || vidBusy}
                      onClick={() => props.onGenerate(s.id, 'videos')}
                    >
                      {vidBusy ? '生成中' : '生成视频'}
                    </button>
                    <Menu
                      label={`更多操作 ${s.code}`}
                      disabled={busy}
                      items={[
                        { label: '上移', disabled: i === 0, onSelect: () => props.onMove(s.id, -1) },
                        { label: '下移', disabled: i === shots.length - 1, onSelect: () => props.onMove(s.id, 1) },
                        { label: '拆分', onSelect: () => props.onSplit(s.id) },
                        { label: '在后面插入', onSelect: () => props.onInsertAfter(s.id) },
                        { label: '删除', danger: true, onSelect: () => props.onDelete(s.id) },
                      ]}
                    />
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
