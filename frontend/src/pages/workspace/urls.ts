// 单集工作台的地址：/projects/<slug>/episodes/<id>?stage=…&shot=…&char=…
// 旧的四个页面（/script /cast /director /export）重定向过来；后端任务 context.url 还是旧形式，前端统一在这里换。
import { enc } from '../../api/client';
import type { StageKey } from '../stages';

/** 旧页面 → 工作台阶段 */
export const LEGACY_STAGE: Record<'script' | 'cast' | 'director' | 'export', StageKey> = {
  script: 'script',
  cast: 'cast',
  director: 'keyframes',
  export: 'export',
};

export function workspacePath(slug: string, episodeId: number): string {
  return `/projects/${enc(slug)}/episodes/${episodeId}`;
}

/** 把查询串换成指定的阶段：stage 放最前，其余参数原样保留 */
export function stageSearch(search: string, stage: StageKey): string {
  const out = new URLSearchParams({ stage });
  for (const [k, v] of new URLSearchParams(search)) {
    if (k !== 'stage') out.append(k, v);
  }
  return `?${out.toString()}`;
}

const LEGACY_RE = /^(\/projects\/[^/?#]+\/episodes\/\d+)\/(script|cast|director|export)(\?[^#]*)?$/;

/** 旧形式的剧集页面地址换成工作台地址；不是的原样返回 */
export function toWorkspaceUrl(url: string): string {
  const m = LEGACY_RE.exec(url);
  if (!m) return url;
  return `${m[1]}${stageSearch(m[3] ?? '', LEGACY_STAGE[m[2] as keyof typeof LEGACY_STAGE])}`;
}
