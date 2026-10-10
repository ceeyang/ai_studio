// 角色阶段的角色卡：当前定妆照大图、名字、音色、状态角标；一个按钮（普通样式，全页唯一的主按钮在阶段条）：
// 缺定妆照时「抽定妆照」（默认 2 张、项目默认引擎），否则「编辑」打开角色抽屉。点卡片也打开抽屉。
import { useId } from 'react';
import { errorText } from '../../api/client';
import { useDrawPortraits } from '../../api/queries';
import type { Character } from '../../api/types';
import { nextActionProps } from '../../components/EpisodeHeader';
import { VOICES } from '../../format';
import { hasPortrait } from '../stages';

/** 卡片上的「抽定妆照」一次抽几张 */
export const CARD_DRAW_COUNT = 2;

export function voiceLabel(voice: string | null): string {
  if (!voice) return '未设音色';
  return VOICES.find((v) => v.id === voice)?.label ?? voice;
}

/** 三视图切出的视角有几格已经有图（共 7 格：全身 4 + 头像 3） */
function turnaroundText(c: Character): string | null {
  const t = c.turnaround;
  if (!t) return null;
  const total = t.cells.length || 7;
  const done = t.cells.filter((x) => x.url).length;
  return `三视图 ${done}/${total}`;
}

export function CastCard({
  c,
  isNext = false,
  onOpen,
  slug,
}: {
  c: Character;
  slug: string;
  /** 阶段条的下一步是「抽定妆照」且轮到这个角色：「抽定妆照」打上 data-next-action */
  isNext?: boolean;
  onOpen(): void;
}) {
  const draw = useDrawPortraits(slug);
  const uid = useId();
  const ids = { open: `${uid}-open`, name: `${uid}-name`, status: `${uid}-status` };
  const portrait = hasPortrait(c);
  const jobs = c.active_jobs ?? [];
  const drawing = jobs.some((j) => j.type === 'image.portrait');
  const busy = jobs.length > 0;
  const failed = !drawing && !!c.last_failure;
  const turn = turnaroundText(c);
  const noPrompt = !c.visual_prompt.trim();

  const cls = ['cast-card'];
  if (!portrait) cls.push('is-missing');
  if (failed) cls.push('is-failed');

  return (
    <article className={cls.join(' ')} data-testid={`character-${c.id}`} aria-label={`角色 ${c.name}`}>
      <button
        type="button"
        className="cast-open"
        aria-labelledby={`${ids.open} ${ids.name}`}
        aria-describedby={ids.status}
        onClick={onOpen}
      >
        <span id={ids.open} className="visually-hidden">
          打开
        </span>
        <span className="cast-thumb">
          {c.portrait_url ? (
            <img src={c.portrait_url} alt={`${c.name} 定妆照`} loading="lazy" />
          ) : (
            <span className="cast-empty">还没有定妆照</span>
          )}
        </span>
        <span className="cast-meta">
          <span id={ids.name} className="cast-name">
            {c.name}
          </span>
          <span className={`cast-voice${c.voice ? '' : ' muted'}`}>{voiceLabel(c.voice)}</span>
        </span>
      </button>
      <div id={ids.status} className="cast-chips">
        {portrait ? <span className="badge ok">已定妆</span> : <span className="badge warn">缺定妆照</span>}
        {turn && <span className="badge">{turn}</span>}
        {busy && <span className="badge busy">生成中</span>}
        {failed && (
          <span className="badge fail" title={c.last_failure?.error ?? undefined}>
            抽卡失败
          </span>
        )}
      </div>
      {!portrait && noPrompt && <p className="muted small">先写外貌提示词或用 AI 推荐造型（点卡片打开）</p>}
      {draw.error && <p className="error small">{errorText(draw.error)}</p>}
      <div className="cast-actions">
        {portrait ? (
          <button type="button" className="sm soft" aria-label={`编辑 ${c.name}`} onClick={onOpen}>
            编辑
          </button>
        ) : (
          <button
            type="button"
            className="sm soft"
            {...nextActionProps(isNext)}
            disabled={draw.isPending || drawing || noPrompt}
            title={noPrompt ? '先写外貌提示词或用 AI 推荐造型' : `用外貌提示词抽 ${CARD_DRAW_COUNT} 张定妆照（项目默认图像引擎）`}
            onClick={() => draw.mutate({ id: c.id, count: CARD_DRAW_COUNT })}
          >
            抽定妆照
          </button>
        )}
      </div>
    </article>
  );
}
