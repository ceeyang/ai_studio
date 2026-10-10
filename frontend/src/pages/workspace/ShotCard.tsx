// 镜头卡片（首帧 / 视频阶段）：9:16 缩略 + 一行（镜号、时长、台词数、出场角色）+ 一行动作 + 状态角标；
// 一个随状态变的按钮（普通样式，全页唯一的主按钮在阶段条）和「⋯」菜单；点卡片打开镜头抽屉，左上角勾选多选。
import { useId } from 'react';
import type { Character, ShotDetail } from '../../api/types';
import { Menu } from '../../components/Menu';
import { type CardAction, type GenStage, activeText, cardAction, cellStatus } from '../director/shotStatus';

export interface ShotCardProps {
  shot: ShotDetail;
  stage: GenStage;
  selected: boolean;
  /** 第一个 / 最后一个镜头：不能上移 / 下移 */
  first: boolean;
  last: boolean;
  /** 出场角色（按 shot.cast 找到的） */
  cast: Character[];
  /** 生成请求还没拿到响应：主按钮禁用，防止重复提交 */
  generating?: boolean;
  /** 排序、拆分、删除等进行中：菜单禁用 */
  busy?: boolean;
  onToggle(): void;
  onOpen(): void;
  onAction(action: CardAction): void;
  onMove(delta: -1 | 1): void;
  onSplit(): void;
  onInsertAfter(): void;
  onDelete(): void;
}

function Avatar({ c }: { c: Character }) {
  return c.portrait_url ? (
    <img className="shot-avatar" src={c.portrait_url} alt={c.name} title={c.name} loading="lazy" />
  ) : (
    <span className="shot-avatar" role="img" aria-label={c.name} title={c.name}>
      {c.name.slice(0, 1)}
    </span>
  );
}

export function ShotCard(props: ShotCardProps) {
  const { shot, stage, selected, cast, generating = false, busy = false } = props;
  const st = cellStatus(shot, stage);
  const kf = stage === 'videos' ? cellStatus(shot, 'keyframes') : st;
  const action = cardAction(shot, stage);
  const code = shot.code;
  // 打开按钮：名字「打开 S01」，描述 = 状态角标 + 过期 / 失败原因（读屏能听到，不被按钮名盖掉）
  const uid = useId();
  const ids = { open: `${uid}-open`, code: `${uid}-code`, status: `${uid}-status`, stale: `${uid}-stale`, fail: `${uid}-fail` };
  const describedBy = [ids.status, st.mark === 'stale' && ids.stale, st.failed && ids.fail].filter(Boolean).join(' ');

  let media;
  if (st.cand && stage === 'videos') {
    media = (
      <>
        <video className="shot-media" src={st.cand.url} poster={kf.cand?.url} muted preload="metadata" aria-label={`${code} 视频`} />
      </>
    );
  } else if (st.cand || (stage === 'videos' && kf.cand)) {
    const src = (st.cand ?? kf.cand)!.url;
    media = (
      <>
        <img className={`shot-media${st.cand ? '' : ' is-under'}`} src={src} alt={`${code} 首帧`} loading="lazy" />
        {!st.cand && <span className="shot-empty-tag">还没有视频</span>}
      </>
    );
  } else {
    media = <span className="shot-empty">未生成</span>;
  }

  const cls = ['shot-card'];
  if (selected) cls.push('is-selected');
  if (st.mark === 'stale') cls.push('is-stale');
  if (st.active) cls.push('is-busy');
  if (st.failed) cls.push('is-failed');

  return (
    <article className={cls.join(' ')} data-testid={`shot-${code}`} aria-label={`镜头 ${code}`}>
      <input type="checkbox" className="shot-check" aria-label={`选择 ${code}`} checked={selected} onChange={props.onToggle} />
      <button
        type="button"
        className="shot-open"
        aria-labelledby={`${ids.open} ${ids.code}`}
        aria-describedby={describedBy}
        onClick={props.onOpen}
      >
        <span id={ids.open} className="visually-hidden">
          打开
        </span>
        <span className="shot-thumb">
          {media}
          <span id={ids.status} className="shot-status">
            {st.mark === 'ok' && (
              <span className="mark-ok" title="最新：按当前输入生成">
                <span className="visually-hidden">已选定</span>
              </span>
            )}
            {st.mark === 'stale' && (
              <span className="badge stale" title={st.staleReason}>
                已过期
              </span>
            )}
            {st.failed && <span className="badge fail">失败</span>}
            {st.active && <span className="media-busy">{activeText(st.active)}</span>}
            {st.fresh > 0 && <span className="badge new-cands">{st.fresh} 张新候选</span>}
            {st.cand && stage === 'videos' && (
              <span className="shot-play">
                <span aria-hidden="true">▶</span>
                <span className="visually-hidden">播放</span>
              </span>
            )}
          </span>
        </span>
        <span className="shot-meta">
          {/* 紧凑：镜号、时长、台词数、出场角色头像一行；动作一行截断（全文在悬停提示和抽屉里） */}
          <span className="shot-head">
            <span id={ids.code} className="shot-code">
              {code}
            </span>
            <span className="shot-dur">{shot.duration.toFixed(1)} 秒</span>
            {shot.dialogue.length > 0 && (
              <span className="shot-lines" title={`${shot.dialogue.length} 句台词`}>
                {shot.dialogue.length} 句
                <span className="visually-hidden">台词</span>
              </span>
            )}
            {cast.length > 0 && (
              <span className="shot-cast">
                {cast.map((c) => (
                  <Avatar key={c.id} c={c} />
                ))}
              </span>
            )}
          </span>
          <span className="shot-action" title={shot.action || undefined}>
            {shot.action || <span className="muted">（没有动作描述）</span>}
          </span>
        </span>
      </button>
      {st.mark === 'stale' && (
        <p id={ids.stale} className="shot-stale" title={st.staleReason}>
          已过期：{st.staleReason}
        </p>
      )}
      {st.failed && (
        <p id={ids.fail} className="shot-fail" title={st.failed.error ?? undefined}>
          {st.failed.error || '没有错误信息'}
        </p>
      )}
      <div className="shot-actions">
        <button
          type="button"
          className="sm soft"
          aria-label={`${action.aria} ${code}`}
          disabled={action.kind === 'busy' || generating}
          onClick={() => props.onAction(action)}
        >
          {action.label}
        </button>
        <Menu
          label={`更多操作 ${code}`}
          disabled={busy}
          items={[
            { label: '在后面插入', onSelect: props.onInsertAfter },
            { label: '拆分', onSelect: props.onSplit },
            { label: '上移', disabled: props.first, onSelect: () => props.onMove(-1) },
            { label: '下移', disabled: props.last, onSelect: () => props.onMove(1) },
            { label: '删除', danger: true, onSelect: props.onDelete },
          ]}
        />
      </div>
    </article>
  );
}
