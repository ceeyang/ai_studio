// 剧本页：只管剧本——原文 / 梗概 + 剧本段落（都可折叠，收起时保持挂载不丢编辑）。
// 角色、场景、风格在角色页（右上「本集角色」条进去）。重跑前先问后端会删什么，确认后才提交。
import { useQueries } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, enc, errorText } from '../api/client';
import {
  fetchRerunImpact,
  keys,
  useCharacters,
  useEpisode,
  useLocations,
  usePatchEpisode,
  usePatchSceneBlock,
  useRunStage,
  useStyle,
} from '../api/queries';
import type { Character, EpisodePatch, Job, RerunImpact, SceneBlock } from '../api/types';
import { useConfirm } from '../components/ConfirmDialog';
import { EpisodeHeader, EpisodeLoadError, nextActionProps, useNextAction } from '../components/EpisodeHeader';
import { EditableFields, type FieldSpec, type FieldValues, type FieldsState, useUnsavedGuard } from '../components/EditableFields';
import { useJobNotice } from '../components/JobNotice';
import { EPISODE_STATUS_LABEL } from '../format';

export { toCharacterPatch } from './script/CharacterCard';

const EPISODE_FIELDS: FieldSpec[] = [
  { name: 'title', label: '本集标题' },
  { name: 'target_seconds', label: '目标时长（秒）', kind: 'number', step: '1', min: 5, max: 3600 },
  { name: 'source_text', label: '原文 / 梗概', kind: 'textarea', rows: 14 },
];

type Stage = 'script' | 'storyboard';
const STAGE_LABEL: Record<Stage, string> = { script: '运行剧本', storyboard: '运行分镜' };

function toEpisodePatch(c: FieldValues): EpisodePatch {
  const out: EpisodePatch = {};
  if (c.title !== undefined) out.title = c.title;
  if (c.source_text !== undefined) out.source_text = c.source_text;
  if (c.target_seconds !== undefined) out.target_seconds = Number(c.target_seconds);
  return out;
}

/** 重跑影响写成一句话：「将重建段落 3、5 的镜头：删除 12 个镜头（其中 8 个有选定视频），保留 2 个手工镜头；会覆盖角色描述、场景氛围」 */
export function impactText(im: RerunImpact): string {
  const parts: string[] = [];
  if (im.shots_deleted > 0 || im.blocks.length > 0) {
    const where = im.blocks.length > 0 ? `将重建段落 ${im.blocks.join('、')} 的镜头` : '将重建镜头';
    let del = `删除 ${im.shots_deleted} 个镜头`;
    if (im.selected_videos > 0) del += `（其中 ${im.selected_videos} 个有选定视频）`;
    else if (im.selected_first_frames > 0) del += `（其中 ${im.selected_first_frames} 个有选定首帧）`;
    if (im.manual_kept > 0) del += `，保留 ${im.manual_kept} 个手工镜头`;
    parts.push(`${where}：${del}`);
  }
  if (im.overwrites.length > 0) parts.push(`会覆盖${im.overwrites.join('、')}`);
  return parts.join('；');
}

const ENDED = new Set(['done', 'failed', 'canceled', 'waiting_input']);

/** 盯住刚提交的剧本 / 分镜任务：全部真正跑完（不是跳过）时回调一次。和提示条共用 ['job', id] 缓存。 */
function useOnStageDone(run: { stage: Stage; ids: number[] } | null, onDone: (stage: Stage) => void, onEnd: () => void) {
  const results = useQueries({
    queries: (run?.ids ?? []).map((id) => ({
      queryKey: keys.job(id),
      queryFn: () => api.get<Job>(`/api/jobs/${id}`),
      refetchInterval: (q: { state: { data?: Job } }) => (q.state.data && ENDED.has(q.state.data.status) ? false : 5000),
    })),
  });
  const jobs = results.map((r) => r.data);
  const key = jobs.map((j) => (j ? `${j.id}:${j.status}` : '-')).join(',');
  useEffect(() => {
    if (!run || jobs.length === 0 || jobs.some((j) => !j || !ENDED.has(j.status))) return;
    const ok = jobs.every((j) => j!.status === 'done') && jobs.some((j) => j!.result?.skipped !== true);
    if (ok) onDone(run.stage);
    else onEnd();
    // key 概括了 jobs 里会影响结果的部分
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, run]);
}

/** 右上的「本集角色」条：头像、名字、有没有定妆照，点了去角色页。 */
function CastStrip({ to, chars }: { to: string; chars: Character[] }) {
  const withPortrait = chars.filter((c) => c.portrait_url).length;
  return (
    <Link className="cast-strip" to={to} title="去角色页：定妆照、三视图、场景和风格">
      <span className="cast-strip-label">本集角色</span>
      {chars.length === 0 ? (
        <span className="muted">还没有角色</span>
      ) : (
        <>
          <span className="cast-strip-list">
            {chars.slice(0, 6).map((c) => (
              <span key={c.id} className={`cast-chip${c.portrait_url ? '' : ' is-missing'}`}>
                {c.portrait_url ? (
                  <img src={c.portrait_url} alt={c.name} />
                ) : (
                  <span className="cast-avatar" aria-hidden="true">
                    {Array.from(c.name)[0] ?? '?'}
                  </span>
                )}
                <span>{c.name}</span>
              </span>
            ))}
            {chars.length > 6 && <span className="muted">等 {chars.length} 个</span>}
          </span>
          <span className="cast-strip-count">
            {withPortrait}/{chars.length} 有定妆照
          </span>
        </>
      )}
      <span className="chev" aria-hidden="true" />
    </Link>
  );
}

/** 一个段落：收起时一行摘要（编号 · 场景 · 正文开头），点开编辑；收起时编辑区只是藏起来，不丢内容。 */
function BlockRow({
  b,
  locationName,
  locationOptions,
  open,
  saving,
  error,
  onToggle,
  onSave,
  onDirtyChange,
  onValuesChange,
}: {
  b: SceneBlock;
  locationName: string;
  locationOptions: { value: string; label: string }[];
  open: boolean;
  saving: boolean;
  error: string | null;
  onToggle: () => void;
  onSave: (v: FieldValues) => void;
  onDirtyChange: (dirty: boolean) => void;
  onValuesChange: (state: FieldsState) => void;
}) {
  return (
    <div className={`block${open ? ' is-open' : ''}`} data-testid={`block-${b.id}`}>
      <button type="button" className="block-row" aria-expanded={open} onClick={onToggle}>
        <span className="block-no">段落 {b.order + 1}</span>
        <span className="block-loc">{locationName}</span>
        <span className="block-sum">{b.text}</span>
        <span className="chev" aria-hidden="true" />
      </button>
      <div className="block-edit" hidden={!open}>
        <EditableFields
          fields={[
            { name: 'location_id', label: '场景', kind: 'select', options: locationOptions },
            { name: 'text', label: '段落内容', kind: 'textarea', rows: 8 },
          ]}
          initial={{ location_id: b.location_id == null ? '' : String(b.location_id), text: b.text }}
          saving={saving}
          error={error}
          onSave={onSave}
          onDirtyChange={onDirtyChange}
          onValuesChange={onValuesChange}
        />
      </div>
    </div>
  );
}

export function ScriptPage() {
  const { slug = '', episodeId = '' } = useParams();
  const id = Number(episodeId);
  const ep = useEpisode(id);
  const locations = useLocations(slug);
  const characters = useCharacters(slug);
  const style = useStyle(slug);
  const patchEp = usePatchEpisode(id);
  const patchBlock = usePatchSceneBlock(id);
  const run = useRunStage(id);
  const confirm = useConfirm();
  const notice = useJobNotice();
  const [force, setForce] = useState(false);
  const [checking, setChecking] = useState(false);
  const [openBlocks, setOpenBlocks] = useState<Set<number>>(new Set());
  const [srcOpen, setSrcOpen] = useState<boolean | null>(null);
  const [src, setSrc] = useState<FieldsState | null>(null);
  const [lastRun, setLastRun] = useState<{ stage: Stage; ids: number[] } | null>(null);
  // 改过没保存的段落：和原文一起决定换页面前要不要问一句
  const [dirtyBlocks, setDirtyBlocks] = useState<Set<number>>(() => new Set());
  // 每个段落编辑框的当前内容：「保存并运行分镜」要先把没保存的改动存上
  const blockStates = useRef(new Map<number, FieldsState>());
  useUnsavedGuard(!!src?.dirty || dirtyBlocks.size > 0);
  // 下一步是运行剧本 / 运行分镜时，对应按钮是本页唯一的主按钮
  const nextAct = useNextAction(slug, ep.data, 'script');
  const base = `/projects/${enc(slug)}/episodes/${id}`;

  useOnStageDone(
    lastRun,
    (stage) => {
      setLastRun(null);
      if (stage === 'script') {
        notice.info('剧本好了。下一步：去角色页抽定妆照', { action: { label: '去角色页', to: `${base}/cast` } });
      } else {
        notice.info('分镜好了。下一步：去导演台生成首帧', { action: { label: '去导演台', to: `${base}/director` } });
      }
    },
    () => setLastRun(null),
  );

  if (ep.isLoading) return <p className="loading">加载中…</p>;
  if (ep.error || !ep.data) return <EpisodeLoadError slug={slug} error={ep.error} />;
  const e = ep.data;
  const locationOptions = [
    { value: '', label: '（未指定）' },
    ...(locations.data ?? []).map((l) => ({ value: String(l.id), label: l.name })),
  ];
  const chars = characters.data ?? [];
  const locName = new Map((locations.data ?? []).map((l) => [l.id, l.name]));
  // 还没有段落或原文时默认展开原文：要先写原文才能运行剧本
  const sourceOpen = srcOpen ?? (e.scene_blocks.length === 0 || !e.source_text.trim());
  const allOpen = e.scene_blocks.length > 0 && e.scene_blocks.every((b) => openBlocks.has(b.id));
  const srcDirty = !!src?.dirty;
  const srcValid = src?.valid ?? true;
  const busy = run.isPending || patchEp.isPending || patchBlock.isPending || checking;
  const dirtyBlockIds = e.scene_blocks.map((b) => b.id).filter((bid) => dirtyBlocks.has(bid));
  const toggleBlock = (bid: number) =>
    setOpenBlocks((prev) => {
      const next = new Set(prev);
      if (next.has(bid)) next.delete(bid);
      else next.add(bid);
      return next;
    });

  const runStage = async (stage: Stage) => {
    notice.clear();
    // 原文有没保存的修改：先保存（剧本按保存后的原文跑）
    if (stage === 'script' && src?.dirty) {
      if (!src.valid) return;
      try {
        await patchEp.mutateAsync(toEpisodePatch(src.changed));
      } catch (err) {
        notice.error(errorText(err));
        return;
      }
    }
    // 段落有没保存的修改：分镜先保存再按新段落跑；剧本会重新拆段落，这些修改会丢，下面确认时一起说
    if (stage === 'storyboard' && dirtyBlockIds.length > 0) {
      try {
        for (const bid of dirtyBlockIds) {
          const v = blockStates.current.get(bid)?.values;
          if (!v) continue;
          await patchBlock.mutateAsync({
            id: bid,
            text: v.text,
            location_id: v.location_id === '' ? null : Number(v.location_id),
          });
        }
      } catch (err) {
        notice.error(errorText(err));
        return;
      }
    }
    const discardBlocks = stage === 'script' ? dirtyBlockIds.length : 0;
    // 先问后端重跑会动什么：分镜看已有镜头；剧本的视觉阶段会改整个项目的角色外观、场景外观和风格，
    // 所以只要本集有段落、或者项目里已经有角色 / 场景 / 风格就要问（还在加载时也问，宁可多问一次）
    const needImpact =
      stage === 'storyboard'
        ? e.shots.length > 0
        : e.scene_blocks.length > 0 ||
          !characters.data ||
          chars.length > 0 ||
          !locations.data ||
          locations.data.length > 0 ||
          style.data !== null;
    if (needImpact) {
      let im: RerunImpact;
      setChecking(true);
      try {
        im = await fetchRerunImpact(id, stage, force);
      } catch (err) {
        notice.error(errorText(err));
        return;
      } finally {
        setChecking(false);
      }
      if (!im.will_run) {
        notice.info('输入没变，会跳过；要重做请勾强制重跑');
        return;
      }
      const parts = [impactText(im)].filter(Boolean);
      if (discardBlocks > 0) {
        parts.push(`有 ${discardBlocks} 个段落改了还没保存，重新拆段落时这些修改会丢掉`);
      }
      if (parts.length > 0) {
        const ok = await confirm({
          title: stage === 'script' ? '重新运行剧本？' : '重新运行分镜？',
          body: parts.join('；'),
          confirmText: '继续运行',
          danger: im.shots_deleted > 0 || discardBlocks > 0,
        });
        if (!ok) return;
      }
    }
    run.mutate(
      { stage, force },
      {
        onSuccess: (r) => {
          notice.track(r.job_ids, STAGE_LABEL[stage]);
          setLastRun({ stage, ids: r.job_ids });
        },
        onError: (err) => notice.error(errorText(err)),
      },
    );
  };

  return (
    <div className="page script-page">
      <EpisodeHeader slug={slug} ep={e} page="script" title="剧本">
        <span className="chip">
          {EPISODE_STATUS_LABEL[e.status] ?? e.status} · 已有 {e.shots.length} 个镜头
        </span>
        <CastStrip to={`${base}/cast`} chars={chars} />
      </EpisodeHeader>

      <div className="toolbar">
        <button
          type="button"
          {...nextActionProps(nextAct?.key === 'script')}
          disabled={busy || (srcDirty && !srcValid)}
          title={srcDirty ? '先保存原文的修改，再按新原文运行剧本' : undefined}
          onClick={() => runStage('script')}
        >
          {srcDirty ? '保存并运行剧本' : '运行剧本'}
        </button>
        <button
          type="button"
          {...nextActionProps(nextAct?.key === 'storyboard')}
          disabled={busy || e.scene_blocks.length === 0}
          title={
            e.scene_blocks.length === 0
              ? '先运行剧本生成段落'
              : dirtyBlockIds.length > 0
                ? '先保存段落的修改，再按新段落运行分镜'
                : undefined
          }
          onClick={() => runStage('storyboard')}
        >
          {dirtyBlockIds.length > 0 ? '保存并运行分镜' : '运行分镜'}
        </button>
        <span className="sep" aria-hidden="true" />
        <label>
          <input type="checkbox" checked={force} onChange={(ev) => setForce(ev.target.checked)} /> 强制重跑（输入没变也重新生成）
        </label>
      </div>
      {notice.view}

      <section className="panel script-pane script-solo" aria-label="剧本">
        <div className={`block src-box${sourceOpen ? ' is-open' : ''}`}>
          <button type="button" className="block-row" aria-expanded={sourceOpen} onClick={() => setSrcOpen(!sourceOpen)}>
            <span className="block-no">原文 / 梗概</span>
            <span className="block-loc">目标 {e.target_seconds} 秒</span>
            <span className="block-sum">
              {srcDirty ? '有未保存的修改 · ' : ''}
              {e.source_text.trim() ? `${e.source_text.length} 字 · ${e.source_text}` : '还没有原文'}
            </span>
            <span className="chev" aria-hidden="true" />
          </button>
          <div className="block-edit" hidden={!sourceOpen}>
            <EditableFields
              fields={EPISODE_FIELDS}
              initial={{ title: e.title, target_seconds: String(e.target_seconds), source_text: e.source_text }}
              saving={patchEp.isPending}
              error={patchEp.error ? errorText(patchEp.error) : null}
              onValuesChange={setSrc}
              onSave={(_v, c) => patchEp.mutate(toEpisodePatch(c))}
            />
          </div>
        </div>

        <div className="pane-head">
          <h3>剧本段落（{e.scene_blocks.length}）</h3>
          {e.scene_blocks.length > 0 && (
            <button
              type="button"
              className="ghost"
              onClick={() => setOpenBlocks(allOpen ? new Set() : new Set(e.scene_blocks.map((b) => b.id)))}
            >
              {allOpen ? '全部收起' : '全部展开'}
            </button>
          )}
        </div>
        {e.scene_blocks.length === 0 && <p className="muted">还没有段落。点"运行剧本"由模型拆分。</p>}
        <div className="blocks">
          {e.scene_blocks.map((b) => {
            const mine = patchBlock.variables?.id === b.id;
            return (
              <BlockRow
                key={b.id}
                b={b}
                locationName={(b.location_id != null && locName.get(b.location_id)) || '未指定场景'}
                locationOptions={locationOptions}
                open={openBlocks.has(b.id)}
                saving={patchBlock.isPending && mine}
                error={patchBlock.error && mine ? errorText(patchBlock.error) : null}
                onToggle={() => toggleBlock(b.id)}
                onDirtyChange={(d) =>
                  setDirtyBlocks((prev) => {
                    if (d === prev.has(b.id)) return prev;
                    const next = new Set(prev);
                    if (d) next.add(b.id);
                    else next.delete(b.id);
                    return next;
                  })
                }
                onValuesChange={(st) => blockStates.current.set(b.id, st)}
                onSave={(v) =>
                  patchBlock.mutate({
                    id: b.id,
                    text: v.text,
                    location_id: v.location_id === '' ? null : Number(v.location_id),
                  })
                }
              />
            );
          })}
        </div>
      </section>
    </div>
  );
}
