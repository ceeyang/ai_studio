// 单集工作台的成片阶段：就绪检查、导出选项、高棉语工具、成片列表。
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { errorText } from '../../api/client';
import {
  useBgm,
  useDeleteBgm,
  useEpisode,
  useExports,
  useFitKhmer,
  usePatchExport,
  useRunStage,
  useTranslateSubtitles,
  useUploadBgm,
} from '../../api/queries';
import type { Bgm, ExportOptions, ExportRow, ShotDetail } from '../../api/types';
import { EpisodeLoadError, nextActionProps } from '../../components/EpisodeHeader';
import { useJobNotice } from '../../components/JobNotice';
import { fmtTime } from '../../format';
import { EndCardPanel } from '../director/EndCardPanel';
import { qcIssues } from '../director/shotStatus';
import { selectedOf, slotMark, slotOf } from '../director/slots';
import { workspacePath } from './urls';

const SUBTITLE_TAG: Record<string, string> = { zh: '中文字幕', 'zh+km': '中高字幕' };

/** 旧成片（后端还没给 label 时）的标签：字幕、配音、原声底、对齐、片尾卡、时长。 */
export function exportTags(settings: Record<string, unknown>): string[] {
  const tags: string[] = [];
  const subs = SUBTITLE_TAG[String(settings.subtitles ?? '')];
  if (subs) tags.push(subs);
  if (settings.dub === 'km') tags.push('高棉语配音');
  if (settings.bed === false) tags.push('无原声底');
  if (settings.align === 'detected') tags.push('配音对齐说话');
  if (settings.music === true) tags.push('配乐');
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
  /** 配乐：垫这一集上传的背景音乐 */
  music: boolean;
  musicDb: number;
  fadeIn: number;
  fadeOut: number;
  /** 有台词时自动压低配乐 */
  duck: boolean;
}

const DEFAULT_CHOICE: Choice = {
  subZh: false,
  subKm: false,
  dubKm: false,
  endCard: false,
  bed: true,
  alignDetected: false,
  music: false,
  musicDb: -18,
  fadeIn: 1,
  fadeOut: 2,
  duck: true,
};

/** 数字选项的范围（和后端 ExportOptions 一致） */
const LIMITS: Partial<Record<keyof Choice, [number, number]>> = {
  musicDb: [-40, 6],
  fadeIn: [0, 10],
  fadeOut: [0, 10],
};

const clamp = (k: keyof Choice, v: number) => {
  const [lo, hi] = LIMITS[k] ?? [-Infinity, Infinity];
  return Math.min(hi, Math.max(lo, v));
};

export const OPTIONS_KEY = (episodeId: number) => `ai-studio.export-options.${episodeId}`;

function loadChoice(episodeId: number): Choice {
  try {
    const raw = localStorage.getItem(OPTIONS_KEY(episodeId));
    if (!raw) return DEFAULT_CHOICE;
    const saved = JSON.parse(raw) as Partial<Choice>;
    const out = { ...DEFAULT_CHOICE };
    for (const k of Object.keys(DEFAULT_CHOICE) as (keyof Choice)[]) {
      const v = saved[k];
      if (typeof DEFAULT_CHOICE[k] === 'boolean' && typeof v === 'boolean') (out as Record<string, unknown>)[k] = v;
      if (typeof DEFAULT_CHOICE[k] === 'number' && typeof v === 'number' && Number.isFinite(v))
        (out as Record<string, unknown>)[k] = clamp(k, v);
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

/** hasBgm：这一集有没有上传配乐（没有时不带配乐，免得后端拒绝） */
export function toOptions(c: Choice, hasBgm = false): ExportOptions {
  const out: ExportOptions = {
    subtitles: c.subKm ? 'zh+km' : c.subZh ? 'zh' : 'none',
    dub: c.dubKm ? 'km' : 'off',
    end_card: c.endCard,
    bed: c.bed,
    align: c.dubKm && c.alignDetected ? 'detected' : 'timeline',
  };
  if (c.music && hasBgm) {
    out.music = true;
    out.music_db = c.musicDb;
    out.music_fade_in = c.fadeIn;
    out.music_fade_out = c.fadeOut;
    out.music_duck = c.duck;
  }
  return out;
}

/** 数字输入：边打边存在本地（能清空、能先打负号），是范围内的数字才提交，失焦时回到当前值。 */
function NumField({
  label,
  value,
  range,
  step,
  disabled,
  onChange,
}: {
  label: string;
  value: number;
  range: [number, number];
  step: number;
  disabled: boolean;
  onChange(v: number): void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  return (
    <label className="inline-field">
      <span>{label}</span>
      <input
        type="number"
        aria-label={label}
        step={step}
        min={range[0]}
        max={range[1]}
        value={text}
        disabled={disabled}
        onChange={(ev) => {
          const raw = ev.target.value;
          setText(raw);
          const v = Number(raw);
          if (raw.trim() !== '' && Number.isFinite(v) && v >= range[0] && v <= range[1]) onChange(v);
        }}
        onBlur={() => setText(String(value))}
      />
    </label>
  );
}

/** 配乐：上传 / 替换 / 去掉、试听，和导出时怎么混（音量、淡入淡出、有台词时压低）。 */
function MusicGroup({
  episodeId,
  bgm,
  choice,
  set,
}: {
  episodeId: number;
  bgm: Bgm | null;
  choice: Choice;
  set: (patch: Partial<Choice>) => void;
}) {
  const upload = useUploadBgm(episodeId);
  const del = useDeleteBgm(episodeId);
  const error = upload.error ?? del.error;
  const on = choice.music && !!bgm;
  const num = (k: 'musicDb' | 'fadeIn' | 'fadeOut', label: string, step: number) => (
    <NumField label={label} value={choice[k]} range={LIMITS[k]!} step={step} disabled={!on} onChange={(v) => set({ [k]: v })} />
  );
  return (
    <fieldset className="export-group export-group-music">
      <legend>配乐</legend>
      {bgm ? (
        <div className="bgm-current">
          <audio data-testid="bgm-player" src={bgm.url} controls preload="metadata" />
          <span className="muted small" title={bgm.filename}>
            {bgm.filename}
          </span>
        </div>
      ) : (
        <p className="muted small">这一集还没有配乐。上传一首背景音乐（mp3 / wav / m4a / aac / ogg / flac），会循环铺满整集。</p>
      )}
      <div className="row">
        <label className={`btn sm${upload.isPending ? ' is-disabled' : ''}`}>
          {upload.isPending ? '上传中…' : bgm ? '替换配乐' : '上传配乐'}
          <input
            type="file"
            accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac"
            aria-label={bgm ? '替换配乐' : '上传配乐'}
            hidden
            disabled={upload.isPending}
            onChange={(ev) => {
              const f = ev.target.files?.[0];
              ev.target.value = '';
              if (f) upload.mutate(f, { onSuccess: () => set({ music: true }) });
            }}
          />
        </label>
        {bgm && (
          <button type="button" className="sm ghost" disabled={del.isPending} onClick={() => del.mutate()}>
            去掉配乐
          </button>
        )}
      </div>
      <label>
        <input type="checkbox" aria-label="成片垫配乐" checked={on} disabled={!bgm} onChange={(ev) => set({ music: ev.target.checked })} /> 成片垫配乐
      </label>
      <div className="row">
        {num('musicDb', '音量（dB）', 1)}
        {num('fadeIn', '淡入（秒）', 0.5)}
        {num('fadeOut', '淡出（秒）', 0.5)}
      </div>
      <label title="有台词的地方把配乐再压低 10 dB，前后各 0.3 秒过渡">
        <input type="checkbox" aria-label="有台词时自动压低" checked={choice.duck} disabled={!on} onChange={(ev) => set({ duck: ev.target.checked })} />{' '}
        有台词时自动压低
      </label>
      {error && <p className="error small">{errorText(error)}</p>}
    </fieldset>
  );
}

/** 就绪检查：几镜有选定视频、哪些缺、哪些视频过期、哪些选定的视频出片自检有问题（疑似跳切 / 开头没接上首帧）。 */
export function readiness(shots: ShotDetail[]) {
  const marks = shots.map((s) => ({ code: s.code, mark: slotMark(slotOf(s, 'video')) }));
  const missing = marks.filter((m) => m.mark === 'none').map((m) => m.code);
  const stale = marks.filter((m) => m.mark === 'stale').map((m) => m.code);
  const flagged = shots
    .map((s) => ({ code: s.code, issues: qcIssues(selectedOf(slotOf(s, 'video'))) }))
    .filter((x) => x.issues.length > 0)
    .map((x) => `${x.code}（${x.issues.map((q) => q.short).join('、')}）`);
  return { total: shots.length, ready: shots.length - missing.length, missing, stale, flagged };
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
  const bgmQ = useBgm(id);
  const bgm = bgmQ.data ?? null;
  const notice = useJobNotice();
  const [choice, setChoice] = useState<Choice>(() => loadChoice(id));
  useEffect(() => saveChoice(id, choice), [id, choice]);
  const set = (patch: Partial<Choice>) => setChoice((c) => ({ ...c, ...patch }));

  if (ep.isLoading) return <p className="loading">加载中…</p>;
  if (ep.error || !ep.data) return <EpisodeLoadError slug={slug} error={ep.error} />;
  const e = ep.data;
  const r = readiness(e.shots);
  const videosStage = `${workspacePath(slug, id)}?stage=videos`;
  const keyframesStage = `${workspacePath(slug, id)}?stage=keyframes`;
  const canExport = r.total > 0 && r.missing.length === 0;

  const doExport = () => {
    notice.clear();
    run.mutate(
      { stage: 'export', export: toOptions(choice, !!bgm) },
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
    <div className="stage-body export-page">
      <section className="panel" aria-label="就绪检查">
        <div className="row export-ready">
          <span data-testid="readiness" className={r.missing.length > 0 ? 'warn' : undefined}>
            {r.total === 0 ? '这一集还没有镜头' : `${r.ready}/${r.total} 镜有视频`}
            {r.stale.length > 0 && ` · ${r.stale.length} 镜视频已过期（${r.stale.join('、')}）`}
          </span>
          <span className="grow" aria-hidden="true" />
          {/* 视频齐了：成片阶段的下一步就是合成导出（或再导出一版），打上 data-next-action，阶段条的「下一步」触发它 */}
          <button
            type="button"
            {...nextActionProps(canExport)}
            disabled={!canExport || run.isPending}
            title={canExport ? undefined : '每个镜头都要有选定的视频才能合成'}
            onClick={doExport}
          >
            合成导出
          </button>
        </div>
        {r.flagged.length > 0 && (
          <p className="warn" data-testid="flagged-videos">
            选定的视频出片自检有问题：{r.flagged.join('、')}（时间点是疑似跳切）。合成后这些地方画面会突变或接不上，建议先到<Link to={videosStage}>视频阶段</Link>重拍或换一条。
          </p>
        )}
        {r.missing.length > 0 && (
          <p className="warn" data-testid="missing-videos">
            还缺视频：{r.missing.join('、')}。<Link to={videosStage}>去视频阶段</Link>
          </p>
        )}
        {r.total === 0 && (
          <p className="muted">
            先生成首帧和视频。<Link to={keyframesStage}>去首帧阶段</Link>
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
            <label title="取消后视频原声（含普通话）整条静音，成片里只有高棉语配音">
              <input type="checkbox" aria-label="保留原声底" checked={choice.bed} onChange={(ev) => set({ bed: ev.target.checked })} />{' '}
              保留原声底（原声压低垫在下面）
            </label>
            <label title="配音放到视频原声里实际开口的位置（检测不到就用台词时间）">
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
          <MusicGroup episodeId={id} bgm={bgm} choice={choice} set={set} />
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
          先翻译出高棉语字幕（只补缺的句子，AI 译稿要请母语者校对）；想让嘴型跟着高棉语配音的长短走，再按高棉语时长改写中文，改完要回视频阶段重新生成视频。
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

export function ExportStage() {
  const { slug = '', episodeId = '' } = useParams();
  const id = Number(episodeId);
  // 换集时整个重建：选项按剧集读回来
  return <ExportBody key={id} slug={slug} id={id} />;
}
