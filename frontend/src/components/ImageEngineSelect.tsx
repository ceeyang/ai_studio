// 选图像引擎的小下拉框（定妆照抽卡、三视图用）。空值表示用默认引擎；网页出图的引擎标「浏览器」。
import { useEngines } from '../api/queries';
import { engineOptionDisabled, engineOptionText } from '../format';

export function ImageEngineSelect({
  value,
  onChange,
  defaultLabel,
  ariaLabel,
}: {
  value: string;
  onChange: (v: string) => void;
  defaultLabel: string;
  ariaLabel: string;
}) {
  const engines = useEngines();
  return (
    <label className="inline-field">
      <span>引擎</span>
      <select aria-label={ariaLabel} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{defaultLabel}</option>
        {(engines.data?.image ?? []).map((r) => (
          <option key={r.name} value={r.name} disabled={engineOptionDisabled(r)} title={r.note ?? undefined}>
            {engineOptionText(r)}
          </option>
        ))}
      </select>
    </label>
  );
}
