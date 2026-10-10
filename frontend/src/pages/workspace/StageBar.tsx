// 单集工作台的阶段条：五段（剧本 / 角色 / 首帧 / 视频 / 成片）每段可点切换主区，右边是全页唯一的主按钮「下一步」。
import { NEXT_ACTION_EVENT } from '../../components/EpisodeHeader';
import { type NextStep, STAGE_STATE_TEXT, type Stage, type StageKey } from '../stages';

const STATE_MARK: Record<Stage['state'], string> = { done: '✓', active: '', todo: '', stale: '!' };

interface StageBarProps {
  stages: Stage[];
  current: StageKey;
  next: NextStep | null;
  onSelect: (stage: StageKey) => void;
  /** 下一步就在当前阶段、要「运行」时：触发当前阶段里的动作按钮 */
  onRun: () => void;
}

/**
 * 触发某块阶段内容里标了 data-next-action 的动作按钮：先发 NEXT_ACTION_EVENT（内容可以先切到对应页签），
 * 下一帧滚过去、聚焦；按钮可用就点它，禁用就只聚焦（旁边的提示会说为什么）。
 */
export function triggerNextAction(panel: string) {
  window.dispatchEvent(new Event(NEXT_ACTION_EVENT));
  window.setTimeout(() => {
    const el = document.querySelector<HTMLElement>(`[data-stage-panel="${panel}"] [data-next-action]`);
    if (!el) return;
    el.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    el.focus();
    if (!(el as HTMLButtonElement).disabled) el.click();
  }, 0);
}

export function StageBar({ stages, current, next, onSelect, onRun }: StageBarProps) {
  const runHere = !!next && next.stage === current && next.action === 'run';
  const target = next ? stages.find((s) => s.key === next.stage) : undefined;
  return (
    <div className="stage-bar">
      <ol className="stepper" aria-label="制作阶段">
        {stages.map((s, i) => {
          const here = s.key === current;
          return (
            <li key={s.key} className={`step is-${s.state}${here ? ' is-here' : ''}`}>
              <button type="button" aria-current={here ? 'step' : undefined} onClick={() => onSelect(s.key)}>
                <span className="step-dot" aria-hidden="true">
                  {STATE_MARK[s.state] || i + 1}
                </span>
                <span className="step-text">
                  <span className="step-label">{s.label}</span>
                  <span className="step-detail">{s.detail}</span>
                </span>
                <span className="visually-hidden">（{STAGE_STATE_TEXT[s.state]}）</span>
              </button>
            </li>
          );
        })}
      </ol>
      {next ? (
        <button
          type="button"
          className="primary stage-next"
          title={runHere || !target ? undefined : `切到「${target.label}」阶段`}
          onClick={() => (runHere ? onRun() : onSelect(next.stage))}
        >
          下一步：{next.label}
        </button>
      ) : (
        <span className="chip ep-all-done">五段都完成了</span>
      )}
    </div>
  );
}
