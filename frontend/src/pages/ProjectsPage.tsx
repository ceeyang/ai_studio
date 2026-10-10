import { useQueries } from '@tanstack/react-query';
import { type CSSProperties, type FormEvent, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, enc, errorText } from '../api/client';
import {
  useCreateEpisode,
  useCharacters,
  useCreateProject,
  useEngines,
  useLocations,
  useStyle,
  useEpisodes,
  useProject,
  useProjectOverview,
  useProjects,
  keys,
} from '../api/queries';
import type { Aspect, EngineRow, EpisodeStats, Project, ProjectOverview } from '../api/types';
import { Dialog } from '../components/Dialog';
import { EPISODE_STATUS_LABEL, engineLabel } from '../format';
import { readLastEpisode } from '../lastEpisode';

function pickDefault(rows: EngineRow[] | undefined, preferred: string): string {
  if (!rows || rows.length === 0) return '';
  return rows.some((r) => r.name === preferred) ? preferred : rows[0].name;
}

function statusLabel(s: string): string {
  return EPISODE_STATUS_LABEL[s] ?? s;
}

/** 没有首帧时的封面：按标题算一个稳定的色相，配大号首字。 */
function hueOf(text: string): number {
  let h = 0;
  for (const ch of text) h = (h * 31 + (ch.codePointAt(0) ?? 0)) % 360;
  return h;
}

function Cover({ title, url, className = '' }: { title: string; url?: string | null; className?: string }) {
  if (url) {
    return (
      <div className={`cover ${className}`}>
        <img src={url} alt="" loading="lazy" />
      </div>
    );
  }
  const style = { '--h': hueOf(title) } as CSSProperties;
  return (
    <div className={`cover cover-gen ${className}`} style={style} aria-hidden="true">
      <span>{Array.from(title.trim())[0] ?? '?'}</span>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  return <span className={`pill ep-${status}`}>{statusLabel(status)}</span>;
}

// ------------------------------------------------------------ 新建项目

function NewProjectDialog({ onClose }: { onClose: () => void }) {
  const engines = useEngines();
  const create = useCreateProject();
  const navigate = useNavigate();
  const [title, setTitle] = useState('');
  const [aspect, setAspect] = useState<Aspect>('9:16');
  const [imageEngine, setImageEngine] = useState('');
  const [videoEngine, setVideoEngine] = useState('');
  const image = imageEngine || pickDefault(engines.data?.image, 'zimage');
  const video = videoEngine || pickDefault(engines.data?.video, 'h3_video');

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      { title: title.trim(), aspect, image_engine: image, video_engine: video },
      { onSuccess: (p) => navigate(`/projects/${enc(p.slug)}`) },
    );
  };

  return (
    <Dialog title="新建项目" subtitle="一个项目对应一部剧：画幅和默认引擎对它的每一集生效，之后也能按镜头单独换。" onClose={onClose}>
      <form className="fields" onSubmit={submit}>
        <label className="field">
          <span>项目标题</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="如：提词员" />
        </label>
        <div className="field-grid">
          <label className="field">
            <span>画幅</span>
            <select value={aspect} onChange={(e) => setAspect(e.target.value as Aspect)}>
              <option value="9:16">9:16 竖屏</option>
              <option value="16:9">16:9 横屏</option>
            </select>
          </label>
          <label className="field">
            <span>图像引擎</span>
            <select value={image} onChange={(e) => setImageEngine(e.target.value)}>
              {(engines.data?.image ?? []).map((r) => (
                <option key={r.name} value={r.name}>
                  {engineLabel(r)}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>视频引擎</span>
            <select value={video} onChange={(e) => setVideoEngine(e.target.value)}>
              {(engines.data?.video ?? []).map((r) => (
                <option key={r.name} value={r.name}>
                  {r.name}
                  {r.caps.has_audio ? '（有声）' : '（无声）'}
                </option>
              ))}
            </select>
          </label>
        </div>
        {engines.error && <p className="error">读取引擎列表失败：{errorText(engines.error)}</p>}
        {create.error && <p className="error">{errorText(create.error)}</p>}
        <div className="modal-foot">
          <button type="button" className="ghost" onClick={onClose}>
            取消
          </button>
          <button type="submit" className="primary" disabled={!title.trim() || !image || !video || create.isPending}>
            创建项目
          </button>
        </div>
      </form>
    </Dialog>
  );
}

// ------------------------------------------------------------ 项目总览

function ProjectCard({ p }: { p: Project }) {
  const overview = useProjectOverview(p.slug);
  const o = overview.data;
  const latest: EpisodeStats | undefined = o?.episodes[o.episodes.length - 1];
  const exports = o?.episodes.reduce((n, e) => n + e.export_count, 0) ?? 0;
  const to = `/projects/${enc(p.slug)}`;
  return (
    <article className="project-card" data-testid={`project-${p.slug}`}>
      <Link to={to} className="project-cover" tabIndex={-1} aria-hidden="true">
        <Cover title={p.title} url={o?.cover_url} />
        <span className="cover-tag">{p.aspect}</span>
        {latest && (
          <span className="cover-status">
            <StatusPill status={latest.status} />
          </span>
        )}
      </Link>
      <div className="project-body">
        <h3>
          <Link to={to}>{p.title}</Link>
        </h3>
        <p className="project-stats">
          {o ? (
            <>
              <b>{o.episode_count}</b> 集<span className="dot">·</span>
              <b>{exports}</b> 个成片
            </>
          ) : (
            <span className="muted">…</span>
          )}
        </p>
        <div className="engine-chips">
          <span className="chip" title="图像引擎">
            <b>图</b> {p.image_engine}
          </span>
          <span className="chip" title="视频引擎">
            <b>视</b> {p.video_engine}
          </span>
        </div>
        <div className="project-actions">
          <Link className="btn sm" to={to}>
            打开
          </Link>
          {latest && (
            <Link
              className="btn sm ghost"
              to={`/projects/${enc(p.slug)}/episodes/${latest.id}/director`}
              title={`第 ${latest.number} 集的导演台`}
            >
              导演台 · 第 {latest.number} 集
            </Link>
          )}
        </div>
      </div>
    </article>
  );
}

interface ContinueTarget {
  project: Project;
  episode: EpisodeStats;
}

/** “继续制作”：优先上次打开的那一集，否则取最新项目的最新一集。 */
function pickContinue(rows: Project[], overviews: (ProjectOverview | undefined)[]): ContinueTarget | null {
  const last = readLastEpisode();
  if (last) {
    const i = rows.findIndex((p) => p.slug === last.slug);
    const ep = i >= 0 ? overviews[i]?.episodes.find((e) => e.id === last.episodeId) : undefined;
    if (ep) return { project: rows[i], episode: ep };
  }
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const eps = overviews[i]?.episodes ?? [];
    if (eps.length) return { project: rows[i], episode: eps[eps.length - 1] };
  }
  return null;
}

function ContinueCard({ target }: { target: ContinueTarget }) {
  const { project: p, episode: ep } = target;
  const base = `/projects/${enc(p.slug)}/episodes/${ep.id}`;
  return (
    <section className="continue-card" aria-label="继续制作">
      <Cover title={p.title} url={ep.cover_url} className="continue-cover" />
      <div className="continue-main">
        <span className="eyebrow">继续制作</span>
        <div className="continue-title">
          <Link to={`/projects/${enc(p.slug)}`}>{p.title}</Link>
          <span className="muted">
            第 {ep.number} 集 {ep.title || '（未命名）'}
          </span>
        </div>
        <div className="ep-facts">
          <StatusPill status={ep.status} />
          <span>{ep.shot_count} 个镜头</span>
          <span className={ep.export_count ? 'has' : undefined}>{ep.export_count} 个成片</span>
        </div>
      </div>
      <div className="continue-actions">
        <Link className="btn" to={`${base}/script`}>
          剧本
        </Link>
        <Link className="btn primary" to={`${base}/director`}>
          打开导演台
        </Link>
      </div>
    </section>
  );
}

function StatTiles({ rows, overviews }: { rows: Project[]; overviews: (ProjectOverview | undefined)[] }) {
  const eps = overviews.flatMap((o) => o?.episodes ?? []);
  const tiles: [string, number][] = [
    ['项目', rows.length],
    ['剧集', eps.length],
    ['镜头', eps.reduce((n, e) => n + e.shot_count, 0)],
    ['成片', eps.reduce((n, e) => n + e.export_count, 0)],
  ];
  return (
    <dl className="stat-tiles">
      {tiles.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function ProjectsOverview() {
  const projects = useProjects();
  const [creating, setCreating] = useState(false);
  const rows = projects.data ?? [];
  const overviews = useQueries({
    queries: rows.map((p) => ({
      queryKey: keys.projectOverview(p.slug),
      queryFn: () => api.get<ProjectOverview>(`/api/projects/${enc(p.slug)}/overview`),
    })),
  }).map((q) => q.data);
  const target = pickContinue(rows, overviews);
  return (
    <div className="page">
      <header className="page-head hero-head">
        <span className="eyebrow">01 · 项目中心</span>
        <h2>项目中心</h2>
        {projects.data && <span className="chip">{rows.length} 个项目</span>}
        <button type="button" className="primary lg head-action" onClick={() => setCreating(true)}>
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            <path d="M7 2v10M2 7h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
          新建项目
        </button>
      </header>
      {projects.isLoading && <p className="loading">加载中…</p>}
      {projects.error && <p className="error">{errorText(projects.error)}</p>}
      {projects.data && rows.length === 0 && (
        <section className="panel empty">
          <p>还没有项目。新建一个，把第一集的梗概贴进去就能开工。</p>
        </section>
      )}
      {rows.length > 0 && (
        <div className={target ? 'overview-top' : 'overview-top solo'}>
          {target && <ContinueCard target={target} />}
          <StatTiles rows={rows} overviews={overviews} />
        </div>
      )}
      {rows.length > 0 && (
        <div className="project-grid">
          {rows.map((p) => (
            <ProjectCard key={p.id} p={p} />
          ))}
          <button type="button" className="new-tile" onClick={() => setCreating(true)}>
            <span className="new-tile-plus" aria-hidden="true" />
            新建一个项目
          </button>
        </div>
      )}
      {creating && <NewProjectDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

// ------------------------------------------------------------ 项目详情

/** 目标时长范围：和剧本页的目标时长字段（EPISODE_FIELDS）一致 */
const MIN_SECONDS = 5;
const MAX_SECONDS = 3600;

function NewEpisodeDialog({ slug, nextNumber, onClose }: { slug: string; nextNumber: number; onClose: () => void }) {
  const create = useCreateEpisode(slug);
  const navigate = useNavigate();
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [seconds, setSeconds] = useState('30');
  // 和剧本页的目标时长字段同一范围：太短的集剧本页一个字都存不了
  const secondsOk = seconds.trim() !== '' && Number(seconds) >= MIN_SECONDS && Number(seconds) <= MAX_SECONDS;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      { title: title.trim(), source_text: text, target_seconds: Number(seconds) },
      { onSuccess: (ep) => navigate(`/projects/${enc(slug)}/episodes/${ep.id}/script`) },
    );
  };
  return (
    <Dialog
      title={`新建剧集 · 第 ${nextNumber} 集`}
      subtitle="贴原文或梗概，创建后到剧本页运行剧本。"
      onClose={onClose}
      wide
      guard={() => text.trim() !== ''}
      busy={create.isPending}
    >
      <form className="fields" onSubmit={submit}>
        <div className="field-grid two">
          <label className="field">
            <span>本集标题</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="可以留空，跑剧本时自动生成" />
          </label>
          <label className="field">
            <span>目标时长（秒）</span>
            <input type="number" min={MIN_SECONDS} max={MAX_SECONDS} value={seconds} onChange={(e) => setSeconds(e.target.value)} />
          </label>
        </div>
        {!secondsOk && <p className="error">目标时长要在 {MIN_SECONDS}–{MAX_SECONDS} 秒之间</p>}
        <label className="field">
          <span>原文或梗概</span>
          <textarea rows={14} value={text} onChange={(e) => setText(e.target.value)} />
        </label>
        {create.error && <p className="error">{errorText(create.error)}</p>}
        <div className="modal-foot">
          <span className="muted">{text.trim() ? `${Array.from(text.trim()).length} 字` : ''}</span>
          <button type="button" className="ghost" disabled={create.isPending} onClick={onClose}>
            取消
          </button>
          <button type="submit" className="primary" disabled={!text.trim() || !secondsOk || create.isPending}>
            {create.isPending ? '创建中…' : '创建剧集'}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function ProjectBible({ slug }: { slug: string }) {
  const characters = useCharacters(slug);
  const locations = useLocations(slug);
  const style = useStyle(slug);
  const chars = characters.data ?? [];
  const locs = locations.data ?? [];
  const st = style.data;
  return (
    <aside className="bible" aria-label="项目设定">
      <div className="bible-block">
        <h4>
          角色 <span className="muted">{chars.length}</span>
        </h4>
        {chars.length === 0 ? (
          <p className="muted">运行第一集的剧本后自动抽取。</p>
        ) : (
          <ul className="bible-list">
            {chars.map((c) => (
              <li key={c.id}>
                <span className="avatar" style={{ '--h': hueOf(c.name) } as CSSProperties} aria-hidden="true">
                  {Array.from(c.name)[0]}
                </span>
                <div>
                  <b>{c.name}</b>
                  <p>{c.description || c.core_features}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="bible-block">
        <h4>
          场景 <span className="muted">{locs.length}</span>
        </h4>
        {locs.length === 0 ? (
          <p className="muted">运行剧本后自动抽取。</p>
        ) : (
          <ul className="bible-list">
            {locs.map((l) => (
              <li key={l.id}>
                <div>
                  <b>{l.name}</b>
                  {l.time_of_day && <span className="chip">{l.time_of_day}</span>}
                  <p>{l.atmosphere}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="bible-block">
        <h4>风格</h4>
        {st ? (
          <>
            {st.mood_keywords.length > 0 && (
              <div className="engine-chips">
                {st.mood_keywords.map((k) => (
                  <span key={k} className="chip">
                    {k}
                  </span>
                ))}
              </div>
            )}
            <p className="bible-anchor">{st.anchor_text || st.palette}</p>
          </>
        ) : (
          <p className="muted">还没有风格设定。</p>
        )}
      </div>
    </aside>
  );
}

function ProjectDetail({ slug }: { slug: string }) {
  const project = useProject(slug);
  const episodes = useEpisodes(slug);
  const overview = useProjectOverview(slug);
  const [creating, setCreating] = useState(false);

  if (project.error) {
    return (
      <div className="page">
        <Link className="back-link" to="/">
          ← 全部项目
        </Link>
        <section className="panel error">{errorText(project.error)}</section>
      </div>
    );
  }

  const p = project.data;
  const eps = episodes.data ?? [];
  const stats = new Map((overview.data?.episodes ?? []).map((e) => [e.id, e]));
  const exportTotal = overview.data?.episodes.reduce((n, e) => n + e.export_count, 0) ?? 0;
  const shotTotal = overview.data?.episodes.reduce((n, e) => n + e.shot_count, 0) ?? 0;
  const nextNumber = eps.reduce((n, e) => Math.max(n, e.number), 0) + 1;
  const title = p?.title ?? slug;
  const base = `/projects/${enc(slug)}/episodes`;

  return (
    <div className="page">
      <Link className="back-link" to="/">
        ← 全部项目
      </Link>
      <div className="detail-body">
        <div className="detail-main">
          <header className="project-hero">
            <Cover title={title} url={overview.data?.cover_url} className="hero-cover" />
            <div className="hero-main">
              <span className="eyebrow">项目</span>
              <h2>{title}</h2>
              {p && (
                <div className="meta">
                  <span className="chip">
                    <b>目录</b> {p.slug}
                  </span>
                  <span className="chip">
                    <b>画幅</b> {p.aspect}
                  </span>
                  <span className="chip">
                    <b>图像</b> {p.image_engine}
                  </span>
                  <span className="chip">
                    <b>视频</b> {p.video_engine}
                  </span>
                </div>
              )}
              <dl className="hero-stats">
                <div>
                  <dt>剧集</dt>
                  <dd>{eps.length}</dd>
                </div>
                <div>
                  <dt>镜头</dt>
                  <dd>{shotTotal}</dd>
                </div>
                <div>
                  <dt>成片</dt>
                  <dd>{exportTotal}</dd>
                </div>
              </dl>
            </div>
            <div className="hero-actions">
              <button type="button" className="primary lg" onClick={() => setCreating(true)}>
                <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
                  <path d="M7 2v10M2 7h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                </svg>
                新建剧集
              </button>
            </div>
          </header>
          <section className="section">
            <div className="section-head">
              <h3>剧集</h3>
              <span className="muted">{eps.length} 集</span>
            </div>
            {episodes.error && <p className="error">{errorText(episodes.error)}</p>}
            {episodes.data && eps.length === 0 && (
              <div className="panel empty">
                <p>还没有剧集。点右上角“新建剧集”，粘贴梗概开始第一集。</p>
              </div>
            )}
            <ul className="episode-grid">
              {eps.map((ep) => {
                const st = stats.get(ep.id);
                return (
                  <li key={ep.id} className="episode-card" data-testid={`episode-${ep.id}`}>
                    <Cover title={ep.title || title} url={st?.cover_url} className="ep-cover" />
                    <div className="ep-body">
                      <div className="ep-head">
                        <span className="ep-num">第 {ep.number} 集</span>
                        <StatusPill status={ep.status} />
                      </div>
                      <h4 className="ep-title">{ep.title || '（未命名）'}</h4>
                      <p className="ep-synopsis">{ep.source_text || '（还没有原文）'}</p>
                      <div className="ep-facts">
                        <span>目标 {ep.target_seconds} 秒</span>
                        {st && <span>{st.shot_count} 个镜头</span>}
                        {st && <span className={st.export_count ? 'has' : undefined}>{st.export_count} 个成片</span>}
                      </div>
                      <div className="ep-actions">
                        <Link className="btn sm" to={`${base}/${ep.id}/script`}>
                          去剧本
                        </Link>
                        <Link className="btn sm primary" to={`${base}/${ep.id}/director`}>
                          去导演台
                        </Link>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        </div>
        <ProjectBible slug={slug} />
      </div>
      {creating && <NewEpisodeDialog slug={slug} nextNumber={nextNumber} onClose={() => setCreating(false)} />}
    </div>
  );
}

export function ProjectsPage() {
  const { slug } = useParams();
  return slug ? <ProjectDetail key={slug} slug={slug} /> : <ProjectsOverview />;
}
