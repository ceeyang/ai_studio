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
  const disabled = (label: string) => (
    <span className="nav-disabled" title="先在项目中心打开一集">
      {label}
    </span>
  );

  return (
    <div className="app">
      <nav className="nav" aria-label="阶段导航">
        <div className="brand">AI Studio</div>
        <NavLink to="/" end>
          项目中心
        </NavLink>
        {base ? <NavLink to={`${base}/script`}>剧本</NavLink> : disabled('剧本')}
        {base ? <NavLink to={`${base}/director`}>导演台</NavLink> : disabled('导演台')}
        <NavLink to="/jobs">任务</NavLink>
        <NavLink to="/settings">设置</NavLink>
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
