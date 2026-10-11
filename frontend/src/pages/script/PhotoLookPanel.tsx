// 角色资料页的「从参考图写外观」：上传一张参考照片 → LLM 看图写英文外观提示词（只写外观）→
// 对照现在的外观，用户确认后才填进外观提示词。照片只给 LLM 看，不当出图参考图。
import { useState } from 'react';
import { errorText } from '../../api/client';
import {
  useAppearanceFromPhoto,
  useDiscardPhotoLook,
  usePatchCharacter,
  useRefreshShotLooks,
  useUploadAppearanceRef,
} from '../../api/queries';
import type { Character } from '../../api/types';
import { useConfirm } from '../../components/ConfirmDialog';
import { useJobNotice } from '../../components/JobNotice';
import { refreshText } from './CharacterLooks';

const DIRTY_HINT = '资料里有没保存的修改：先保存或撤销，再填入参考图写的外观';

export function PhotoLookPanel({ c, slug, profileDirty }: { c: Character; slug: string; profileDirty: boolean }) {
  const upload = useUploadAppearanceRef(slug);
  const run = useAppearanceFromPhoto(slug);
  const discard = useDiscardPhotoLook(slug);
  const patch = usePatchCharacter(slug);
  const refresh = useRefreshShotLooks();
  const confirm = useConfirm();
  const notice = useJobNotice();
  const [hint, setHint] = useState<string | null>(null);
  const ref = c.appearance_ref ?? null;
  const writing = (c.active_jobs ?? []).some((j) => j.type === 'character.from_photo');
  const look = ref?.visual_prompt?.trim() ?? '';
  const pending = look !== '' && look !== c.visual_prompt.trim();
  const error = [upload.error, run.error, discard.error, patch.error, refresh.error].find(Boolean);
  const busy = upload.isPending || run.isPending || discard.isPending || patch.isPending;

  const write = () =>
    run.mutate(c.id, { onSuccess: (r) => notice.track(r.job_ids, '从参考图写外观') });

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
