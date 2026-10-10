// 剧集各页（剧本 / 角色 / 导演台 / 导出）共用的页头：面包屑、上一集 / 下一集、六段进度和唯一的主按钮「下一步」。
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { enc, errorText } from '../api/client';
import { useCharacters, useEpisodes, useExports, useProject } from '../api/queries';
import type { EpisodeDetail } from '../api/types';
import { episodeStages } from '../pages/stages';
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

export function EpisodeHeader({ slug, ep, page, title, children }: EpisodeHeaderProps) {
  const project = useProject(slug);
  const episodes = useEpisodes(slug);
  const characters = useCharacters(slug);
  const exports = useExports(ep.id);
  const projectBase = `/projects/${enc(slug)}`;
  const base = `${projectBase}/episodes/${ep.id}`;
  const { stages, next } = episodeStages(ep, characters.data ?? [], exports.data ?? [], base);

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
    <header className="page-head ep-head">
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
        {next ? (
          <Link className="btn primary" to={next.to}>
            {next.label}
          </Link>
        ) : (
          <span className="chip ep-all-done">六段都完成了</span>
        )}
      </div>
      <StageStepper stages={stages} here={`${base}/${page}`} />
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
