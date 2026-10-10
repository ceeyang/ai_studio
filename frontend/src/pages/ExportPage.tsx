import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { enc, errorText } from '../api/client';
import { useEpisode, useExports, useFitKhmer, usePatchExport, useRunStage, useTranslateSubtitles } from '../api/queries';
import type { ExportOptions, ExportRow, ShotDetail } from '../api/types';
import { EpisodeHeader, EpisodeLoadError, nextActionProps, useNextAction } from '../components/EpisodeHeader';
import { useJobNotice } from '../components/JobNotice';
import { fmtTime } from '../format';
import { EndCardPanel } from './director/EndCardPanel';
import { slotMark, slotOf } from './director/slots';

const SUBTITLE_TAG: Record<string, string> = { zh: '中文字幕', 'zh+km': '中高字幕' };

/** 旧成片（后端还没给 label 时）的标签：字幕、配音、原声底、对齐、片尾卡、时长。 */
export function exportTags(settings: Record<string, unknown>): string[] {
  const tags: string[] = [];
  const subs = SUBTITLE_TAG[String(settings.subtitles ?? '')];
  if (subs) tags.push(subs);
  if (settings.dub === 'km') tags.push('高棉语配音');
  if (settings.bed === false) tags.push('无原声底');
  if (settings.align === 'detected') tags.push('配音对齐说话');
  if (settings.end_card === true) tags.push('片尾卡');
  if (typeof settings.duration === 'number') tags.push(`${settings.duration.toFixed(1)} 秒`);
  return tags;
}

function exportLabel(x: ExportRow): string {
  if (x.label) return x.label;
  return [x.variant, `${String(x.settings.clips ?? '?')} 镜`, ...exportTags(x.settings)].join(' · ');
}

/** 导出页上勾的选项；按剧集记在浏览器里，下次打开还是上次的选择。 */
interface Choice {
  subZh: boolean;
  subKm: boolean;
  dubKm: boolean;
  endCard: boolean;
  bed: boolean;
  alignDetected: boolean;
}

const DEFAULT_CHOICE: Choice = { subZh: false, subKm: false, dubKm: false, endCard: false, bed: true, alignDetected: false };

export const OPTIONS_KEY = (episodeId: number) => `ai-studio.export-options.${episodeId}`;

function loadChoice(episodeId: number): Choice {
  try {
    const raw = localStorage.getItem(OPTIONS_KEY(episodeId));
    if (!raw) return DEFAULT_CHOICE;
    const saved = JSON.parse(raw) as Partial<Choice>;
    const out = { ...DEFAULT_CHOICE };
    for (const k of Object.keys(DEFAULT_CHOICE) as (keyof Choice)[]) {
      if (typeof saved[k] === 'boolean') out[k] = saved[k] as boolean;
    }
    return out;
  } catch {
    // 隐私模式、存储被禁用、内容坏了：用默认值
    return DEFAULT_CHOICE;
  }
}

function saveChoice(episodeId: number, c: Choice) {
  try {
    localStorage.setItem(OPTIONS_KEY(episodeId), JSON.stringify(c));
  } catch {
    // 存不了就只在这次打开里有效
  }
}

function toOptions(c: Choice): ExportOptions {
  return {
    subtitles: c.subKm ? 'zh+km' : c.subZh ? 'zh' : 'none',
    dub: c.dubKm ? 'km' : 'off',
    end_card: c.endCard,
    bed: c.bed,
    align: c.dubKm && c.alignDetected ? 'detected' : 'timeline',
  };
}

/** 就绪检查：几镜有选定视频、哪些缺、哪些视频过期。 */
export function readiness(shots: ShotDetail[]) {
  const marks = shots.map((s) => ({ code: s.code, mark: slotMark(slotOf(s, 'video')) }));
  const missing = marks.filter((m) => m.mark === 'none').map((m) => m.code);
  const stale = marks.filter((m) => m.mark === 'stale').map((m) => m.code);
  return { total: shots.length, ready: shots.length - missing.length, missing, stale };
}

function ExportsList({ rows, episodeId }: { rows: ExportRow[]; episodeId: number }) {
  const patch = usePatchExport(episodeId);
  const [playing, setPlaying] = useState<number | null>(null);
  if (rows.length === 0) {
    return <p className="muted">还没有导出。所有镜头都有选定视频后点「合成导出」。</p>;
  }
  const current = rows.find((x) => x.id === playing) ?? rows[0];
  const needsReview = rows.some((x) => x.settings.km_review === 'ai_draft');
  return (
    <div className="export-body">
      <video data-testid="export-player" className="export-preview" src={current.url} controls preload="metadata" />
      <div className="export-side">
        {needsReview && (
          <p className="export-review">
            <span className="badge stale">高棉语待母语校对</span>
            <span className="muted">带高棉语字幕或配音的版本是 AI 译稿，发布前请母语者看一遍</span>
          </p>
        )}
        <ul className="list export-list" aria-label="成片列表">
          {rows.map((x) => (
            <li key={x.id} className={`${x.id === current.id ? 'is-playing' : ''}${x.is_final ? ' is-final' : ''}`}>
              <button
                type="button"
                className="export-pick"
                aria-pressed={x.id === current.id}
                onClick={() => setPlaying(x.id)}
              >
                <span className="export-label">{exportLabel(x)}</span>
                <span className="muted">{fmtTime(x.created_at)}</span>
              </button>
              <div className="export-meta">
                {x.is_final ? (
                  <>
                    <span className="badge ok">定稿</span>
                    <button
                      type="button"
                      className="sm ghost"
                      disabled={patch.isPending}
                      onClick={() => patch.mutate({ id: x.id, is_final: false })}
                    >
                      取消定稿
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="sm ghost"
                    disabled={patch.isPending}
                    onClick={() => patch.mutate({ id: x.id, is_final: true })}
                  >
                    设为定稿
                  </button>
                )}
                <a data-testid="export-link" className="export-file" href={x.url} download>
                  {x.file_path.split('/').pop()}
                </a>
              </div>
            </li>
          ))}
        </ul>
        {patch.error && <p className="error">{errorText(patch.error)}</p>}
      </div>
    </div>
  );
}

function ExportBody({ slug, id }: { slug: string; id: number }) {
  const ep = useEpisode(id);
  const exportsQ = useExports(id);
  const run = useRunStage(id);
  const translate = useTranslateSubtitles(id);
  const fit = useFitKhmer(id);
  const notice = useJobNotice();
  const [choice, setChoice] = useState<Choice>(() => loadChoice(id));
  useEffect(() => saveChoice(id, choice), [id, choice]);
  const set = (patch: Partial<Choice>) => setChoice((c) => ({ ...c, ...patch }));
  const nextAct = useNextAction(slug, ep.data, 'export');

  if (ep.isLoading) return <p className="loading">加载中…</p>;
  if (ep.error || !ep.data) return <EpisodeLoadError slug={slug} error={ep.error} />;
  const e = ep.data;
  const r = readiness(e.shots);
  const director = `/projects/${enc(slug)}/episodes/${id}/director`;
  const canExport = r.total > 0 && r.missing.length === 0;

  const doExport = () => {
    notice.clear();
    run.mutate(
      { stage: 'export', export: toOptions(choice) },
      {
        onSuccess: (res) => notice.track(res.job_ids, '合成导出'),
        onError: (err) => notice.error(errorText(err)),
      },
    );
  };
  const doTranslate = () => {
    notice.clear();
    translate.mutate(
      { force: false },
      {
        onSuccess: (res) => notice.track(res.job_ids, '翻译高棉语字幕'),
        onError: (err) => notice.error(errorText(err)),
      },
    );
  };
  const doFit = () => {
    notice.clear();
    fit.mutate(
      {},
      {
        onSuccess: (res) => notice.track(res.job_ids, '按高棉语时长改写中文'),
        onError: (err) => notice.error(errorText(err)),
      },
    );
  };

  return (
    <div className="page export-page">
      <EpisodeHeader slug={slug} ep={e} page="export" title="导出" />

      <section className="panel" aria-label="就绪检查">
        <div className="row export-ready">
          <span data-testid="readiness" className={r.missing.length > 0 ? 'warn' : undefined}>
            {r.total === 0 ? '这一集还没有镜头' : `${r.ready}/${r.total} 镜有视频`}
            {r.stale.length > 0 && ` · ${r.stale.length} 镜视频已过期（${r.stale.join('、')}）`}
          </span>
          <span className="grow" aria-hidden="true" />
          {/* 下一步是合成导出时它是本页唯一的主按钮，否则页头的「下一步」才是 */}
          <button
            type="button"
            {...nextActionProps(nextAct?.key === 'export')}
            disabled={!canExport || run.isPending}
            title={canExport ? undefined : '每个镜头都要有选定的视频才能合成'}
            onClick={doExport}
          >
            合成导出
          </button>
        </div>
        {r.missing.length > 0 && (
          <p className="warn" data-testid="missing-videos">
            还缺视频：{r.missing.join('、')}。<Link to={director}>去导演台</Link>
          </p>
        )}
        {r.total === 0 && (
          <p className="muted">
            先在导演台生成首帧和视频。<Link to={director}>去导演台</Link>
          </p>
        )}
        {notice.view}
      </section>

      <section className="panel" aria-label="导出选项">
        <h3>导出选项</h3>
        <div className="export-groups">
          <fieldset className="export-group">
            <legend>字幕</legend>
            <label>
              <input
                type="checkbox"
                aria-label="中文字幕"
                checked={choice.subZh}
                onChange={(ev) => set(ev.target.checked ? { subZh: true } : { subZh: false, subKm: false })}
              />{' '}
              中文字幕
            </label>
            <label>
              <input
                type="checkbox"
                aria-label="高棉语字幕"
                checked={choice.subKm}
                onChange={(ev) => set(ev.target.checked ? { subKm: true, subZh: true } : { subKm: false })}
              />{' '}
              高棉语字幕（在中文下面）
            </label>
          </fieldset>
          <fieldset className="export-group">
            <legend>配音</legend>
            <label>
              <input type="checkbox" aria-label="高棉语配音" checked={choice.dubKm} onChange={(ev) => set({ dubKm: ev.target.checked })} />{' '}
              高棉语配音（Edge TTS）
            </label>
            <label title="取消后 H3 原音轨（含普通话）整条静音，成片里只有高棉语配音">
              <input type="checkbox" aria-label="保留原声底" checked={choice.bed} onChange={(ev) => set({ bed: ev.target.checked })} />{' '}
              保留原声底（原声压低垫在下面）
            </label>
            <label title="配音放到 H3 音轨里实际开口的位置（检测不到就用台词时间）">
              <input
                type="checkbox"
                aria-label="配音对齐实际说话"
                checked={choice.dubKm && choice.alignDetected}
                disabled={!choice.dubKm}
                onChange={(ev) => set({ alignDetected: ev.target.checked })}
              />{' '}
              配音对齐实际说话
            </label>
          </fieldset>
          <fieldset className="export-group export-group-card">
            <legend>片尾卡</legend>
            <label>
              <input type="checkbox" aria-label="片尾卡" checked={choice.endCard} onChange={(ev) => set({ endCard: ev.target.checked })} />{' '}
              成片末尾接片尾卡
            </label>
            {choice.endCard ? <EndCardPanel slug={slug} /> : <p className="muted">勾上后在这里改片尾卡的文字和时长（整个项目共用）。</p>}
          </fieldset>
        </div>
      </section>

      <section className="panel" aria-label="高棉语工具">
        <h3>高棉语工具</h3>
        <p className="muted">
          先翻译出高棉语字幕（只补缺的句子，AI 译稿要请母语者校对）；想让嘴型跟着高棉语配音的长短走，再按高棉语时长改写中文，改完要回导演台重新生成视频。
        </p>
        <div className="row">
          <button type="button" className="ghost" disabled={r.total === 0 || translate.isPending} onClick={doTranslate}>
            翻译高棉语字幕
          </button>
          <button type="button" className="ghost" disabled={r.total === 0 || fit.isPending} onClick={doFit}>
            按高棉语时长改写中文
          </button>
        </div>
      </section>

      <section className="panel" aria-label="成片">
        <h3>成片</h3>
        <ExportsList rows={exportsQ.data ?? []} episodeId={id} />
      </section>
    </div>
  );
}

export function ExportPage() {
  const { slug = '', episodeId = '' } = useParams();
  const id = Number(episodeId);
  // 换集时整个重建：选项按剧集读回来
  return <ExportBody key={id} slug={slug} id={id} />;
}
