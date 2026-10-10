import { Link } from 'react-router-dom';
import { STAGE_STATE_TEXT, type Stage } from '../pages/stages';

const STATE_MARK: Record<Stage['state'], string> = { done: '✓', active: '', todo: '', stale: '!' };

/**
 * 一集的六段进度条：剧本 → 角色 → 分镜 → 首帧 → 视频 → 导出。每段可点，去做这一段的页面；
 * here 是当前页面的路径，指向它的段标为当前（aria-current="step"）。
 */
export function StageStepper({ stages, here }: { stages: Stage[]; here: string }) {
  return (
    <ol className="stepper" aria-label="制作进度">
      {stages.map((s, i) => {
        const current = s.to === here;
        return (
          <li key={s.key} className={`step is-${s.state}${current ? ' is-here' : ''}`}>
            <Link to={s.to} aria-current={current ? 'step' : undefined}>
              <span className="step-dot" aria-hidden="true">
                {STATE_MARK[s.state] || i + 1}
              </span>
              <span className="step-text">
                <span className="step-label">{s.label}</span>
                <span className="step-detail">{s.detail}</span>
              </span>
              <span className="visually-hidden">（{STAGE_STATE_TEXT[s.state]}）</span>
            </Link>
          </li>
        );
      })}
    </ol>
  );
}
