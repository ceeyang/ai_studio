import { describe, expect, it } from 'vitest';
import { stageSearch, toWorkspaceUrl, workspacePath } from './urls';

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
