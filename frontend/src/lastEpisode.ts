// 左侧导航的"剧本 / 导演台"指向最近打开的那一集。只是本机浏览器里的便利记忆，读不到就当没有。
const KEY = 'studio.lastEpisode';

export interface EpisodeRef {
  slug: string;
  episodeId: number;
}

export function readLastEpisode(): EpisodeRef | null {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? 'null') as Partial<EpisodeRef> | null;
    return v && typeof v.slug === 'string' && typeof v.episodeId === 'number'
      ? { slug: v.slug, episodeId: v.episodeId }
      : null;
  } catch {
    return null;
  }
}

export function rememberEpisode(ref: EpisodeRef): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(ref));
  } catch {
    // 隐私模式或存储被禁用：不记也不影响使用
  }
}
