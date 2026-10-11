import type { StylePresetKey } from '../api/types';

/** 风格预设的名字和一句说明（和 backend/domain/style_presets.py 一致；推荐引擎从 GET /api/style-presets 拿） */
export const STYLE_PRESETS: { key: StylePresetKey; label: string; description: string }[] = [
  { key: 'live_action', label: '真人写实', description: '像真人实拍的短剧，真实皮肤和布料质感。' },
  { key: 'cg_realistic', label: '3D 写实', description: '高精度 3D 渲染，像游戏过场或 CG 短片。' },
  { key: 'anime', label: '日系动漫', description: '赛璐璐画风：干净线条、平涂上色。' },
  { key: 'guofeng', label: '国风漫画', description: '水墨笔触、工笔线条、传统配色。' },
  { key: 'western_comic', label: '美式漫画', description: '粗黑描边、网点阴影、高对比色块。' },
  { key: 'commercial', label: '商业广告', description: '产品特写，结尾落到行动号召（配片尾卡）。' },
];

export function stylePresetLabel(key: string | null | undefined): string {
  return STYLE_PRESETS.find((p) => p.key === key)?.label ?? '真人写实';
}

/** 风格预设单选：六张小卡片（名字 + 一句说明），键盘用方向键切换（原生单选组）。 */
export function StylePresetPicker({
  value,
  onChange,
  name = 'style-preset',
}: {
  value: StylePresetKey;
  onChange: (key: StylePresetKey) => void;
  name?: string;
}) {
  return (
    <fieldset className="preset-picker">
      <legend>风格预设</legend>
      <div className="preset-grid">
        {STYLE_PRESETS.map((p) => (
          <label key={p.key} className={p.key === value ? 'preset-option is-selected' : 'preset-option'}>
            <input
              type="radio"
              name={name}
              value={p.key}
              checked={p.key === value}
              onChange={() => onChange(p.key)}
              aria-describedby={`${name}-${p.key}-desc`}
            />
            <span className="preset-name">{p.label}</span>
            <span className="preset-desc" id={`${name}-${p.key}-desc`}>
              {p.description}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
