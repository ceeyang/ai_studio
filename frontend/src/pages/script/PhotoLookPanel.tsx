// 角色资料页的「从参考图写外观」：上传一张参考照片 → LLM 看图写英文外观提示词（只写外观）→
// 对照现在的外观，用户确认后才填进外观提示词。照片只给 LLM 看，不当出图参考图。
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { errorText } from '../../api/client';
import {
  useAppSettings,
  useAppearanceFromPhoto,
  useDiscardPhotoLook,
  useJob,
  usePatchCharacter,
  useRefreshShotLooks,
  useSaveSettings,
  useUploadAppearanceRef,
} from '../../api/queries';
import type { Character } from '../../api/types';
import { useConfirm } from '../../components/ConfirmDialog';
import { useJobNotice } from '../../components/JobNotice';
import { refreshText } from './CharacterLooks';

const DIRTY_HINT = '资料里有没保存的修改：先保存或撤销，再填入参考图写的外观';
const STEP = 'character.from_photo';
/** 网关在文本模型看不了图时报的错（backend/llm/gateway.py no_vision_error） */
const NO_VISION = /看不了图片/;

/** 这一步的文本模型看不了图片：一键把这一步改成「手动粘贴」（自己去网页上问），或去设置页换能看图的模型。 */
function VisionFallback() {
  const settings = useAppSettings();
  const save = useSaveSettings();
  const manual = settings.data?.choices.llm.find((c) => c.kind === 'manual');
  return (
    <div className="notice small" data-testid="vision-fallback">
      {save.isSuccess ? (
        <span>已改成手动粘贴：再点一次「从参考图写外观」，任务会停下来等你把照片和提示词贴到网页上问，再把回答贴回来。</span>
      ) : (
        <span>这一步的文本模型看不了图片：可以改用手动粘贴（自己把照片和提示词发给网页上的模型），或到设置页换成能看图的模型。</span>
      )}{' '}
      {!save.isSuccess && (
        <button
          type="button"
          className="sm soft"
          disabled={!manual || save.isPending}
          title={manual ? undefined : '配置文件里没有手动粘贴（kind: manual）的文本模型'}
          onClick={() => manual && save.mutate({ steps: { [STEP]: manual.id } })}
        >
          {save.isPending ? '改设置中…' : '改用手动粘贴'}
        </button>
      )}{' '}
      <Link to={`/settings?step=${STEP}`}>去设置这一步</Link>
      {save.error && <span className="error"> {errorText(save.error)}</span>}
    </div>
  );
}

export function PhotoLookPanel({ c, slug, profileDirty }: { c: Character; slug: string; profileDirty: boolean }) {
  const upload = useUploadAppearanceRef(slug);
  const run = useAppearanceFromPhoto(slug);
  const discard = useDiscardPhotoLook(slug);
  const patch = usePatchCharacter(slug);
  const refresh = useRefreshShotLooks();
  const confirm = useConfirm();
  const notice = useJobNotice();
  const [hint, setHint] = useState<string | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const job = useJob(jobId);
  const noVision = job.data?.status === 'failed' && NO_VISION.test(job.data.error ?? '');
  const ref = c.appearance_ref ?? null;
  const writing = (c.active_jobs ?? []).some((j) => j.type === 'character.from_photo');
  const look = ref?.visual_prompt?.trim() ?? '';
  const pending = look !== '' && look !== c.visual_prompt.trim();
  const error = [upload.error, run.error, discard.error, patch.error, refresh.error].find(Boolean);
  const busy = upload.isPending || run.isPending || discard.isPending || patch.isPending;

  const write = () =>
    run.mutate(c.id, {
      onSuccess: (r) => {
        notice.track(r.job_ids, '从参考图写外观');
        setJobId(r.job_ids[0] ?? null);
      },
    });

  const accept = async () => {
    if (profileDirty) {
      setHint(DIRTY_HINT);
      return;
    }
    setHint(null);
    const ok = await confirm({
      title: '用参考图写的外观替换外观提示词？',
      body: `替换后出场镜头的首帧会按新外观变过期${
        c.portrait_url ? '；已选定的定妆照不会变，要和新外观对上请再抽一次定妆照' : ''
      }；镜头提示词里一字不差抄着旧外貌的地方会一起换成新外观。`,
      confirmText: '替换',
    });
    if (!ok) return;
    const old = c.visual_prompt;
    const body: { id: number; visual_prompt: string; core_features?: string } = { id: c.id, visual_prompt: look };
    if (ref?.core_features?.trim()) body.core_features = ref.core_features.trim();
    patch.mutate(body, {
      onSuccess: () => {
        // 用了就把等确认的外观清掉：之后再改外观提示词，对照不会又冒出来
        discard.mutate(c.id);
        if (old.trim()) refresh.mutate({ id: c.id, old: [old] }, { onSuccess: (r) => setHint(refreshText(r)) });
      },
    });
  };

  return (
    <section className="photo-look" aria-label="从参考图写外观">
      <div className="photo-look-row">
        {ref && <img className="photo-look-thumb" src={ref.url} alt={`${c.name} 的外观参考照片`} />}
        <div className="photo-look-body">
          <p className="muted">
            有真人参考照片时：上传后让 AI 看图写英文外观提示词（只写脸、发型、体型、服装，不认人）。照片只用来写外观，不当出图参考图。
          </p>
          <div className="photo-look-actions">
            <label className={`btn sm${upload.isPending ? ' is-disabled' : ''}`}>
              {upload.isPending ? '上传中…' : ref ? '换一张参考照片' : '上传参考照片'}
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                aria-label="上传外观参考照片"
                hidden
                disabled={upload.isPending}
                onChange={(ev) => {
                  const f = ev.target.files?.[0];
                  ev.target.value = '';
                  if (f) upload.mutate({ id: c.id, file: f });
                }}
              />
            </label>
            <button type="button" className="sm" disabled={!ref || busy || writing} onClick={write}>
              {writing ? '正在看图写外观…' : look ? '重新从参考图写外观' : '从参考图写外观'}
            </button>
          </div>
          {notice.view}
          {noVision && <VisionFallback />}
        </div>
      </div>
      {pending && (
        <>
          <div className="photo-look-diff" aria-label="外观对照">
            <div>
              <h5>现在的外观提示词</h5>
              {c.visual_prompt.trim() || '（空）'}
            </div>
            <div className="is-new">
              <h5>参考图写的外观</h5>
              {look}
              {ref?.core_features && (
                <>
                  {'\n'}
                  <b>核心特征：</b>
                  {ref.core_features}
                </>
              )}
            </div>
          </div>
          <div className="photo-look-actions">
            <button type="button" className="soft sm" disabled={busy} onClick={() => void accept()}>
              用这个外观
            </button>
            <button type="button" className="ghost sm" disabled={busy} onClick={() => discard.mutate(c.id)}>
              不用
            </button>
          </div>
        </>
      )}
      {hint && <p className="muted" role="status">{hint}</p>}
      {error && <p className="error">{errorText(error)}</p>}
    </section>
  );
}
