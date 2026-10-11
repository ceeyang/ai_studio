// 阶段条「生成场景图」这一步的「跳过」：按集记在本机（localStorage；读写不了时只记在这次打开的页面里）。
// 场景图是建议步骤，跳过后角色阶段算完成、下一步直接去首帧；阶段条和角色阶段共用这份状态。
import { useSyncExternalStore } from 'react';

const memory = new Map<number, boolean>();
const listeners = new Set<() => void>();
const key = (episodeId: number) => `studio:scene-skip:${episodeId}`;

export function isSceneSkipped(episodeId: number): boolean {
  if (memory.has(episodeId)) return memory.get(episodeId) ?? false;
  try {
    return window.localStorage.getItem(key(episodeId)) === '1';
  } catch {
    return false;
  }
}

export function setSceneSkipped(episodeId: number, skipped: boolean): void {
  memory.set(episodeId, skipped);
  try {
    if (skipped) window.localStorage.setItem(key(episodeId), '1');
    else window.localStorage.removeItem(key(episodeId));
  } catch {
    // 存不了（隐私模式等）：这次打开的页面里照样算跳过
  }
  listeners.forEach((f) => f());
}

function subscribe(f: () => void): () => void {
  listeners.add(f);
  return () => listeners.delete(f);
}

export function useSceneSkipped(episodeId: number): boolean {
  return useSyncExternalStore(subscribe, () => isSceneSkipped(episodeId));
}
