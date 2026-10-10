import { describe, expect, it } from 'vitest';
import type { JobContext } from '../../api/types';
import { isSameTarget, jobUrl, stageSearch, toWorkspaceUrl, workspacePath } from './urls';

describe('工作台地址', () => {
  it('workspacePath：目录名要编码', () => {
    expect(workspacePath('提词员', 4)).toBe(`/projects/${encodeURIComponent('提词员')}/episodes/4`);
  });

  it('stageSearch：stage 放最前，其余参数原样保留，原有的 stage 被替换', () => {
    expect(stageSearch('', 'cast')).toBe('?stage=cast');
    expect(stageSearch('?shot=12', 'keyframes')).toBe('?stage=keyframes&shot=12');
    expect(stageSearch('?stage=script&char=3', 'cast')).toBe('?stage=cast&char=3');
  });

  it('旧的四个页面地址换成工作台地址（导演台 → 首帧），保留查询参数；别的地址不动', () => {
    expect(toWorkspaceUrl('/projects/demo/episodes/4/script')).toBe('/projects/demo/episodes/4?stage=script');
    expect(toWorkspaceUrl('/projects/demo/episodes/4/cast')).toBe('/projects/demo/episodes/4?stage=cast');
    expect(toWorkspaceUrl('/projects/demo/episodes/4/director?shot=12')).toBe('/projects/demo/episodes/4?stage=keyframes&shot=12');
    expect(toWorkspaceUrl('/projects/demo/episodes/4/export')).toBe('/projects/demo/episodes/4?stage=export');
    expect(toWorkspaceUrl('/projects/demo/episodes/4?stage=videos')).toBe('/projects/demo/episodes/4?stage=videos');
    expect(toWorkspaceUrl('/jobs?job=3')).toBe('/jobs?job=3');
    expect(toWorkspaceUrl('/projects/demo')).toBe('/projects/demo');
  });
});

describe('任务 → 工作台地址', () => {
  const ctx = (p: Partial<JobContext>): JobContext => ({
    label: 'x', url: '/projects/demo/episodes/4/script', project_slug: 'demo', episode_id: 4, shot_id: null, character_id: null, ...p,
  });

  it('按任务类型选阶段：首帧 → keyframes、视频 → videos（带 shot）；角色相关 → cast（带 char）；导出 → export；其余剧集任务 → script', () => {
    const shot = ctx({ url: '/projects/demo/episodes/4/director?shot=12', shot_id: 12 });
    expect(jobUrl({ type: 'image.keyframe', context: shot })).toBe('/projects/demo/episodes/4?stage=keyframes&shot=12');
    expect(jobUrl({ type: 'video.shot', context: shot })).toBe('/projects/demo/episodes/4?stage=videos&shot=12');
    const ch = ctx({ url: '/projects/demo/episodes/4/cast', character_id: 8 });
    expect(jobUrl({ type: 'image.portrait', context: ch })).toBe('/projects/demo/episodes/4?stage=cast&char=8');
    expect(jobUrl({ type: 'character.recommend', context: ctx({}) })).toBe('/projects/demo/episodes/4?stage=cast');
    expect(jobUrl({ type: 'export.episode', context: ctx({ url: '/projects/demo/episodes/4/export' }) })).toBe('/projects/demo/episodes/4?stage=export');
    expect(jobUrl({ type: 'storyboard', context: ctx({}) })).toBe('/projects/demo/episodes/4?stage=script');
  });

  it('没有剧集的任务：原样（旧形式也换掉）；没有上下文：null', () => {
    expect(jobUrl({ type: 'image.portrait', context: ctx({ url: '/projects/demo', episode_id: null, character_id: 8 }) })).toBe('/projects/demo');
    expect(jobUrl({ type: 'x', context: null })).toBeNull();
  });

  it('isSameTarget：比路径 + stage（目标带 shot / char 时也要一致），不比参数顺序和其他参数', () => {
    const t = '/projects/demo/episodes/4?stage=keyframes&shot=12';
    expect(isSameTarget(t, '/projects/demo/episodes/4', '?shot=12&stage=keyframes')).toBe(true);
    expect(isSameTarget(t, '/projects/demo/episodes/4', '?stage=keyframes')).toBe(false);
    expect(isSameTarget(t, '/projects/demo/episodes/4', '?stage=videos&shot=12')).toBe(false);
    expect(isSameTarget('/projects/demo/episodes/4?stage=script', '/projects/demo/episodes/4', '?stage=script&char=3')).toBe(true);
    expect(isSameTarget('/projects/demo/episodes/4?stage=script', '/projects/demo/episodes/5', '?stage=script')).toBe(false);
    expect(isSameTarget('/projects/demo', '/projects/demo', '')).toBe(true);
  });
});
