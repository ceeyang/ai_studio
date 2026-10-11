import { type FormEvent, useState } from 'react';
import { errorText } from '../api/client';
import { usePatchProject } from '../api/queries';
import type { Project, StylePresetKey } from '../api/types';
import { Dialog } from './Dialog';
import { StylePresetPicker, stylePresetLabel } from './StylePresetPicker';

/** 项目设置：风格预设。改了不会自动重写已有的风格锚和外观，要重新生成剧本才按新预设写。 */
export function ProjectSettingsDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  const patch = usePatchProject(project.slug);
  const [preset, setPreset] = useState<StylePresetKey>(project.style_preset ?? 'live_action');
  const changed = preset !== (project.style_preset ?? 'live_action');
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (changed) patch.mutate({ style_preset: preset }, { onSuccess: onClose });
  };
  return (
    <Dialog
      title="项目设置"
      subtitle={`「${project.title}」的风格预设：决定风格锚的画风方向、负面词默认值和分镜写法。`}
      onClose={onClose}
      guard={() => changed}
      busy={patch.isPending}
    >
      <form className="fields" onSubmit={submit}>
        <StylePresetPicker value={preset} onChange={setPreset} name="project-style-preset" />
        {changed && (
          <p className="preset-note" role="note">
            已经写好的风格锚、角色和场景外观不会自动改：到工作台的剧本阶段「重新生成剧本」，视觉设定才会按「
            {stylePresetLabel(preset)}」重写（已定妆的角色外观不覆盖）。负面词还是原预设的默认值时会跟着换；项目的图像/视频引擎不变（推荐引擎只在新建项目时预选）。
          </p>
        )}
        {patch.error && <p className="error">{errorText(patch.error)}</p>}
        <div className="modal-foot">
          <button type="button" className="ghost" disabled={patch.isPending} onClick={onClose}>
            取消
          </button>
          <button type="submit" className="primary" disabled={!changed || patch.isPending}>
            {patch.isPending ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
