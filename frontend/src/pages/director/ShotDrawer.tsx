import { errorText } from '../../api/client';
import { useEngines, usePatchShot, useSelectCandidate } from '../../api/queries';
import { type EngineRow, type ShotDetail, type ShotPatch, TRANSITIONS, type Transition } from '../../api/types';
import { EditableFields, type FieldSpec, type FieldValues } from '../../components/EditableFields';
import { CandidateGrid } from './CandidateGrid';
import type { GenStage } from './ShotTable';
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
  return [{ value: '', label: '项目默认' }, ...(rows ?? []).map((r) => ({ value: r.name, label: r.name }))];
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
  onGenerate(stage: GenStage): void;
}

export function ShotDrawer({ shot, episodeId, onClose, onGenerate, generating = false }: ShotDrawerProps) {
  const engines = useEngines();
  const patch = usePatchShot(episodeId);
  const select = useSelectCandidate(episodeId);
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

  return (
    <aside className="drawer" role="dialog" aria-label={`镜头 ${shot.code} 详情`}>
      <header className="drawer-head">
        <h3>
          镜头 <span className="code">{shot.code}</span>
        </h3>
        <button type="button" className="ghost" onClick={onClose}>
          关闭
        </button>
      </header>
      <div className="row gen-row">
        <button type="button" className="soft" disabled={generating} onClick={() => onGenerate('keyframes')}>
          生成首帧
        </button>
        <button type="button" className="soft" disabled={generating} onClick={() => onGenerate('videos')}>
          生成视频
        </button>
        <span className="muted">候选数和"强制重跑"用导演台工具栏上的设置</span>
      </div>

      <section>
        <h4>提示词与参数</h4>
        <EditableFields
          fields={shotFields(engines.data?.image, engines.data?.video)}
          initial={initial}
          saving={patch.isPending}
          error={patch.error ? errorText(patch.error) : null}
          onSave={(_v, changed) => patch.mutate({ id: shot.id, ...toShotPatch(changed) })}
        />
      </section>

      {shot.dialogue.length > 0 && (
        <section>
          <h4>对白</h4>
          <ul className="list dialogue-list">
            {shot.dialogue.map((d, i) => (
              <li key={i} className="dialogue-line">
                <span className="t">
                  {d.start.toFixed(1)}–{d.end.toFixed(1)}s
                </span>
                {d.speaker}：{d.zh}
              </li>
            ))}
          </ul>
        </section>
      )}

      {slots.length === 0 && <p className="muted">还没有生成任何首帧或视频。</p>}
      {slots.map((sl) => (
        <section key={sl.id}>
          <h4>
            {SLOT_LABEL[sl.kind] ?? sl.kind}候选
            {sl.stale && <span className="badge stale">已过期</span>}
          </h4>
          <CandidateGrid slot={sl} selecting={select.isPending} onSelect={(cid) => select.mutate(cid)} />
        </section>
      ))}
      {select.error && <p className="error">{errorText(select.error)}</p>}
    </aside>
  );
}
