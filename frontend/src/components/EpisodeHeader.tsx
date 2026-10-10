// 剧集各页（剧本 / 角色 / 导演台 / 导出）共用的页头：面包屑、上一集 / 下一集、六段进度和唯一的主按钮「下一步」。
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { enc, errorText } from '../api/client';
import { useCharacters, useEpisodes, useExports, useProject } from '../api/queries';
import type { EpisodeDetail } from '../api/types';
import { type NextStep, type Stage, episodeStages } from '../pages/stages';
import { StageStepper } from './StageStepper';

export type EpisodePage = 'script' | 'cast' | 'director' | 'export';

interface EpisodeHeaderProps {
  slug: string;
  ep: EpisodeDetail;
  /** 当前是哪一页：切集时停在同一页，进度条标出对应的段 */
  page: EpisodePage;
  /** 页面名，如「导演台」 */
  title: string;
  /** 标题旁的补充信息（芯片、次要链接） */
  children?: ReactNode;
}

/** 页头的「下一步」就在本页时，点提示会发这个事件（页面可以先切到对应页签），再聚焦标了 data-next-action 的元素 */
export const NEXT_ACTION_EVENT = 'studio:next-action';

const episodeBase = (slug: string, id: number) => `/projects/${enc(slug)}/episodes/${id}`;

/** 一集的六段进度和下一步（角色、成片和页头共用缓存）。ep 还没加载时为空。 */
export function useEpisodeProgress(slug: string, ep: EpisodeDetail | undefined): { stages: Stage[]; next: NextStep | null } {
  const characters = useCharacters(slug);
  const exports = useExports(ep?.id ?? 0);
  if (!ep) return { stages: [], next: null };
  return episodeStages(ep, characters.data ?? [], exports.data ?? [], episodeBase(slug, ep.id));
}

/**
 * 下一步就在这一页时返回它（页面把对应的动作按钮标成 primary 并加 data-next-action），否则 null。
 * 约定：一页只有一个主按钮——下一步在别的页面时是页头的链接，在本页时是这个动作按钮。
 */
export function useNextAction(slug: string, ep: EpisodeDetail | undefined, page: EpisodePage): NextStep | null {
  const { next } = useEpisodeProgress(slug, ep);
  return ep && next && next.to === `${episodeBase(slug, ep.id)}/${page}` ? next : null;
}

/** 本页的下一步按钮要加的属性：是下一步就成为主按钮并打上 data-next-action，不是就什么都不加 */
export function nextActionProps(isNext: boolean): { className?: string; 'data-next-action'?: true } {
  return isNext ? { className: 'primary', 'data-next-action': true } : {};
}

/** 点页头的提示：通知页面（切页签等），下一帧滚到并聚焦本页的下一步按钮 */
function focusNextAction() {
  window.dispatchEvent(new Event(NEXT_ACTION_EVENT));
  window.setTimeout(() => {
    const el = document.querySelector<HTMLElement>('[data-next-action]');
    if (!el) return;
    el.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    el.focus();
  }, 0);
}

export function EpisodeHeader({ slug, ep, page, title, children }: EpisodeHeaderProps) {
  const project = useProject(slug);
  const episodes = useEpisodes(slug);
  const projectBase = `/projects/${enc(slug)}`;
  const base = episodeBase(slug, ep.id);
  const { stages, next } = useEpisodeProgress(slug, ep);
  const here = `${base}/${page}`;

  const ordered = [...(episodes.data ?? [])].sort((a, b) => a.number - b.number);
  const at = ordered.findIndex((e) => e.id === ep.id);
  const prev = at > 0 ? ordered[at - 1] : null;
  const after = at >= 0 && at < ordered.length - 1 ? ordered[at + 1] : null;
  const switchLink = (target: typeof prev, label: string, arrow: 'prev' | 'next') => {
    const text = arrow === 'prev' ? `‹ ${label}` : `${label} ›`;
    return target ? (
      <Link className="btn sm ghost" to={`${projectBase}/episodes/${target.id}/${page}`} title={`第 ${target.number} 集 ${target.title}`}>
        {text}
      </Link>
    ) : (
      <span className="btn sm ghost is-disabled" aria-disabled="true" title={arrow === 'prev' ? '已经是第一集' : '已经是最后一集'}>
        {text}
      </span>
    );
  };

  return (
    <header className="page-head episode-header">
      <nav className="crumbs" aria-label="面包屑">
        <Link to={projectBase}>{project.data?.title ?? slug}</Link>
        <span className="crumb-sep" aria-hidden="true">
          ›
        </span>
        <span aria-current="page">
          第 {ep.number} 集 {ep.title}
        </span>
      </nav>
      <div className="ep-head-main">
        <h2>{title}</h2>
        {children}
        <span className="grow" aria-hidden="true" />
        <div className="ep-switch">
          {switchLink(prev, '上一集', 'prev')}
          {switchLink(after, '下一集', 'next')}
        </div>
        {next && next.to !== here && (
          <Link className="btn primary" to={next.to}>
            {next.label}
          </Link>
        )}
        {next && next.to === here && (
          // 下一步就在本页：不重复放主按钮，点了带用户到本页的那个动作按钮
          <button type="button" className="next-hint" onClick={focusNextAction}>
            {next.label} ↓
          </button>
        )}
        {!next && (
          <span className="chip ep-all-done">六段都完成了</span>
        )}
      </div>
      <StageStepper stages={stages} here={here} />
    </header>
  );
}

/** 剧集加载失败（或不存在）：说原因，给一个回到项目的出口。 */
export function EpisodeLoadError({ slug, error }: { slug: string; error: unknown }) {
  return (
    <div className="page">
      <section className="panel">
        <p className="error">{error ? errorText(error) : '找不到这一集'}</p>
        <Link className="btn" to={`/projects/${enc(slug)}`}>
          回到项目
        </Link>
      </section>
    </div>
  );
}
