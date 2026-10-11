// 单集工作台的剧本阶段：原文 / 梗概 + 剧本段落（都可折叠，收起时保持挂载不丢编辑），生成剧本、生成分镜都在这里。
// 角色、场景、风格在角色阶段；左栏有剧本大纲和角色列表。
// 第一次生成剧本 / 分镜前先问后端会删什么、覆盖什么；已经有了再点是「重新生成…」，打开对话框（可写修改意见、看影响预估、一定强制重出）。
import { useQueries } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api, errorText } from '../../api/client';
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
} from '../../api/queries';
import type { EpisodePatch, Job, RerunImpact, SceneBlock } from '../../api/types';
import type { ScriptBlockEdits } from './ShotGrid';
import { useConfirm } from '../../components/ConfirmDialog';
import { RegenerateDialog, impactLosesWork, impactText } from '../../components/RegenerateDialog';
import { EpisodeLoadError, nextActionProps, useNextAction } from '../../components/EpisodeHeader';
import { EditableFields, type FieldSpec, type FieldValues, type FieldsState, useUnsavedGuard } from '../../components/EditableFields';
import { useJobNotice } from '../../components/JobNotice';
import { EPISODE_STATUS_LABEL } from '../../format';
import { workspacePath } from './urls';

const EPISODE_FIELDS: FieldSpec[] = [
  { name: 'title', label: '本集标题' },
  { name: 'target_seconds', label: '目标时长（秒）', kind: 'number', step: '1', min: 5, max: 3600 },
  { name: 'source_text', label: '原文 / 梗概', kind: 'textarea', rows: 14 },
];

type Stage = 'script' | 'storyboard';
const STAGE_LABEL: Record<Stage, string> = { script: '生成剧本', storyboard: '生成分镜' };
const REGEN_LABEL: Record<Stage, string> = { script: '重新生成剧本', storyboard: '重新生成分镜' };
const UNSAVED_NOTE = '有未保存的修改，重新生成前会先保存';

function toEpisodePatch(c: FieldValues): EpisodePatch {
  const out: EpisodePatch = {};
  if (c.title !== undefined) out.title = c.title;
  if (c.source_text !== undefined) out.source_text = c.source_text;
  if (c.target_seconds !== undefined) out.target_seconds = Number(c.target_seconds);
  return out;
}

const ENDED = new Set(['done', 'failed', 'canceled', 'waiting_input']);

/**
 * 任务跑完了但其实什么都没重出：分镜 blocks_done 为 0（要重写的段落已经不在了，或输入没变全跳过），
 * 剧本结构和视觉设定都是 skipped。
 */
function nothingRegenerated(stage: Stage, jobs: Job[]): boolean {
  const r = (j: Job) => (j.result ?? {}) as Record<string, unknown>;
  if (stage === 'storyboard') return jobs.every((j) => r(j).blocks_done === 0);
  return jobs.every((j) => r(j).structure === 'skipped' && r(j).visual === 'skipped');
}

/** 盯住刚提交的剧本 / 分镜任务：全部真正跑完（不是跳过）时回调一次。和提示条共用 ['job', id] 缓存。 */
function useOnStageDone(
  run: { stage: Stage; ids: number[] } | null,
  onDone: (stage: Stage, jobs: Job[]) => void,
  onEnd: () => void,
) {
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
    if (ok) onDone(run.stage, jobs as Job[]);
    else onEnd();
    // key 概括了 jobs 里会影响结果的部分
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, run]);
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
  onRegen,
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
  /** 「重写本段分镜…」（这一段还没镜头时就是生成这一段） */
  onRegen?: () => void;
}) {
  return (
    <div className={`block${open ? ' is-open' : ''}`} data-testid={`block-${b.id}`}>
      <div className="block-line">
        <button type="button" className="block-row" aria-expanded={open} onClick={onToggle}>
          <span className="block-no">段落 {b.order + 1}</span>
          <span className="block-loc">{locationName}</span>
          <span className="block-sum">{b.text}</span>
          <span className="chev" aria-hidden="true" />
        </button>
        {onRegen && (
          <button type="button" className="sm ghost block-act" onClick={onRegen}>
            重写本段分镜…
          </button>
        )}
      </div>
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

export function ScriptStage({
  setScriptEdits,
}: {
  /** 登记段落编辑给工作台：镜头网格的「重写本段分镜…」先保存那一段 */
  setScriptEdits?: (edits: ScriptBlockEdits | null) => void;
} = {}) {
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
  const [checking, setChecking] = useState(false);
  const [openBlocks, setOpenBlocks] = useState<Set<number>>(new Set());
  const [srcOpen, setSrcOpen] = useState<boolean | null>(null);
  const [src, setSrc] = useState<FieldsState | null>(null);
  const [lastRun, setLastRun] = useState<{ stage: Stage; ids: number[] } | null>(null);
  // 打开着的「重新生成…」对话框：哪个阶段、哪几段（不给 = 整集）
  const [regen, setRegen] = useState<{ stage: Stage; blockIds?: number[]; scope: string } | null>(null);
  // 改过没保存的段落：和原文一起决定换页面前要不要问一句
  const [dirtyBlocks, setDirtyBlocks] = useState<Set<number>>(() => new Set());
  // 每个段落编辑框的当前内容：「保存并生成分镜」「重新生成分镜…」要先把没保存的改动存上
  const blockStates = useRef(new Map<number, FieldsState>());
  useUnsavedGuard(!!src?.dirty || dirtyBlocks.size > 0);
  // 登记给工作台的段落编辑：读最新的脏标记、调最新的保存函数
  const dirtyRef = useRef(dirtyBlocks);
  dirtyRef.current = dirtyBlocks;
  const saveRef = useRef<(ids: number[]) => Promise<boolean>>(async () => true);
  useEffect(() => {
    if (!setScriptEdits) return;
    setScriptEdits({ isDirty: (bid) => dirtyRef.current.has(bid), save: (bid) => saveRef.current([bid]) });
    return () => setScriptEdits(null);
  }, [setScriptEdits]);
  // 下一步是生成剧本 / 生成分镜时，对应按钮打上 data-next-action，阶段条的「下一步」触发它
  const nextAct = useNextAction(slug, ep.data, ['script']);
  const base = workspacePath(slug, id);

  useOnStageDone(
    lastRun,
    (stage, jobs) => {
      setLastRun(null);
      if (nothingRegenerated(stage, jobs)) {
        notice.info(
          stage === 'storyboard'
            ? '分镜没有重新生成：要重写的段落已经不在了（剧本可能刚重新生成过），或者输入没变。'
            : '剧本没有重新生成：输入没变。',
        );
      } else if (stage === 'script') {
        notice.info('剧本好了。下一步：生成分镜');
      } else {
        notice.info('分镜好了。下一步：确认角色定妆照，再生成首帧', { action: { label: '去角色', to: `${base}?stage=cast` } });
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
  // 还没有段落或原文时默认展开原文：要先写原文才能生成剧本
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

  /** 原文有没保存的修改：先保存（剧本按保存后的原文跑）。保存失败返回 false。 */
  const saveSource = async (): Promise<boolean> => {
    if (!src?.dirty) return true;
    if (!src.valid) return false;
    try {
      await patchEp.mutateAsync(toEpisodePatch(src.changed));
      return true;
    } catch (err) {
      notice.error(errorText(err));
      return false;
    }
  };

  /** 段落有没保存的修改：分镜先保存再按新段落跑（ids 不给 = 所有改过的段落）。保存失败返回 false（错误也显示在那一段里）。 */
  const saveBlocks = async (ids: number[] = dirtyBlockIds): Promise<boolean> => {
    try {
      for (const bid of ids.filter((x) => dirtyBlocks.has(x))) {
        const v = blockStates.current.get(bid)?.values;
        if (!v) continue;
        await patchBlock.mutateAsync({
          id: bid,
          text: v.text,
          location_id: v.location_id === '' ? null : Number(v.location_id),
        });
      }
      return true;
    } catch (err) {
      notice.error(errorText(err));
      return false;
    }
  };

  saveRef.current = saveBlocks;

  const track = (stage: Stage, ids: number[], label: string) => {
    notice.track(ids, label);
    setLastRun({ stage, ids });
  };

  /** 第一次生成（还没有段落 / 还没有镜头）：先保存，必要时问后端会覆盖什么，确认后提交（不强制）。 */
  const runStage = async (stage: Stage) => {
    notice.clear();
    if (stage === 'script' && !(await saveSource())) return;
    if (stage === 'storyboard' && !(await saveBlocks())) return;
    // 先问后端会动什么：分镜看已有镜头；剧本的视觉阶段会改整个项目的角色外观、场景外观和风格，
    // 所以只要项目里已经有角色 / 场景 / 风格就要问（还在加载时也问，宁可多问一次）
    const needImpact =
      stage === 'storyboard'
        ? e.shots.length > 0
        : e.scene_blocks.length > 0 ||
          !characters.data ||
          chars.length > 0 ||
          !locations.data ||
          locations.data.length > 0 ||
          style.data !== null;
    // 用户在确认框里看过影响并点了继续：带 confirm，后端才会删有选定首帧/视频或改过的镜头
    let confirmed = false;
    if (needImpact) {
      let im: RerunImpact;
      setChecking(true);
      try {
        im = await fetchRerunImpact(id, stage, false);
      } catch (err) {
        notice.error(errorText(err));
        return;
      } finally {
        setChecking(false);
      }
      if (!im.will_run) {
        notice.info(
          im.rules_outdated
            ? '输入没变，已经是最新的；分镜规则已更新，要按新规则重出请用「重新生成分镜…」'
            : '输入没变，已经是最新的',
        );
        return;
      }
      const body = impactText(im);
      if (body) {
        const ok = await confirm({
          title: stage === 'script' ? '生成剧本？' : '生成分镜？',
          body,
          confirmText: '继续生成',
          danger: im.shots_deleted > 0 || impactLosesWork(im),
        });
        if (!ok) return;
        confirmed = true;
      }
    }
    run.mutate(
      { stage, force: false, ...(confirmed ? { confirm: true } : {}) },
      {
        onSuccess: (r) => track(stage, r.job_ids, STAGE_LABEL[stage]),
        onError: (err) => notice.error(errorText(err)),
      },
    );
  };

  const openRegen = (stage: Stage, blockIds?: number[], scope = '整集') => {
    notice.clear();
    setRegen({ stage, blockIds, scope });
  };

  // 对话框里的未保存提示：剧本先保存原文，但会重新拆段落，没保存的段落修改会丢；分镜先保存段落
  const regenNote = (stage: Stage): string | undefined => {
    if (stage === 'storyboard') return dirtyBlockIds.length > 0 ? UNSAVED_NOTE : undefined;
    const parts: string[] = [];
    if (srcDirty) parts.push(UNSAVED_NOTE);
    if (dirtyBlockIds.length > 0) parts.push(`有 ${dirtyBlockIds.length} 个段落改了还没保存，重新拆段落时这些修改会丢掉`);
    return parts.length > 0 ? parts.join('；') : undefined;
  };
  const hasBlocks = e.scene_blocks.length > 0;
  const hasShots = e.shots.length > 0;

  return (
    <div className="stage-body script-page">
      <div className="toolbar">
        <span className="chip">
          {EPISODE_STATUS_LABEL[e.status] ?? e.status} · 已有 {e.shots.length} 个镜头
        </span>
        <span className="grow" aria-hidden="true" />
        {hasBlocks ? (
          <button
            type="button"
            disabled={busy || (srcDirty && !srcValid)}
            title="按原文重新拆段落，可以写修改意见"
            onClick={() => openRegen('script')}
          >
            重新生成剧本…
          </button>
        ) : (
          <button
            type="button"
            {...nextActionProps(!!nextAct)}
            disabled={busy || (srcDirty && !srcValid)}
            title={srcDirty ? '先保存原文的修改，再按新原文生成剧本' : undefined}
            onClick={() => runStage('script')}
          >
            {srcDirty ? '保存并生成剧本' : '生成剧本'}
          </button>
        )}
        {hasShots ? (
          <button
            type="button"
            {...nextActionProps(!!nextAct)}
            disabled={busy}
            title="按段落重新出整集的镜头，可以写修改意见"
            onClick={() => openRegen('storyboard')}
          >
            重新生成分镜…
          </button>
        ) : (
          <button
            type="button"
            {...nextActionProps(!!nextAct && hasBlocks)}
            disabled={busy || !hasBlocks}
            title={
              !hasBlocks
                ? '先生成剧本拆出段落'
                : dirtyBlockIds.length > 0
                  ? '先保存段落的修改，再按新段落生成分镜'
                  : undefined
            }
            onClick={() => runStage('storyboard')}
          >
            {dirtyBlockIds.length > 0 ? '保存并生成分镜' : '生成分镜'}
          </button>
        )}
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
        {e.scene_blocks.length === 0 && <p className="muted">还没有段落。点「生成剧本」由模型拆分。</p>}
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
                onRegen={() => openRegen('storyboard', [b.id], `第 ${b.order + 1} 段`)}
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

      <RegenerateDialog
        open={!!regen}
        onClose={() => setRegen(null)}
        stage={regen?.stage ?? 'script'}
        episodeId={id}
        blockIds={regen?.blockIds}
        scopeLabel={regen?.scope ?? '整集'}
        unsavedNote={regen ? regenNote(regen.stage) : undefined}
        beforeRun={() => (regen?.stage === 'storyboard' ? saveBlocks() : saveSource())}
        onSubmitted={(ids) => {
          if (regen) track(regen.stage, ids, REGEN_LABEL[regen.stage]);
        }}
      />
    </div>
  );
}
