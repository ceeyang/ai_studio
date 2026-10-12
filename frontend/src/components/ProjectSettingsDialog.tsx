import { type FormEvent, useState } from 'react';
import { errorText } from '../api/client';
import { usePatchProject } from '../api/queries';
import type { FixedEnginePack, Project, ProjectPatch, StylePresetKey } from '../api/types';
import { Dialog } from './Dialog';
import { StylePresetPicker, stylePresetLabel } from './StylePresetPicker';

/** 镜头节奏的选项：0 = 跟随视频引擎（每镜默认秒数），其余是每镜几秒 */
const PACES: { value: 0 | 3 | 4 | 5; label: string }[] = [
  { value: 0, label: '跟随视频引擎（默认）' },
  { value: 3, label: '快：每镜约 3 秒' },
  { value: 4, label: '中：每镜约 4 秒' },
  { value: 5, label: '慢：每镜约 5 秒' },
];

/** 固定时长视频引擎怎么用满的选项 */
const PACKS: { value: FixedEnginePack; label: string }[] = [
  { value: 'beats', label: '多拍镜头（一次生成里切 2–4 拍）' },
  { value: 'group', label: '组镜拆分（一次生成拆给几个镜头）' },
  { value: 'off', label: '不合并（一镜一生成）' },
];

function packOf(p: Project): FixedEnginePack {
  return p.fixed_engine_pack ?? 'beats';
}

function paceOf(p: Project): 0 | 3 | 4 | 5 {
  const v = Math.round(p.shot_seconds ?? 0);
  return v === 3 || v === 4 || v === 5 ? v : 0;
}

/**
 * 项目设置：风格预设、镜头节奏、固定时长视频引擎怎么用满、视频自动重拍。只发改了的字段。
 * 改预设不会自动重写已有的风格锚和外观，要重新生成剧本才按新预设写；
 * 改节奏影响之后的剧本镜头预算和分镜（整集运行会提示分镜要重出）。
 */
export function ProjectSettingsDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  const patch = usePatchProject(project.slug);
  const [preset, setPreset] = useState<StylePresetKey>(project.style_preset ?? 'live_action');
  const [pace, setPace] = useState(paceOf(project));
  const [retry, setRetry] = useState(!!project.auto_retry_video);
  const [pack, setPack] = useState<FixedEnginePack>(packOf(project));
  const presetChanged = preset !== (project.style_preset ?? 'live_action');
  const body: ProjectPatch = {
    ...(presetChanged ? { style_preset: preset } : {}),
    ...(pace !== paceOf(project) ? { shot_seconds: pace } : {}),
    ...(retry !== !!project.auto_retry_video ? { auto_retry_video: retry } : {}),
    ...(pack !== packOf(project) ? { fixed_engine_pack: pack } : {}),
  };
  const changed = Object.keys(body).length > 0;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (changed) patch.mutate(body, { onSuccess: onClose });
  };
  return (
    <Dialog
      title="项目设置"
      subtitle={`「${project.title}」的风格预设、镜头节奏、固定时长视频引擎用法和视频自动重拍。`}
      onClose={onClose}
      guard={() => changed}
      busy={patch.isPending}
    >
      <form className="fields" onSubmit={submit}>
        <StylePresetPicker value={preset} onChange={setPreset} name="project-style-preset" />
        {presetChanged && (
          <p className="preset-note" role="note">
            已经写好的风格锚、角色和场景外观不会自动改：到工作台的剧本阶段「重新生成剧本」，视觉设定才会按「
            {stylePresetLabel(preset)}」重写（已定妆的角色外观不覆盖）。负面词还是原预设的默认值时会跟着换；项目的图像/视频引擎不变（推荐引擎只在新建项目时预选）。
          </p>
        )}
        <label>
          镜头节奏
          <select aria-label="镜头节奏" value={pace} onChange={(e) => setPace(Number(e.target.value) as 0 | 3 | 4 | 5)}>
            {PACES.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <p className="muted small">
          决定一集拆成几个镜头（目标时长 ÷ 每镜秒数）。改了之后，剧本的镜头预算和分镜按新节奏写；已有的分镜要重新生成才会变。
        </p>
        <label>
          固定时长视频引擎怎么用满
          <select
            aria-label="固定时长视频引擎怎么用满"
            value={pack}
            onChange={(e) => setPack(e.target.value as FixedEnginePack)}
          >
            {PACKS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <p className="muted small">
          Gemini 网页每次固定出 10 秒、Muse 只认 3 / 5 / 10 秒。多拍镜头：每个镜头就是一次生成，分镜在里面按时间切 2–4 拍（换景别、换机位），一次出满不浪费；组镜拆分：镜头照常 2–8 秒、各有台词和字幕，相连的几镜一次生成，导出时按时间拆回各镜；不合并：一镜一生成，短镜头多出来的秒数白出。能精确控时长的引擎不受影响。改了之后重新生成分镜才生效，已有的镜头不动。
        </p>
        <label className="check">
          <input type="checkbox" checked={retry} onChange={(e) => setRetry(e.target.checked)} />
          视频开头没接上首帧时自动重拍一次（换种子再出一条，接上了就选用它；多占一次显卡时间）
        </label>
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
