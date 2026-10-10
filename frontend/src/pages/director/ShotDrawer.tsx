import { type ReactNode, useEffect, useRef, useState } from 'react';
import { errorText } from '../../api/client';
import { useEngines, usePatchShot, useSelectCandidate } from '../../api/queries';
import { type DialogueLine, type EngineRow, type ShotDetail, type ShotPatch, TRANSITIONS, type Transition } from '../../api/types';
import { useConfirm } from '../../components/ConfirmDialog';
import { EditableFields, type FieldSpec, type FieldValues, type FieldsState, useUnsavedGuard } from '../../components/EditableFields';
import { engineLabel } from '../../format';
import { CandidateGrid } from './CandidateGrid';
import { DialogueEditor } from './DialogueEditor';
import { type GenStage, CELL, activeJob, activeText, failedJob, genLabel } from './shotStatus';
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

function engineOptions(rows: EngineRow[] | undefined) {
  return [{ value: '', label: '项目默认' }, ...(rows ?? []).map((r) => ({ value: r.name, label: engineLabel(r) }))];
}

function shotFields(image: EngineRow[] | undefined, video: EngineRow[] | undefined): FieldSpec[] {
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
    { name: 'image_engine', label: '图像引擎', kind: 'select', options: engineOptions(image) },
    { name: 'video_engine', label: '视频引擎', kind: 'select', options: engineOptions(video) },
    { name: 'image_prompt', label: '图像提示词（英文）', kind: 'textarea', rows: 6 },
    { name: 'motion_prompt', label: '运动提示词（英文）', kind: 'textarea', rows: 3 },
    { name: 'audio_prompt', label: '声音提示词（英文）', kind: 'textarea', rows: 3 },
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

export interface ShotDrawerProps {
  shot: ShotDetail;
  episodeId: number;
  /** 生成任务（首帧/视频）提交后、还没拿到响应期间为 true：生成按钮禁用，防止重复点击提交两次。 */
  generating?: boolean;
  onClose(): void;
  /** 已有选定结果时是「重拍」（父组件强制提交）。saved：先把改过的提示词 / 对白保存了 */
  onGenerate(stage: GenStage, saved?: boolean): void;
  /** 候选数：和导演台工具栏共用一份 */
  count?: number;
  onCountChange?(n: number): void;
  /** 导演台的提示条：抽屉开着时显示在抽屉里，免得被遮罩挡住 */
  notice?: ReactNode;
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
}: ShotDrawerProps) {
  const engines = useEngines();
  const patch = usePatchShot(episodeId);
  const patchDialogue = usePatchShot(episodeId);
  const select = useSelectCandidate(episodeId);
  const confirm = useConfirm();
  const [fields, setFields] = useState<FieldsState | null>(null);
  const [dialogueDirty, setDialogueDirty] = useState(false);
  const [dialogueDraft, setDialogueDraft] = useState<DialogueLine[] | null>(null);
  const [saving, setSaving] = useState(false);
  const box = useRef<HTMLElement>(null);
  const fieldsDirty = !!fields?.dirty;
  // 提示词或对白有没保存的修改：生成按钮先保存（生成按旧内容跑就白跑了）
  const unsaved = fieldsDirty || dialogueDirty;
  // 关抽屉另有确认；换到别的页面由全站的离开拦截问
  useUnsavedGuard(unsaved);
  const latest = useRef({ dirty: false, onClose });
  latest.current = { dirty: fieldsDirty || dialogueDirty, onClose };

  /** 想关抽屉：有没保存的修改先问一句。 */
  const requestClose = useRef(async () => {
    if (latest.current.dirty) {
      const ok = await confirm({
        title: '放弃没保存的修改？',
        body: '这个镜头有改过但没保存的内容，关掉抽屉就没了。',
        confirmText: '放弃修改',
        cancelText: '继续编辑',
        danger: true,
      });
      if (!ok) return;
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
    if (saveFirst) onGenerate(stage, true);
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
  const slots = [...shot.slots].sort((a, b) => SLOT_ORDER.indexOf(a.kind) - SLOT_ORDER.indexOf(b.kind));

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
              return (
                <button
                  key={st}
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
          {failures.map(({ st, job }) => (
            <p key={st} className="error">
              {`${CELL[st === 'keyframes' ? 'first_frame' : 'video'].label}上次生成失败：${job?.error || '没有错误信息'}`}
            </p>
          ))}
        </div>
        {notice}

        <section>
          <h4>提示词与参数</h4>
          <EditableFields
            fields={shotFields(engines.data?.image, engines.data?.video)}
            initial={initial}
            saving={patch.isPending}
            error={patch.error ? errorText(patch.error) : null}
            onValuesChange={setFields}
            onSave={(_v, changed) => patch.mutate({ id: shot.id, ...toShotPatch(changed) })}
          />
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

        {slots.length === 0 && <p className="muted">还没有生成任何首帧或视频。</p>}
        {slots.map((sl) => (
          <section key={sl.id}>
            <h4>
              {SLOT_LABEL[sl.kind] ?? sl.kind}候选
              {sl.stale && <span className="badge stale">已过期</span>}
            </h4>
            {sl.stale && sl.stale_reason && <p className="muted stale-reason">过期原因：{sl.stale_reason}</p>}
            <CandidateGrid slot={sl} selecting={select.isPending} onSelect={(cid) => select.mutate(cid)} />
          </section>
        ))}
        {select.error && <p className="error">{errorText(select.error)}</p>}
      </aside>
    </>
  );
}
