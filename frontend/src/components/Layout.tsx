import { useEffect } from 'react';
import { NavLink, Outlet, matchPath, useLocation } from 'react-router-dom';
import { enc } from '../api/client';
import { useEpisodes, useProject } from '../api/queries';
import { type EpisodeRef, readLastEpisode, rememberEpisode } from '../lastEpisode';
import { TaskBar } from './TaskBar';
import { Toasts } from './Toasts';

function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}


type IconName = 'projects' | 'script' | 'cast' | 'director' | 'export' | 'jobs' | 'settings';

const ICON_PATHS: Record<IconName, string> = {
  projects: 'M3 6.5A1.5 1.5 0 0 1 4.5 5h4l1.5 2h5.5A1.5 1.5 0 0 1 17 8.5v6a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 14.5z',
  script: 'M6 3h6l4 4v9.5a.5.5 0 0 1-.5.5h-9.5a.5.5 0 0 1-.5-.5V3.5A.5.5 0 0 1 6 3zM12 3v4h4M8 10h6M8 13h4',
  cast: 'M10 9.5a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM4.5 16.5c.6-3 2.8-4.5 5.5-4.5s4.9 1.5 5.5 4.5',
  director: 'M3 7h14v9.5a.5.5 0 0 1-.5.5h-13a.5.5 0 0 1-.5-.5zM3 7l1.5-3.5h12L17 7M6.5 3.5 8 7M10.5 3.5 12 7',
  export: 'M10 3v9M6.5 8.5 10 12l3.5-3.5M4 14v2.5h12V14',
  jobs: 'M4 5h12M4 10h12M4 15h7M15 13v4M13 15h4',
  settings: 'M10 7.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6zM10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4',
};

function NavIcon({ name }: { name: IconName }) {
  return (
    <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={ICON_PATHS[name]} />
    </svg>
  );
}

/** 剧集的四个阶段页：导航里指向「当前这一集」 */
const STAGE_ITEMS: { page: string; label: string; icon: IconName }[] = [
  { page: 'script', label: '剧本', icon: 'script' },
  { page: 'cast', label: '角色', icon: 'cast' },
  { page: 'director', label: '导演台', icon: 'director' },
  { page: 'export', label: '导出', icon: 'export' },
];

interface NavTarget {
  ep: EpisodeRef | null;
  /** 没有可指向的剧集时，导航里显示的原因 */
  reason: string;
}

/**
 * 导航指向哪一集：在剧集页面就是这一集；在项目详情页是这个项目最近打开的一集（没打开过就取集数最大的）；
 * 其他页面是上次打开的那一集。
 */
function useNavTarget(pathname: string): NavTarget {
  const m = matchPath('/projects/:slug/episodes/:episodeId/*', pathname);
  const pm = m ? null : matchPath('/projects/:slug', pathname);
  const curSlug = m?.params.slug ? safeDecode(m.params.slug) : null;
  const curId = m?.params.episodeId ? Number(m.params.episodeId) : null;
  const projectSlug = pm?.params.slug ? safeDecode(pm.params.slug) : '';
  const episodes = useEpisodes(projectSlug);

  useEffect(() => {
    if (curSlug && curId) rememberEpisode({ slug: curSlug, episodeId: curId });
  }, [curSlug, curId]);

  if (curSlug && curId) return { ep: { slug: curSlug, episodeId: curId }, reason: '' };
  const last = readLastEpisode();
  if (projectSlug) {
    if (!episodes.data) return { ep: null, reason: episodes.error ? '读不到这个项目的剧集' : '正在读取剧集…' };
    if (episodes.data.length === 0) return { ep: null, reason: '这个项目还没有剧集，先新建一集' };
    const remembered = last?.slug === projectSlug ? episodes.data.find((e) => e.id === last.episodeId) : undefined;
    const latest = remembered ?? episodes.data.reduce((a, b) => (b.number > a.number ? b : a));
    return { ep: { slug: projectSlug, episodeId: latest.id }, reason: '' };
  }
  return last ? { ep: last, reason: '' } : { ep: null, reason: '先在项目中心打开一集' };
}

/** 导航上方的「项目 · 第 N 集」：数据还没到时先显示目录名 */
function NavContext({ ep }: { ep: EpisodeRef }) {
  const project = useProject(ep.slug);
  const episodes = useEpisodes(ep.slug);
  const number = episodes.data?.find((e) => e.id === ep.episodeId)?.number;
  return (
    <div className="nav-context" data-testid="nav-context" title="导航里的剧本、角色、导演台、导出都指向这一集">
      {project.data?.title ?? ep.slug}
      {number !== undefined && ` · 第 ${number} 集`}
    </div>
  );
}

export function Layout() {
  const { pathname } = useLocation();
  const { ep, reason } = useNavTarget(pathname);
  const base = ep ? `/projects/${enc(ep.slug)}/episodes/${ep.episodeId}` : null;
  const hintId = 'nav-stage-hint';
  // 不可点的项仍可聚焦（读屏和键盘用户能听到原因），原因同时写在下面
  const disabled = (label: string, icon: IconName) => (
    <span key={label} className="nav-disabled" role="link" aria-disabled="true" tabIndex={0} aria-describedby={hintId}>
      <NavIcon name={icon} />
      {label}
    </span>
  );
  const link = (to: string, label: string, icon: IconName, num?: string, end = false) => (
    <NavLink key={label} to={to} end={end}>
      <NavIcon name={icon} />
      {label}
      {num && (
        <span className="nav-num" aria-hidden="true">
          {num}
        </span>
      )}
    </NavLink>
  );

  return (
    <div className="app">
      <nav className="nav" aria-label="阶段导航">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
              <path d="M4 2.5v11a.5.5 0 0 0 .77.42l8.5-5.5a.5.5 0 0 0 0-.84l-8.5-5.5A.5.5 0 0 0 4 2.5Z" />
            </svg>
          </span>
          <span>
            AI Studio
            <span className="brand-sub" aria-hidden="true">
              漫剧工作台
            </span>
          </span>
        </div>
        <div className="nav-section" aria-hidden="true">
          制作流程
        </div>
        <div className="pipe">
          {link('/', '项目中心', 'projects', '01', true)}
          {ep && <NavContext ep={ep} />}
          <div className="pipe-stages">
            {STAGE_ITEMS.map((it) => (base ? link(`${base}/${it.page}`, it.label, it.icon) : disabled(it.label, it.icon)))}
          </div>
          {!base && (
            <p className="nav-hint" id={hintId}>
              {reason}
            </p>
          )}
        </div>
        <div className="nav-section" aria-hidden="true">
          系统
        </div>
        {link('/jobs', '任务', 'jobs', '04')}
        {link('/settings', '设置', 'settings', '05')}
        <div className="nav-foot" aria-hidden="true">
          9:16 竖屏 · 本地单机
        </div>
      </nav>
      <div className="main">
        <TaskBar />
        <main className="content">
          <Outlet />
        </main>
      </div>
      <Toasts />
    </div>
  );
}
