import { type FormEvent, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { enc, errorText } from '../api/client';
import { useCreateEpisode, useCreateProject, useEngines, useEpisodes, useProject, useProjects } from '../api/queries';
import type { Aspect, EngineRow } from '../api/types';
import { EPISODE_STATUS_LABEL } from '../format';

function pickDefault(rows: EngineRow[] | undefined, preferred: string): string {
  if (!rows || rows.length === 0) return '';
  return rows.some((r) => r.name === preferred) ? preferred : rows[0].name;
}

function ProjectList({ active }: { active?: string }) {
  const projects = useProjects();
  if (projects.isLoading) return <p className="muted">加载中…</p>;
  if (projects.error) return <p className="error">{errorText(projects.error)}</p>;
  const rows = projects.data ?? [];
  if (rows.length === 0) return <p className="muted">还没有项目。</p>;
  return (
    <ul className="list">
      {rows.map((p) => (
        <li key={p.id} className={p.slug === active ? 'active' : undefined}>
          <Link to={`/projects/${enc(p.slug)}`}>{p.title}</Link>{' '}
          <span className="muted">
            {p.aspect} · {p.image_engine} / {p.video_engine}
          </span>
        </li>
      ))}
    </ul>
  );
}

function NewProjectForm() {
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
      {
        onSuccess: (p) => {
          setTitle('');
          navigate(`/projects/${enc(p.slug)}`);
        },
      },
    );
  };

  return (
    <form className="fields" onSubmit={submit}>
      <h3>新建项目</h3>
      <label className="field">
        <span>项目标题</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="如：提词员" />
      </label>
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
              {r.name}
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
      <div className="row">
        <button type="submit" className="primary" disabled={!title.trim() || !image || !video || create.isPending}>
          新建项目
        </button>
      </div>
      {engines.error && <p className="error">读取引擎列表失败：{errorText(engines.error)}</p>}
      {create.error && <p className="error">{errorText(create.error)}</p>}
    </form>
  );
}

function ProjectEpisodes({ slug }: { slug: string }) {
  const project = useProject(slug);
  const episodes = useEpisodes(slug);
  const create = useCreateEpisode(slug);
  const navigate = useNavigate();
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [seconds, setSeconds] = useState('60');

  if (project.error) return <section className="panel error">{errorText(project.error)}</section>;

  const secondsOk = Number(seconds) > 0 && Number(seconds) <= 3600;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      { title: title.trim(), source_text: text, target_seconds: Number(seconds) },
      { onSuccess: (ep) => navigate(`/projects/${enc(slug)}/episodes/${ep.id}/script`) },
    );
  };

  return (
    <section className="panel">
      <h2>{project.data?.title ?? slug}</h2>
      {project.data && (
        <p className="muted">
          目录 {project.data.slug} · 画幅 {project.data.aspect} · 图像 {project.data.image_engine} · 视频{' '}
          {project.data.video_engine}
        </p>
      )}
      <h3>剧集</h3>
      {episodes.error && <p className="error">{errorText(episodes.error)}</p>}
      {episodes.data && episodes.data.length === 0 && <p className="muted">还没有剧集，在下面粘贴梗概新建第一集。</p>}
      <ul className="list">
        {(episodes.data ?? []).map((ep) => (
          <li key={ep.id}>
            第 {ep.number} 集 {ep.title || '（未命名）'}{' '}
            <span className="muted">
              · {EPISODE_STATUS_LABEL[ep.status] ?? ep.status} · 目标 {ep.target_seconds} 秒
            </span>{' '}
            <Link to={`/projects/${enc(slug)}/episodes/${ep.id}/script`}>剧本</Link>{' '}
            <Link to={`/projects/${enc(slug)}/episodes/${ep.id}/director`}>导演台</Link>
          </li>
        ))}
      </ul>
      <form className="fields" onSubmit={submit}>
        <h3>新建剧集</h3>
        <label className="field">
          <span>本集标题</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="可以留空，跑剧本时自动生成" />
        </label>
        <label className="field">
          <span>原文或梗概</span>
          <textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} />
        </label>
        <label className="field">
          <span>目标时长（秒）</span>
          <input type="number" min={1} max={3600} value={seconds} onChange={(e) => setSeconds(e.target.value)} />
        </label>
        <div className="row">
          <button type="submit" className="primary" disabled={!text.trim() || !secondsOk || create.isPending}>
            新建剧集
          </button>
        </div>
        {create.error && <p className="error">{errorText(create.error)}</p>}
      </form>
    </section>
  );
}

export function ProjectsPage() {
  const { slug } = useParams();
  return (
    <div className="split">
      <section className="panel">
        <h2>项目中心</h2>
        <ProjectList active={slug} />
        <NewProjectForm />
      </section>
      {slug ? (
        <ProjectEpisodes key={slug} slug={slug} />
      ) : (
        <section className="panel muted">选择左侧的项目，或新建一个。</section>
      )}
    </div>
  );
}
