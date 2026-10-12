import type { Beat, EngineRow } from '../../api/types';

/**
 * 固定时长视频引擎（设计文档 docs/specs/2026-10-12-fixed-length-engines-design.md）：和后端
 * backend/domain/beats.py 同一套算法——生成单元、浪费秒数、提醒文字。
 */

/** 镜头比一次生成短这么多秒才提醒浪费（后端 WASTE_MIN） */
export const WASTE_MIN = 1.5;

type Caps = Record<string, unknown> | undefined;

function control(caps: Caps): string {
  return typeof caps?.duration_control === 'string' ? caps.duration_control : 'param';
}

function allowed(caps: Caps): number[] {
  const a = caps?.allowed_seconds;
  return Array.isArray(a) ? a.filter((x): x is number => typeof x === 'number').sort((x, y) => x - y) : [];
}

/** 时长不能精确控制的引擎：fixed（总是固定时长）/ prompt（只能写进提示词、只认几档） */
export function isFixedLength(caps: Caps): boolean {
  const c = control(caps);
  return c === 'fixed' || c === 'prompt';
}

/** 一次生成出多长：fixed 取固定时长；prompt 取能覆盖镜头的最小档（没给镜头时长取不超过 10 秒的最大档）；param 为 null */
export function generationUnit(caps: Caps, need?: number): number | null {
  if (!isFixedLength(caps)) return null;
  const def = typeof caps?.default_seconds === 'number' ? caps.default_seconds : 10;
  const max = typeof caps?.max_seconds === 'number' ? caps.max_seconds : def;
  if (control(caps) === 'fixed') return def;
  const opts = allowed(caps);
  if (opts.length === 0) return need !== undefined ? Math.min(Math.max(need, 1), max) : Math.min(def, max);
  if (need !== undefined) return opts.find((x) => x >= need - 1e-6) ?? opts[opts.length - 1];
  const under = opts.filter((x) => x <= 10 + 1e-6);
  return under.length ? under[under.length - 1] : opts[0];
}

/** 浪费提醒：固定时长引擎、不是多拍镜头、镜头比一次生成短 ≥ 1.5 秒时给 { unit, waste }，否则 null */
export function wasteOf(caps: Caps, duration: number, beats?: Beat[] | null): { unit: number; waste: number } | null {
  if ((beats?.length ?? 0) > 0 || !isFixedLength(caps)) return null;
  const unit = generationUnit(caps, duration);
  if (unit === null) return null;
  const waste = Math.round((unit - duration) * 10) / 10;
  return waste >= WASTE_MIN - 1e-6 ? { unit, waste } : null;
}

const fmt = (n: number) => String(Math.round(n * 10) / 10);

/** 「这个引擎每次固定出 10 秒，这一镜只用 3 秒，会浪费 7 秒」 */
export function wasteText(caps: Caps, duration: number, unit: number, waste: number): string {
  const head =
    control(caps) === 'fixed'
      ? `这个引擎每次固定出 ${fmt(unit)} 秒`
      : allowed(caps).length
        ? `这个引擎只认 ${allowed(caps).map(fmt).join(' / ')} 秒，这一镜要按 ${fmt(unit)} 秒出`
        : `这个引擎每次出 ${fmt(unit)} 秒`;
  return `${head}，这一镜只用 ${fmt(duration)} 秒，会浪费 ${fmt(waste)} 秒`;
}

export function engineCaps(rows: EngineRow[] | undefined, name: string | undefined): Caps {
  return name ? rows?.find((r) => r.name === name)?.caps : undefined;
}
