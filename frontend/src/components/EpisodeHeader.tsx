// 单集工作台的页头：面包屑（项目 › 第 N 集）和上一集 / 下一集（停在同一个阶段）。
// 进度和唯一的主按钮「下一步」在下面的阶段条（pages/workspace/StageBar）。
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { enc, errorText } from '../api/client';
import { useCharacters, useEpisodes, useExports, useProject } from '../api/queries';
import type { EpisodeDetail } from '../api/types';
import { type NextStep, type Stage, type StageKey, episodeStages } from '../pages/stages';
import { workspacePath } from '../pages/workspace/urls';

interface EpisodeHeaderProps {
  slug: string;
  ep: EpisodeDetail;
  /** 当前阶段：切集时停在同一个阶段 */
  stage?: StageKey | null;
  /** 标题旁的补充信息 */
  children?: ReactNode;
}

/**
 * 阶段条的「下一步」就在当前阶段时，点它会先发这个事件（CustomEvent，detail.panel 是要触发的那块阶段内容），
 * 对应的阶段内容可以先切到对应页签；然后再触发它里面标了 data-next-action 的动作按钮。
 */
export const NEXT_ACTION_EVENT = 'studio:next-action';

export interface NextActionDetail {
  panel: string;
}

/** 这个下一步事件是不是发给这块阶段内容的 */
export function isNextActionFor(ev: Event, panel: string): boolean {
  return (ev as CustomEvent<NextActionDetail>).detail?.panel === panel;
}

/** 一集的五段进度和下一步（阶段条和各阶段共用缓存）。ep 还没加载时为空。 */
export function useEpisodeProgress(slug: string, ep: EpisodeDetail | undefined): { stages: Stage[]; next: NextStep | null } {
  const characters = useCharacters(slug);
  const exports = useExports(ep?.id ?? 0);
  if (!ep) return { stages: [], next: null };
  return episodeStages(ep, characters.data ?? [], exports.data ?? []);
}

/**
 * 下一步要在这些阶段里「运行」时返回它（阶段内容给对应的动作按钮加 data-next-action），否则 null。
 * 一个阶段内容可能服务几个阶段（导演台暂时同时是首帧和视频阶段）。
 */
export function useNextAction(slug: string, ep: EpisodeDetail | undefined, stages: StageKey[]): NextStep | null {
  const { next } = useEpisodeProgress(slug, ep);
  return ep && next && next.action === 'run' && stages.includes(next.stage) ? next : null;
}

/** 动作按钮要加的属性：是下一步就打上 data-next-action（阶段条的「下一步」会触发它）；全页唯一的主按钮在阶段条，这里不加 primary */
export function nextActionProps(isNext: boolean): { 'data-next-action'?: true } {
  return isNext ? { 'data-next-action': true } : {};
}

export function EpisodeHeader({ slug, ep, stage, children }: EpisodeHeaderProps) {
  const project = useProject(slug);
  const episodes = useEpisodes(slug);
  const projectBase = `/projects/${enc(slug)}`;

  const ordered = [...(episodes.data ?? [])].sort((a, b) => a.number - b.number);
  const at = ordered.findIndex((e) => e.id === ep.id);
  const prev = at > 0 ? ordered[at - 1] : null;
  const after = at >= 0 && at < ordered.length - 1 ? ordered[at + 1] : null;
  const switchLink = (target: typeof prev, label: string, arrow: 'prev' | 'next') => {
    const text = arrow === 'prev' ? `‹ ${label}` : `${label} ›`;
    return target ? (
      <Link
        className="btn sm ghost"
        to={`${workspacePath(slug, target.id)}${stage ? `?stage=${stage}` : ''}`}
        title={`第 ${target.number} 集 ${target.title}`}
      >
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
      <div className="ep-head-main">
        <nav className="crumbs" aria-label="面包屑">
          <Link to={projectBase}>{project.data?.title ?? slug}</Link>
          <span className="crumb-sep" aria-hidden="true">
            ›
          </span>
        </nav>
        <h2>
          第 {ep.number} 集 {ep.title}
        </h2>
        {children}
        <span className="grow" aria-hidden="true" />
        <div className="ep-switch">
          {switchLink(prev, '上一集', 'prev')}
          {switchLink(after, '下一集', 'next')}
        </div>
      </div>
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
