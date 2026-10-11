// 按镜头推荐视频引擎（只是建议，不自动换）：镜头抽屉「视频引擎」下面一行「建议：即梦网页（难镜头用强模型更稳…）[用这个]」，
// 当前选的引擎不满足这一镜的硬性要求（有台词没声音、要尾帧不支持…）时一行警告；视频阶段工具栏「N 镜建议换引擎」。
// 数据来自 GET /api/episodes/{id}/engine-advice（挂在 ['episode', id] 下，改了镜头跟着刷新）。
import type { EpisodeEngineAdvice, ShotEngineAdvice } from '../../api/types';
import { engineLabel } from '../../format';

export const DIFFICULTY_LABEL: Record<ShotEngineAdvice['difficulty'], string> = {
  easy: '简单',
  medium: '中等',
  hard: '难',
};

/**
 * 抽屉里要显示的建议。draft：抽屉里正在选的引擎（没选就是项目的）。
 * - 和保存的一样：按后端的 suggest_switch 决定要不要建议换；
 * - 改成了别的（还没保存）：只在新选的不满足要求、或现在用不了时建议换。
 * 推荐的第一个就是正在选的那个时不建议。
 */
export function adviceFor(advice: ShotEngineAdvice | undefined, draft: string | undefined) {
  if (!advice) return { suggestion: null, problems: [] as string[], note: null as string | null };
  const check = advice.checks.find((c) => c.engine === draft);
  const problems = check ? check.problems : draft === advice.current_engine ? advice.current_problems : [];
  const note = check && !check.ready ? check.note : null;
  const best = advice.recommended.find((r) => r.engine !== draft) ?? null;
  const top = advice.recommended[0];
  let want: boolean;
  if (top && top.engine === draft) want = false;
  else if (draft === advice.current_engine) want = advice.suggest_switch;
  else want = problems.length > 0 || !!note;
  return { suggestion: want ? best : null, problems, note };
}

export function ShotEngineHint({
  advice,
  draft,
  onUse,
  disabled = false,
}: {
  advice: ShotEngineAdvice | undefined;
  /** 抽屉里正在选的视频引擎（没选就是项目的） */
  draft: string | undefined;
  /** 「用这个」：把草稿里的视频引擎改成它（不保存） */
  onUse(engine: string): void;
  disabled?: boolean;
}) {
  if (!advice) return null;
  const { suggestion, problems, note } = adviceFor(advice, draft);
  const name = draft ? engineLabel({ name: draft, label: advice.checks.find((c) => c.engine === draft)?.label }) : '当前视频引擎';
  const tip = `这一镜难度：${DIFFICULTY_LABEL[advice.difficulty]}${advice.reasons.length ? `（${advice.reasons.join('；')}）` : ''}`;
  if (!suggestion && problems.length === 0 && !note) return null;
  return (
    <div className="engine-advice small" data-testid="engine-advice">
      {problems.length > 0 && (
        <p className="warn" data-testid="engine-advice-warn">
          {name}不适合这一镜：{problems.join('；')}
        </p>
      )}
      {note && problems.length === 0 && <p className="warn">{name}现在用不了：{note}</p>}
      {suggestion && (
        <p className="muted" title={tip}>
          建议：{engineLabel({ name: suggestion.engine, label: suggestion.label })}（{suggestion.why}）
          <button type="button" className="sm soft" disabled={disabled} onClick={() => onUse(suggestion.engine)}>
            用这个
          </button>
        </p>
      )}
    </div>
  );
}

/** 视频阶段工具栏的小标签「N 镜建议换引擎」；没有要换的时不显示 */
export function EngineAdviceChip({ advice }: { advice: EpisodeEngineAdvice | undefined }) {
  if (!advice || advice.switch_count === 0) return null;
  const list = advice.shots
    .filter((s) => s.suggest_switch)
    .map((s) => `${s.code}：${s.switch_reason ?? ''}${s.recommended[0] ? ` → ${engineLabel({ name: s.recommended[0].engine, label: s.recommended[0].label })}` : ''}`)
    .join('\n');
  return (
    <span className="chip" data-testid="engine-advice-summary" title={`点开镜头看建议（只是建议，不会自动换）\n${list}`}>
      {advice.switch_count} 镜建议换引擎
    </span>
  );
}
