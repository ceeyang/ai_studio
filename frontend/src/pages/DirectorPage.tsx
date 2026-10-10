import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { enc, errorText } from '../api/client';
import {
  useCreateShot,
  useDeleteShot,
  useEpisode,
  useReorderShots,
  useRunStage,
  useSplitShot,
} from '../api/queries';
import type { RunIn } from '../api/types';
import { ShotDrawer } from './director/ShotDrawer';
import { type GenStage, ShotTable } from './director/ShotTable';
import { moveId, selectedOf, slotOf } from './director/slots';

export function DirectorPage() {
  const { slug = '', episodeId = '' } = useParams();
  const id = Number(episodeId);
  const [params, setParams] = useSearchParams();
  const ep = useEpisode(id);
  const run = useRunStage(id);
  const reorder = useReorderShots(id);
  const split = useSplitShot(id);
  const del = useDeleteShot(id);
  const create = useCreateShot(id);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [count, setCount] = useState(1);
  const [force, setForce] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  if (ep.isLoading) return <p className="loading">加载中…</p>;
  if (ep.error || !ep.data) return <p className="error">{ep.error ? errorText(ep.error) : '找不到这一集'}</p>;
  const e = ep.data;
  const shots = e.shots;
  const openId = Number(params.get('shot')) || null;
  const openShot = shots.find((s) => s.id === openId) ?? null;
  const busy = reorder.isPending || split.isPending || del.isPending || create.isPending;
  const selIds = shots.filter((s) => selected.has(s.id)).map((s) => s.id);
  const scope = selIds.length > 0 ? `选中 ${selIds.length} 镜` : '全部';
  const total = shots.reduce((a, s) => a + s.duration, 0);
  const withVideo = shots.filter((s) => selectedOf(slotOf(s, 'video'))).length;

  const report = (p: Promise<unknown>, ok: string) => {
    p.then(
      () => setNotice(ok),
      (err: unknown) => setNotice(errorText(err)),
    );
  };

  // 镜头用 id 而不是 S01 这类编号：编号在排序、拆分、删除后会变
  const generate = (stage: GenStage, ids: number[] = []) => {
    const body: RunIn = { stage, candidates: count, force };
    if (ids.length > 0) body.shots = ids.map(String);
    run.mutate(body, {
      onSuccess: (r) => setNotice(`已提交 ${r.job_ids.length} 个任务（#${r.job_ids.join(', #')}），进度见顶部任务条`),
      onError: (err) => setNotice(errorText(err)),
    });
  };

  const toggle = (shotId: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(shotId)) next.delete(shotId);
      else next.add(shotId);
      return next;
    });
  const toggleAll = () =>
    setSelected((prev) =>
      shots.every((s) => prev.has(s.id)) ? new Set<number>() : new Set<number>(shots.map((s) => s.id)),
    );

  const move = (shotId: number, delta: -1 | 1) => {
    const next = moveId(shots.map((s) => s.id), shotId, delta);
    if (next) report(reorder.mutateAsync(next), '已调整顺序');
  };

  const remove = (shotId: number) => {
    const s = shots.find((x) => x.id === shotId);
    if (!s || !window.confirm(`删除镜头 ${s.code}？它的候选会一起删掉（媒体文件留在磁盘上）。`)) return;
    setSelected((prev) => {
      const next = new Set(prev);
      next.delete(shotId);
      return next;
    });
    if (openId === shotId) setParams({});
    report(del.mutateAsync(shotId), `已删除 ${s.code}`);
  };

  return (
    <div className="page">
      <header className="page-head">
        <span className="eyebrow">03 · 导演台</span>
        <h2>
          导演台 · 第 {e.number} 集 {e.title}
        </h2>
        <span className="chip">
          {shots.length} 个镜头，共 {total.toFixed(1)} 秒
        </span>
        <Link className="btn ghost" to={`/projects/${enc(slug)}/episodes/${id}/script`}>
          回剧本页
        </Link>
      </header>

      <div className="toolbar">
        <label>
          候选数{' '}
          <select aria-label="候选数" value={count} onChange={(ev) => setCount(Number(ev.target.value))}>
            {[1, 2, 3, 4].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label>
          <input type="checkbox" aria-label="强制重跑" checked={force} onChange={(ev) => setForce(ev.target.checked)} /> 强制重跑
        </label>
        <span className="sep" aria-hidden="true" />
        <button type="button" className="primary" disabled={shots.length === 0 || run.isPending} onClick={() => generate('keyframes', selIds)}>
          生成首帧（{scope}）
        </button>
        <button type="button" className="primary" disabled={shots.length === 0 || run.isPending} onClick={() => generate('videos', selIds)}>
          生成视频（{scope}）
        </button>
        <span className="grow" aria-hidden="true" />
        <button type="button" className="ghost" disabled={busy} onClick={() => report(create.mutateAsync({}), '已在末尾新增镜头')}>
          新增镜头
        </button>
        <Link className="btn" to={`/projects/${enc(slug)}/episodes/${id}/export`}>
          去导出（{withVideo}/{shots.length} 镜有视频）
        </Link>
      </div>
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}

      <div className="director-body">
        {shots.length === 0 ? (
          <p className="panel empty">
            这一集还没有分镜。<Link to={`/projects/${enc(slug)}/episodes/${id}/script`}>去剧本页运行分镜</Link>
          </p>
        ) : (
          <ShotTable
            shots={shots}
            selected={selected}
            busy={busy}
            generating={run.isPending}
            onToggle={toggle}
            onToggleAll={toggleAll}
            onOpen={(shotId) => setParams({ shot: String(shotId) })}
            onMove={move}
            onSplit={(shotId) => report(split.mutateAsync(shotId), '已拆分')}
            onInsertAfter={(shotId) => report(create.mutateAsync({ after_shot_id: shotId }), '已插入新镜头')}
            onDelete={remove}
            onGenerate={(shotId, stage) => generate(stage, [shotId])}
          />
        )}
      </div>

      {openShot && <div className="drawer-scrim" aria-hidden="true" />}
      {openShot && (
        <ShotDrawer
          shot={openShot}
          episodeId={id}
          generating={run.isPending}
          onClose={() => setParams({})}
          onGenerate={(stage) => generate(stage, [openShot.id])}
        />
      )}
    </div>
  );
}
