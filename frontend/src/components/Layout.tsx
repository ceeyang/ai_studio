import { useEffect } from 'react';
import { NavLink, Outlet, matchPath, useLocation } from 'react-router-dom';
import { enc } from '../api/client';
import { type EpisodeRef, readLastEpisode, rememberEpisode } from '../lastEpisode';
import { TaskBar } from './TaskBar';

function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}


type IconName = 'projects' | 'script' | 'director' | 'jobs' | 'settings';

const ICON_PATHS: Record<IconName, string> = {
  projects: 'M3 6.5A1.5 1.5 0 0 1 4.5 5h4l1.5 2h5.5A1.5 1.5 0 0 1 17 8.5v6a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 14.5z',
  script: 'M6 3h6l4 4v9.5a.5.5 0 0 1-.5.5h-9.5a.5.5 0 0 1-.5-.5V3.5A.5.5 0 0 1 6 3zM12 3v4h4M8 10h6M8 13h4',
  director: 'M3 7h14v9.5a.5.5 0 0 1-.5.5h-13a.5.5 0 0 1-.5-.5zM3 7l1.5-3.5h12L17 7M6.5 3.5 8 7M10.5 3.5 12 7',
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

export function Layout() {
  const { pathname } = useLocation();
  const m = matchPath('/projects/:slug/episodes/:episodeId/*', pathname);
  const curSlug = m?.params.slug ? safeDecode(m.params.slug) : null;
  const curId = m?.params.episodeId ? Number(m.params.episodeId) : null;

  useEffect(() => {
    if (curSlug && curId) rememberEpisode({ slug: curSlug, episodeId: curId });
  }, [curSlug, curId]);

  const ep: EpisodeRef | null = curSlug && curId ? { slug: curSlug, episodeId: curId } : readLastEpisode();
  const base = ep ? `/projects/${enc(ep.slug)}/episodes/${ep.episodeId}` : null;
  const disabled = (label: string, icon: IconName, num: string) => (
    <span className="nav-disabled" title="先在项目中心打开一集">
      <NavIcon name={icon} />
      {label}
      <span className="nav-num" aria-hidden="true">
        {num}
      </span>
    </span>
  );
  const link = (to: string, label: string, icon: IconName, num: string, end = false) => (
    <NavLink to={to} end={end}>
      <NavIcon name={icon} />
      {label}
      <span className="nav-num" aria-hidden="true">
        {num}
      </span>
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
          {base ? link(`${base}/script`, '剧本', 'script', '02') : disabled('剧本', 'script', '02')}
          {base ? link(`${base}/director`, '导演台', 'director', '03') : disabled('导演台', 'director', '03')}
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
    </div>
  );
}
