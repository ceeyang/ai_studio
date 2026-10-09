import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { enc, errorText } from '../api/client';
import {
  useCreateShot,
  useDeleteShot,
  useEpisode,
  useExports,
  useReorderShots,
  useRunStage,
  useSplitShot,
  useTranslateSubtitles,
} from '../api/queries';
import type { ExportOptions, ExportRow, RunIn } from '../api/types';
import { fmtTime } from '../format';
import { EndCardPanel } from './director/EndCardPanel';
import { ShotDrawer } from './director/ShotDrawer';
import { type GenStage, ShotTable } from './director/ShotTable';
import { moveId } from './director/slots';

const SUBTITLE_TAG: Record<string, string> = { zh: '中文字幕', 'zh+km': '中高字幕' };

/** 成片的变体标签：字幕、配音、片尾卡、时长；高棉语是 AI 译稿时提醒校对。 */
export function exportTags(settings: Record<string, unknown>): string[] {
  const tags: string[] = [];
  const subs = SUBTITLE_TAG[String(settings.subtitles ?? '')];
  if (subs) tags.push(subs);
  if (settings.dub === 'km') tags.push('高棉语配音');
  if (settings.end_card === true) tags.push('片尾卡');
  if (typeof settings.duration === 'number') tags.push(`${settings.duration.toFixed(1)} 秒`);
  return tags;
}

function ExportsPanel({ rows, slug }: { rows: ExportRow[]; slug: string }) {
  return (
    <section className="panel">
      <h3>成片</h3>
      {rows.length === 0 ? (
        <p className="muted">还没有导出。所有镜头都有选定视频后点"合成导出"。</p>
      ) : (
        <div className="export-body">
          <video className="export-preview" src={rows[0].url} controls preload="metadata" />
          <ul className="list export-list">
            {rows.map((x) => (
              <li key={x.id}>
                <a data-testid="export-link" href={x.url} download>
                  {x.file_path.split('/').pop()}
                </a>{' '}
                <span className="muted">
                  {[x.variant, `${String(x.settings.clips ?? '?')} 镜`, ...exportTags(x.settings), fmtTime(x.created_at)].join(' · ')}
                </span>
                {x.settings.km_review === 'ai_draft' && <span className="badge stale">高棉语待母语校对</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      <details className="end-card-box">
        <summary>片尾卡设置</summary>
        <EndCardPanel slug={slug} />
      </details>
    </section>
  );
}

export function DirectorPage() {
  const { slug = '', episodeId = '' } = useParams();
  const id = Number(episodeId);
  const [params, setParams] = useSearchParams();
  const ep = useEpisode(id);
  const exportsQ = useExports(id);
  const run = useRunStage(id);
  const reorder = useReorderShots(id);
  const split = useSplitShot(id);
  const del = useDeleteShot(id);
  const create = useCreateShot(id);
  const translate = useTranslateSubtitles(id);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [count, setCount] = useState(1);
  const [force, setForce] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [subZh, setSubZh] = useState(false);
  const [subKm, setSubKm] = useState(false);
  const [dubKm, setDubKm] = useState(false);
  const [endCard, setEndCard] = useState(false);

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

  const report = (p: Promise<unknown>, ok: string) => {
    p.then(
      () => setNotice(ok),
      (err: unknown) => setNotice(errorText(err)),
    );
  };

  // 镜头用 id 而不是 S01 这类编号：编号在排序、拆分、删除后会变
  const exportOptions: ExportOptions = {
    subtitles: subKm ? 'zh+km' : subZh ? 'zh' : 'none',
    dub: dubKm ? 'km' : 'off',
    end_card: endCard,
  };

  const generate = (stage: GenStage | 'export', ids: number[] = []) => {
    const body: RunIn = { stage, candidates: count, force };
    if (ids.length > 0) body.shots = ids.map(String);
    if (stage === 'export') body.export = exportOptions;
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
        <button type="button" disabled={shots.length === 0 || run.isPending} onClick={() => generate('export')}>
          合成导出
        </button>
      </div>
      <div className="toolbar export-options" role="group" aria-label="导出选项">
        <span className="muted">导出选项</span>
        <label>
          <input
            type="checkbox"
            aria-label="中文字幕"
            checked={subZh}
            onChange={(ev) => {
              setSubZh(ev.target.checked);
              if (!ev.target.checked) setSubKm(false);
            }}
          />{' '}
          中文字幕
        </label>
        <label>
          <input
            type="checkbox"
            aria-label="高棉语字幕"
            checked={subKm}
            onChange={(ev) => {
              setSubKm(ev.target.checked);
              if (ev.target.checked) setSubZh(true);
            }}
          />{' '}
          高棉语字幕（在中文下面）
        </label>
        <label>
          <input type="checkbox" aria-label="高棉语配音" checked={dubKm} onChange={(ev) => setDubKm(ev.target.checked)} /> 高棉语配音（Edge TTS，原声压低当底）
        </label>
        <label>
          <input type="checkbox" aria-label="片尾卡" checked={endCard} onChange={(ev) => setEndCard(ev.target.checked)} /> 片尾卡
        </label>
        <span className="grow" aria-hidden="true" />
        <button
          type="button"
          className="ghost"
          disabled={shots.length === 0 || translate.isPending}
          onClick={() =>
            translate.mutate(
              { force: false },
              {
                onSuccess: (r) => setNotice(`已提交翻译任务 #${r.job_ids.join(', #')}：只补缺的句子，译文是 AI 草稿，发布前请母语者校对`),
                onError: (err) => setNotice(errorText(err)),
              },
            )
          }
        >
          翻译高棉语字幕
        </button>
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
        <ExportsPanel rows={exportsQ.data ?? []} slug={slug} />
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
