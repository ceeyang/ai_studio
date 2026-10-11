import type { Progress } from '../../ws/events';
import { isDecoding } from '../../ws/events';

/**
 * 细进度条：有进度事件按 value/max 走；没有（还没开始采样、LLM / 网页任务）就是来回扫的不定进度动画。
 * 采样到头、还在解码保存时条填满并呼吸。
 */
export function ThinProgress({ p, label = '任务进度' }: { p?: Progress; label?: string }) {
  if (!p || !(p.max > 0)) {
    return (
      <div className="thin-progress is-indeterminate" role="progressbar" aria-label={label} aria-valuetext="进行中">
        <span />
      </div>
    );
  }
  const pct = Math.min(100, Math.round((p.value / p.max) * 100));
  const decoding = isDecoding(p);
  return (
    <div
      className={`thin-progress${decoding ? ' is-decoding' : ''}`}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={p.max}
      aria-valuenow={Math.min(p.value, p.max)}
      aria-valuetext={decoding ? '采样完成，解码保存中' : `${p.value}/${p.max}`}
    >
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}
