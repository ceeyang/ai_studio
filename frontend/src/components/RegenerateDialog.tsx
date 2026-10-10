// 重新生成剧本 / 分镜的对话框：写明范围（整集 / 第 N 段）、可选的修改意见、实时的影响预估。
// 确认时先让页面保存没保存的修改（beforeRun），再强制提交——对话框里的「重新生成」一定会真的重出，不会因为输入没变被跳过。
import { type FormEvent, useState } from 'react';
import { errorText } from '../api/client';
import { useRerunImpact, useRunStage } from '../api/queries';
import type { RerunImpact } from '../api/types';
import { Dialog } from './Dialog';

export const NOTE_MAX = 500;
export const NOTE_PLACEHOLDER = '例如「第 3 段改成在车底对话」「镜头更紧凑，少用远景」';

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

export interface RegenerateDialogProps {
  open: boolean;
  onClose: () => void;
  stage: 'script' | 'storyboard';
  episodeId: number;
  /** 只重写这些段落的分镜；不给 = 整集 */
  blockIds?: number[];
  /** 范围说明：「整集」「第 2 段」 */
  scopeLabel: string;
  /** 提交前保存页面上没保存的修改；返回 false（保存失败）就不提交 */
  beforeRun?: () => Promise<boolean>;
  onSubmitted: (jobIds: number[]) => void;
  /** 页面上有没保存的修改时的一句提示（如「有未保存的修改，重新生成前会先保存」） */
  unsavedNote?: string;
}

export function RegenerateDialog(props: RegenerateDialogProps) {
  if (!props.open) return null;
  return <RegenerateDialogBody {...props} />;
}

function RegenerateDialogBody({
  onClose,
  stage,
  episodeId,
  blockIds,
  scopeLabel,
  beforeRun,
  onSubmitted,
  unsavedNote,
}: RegenerateDialogProps) {
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useRunStage(episodeId);
  const trimmed = note.trim();
  const impact = useRerunImpact(episodeId, stage, blockIds, trimmed !== '');
  const busy = saving || run.isPending;
  const im = impact.data;
  const danger = !!im && im.selected_videos > 0;

  const submit = async (ev?: FormEvent) => {
    ev?.preventDefault();
    if (busy) return;
    setError(null);
    if (beforeRun) {
      setSaving(true);
      let ok = false;
      try {
        ok = await beforeRun();
      } catch {
        ok = false;
      } finally {
        setSaving(false);
      }
      if (!ok) {
        setError('保存没成功，没有重新生成');
        return;
      }
    }
    try {
      const r = await run.mutateAsync({
        stage,
        force: true,
        ...(trimmed ? { note: trimmed } : {}),
        ...(blockIds && blockIds.length > 0 ? { scene_block_ids: blockIds } : {}),
      });
      onSubmitted(r.job_ids);
      onClose();
    } catch (err) {
      setError(errorText(err));
    }
  };

  let impactView;
  if (impact.isLoading) impactView = <p className="muted">正在估算影响…</p>;
  else if (impact.error) impactView = <p className="error">影响预估没拿到：{errorText(impact.error)}</p>;
  else if (im) impactView = <p className="confirm-body">{impactText(im) || '不会删除镜头，也不会覆盖已有设定'}</p>;

  return (
    <Dialog
      title={stage === 'script' ? '重新生成剧本' : '重新生成分镜'}
      subtitle={`范围：${scopeLabel}`}
      onClose={onClose}
      guard={() => note.trim() !== ''}
      busy={busy}
    >
      <form className="fields" onSubmit={(ev) => void submit(ev)}>
        <label className="field">
          <span>修改意见（可选）</span>
          <textarea
            rows={4}
            maxLength={NOTE_MAX}
            placeholder={NOTE_PLACEHOLDER}
            value={note}
            onChange={(ev) => setNote(ev.target.value)}
          />
        </label>
        {impactView}
        {unsavedNote && <p className="warn">{unsavedNote}</p>}
        {error && <p className="error">{error}</p>}
        <div className="modal-foot">
          <span className="muted">{note ? `${Array.from(note).length}/${NOTE_MAX}` : ''}</span>
          <button type="button" className="ghost" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button type="submit" className={danger ? 'danger' : 'primary'} disabled={busy}>
            {saving ? '保存中…' : run.isPending ? '提交中…' : '重新生成'}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
