import { type ReactNode, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { errorText } from '../../api/client';
import {
  useDeleteShotRef,
  useEngines,
  useFirstFrameFromAsset,
  usePatchShot,
  useProject,
  useSelectCandidate,
  useShotRefsFromAssets,
  useUploadShotRef,
} from '../../api/queries';
import { type Asset, type DialogueLine, type EngineRow, type ShotDetail, type ShotPatch, TRANSITIONS, type Transition } from '../../api/types';
import { AssetPicker, aspectMismatch } from '../../components/AssetPicker';
import { useConfirm } from '../../components/ConfirmDialog';
import { confirmDiscard } from '../../components/confirmDiscard';
import { EditableFields, type FieldSpec, type FieldValues, type FieldsState, useUnsavedGuard } from '../../components/EditableFields';
import { WebConversation, isWebImageEngine } from '../../components/WebConversation';
import { useMediaGroup } from '../../components/MediaViewer';
import { engineLabel, engineOptionDisabled, engineOptionText } from '../../format';
import { ArchivedCandidates } from './ArchivedCandidates';
import { CandidateGrid } from './CandidateGrid';
import { DialogueEditor } from './DialogueEditor';
import { type GenStage, CELL, activeJob, activeText, failedJob, firstFrameReady, genLabel, hasSelected, waitsPrevVideo } from './shotStatus';
import { SLOT_LABEL } from './slots';

const TRANSITION_LABEL: Record<Transition, string> = {
  hard_cut: '硬切',
  'dissolve_0.5': '叠化 0.5 秒',
  'white_flash_0.2': '白闪 0.2 秒',
  'black_dip_0.3': '黑场 0.3 秒',
  'fade_out_1.0': '淡出 1 秒',
};

const SLOT_ORDER = ['first_frame', 'end_frame', 'video'];
const TEXT_FIELDS = ['action', 'shot_size', 'camera_move', 'image_prompt', 'motion_prompt', 'audio_prompt'] as const;

/** 留空 = 跟项目的引擎；知道项目现在用哪个时写成「默认（Z-Image 快速）」 */
function engineOptions(rows: EngineRow[] | undefined, current?: string) {
  const label = current ? `默认（${engineLabel(rows?.find((r) => r.name === current) ?? current)}）` : '项目默认';
  return [{ value: '', label }, ...(rows ?? []).map((r) => ({ value: r.name, label: engineOptionDisabled(r) ? engineOptionText(r) : engineLabel(r) }))];
}

function shotFields(
  image: EngineRow[] | undefined,
  video: EngineRow[] | undefined,
  defaults: { image?: string; video?: string } = {},
): FieldSpec[] {
  return [
    { name: 'action', label: '动作', kind: 'textarea', rows: 2 },
    { name: 'shot_size', label: '景别' },
    { name: 'camera_move', label: '运镜' },
    { name: 'duration', label: '时长（秒）', kind: 'number', step: '0.1' },
    {
      name: 'transition_out',
      label: '出场转场',
      kind: 'select',
      options: TRANSITIONS.map((t) => ({ value: t, label: TRANSITION_LABEL[t] })),
    },
    { name: 'image_engine', label: '图像引擎', kind: 'select', options: engineOptions(image, defaults.image) },
    { name: 'video_engine', label: '视频引擎', kind: 'select', options: engineOptions(video, defaults.video) },
    { name: 'image_prompt', label: '图像提示词（英文）', kind: 'textarea', rows: 6, advanced: true },
    { name: 'motion_prompt', label: '运动提示词（英文）', kind: 'textarea', rows: 3, advanced: true },
    { name: 'audio_prompt', label: '声音提示词（英文）', kind: 'textarea', rows: 3, advanced: true },
  ];
}

/** 只把改过的字段转成 PATCH 体：时长转数字，引擎留空表示回到项目默认（null）。 */
export function toShotPatch(changed: FieldValues): ShotPatch {
  const out: ShotPatch = {};
  for (const [k, v] of Object.entries(changed)) {
    if (k === 'duration') out.duration = Number(v);
    else if (k === 'image_engine' || k === 'video_engine') out[k] = v === '' ? null : v;
    else if (k === 'transition_out') out.transition_out = v as Transition;
    else if ((TEXT_FIELDS as readonly string[]).includes(k)) out[k as (typeof TEXT_FIELDS)[number]] = v;
  }
  return out;
}

/**
 * 「接上一镜」开关：首帧取上一镜选定视频的最后一帧，背景、人物位置接得上。第一镜不能开。
 * 分镜建议这一镜接上一镜（同一场景、动作直接延续）而还没打开时，给一条建议：一键打开，或「不用」去掉建议。
 */
function ContinueToggle({
  shot,
  saving,
  onChange,
  onDismiss,
}: {
  shot: ShotDetail;
  saving: boolean;
  onChange(on: boolean): void;
  onDismiss(): void;
}) {
  const first = shot.order === 0;
  const cp = shot.continue_prev;
  const suggest = !!shot.suggest_continue && !shot.continue_from_prev && !first;
  return (
    <section className="shot-continue" aria-label="接上一镜">
      {suggest && (
        <p className="small notice" data-testid="continue-suggest">
          分镜建议这一镜接上一镜（和上一镜同一场景、动作直接延续）：首帧直接取上一镜视频的最后一帧，画面无缝接上。{' '}
          <button type="button" className="sm soft" disabled={saving} onClick={() => onChange(true)}>
            接上一镜
          </button>{' '}
          <button type="button" className="sm ghost" disabled={saving} onClick={onDismiss}>
            不用
          </button>
        </p>
      )}
      <label title={first ? '第一镜没有上一镜可接' : '首帧不出图，直接取上一镜选定视频的最后一帧'}>
        <input
          type="checkbox"
          aria-label="接上一镜"
          checked={shot.continue_from_prev}
          disabled={saving || (first && !shot.continue_from_prev)}
          onChange={(ev) => onChange(ev.target.checked)}
        />{' '}
        接上一镜
      </label>
      {shot.continue_from_prev && cp && (
        <p className={`small${cp.has_video ? ' muted' : ' warn'}`} data-testid="continue-hint">
          {cp.has_video
            ? `首帧取 ${cp.code} 选定视频的最后一帧；${cp.code} 换了视频，这一镜的首帧会过期重取。`
            : `等上一镜视频：${cp.code} 还没有选定视频，先出 ${cp.code} 的视频（一起出视频时会自动按顺序接上）。`}
        </p>
      )}
      {shot.continue_from_prev && !cp && <p className="small warn">这是第一镜，没有上一镜可接，关掉这个开关。</p>}
    </section>
  );
}

/** 引擎最多吃几张参考图（引擎表里的 caps.max_refs）；不知道这个引擎时 null */
export function engineMaxRefs(rows: EngineRow[] | undefined, name: string | undefined): number | null {
  const row = name ? rows?.find((r) => r.name === name) : undefined;
  const n = row?.caps?.max_refs;
  return typeof n === 'number' ? n : null;
}

/** 首帧参考图的两条提示：参考图上限装不下所有定妆照（哪几个人的脸没锁上）；镜头所在场景还没有场景图。 */
function RefHints({
  shot,
  maxRefs,
  missingLocationImage,
}: {
  shot: ShotDetail;
  /** 按抽屉里正在选的图像引擎（草稿）算的参考图上限 */
  maxRefs: number | null | undefined;
  missingLocationImage?: { name: string; to: string } | null;
}) {
  if (shot.continue_from_prev) return null; // 首帧取上一镜尾帧，不打包参考图
  const dropped = shot.first_frame_portraits_dropped ?? [];
  const showLoc = !!missingLocationImage && maxRefs !== 0;
  if (dropped.length === 0 && !showLoc) return null;
  return (
    <>
      {dropped.length > 0 && (
        <p className="warn small" data-testid="portraits-dropped">
          这个镜头的图像引擎最多 {shot.image_max_refs} 张参考图，装不下所有出场角色的定妆照：{dropped.join('、')}
          没带上，这几个人的脸只能按文字出。要锁脸就减少出场角色，或换参考图上限更大的引擎（如 H3 图像）。
        </p>
      )}
      {showLoc && missingLocationImage && (
        <p className="muted small" data-testid="location-image-missing">
          {`「${missingLocationImage.name}」`}这个场景还没有场景图，去角色阶段的设定里生成 · <Link to={missingLocationImage.to}>去生成场景图</Link>
        </p>
      )}
    </>
  );
}

/**
 * 构图参考：每镜最多 2 张构图 / 动作参考图，首帧参考图里排在场景图后面；引擎不吃参考图时提示用不上。
 * 可以上传，也可以从资产里选（角色定妆照、场景图、别的镜头的首帧……，复制一份过来）。
 * maxRefs 按抽屉里正在选的图像引擎算（还没保存也算），选了吃参考图的引擎就不再提示。
 */
function ShotRefs({ shot, episodeId, maxRefs }: { shot: ShotDetail; episodeId: number; maxRefs: number | null | undefined }) {
  const upload = useUploadShotRef(episodeId);
  const del = useDeleteShotRef(episodeId);
  const fromAssets = useShotRefsFromAssets(episodeId);
  const [picking, setPicking] = useState(false);
  const refs = shot.shot_refs ?? [];
  const full = refs.length >= MAX_SHOT_REFS;
  const viewRef = useMediaGroup(
    refs.map((c, i) => ({
      url: c.url,
      alt: `构图参考 ${i + 1}`,
      title: `${shot.code} 构图参考 ${i + 1}${typeof c.params?.from === 'string' ? ` · 来自 ${c.params.from}` : ''}`,
    })),
    { title: `${shot.code} 构图参考` },
  );
  const error = upload.error ?? del.error;
  return (
    <section className="shot-refs" aria-label="构图参考">
      <h4>构图参考</h4>
      <p className="muted small">
        上传 1–2 张构图或动作参考图（比如同样机位的剧照），也可以从已有的角色、场景、道具、镜头图里选，出首帧时排在场景图后面一起给引擎。
      </p>
      {maxRefs === 0 && (
        <p className="warn small" data-testid="refs-unsupported">
          这个镜头的图像引擎不吃参考图，构图参考用不上；要用就在下面「图像引擎」换成 H3 图像或 ChatGPT / Gemini 网页。
        </p>
      )}
      {shot.continue_from_prev && shot.continue_prev && <p className="muted small">开了「接上一镜」：首帧直接取上一镜尾帧，构图参考用不上。</p>}
      {refs.length > 0 && (
        <div className="shot-ref-list" role="list" aria-label="构图参考图">
          {refs.map((c, i) => (
            <figure key={c.id} role="listitem" className="shot-ref">
              <button type="button" className="media-thumb" aria-label={`看大图：构图参考 ${i + 1}`} onClick={() => viewRef(i)}>
                <img src={c.url} alt={`构图参考 ${i + 1}`} loading="lazy" />
              </button>
              <figcaption>
                <span className="muted small" title={typeof c.params?.from === 'string' ? `来自 ${c.params.from}` : undefined}>
                  构图参考 {i + 1}
                </span>
                <button
                  type="button"
                  className="sm ghost"
                  aria-label={`删除构图参考 ${i + 1}`}
                  disabled={del.isPending}
                  onClick={() => del.mutate({ id: shot.id, candidateId: c.id })}
                >
                  删除
                </button>
              </figcaption>
            </figure>
          ))}
        </div>
      )}
      <div className="shot-ref-actions">
        <label className={`btn sm${upload.isPending || full ? ' is-disabled' : ''}`} title={full ? `每镜最多 ${MAX_SHOT_REFS} 张，先删一张` : undefined}>
          {upload.isPending ? '上传中…' : full ? `已有 ${MAX_SHOT_REFS} 张` : '上传构图参考'}
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            aria-label="上传构图参考"
            hidden
            disabled={upload.isPending || full}
            onChange={(ev) => {
              const f = ev.target.files?.[0];
              ev.target.value = '';
              if (f) upload.mutate({ id: shot.id, file: f });
            }}
          />
        </label>
        <button
          type="button"
          className="sm"
          disabled={full || fromAssets.isPending}
          title={full ? `每镜最多 ${MAX_SHOT_REFS} 张，先删一张` : '从本项目已有的角色、场景、道具、镜头图里选'}
          onClick={() => {
            fromAssets.reset();
            setPicking(true);
          }}
        >
          从资产里选
        </button>
      </div>
      {error && <p className="error small">{errorText(error)}</p>}
      {picking && (
        <AssetPicker
          episodeId={episodeId}
          title={`给 ${shot.code} 选构图参考`}
          subtitle={`还能加 ${MAX_SHOT_REFS - refs.length} 张；选中的图复制一份给这个镜头，原图删了也不影响。`}
          max={MAX_SHOT_REFS - refs.length}
          confirmLabel="用作构图参考"
          busy={fromAssets.isPending}
          error={fromAssets.error}
          onClose={() => setPicking(false)}
          onConfirm={(picks) => fromAssets.mutate({ id: shot.id, assets: picks }, { onSuccess: () => setPicking(false) })}
        />
      )}
    </section>
  );
}

/**
 * 「用已有图片当首帧」：从资产里挑一张（别的镜头的首帧、某个视频的尾帧、场景图……）加成这一镜的首帧候选，
 * 默认设为选定。画幅和项目不一样时提示，可以居中裁成项目画幅。
 */
function FirstFrameFromAsset({ shot, episodeId, aspect }: { shot: ShotDetail; episodeId: number; aspect?: string }) {
  const use = useFirstFrameFromAsset(episodeId);
  const [picking, setPicking] = useState(false);
  const [crop, setCrop] = useState(true);
  const [select, setSelect] = useState(true);
  const own = new Set(shot.slots.filter((s) => s.kind === 'first_frame').flatMap((s) => s.candidates.map((c) => `cand-${c.id}`)));
  const footer = (picked: Asset[]) => {
    const a = picked[0];
    const off = !!a && a.media === 'image' && aspectMismatch(a, aspect);
    return (
      <div className="asset-options">
        {off && (
          <p className="warn small" data-testid="aspect-mismatch">
            这张图是 {a.width}×{a.height}，和项目画幅 {aspect} 不一样，直接当首帧出视频会被拉伸或加黑边。
          </p>
        )}
        {a?.media === 'video' && <p className="muted small">从视频里截一帧；视频和项目画幅一样时不会裁。</p>}
        {(off || a?.media === 'video') && (
          <label className="small">
            <input type="checkbox" checked={crop} onChange={(ev) => setCrop(ev.target.checked)} /> 居中裁切成项目画幅（{aspect}）
          </label>
        )}
        <label className="small">
          <input type="checkbox" checked={select} onChange={(ev) => setSelect(ev.target.checked)} /> 设为这一镜选定的首帧
        </label>
      </div>
    );
  };
  return (
    <div className="ff-from-asset">
      <button
        type="button"
        className="sm"
        disabled={shot.continue_from_prev || use.isPending}
        title={
          shot.continue_from_prev
            ? '开了「接上一镜」：首帧取上一镜尾帧'
            : '从本项目已有的图片里选一张当首帧（比如别的镜头的首帧、某个视频的尾帧、场景图）'
        }
        onClick={() => {
          use.reset();
          setPicking(true);
        }}
      >
        用已有图片当首帧
      </button>
      {picking && (
        <AssetPicker
          episodeId={episodeId}
          title={`给 ${shot.code} 选一张首帧`}
          subtitle="选中的图复制一份加进这一镜的首帧候选（来源记成「已有图片」），出视频就从它开始。"
          max={1}
          confirmLabel="用作首帧"
          exclude={(a) => own.has(a.id)}
          footer={footer}
          busy={use.isPending}
          error={use.error}
          onClose={() => setPicking(false)}
          onConfirm={(picks, assets) => {
            const a = assets[0];
            const needCrop = crop && (a.media === 'video' || aspectMismatch(a, aspect));
            use.mutate({ shotId: shot.id, ...picks[0], crop: needCrop, select }, { onSuccess: () => setPicking(false) });
          }}
        />
      )}
    </div>
  );
}

/** 每镜最多几张构图参考图（和后端 domain/refs.py 的 MAX_SHOT_REFS 一致） */
export const MAX_SHOT_REFS = 2;

export interface ShotDrawerProps {
  shot: ShotDetail;
  episodeId: number;
  /** 生成任务（首帧/视频）提交后、还没拿到响应期间为 true：生成按钮禁用，防止重复点击提交两次。 */
  generating?: boolean;
  onClose(): void;
  /** 已有选定结果时是「重拍」（父组件强制提交）。saved：先把改过的提示词 / 对白保存了；
   *  opts.newConversation：首帧用网页引擎出、勾了「新开对话」（这次不续接之前的网页会话） */
  onGenerate(stage: GenStage, saved?: boolean, opts?: { newConversation?: boolean }): void;
  /** 候选数：和导演台工具栏共用一份 */
  count?: number;
  onCountChange?(n: number): void;
  /** 导演台的提示条：抽屉开着时显示在抽屉里，免得被遮罩挡住 */
  notice?: ReactNode;
  /** 提示词或对白有没保存的修改变化时通知（切阶段前工作台要先问） */
  onDirtyChange?(dirty: boolean): void;
  /** 首帧缺少或过期时点了视频那一格的「先出首帧 / 先重拍首帧」：切到首帧阶段（抽屉留着） */
  onNeedFirstFrame?(): void;
  /** 项目 slug：引擎下拉框的「默认（…）」写成项目现在用的引擎 */
  slug?: string;
  /** 首帧阶段、镜头所在场景还没有场景图时给：场景名和去生成的地址（角色阶段的设定） */
  missingLocationImage?: { name: string; to: string } | null;
}

const STAGES: GenStage[] = ['keyframes', 'videos'];

export function ShotDrawer({
  shot,
  episodeId,
  onClose,
  onGenerate,
  generating = false,
  count = 1,
  onCountChange,
  notice,
  onDirtyChange,
  onNeedFirstFrame,
  slug,
  missingLocationImage,
}: ShotDrawerProps) {
  const engines = useEngines();
  // 留空 = 跟项目的引擎（设置页只管新项目的默认，镜头上没选时就是项目的）
  const project = useProject(slug ?? '');
  const imageDefault = project.data?.image_engine;
  const aspect = project.data?.aspect;
  const videoDefault = project.data?.video_engine;
  const patch = usePatchShot(episodeId);
  const patchDialogue = usePatchShot(episodeId);
  const select = useSelectCandidate(episodeId);
  const patchContinue = usePatchShot(episodeId);
  const confirm = useConfirm();
  const [fields, setFields] = useState<FieldsState | null>(null);
  const [dialogueDirty, setDialogueDirty] = useState(false);
  const [dialogueDraft, setDialogueDraft] = useState<DialogueLine[] | null>(null);
  const [saving, setSaving] = useState(false);
  // 首帧用网页引擎出：这次不续接之前的网页对话（提交后复位）
  const [newConv, setNewConv] = useState(false);
  const box = useRef<HTMLElement>(null);
  const frameBtn = useRef<HTMLButtonElement>(null);
  const fieldsDirty = !!fields?.dirty;
  // 提示词或对白有没保存的修改：生成按钮先保存（生成按旧内容跑就白跑了）
  const unsaved = fieldsDirty || dialogueDirty;
  // 关抽屉另有确认；换到别的页面由全站的离开拦截问
  useUnsavedGuard(unsaved);
  const latest = useRef({ dirty: false, onClose });
  latest.current = { dirty: fieldsDirty || dialogueDirty, onClose };
  const dirtyCb = useRef(onDirtyChange);
  dirtyCb.current = onDirtyChange;
  useEffect(() => {
    dirtyCb.current?.(unsaved);
  }, [unsaved]);
  useEffect(() => () => dirtyCb.current?.(false), []);

  /** 想关抽屉：有没保存的修改先问一句。 */
  const requestClose = useRef(async () => {
    if (latest.current.dirty) {
      if (!(await confirmDiscard(confirm, '这个镜头', '关掉抽屉'))) return;
    }
    latest.current.onClose();
  }).current;

  // 打开时把焦点放进抽屉，关掉后还给原来的元素；Esc 关闭（上面叠着确认框时归确认框管）
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    box.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || document.querySelector('.modal-scrim')) return;
      e.preventDefault();
      void requestClose();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      prev?.focus?.();
    };
  }, [requestClose]);

  /** 提示词或对白改了没保存：一次 PATCH 一起先保存，保存失败就不生成。 */
  const generate = async (stage: GenStage) => {
    const saveFirst = unsaved;
    if (saveFirst) {
      if (fieldsDirty && !fields?.valid) return;
      const body: ShotPatch = fieldsDirty && fields ? toShotPatch(fields.changed) : {};
      if (dialogueDirty && dialogueDraft) body.dialogue = dialogueDraft;
      setSaving(true);
      try {
        await patch.mutateAsync({ id: shot.id, ...body });
      } catch {
        return; // 错误显示在表单下面
      } finally {
        setSaving(false);
      }
    }
    if (stage === 'keyframes' && newConv && webFrame) {
      onGenerate(stage, saveFirst, { newConversation: true });
      setNewConv(false);
    } else if (saveFirst) onGenerate(stage, true);
    else onGenerate(stage);
  };

  const initial: FieldValues = {
    action: shot.action,
    shot_size: shot.shot_size,
    camera_move: shot.camera_move,
    duration: String(shot.duration),
    transition_out: shot.transition_out,
    image_engine: shot.image_engine ?? '',
    video_engine: shot.video_engine ?? '',
    image_prompt: shot.image_prompt,
    motion_prompt: shot.motion_prompt,
    audio_prompt: shot.audio_prompt,
  };
  const slots = shot.slots
    .filter((s) => SLOT_ORDER.includes(s.kind))
    .sort((a, b) => SLOT_ORDER.indexOf(a.kind) - SLOT_ORDER.indexOf(b.kind));
  const waiting = waitsPrevVideo(shot);
  // 参考图上限按抽屉里正在选的图像引擎算（还没保存也算）；和保存的一样时用后端算好的
  const savedImage = shot.image_engine ?? '';
  const draftImage = fields?.values.image_engine ?? savedImage;
  const imageMaxRefs =
    draftImage === savedImage ? shot.image_max_refs : engineMaxRefs(engines.data?.image, draftImage || imageDefault);
  // 首帧这次要用的图像引擎（镜头上没选就是项目的）：网页引擎时才有「新开对话」
  const frameEngine = draftImage || imageDefault;
  const webFrame = isWebImageEngine(frameEngine, engines.data?.image);

  const failures = STAGES.map((st) => ({ st, job: failedJob(shot, st) })).filter((x) => x.job && !activeJob(shot, x.st));

  return (
    <>
      <div className="drawer-scrim" data-testid="drawer-scrim" aria-hidden="true" onMouseDown={() => void requestClose()} />
      <aside
        ref={box}
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`镜头 ${shot.code} 详情`}
        tabIndex={-1}
      >
        <header className="drawer-head">
          <h3>
            镜头 <span className="code">{shot.code}</span>
          </h3>
          <button type="button" className="ghost" onClick={() => void requestClose()}>
            关闭
          </button>
        </header>
        <div className="gen-row">
          <div className="row">
            {STAGES.map((st) => {
              const label = CELL[st === 'keyframes' ? 'first_frame' : 'video'].label;
              const what = genLabel(shot, st);
              const active = activeJob(shot, st);
              // 接上一镜、上一镜还没有视频：两格都要等
              if (!active && waiting) {
                return (
                  <button key={st} type="button" className="soft" disabled title="接上一镜：首帧要取上一镜视频的最后一帧，先出上一镜的视频">
                    等上一镜视频
                  </button>
                );
              }
              // 和镜头卡片一致：首帧缺少或过期时不拿它出视频，视频这一格先去出 / 重拍首帧
              if (st === 'videos' && !active && !firstFrameReady(shot)) {
                const need = hasSelected(shot, 'keyframes') ? '先重拍首帧' : '先出首帧';
                return (
                  <button
                    key={st}
                    type="button"
                    className="soft"
                    title="首帧缺少或已过期，出视频前先在首帧阶段把它出好"
                    onClick={() => {
                      onNeedFirstFrame?.();
                      frameBtn.current?.focus();
                    }}
                  >
                    {need}
                  </button>
                );
              }
              return (
                <button
                  key={st}
                  ref={st === 'keyframes' ? frameBtn : undefined}
                  type="button"
                  className={unsaved ? 'primary' : 'soft'}
                  disabled={generating || saving || !!active || (fieldsDirty && !fields?.valid)}
                  onClick={() => void generate(st)}
                >
                  {active ? `${label}${activeText(active)}` : unsaved ? `保存并${what}` : what}
                </button>
              );
            })}
          </div>
          <div className="row gen-opts">
            <label>
              候选数{' '}
              <select aria-label="候选数" value={count} onChange={(ev) => onCountChange?.(Number(ev.target.value))}>
                {[1, 2, 3, 4].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <WebConversation
            scopeKind="shot"
            scopeId={shot.id}
            engine={frameEngine}
            newConversation={newConv}
            onNewConversationChange={setNewConv}
            label="首帧"
          />
          {failures.map(({ st, job }) => (
            <p key={st} className="error">
              {`${CELL[st === 'keyframes' ? 'first_frame' : 'video'].label}上次生成失败：${job?.error || '没有错误信息'}`}
            </p>
          ))}
        </div>
        {notice}

        {/* 候选在最前：打开抽屉多半是来挑首帧 / 视频的；英文提示词收在「高级」里 */}
        {slots.length === 0 && <p className="muted">还没有生成任何首帧或视频。</p>}
        {slots.map((sl) => (
          <section key={sl.id}>
            <h4>
              {SLOT_LABEL[sl.kind] ?? sl.kind}候选
              {sl.stale && <span className="badge stale">已过期</span>}
            </h4>
            {sl.stale && sl.stale_reason && <p className="muted stale-reason">过期原因：{sl.stale_reason}</p>}
            <CandidateGrid slot={sl} shotCode={shot.code} selecting={select.isPending} onSelect={(cid) => select.mutate(cid)} />
          </section>
        ))}
        {select.error && <p className="error">{errorText(select.error)}</p>}
        <FirstFrameFromAsset shot={shot} episodeId={episodeId} aspect={aspect} />
        <ArchivedCandidates episodeId={episodeId} shotId={shot.id} />

        <ContinueToggle
          shot={shot}
          saving={patchContinue.isPending}
          onChange={(on) => patchContinue.mutate({ id: shot.id, continue_from_prev: on })}
          onDismiss={() => patchContinue.mutate({ id: shot.id, suggest_continue: false })}
        />
        {patchContinue.error && <p className="error">{errorText(patchContinue.error)}</p>}

        <RefHints shot={shot} maxRefs={imageMaxRefs} missingLocationImage={missingLocationImage} />

        <ShotRefs shot={shot} episodeId={episodeId} maxRefs={imageMaxRefs} />

        <section>
          <h4>提示词与参数</h4>
          <EditableFields
            fields={shotFields(engines.data?.image, engines.data?.video, { image: imageDefault, video: videoDefault })}
            initial={initial}
            saving={patch.isPending}
            error={patch.error ? errorText(patch.error) : null}
            onValuesChange={setFields}
            onSave={(_v, changed) => patch.mutate({ id: shot.id, ...toShotPatch(changed) })}
          />
          {Object.keys(shot.placeholder_names ?? {}).length > 0 && (
            <p className="muted small" data-testid="placeholder-legend">
              外貌占位符：
              {Object.entries(shot.placeholder_names ?? {})
                .map(([tok, name]) => `${tok} = ${name}`)
                .join('；')}
            </p>
          )}
          {shot.image_prompt_resolved && (
            <details className="muted small resolved-prompt">
              <summary>{'图像提示词里的 {{char:N}} 是角色外貌占位符，出图时按景别换成角色当前造型：'}</summary>
              <p>{shot.image_prompt_resolved}</p>
            </details>
          )}
        </section>

        {shot.dialogue.length > 0 && (
          <section>
            <h4>对白与字幕</h4>
            <DialogueEditor
              dialogue={shot.dialogue}
              saving={patchDialogue.isPending}
              error={patchDialogue.error ? errorText(patchDialogue.error) : null}
              onDirtyChange={setDialogueDirty}
              onLinesChange={(lines) => setDialogueDraft(lines)}
              onSave={(lines) => patchDialogue.mutate({ id: shot.id, dialogue: lines })}
            />
          </section>
        )}

      </aside>
    </>
  );
}
