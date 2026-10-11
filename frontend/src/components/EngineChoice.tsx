// 新项目的图像 / 视频引擎怎么选：新建项目对话框和「从一个点子开始」向导共用。
// 默认值：手动选的 → 设置页明确设的（和风格预设合得来）→ 预设推荐的 → 设置页 / 内置默认；没配置好的引擎标出来。
import { useState } from 'react';
import { errorText } from '../api/client';
import { useAppSettings, useEngines, useStylePresets } from '../api/queries';
import type { EngineRow, SettingValue, StylePresetKey } from '../api/types';
import { engineLabel, engineOptionDisabled, engineOptionText, engineUnreadyHint } from '../format';

function pickDefault(rows: EngineRow[] | undefined, preferred: string): string {
  if (!rows || rows.length === 0) return '';
  return rows.some((r) => r.name === preferred) ? preferred : rows[0].name;
}

/** 视频引擎的名字：中文名里没写有声 / 无声时补上 */
function videoEngineText(r: EngineRow): string {
  const label = engineLabel(r);
  const text = label.includes('声') ? label : `${label}${r.caps.has_audio ? '（有声）' : '（无声）'}`;
  const hint = engineUnreadyHint(r);
  return hint ? `${text}（${hint}）` : text;
}

/** 按风格预设算出默认引擎；用户在下拉框里手动选过的不随预设变 */
export function useEngineChoice(preset: StylePresetKey) {
  const engines = useEngines();
  // 设置页「新项目的图片/视频来源」；拿不到设置就用 zimage / h3_video。设置还在加载时不让提交，
  // 免得用户手快、按内置默认建了项目
  const settings = useAppSettings();
  const presets = useStylePresets();
  // 用户在下拉框里手动选的引擎（空 = 没动过）：换预设时不覆盖
  const [imageEngine, setImageEngine] = useState('');
  const [videoEngine, setVideoEngine] = useState('');
  const rec = presets.data?.find((p) => p.key === preset);
  const has = (rows: EngineRow[] | undefined, name: string | null | undefined): name is string =>
    !!name && (rows ?? []).some((r) => r.name === name);
  // 设置页明确设了（source=settings）、而且和预设合得来（在预设推荐的引擎里；预设没列就都算合得来）才用设置的
  const fromSettings = (rows: EngineRow[] | undefined, v: SettingValue | undefined, allowed: string[] | undefined) =>
    v && v.source === 'settings' && has(rows, v.effective) && (!allowed?.length || allowed.includes(v.effective!))
      ? v.effective!
      : '';
  // 默认值：手动选的 → 设置页明确设的（和预设合得来）→ 当前预设推荐的（包括一打开就是的默认预设）→ 设置页 / 内置默认
  const defaultEngine = (
    rows: EngineRow[] | undefined,
    v: SettingValue | undefined,
    allowed: string[] | undefined,
    recommended: string | null | undefined,
    builtin: string,
  ) => fromSettings(rows, v, allowed) || (has(rows, recommended) ? recommended : '') || pickDefault(rows, v?.effective ?? builtin);
  const image =
    imageEngine || defaultEngine(engines.data?.image, settings.data?.image_engine, rec?.image_engines, rec?.image_engine, 'zimage');
  const video =
    videoEngine || defaultEngine(engines.data?.video, settings.data?.video_engine, rec?.video_engines, rec?.video_engine, 'h3_video');

  // 首帧引擎吃不吃参考图（caps.max_refs）：不吃的话首帧没法用定妆照锁脸、也用不上场景图
  const imageRow = (engines.data?.image ?? []).find((r) => r.name === image);
  const noRefs = imageRow?.caps?.max_refs === 0;
  const videoRow = (engines.data?.video ?? []).find((r) => r.name === video);
  // 选中的引擎还没配置好（ComfyUI 没连上、扩展没连上、没填 Key…）：表单下面提示一句，仍然能建项目
  const unready = (kind: string, rows: EngineRow[] | undefined, row: EngineRow | undefined) => {
    if (!engines.data) return null;
    if (!rows?.length) return `没有可用的${kind}，先在 config.yaml 或设置页「API 服务」里配置。`;
    if (!row || row.ready !== false) return null;
    return `${kind}「${engineLabel(row)}」未配置好：${row.note ?? engineUnreadyHint(row)}。`;
  };
  const warnings = [unready('图像引擎', engines.data?.image, imageRow), unready('视频引擎', engines.data?.video, videoRow)].filter(
    (w): w is string => !!w,
  );
  // 预设推荐的定妆照引擎（如真人写实的 krea2）：和首帧引擎不同时提一句
  const portraitRec = rec?.portrait_engine ?? null;
  const portraitHint = portraitRec && portraitRec !== image ? portraitRec : null;

  return {
    engines,
    settingsLoading: settings.isPending,
    image,
    video,
    setImageEngine,
    setVideoEngine,
    noRefs,
    portraitHint,
    warnings,
  };
}

export type EngineChoice = ReturnType<typeof useEngineChoice>;

/** 图像、视频引擎两个下拉框（两个 .field，放进调用方的 .field-grid） */
export function EngineSelects({ choice }: { choice: EngineChoice }) {
  const { engines, image, video, setImageEngine, setVideoEngine } = choice;
  return (
    <>
      <label className="field">
        <span>图像引擎</span>
        <select value={image} onChange={(e) => setImageEngine(e.target.value)} title="选了风格预设会预选推荐的引擎，可以改">
          {(engines.data?.image ?? []).map((r) => (
            <option key={r.name} value={r.name} disabled={engineOptionDisabled(r)} title={r.note ?? undefined}>
              {engineOptionText(r)}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>视频引擎</span>
        <select value={video} onChange={(e) => setVideoEngine(e.target.value)}>
          {(engines.data?.video ?? []).map((r) => (
            <option key={r.name} value={r.name} disabled={engineOptionDisabled(r)} title={r.note ?? undefined}>
              {videoEngineText(r)}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}

/** 下拉框下面的说明：首帧引擎吃不吃参考图、定妆照用哪个、没配置好的引擎、引擎列表读失败 */
export function EngineNotes({ choice }: { choice: EngineChoice }) {
  const { engines, image, noRefs, portraitHint, warnings } = choice;
  return (
    <>
      <p className="muted small" data-testid="engine-hint">
        首帧引擎要能吃参考图才能锁脸、用场景图（如 H3 图像）。
        {noRefs && `${engineLabel(image)} 不吃参考图，首帧只能按文字出脸、场景图也用不上。`}
        {portraitHint && `定妆照默认用 ${engineLabel(portraitHint)}（这个风格预设推荐的；设置页「角色定妆照」设了别的、且和预设合得来时用设置的）。`}
      </p>
      {warnings.length > 0 && (
        <p className="warn small" data-testid="engine-unready">
          {warnings.join(' ')}
        </p>
      )}
      {engines.error && <p className="error">读取引擎列表失败：{errorText(engines.error)}</p>}
    </>
  );
}
