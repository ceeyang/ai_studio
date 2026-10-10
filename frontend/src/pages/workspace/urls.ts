// 单集工作台的地址：/projects/<slug>/episodes/<id>?stage=…&shot=…&char=…
// 旧的四个页面（/script /cast /director /export）重定向过来；后端任务 context.url 还是旧形式，前端统一在这里换。
import { enc } from '../../api/client';
import type { Job } from '../../api/types';
import { type StageKey, isStageKey } from '../stages';

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

/** 任务按类型落到哪个阶段（没有镜头 / 角色时）：角色相关的去角色、导出类去成片，其余剧集任务去剧本 */
function stageOfJob(type: string, ctx: NonNullable<Job['context']>): StageKey {
  if (ctx.character_id != null || type.startsWith('character.') || type === 'image.portrait' || type === 'image.turnaround') {
    return 'cast';
  }
  if (ctx.shot_id != null) return type.startsWith('video.') ? 'videos' : 'keyframes';
  if (type.startsWith('export.') || type.startsWith('post.')) return 'export';
  const legacy = new URLSearchParams(toWorkspaceUrl(ctx.url ?? '').split('?')[1] ?? '').get('stage');
  return isStageKey(legacy) ? legacy : 'script';
}

/**
 * 任务「查看」该去的地方：属于某一集的，按任务类型去工作台对应阶段（镜头带 ?shot=、角色带 ?char=）；
 * 不属于某一集的用后端给的地址（旧形式也换掉）；没有上下文为 null。
 */
export function jobUrl(job: Pick<Job, 'type' | 'context'>): string | null {
  const ctx = job.context;
  if (!ctx?.url) return null;
  if (!ctx.project_slug || ctx.episode_id == null) return toWorkspaceUrl(ctx.url);
  const params = new URLSearchParams({ stage: stageOfJob(job.type, ctx) });
  if (ctx.character_id != null) params.set('char', String(ctx.character_id));
  else if (ctx.shot_id != null) params.set('shot', String(ctx.shot_id));
  return `${workspacePath(ctx.project_slug, ctx.episode_id)}?${params.toString()}`;
}

/** 当前页面是不是就是 target：路径相同、stage 相同，target 带了 shot / char 时这两个也要相同；其他参数和顺序不管 */
export function isSameTarget(target: string, pathname: string, search: string): boolean {
  const [path, query = ''] = target.split('?');
  if (path !== pathname) return false;
  const want = new URLSearchParams(query);
  const have = new URLSearchParams(search);
  return ['stage', 'shot', 'char'].every((k) => !want.has(k) || want.get(k) === have.get(k));
}
