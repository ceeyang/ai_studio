import { describe, expect, it } from 'vitest';
import { makeJob } from '../test/factories';
import { eventsReducer, initialEvents, invalidationsFor, isDecoding } from './events';

describe('eventsReducer', () => {
  it('记录连接状态；只要连上过一次，everConnected 就一直是 true（哪怕后来断开）', () => {
    expect(initialEvents.connected).toBe(false);
    expect(initialEvents.everConnected).toBe(false);
    const s = eventsReducer(initialEvents, { type: 'connected' });
    expect(s.connected).toBe(true);
    expect(s.everConnected).toBe(true);
    const dropped = eventsReducer(s, { type: 'disconnected' });
    expect(dropped.connected).toBe(false);
    expect(dropped.everConnected).toBe(true);
  });

  it('进度按任务记录；任务仍在运行时保留，结束后清掉', () => {
    let s = eventsReducer(initialEvents, { type: 'progress', job_id: 5, value: 3, max: 20 });
    expect(s.progress[5]).toEqual({ value: 3, max: 20 });
    s = eventsReducer(s, { type: 'job', job: makeJob({ id: 5, status: 'running' }) });
    expect(s.progress[5]).toEqual({ value: 3, max: 20 });
    s = eventsReducer(s, { type: 'job', job: makeJob({ id: 5, status: 'done' }) });
    expect(s.progress[5]).toBeUndefined();
  });

  it('日志和队列事件不改 reducer 状态', () => {
    const s = eventsReducer(initialEvents, { type: 'log', job_id: 1, line: 'x' });
    expect(s).toBe(initialEvents);
  });
});

describe('isDecoding', () => {
  it('采样还没到 max：不是解码阶段', () => {
    expect(isDecoding({ value: 3, max: 20 })).toBe(false);
  });

  it('value 到了 max：进了解码/保存阶段（WS 的采样进度到头之后，后面没有事件覆盖）', () => {
    expect(isDecoding({ value: 20, max: 20 })).toBe(true);
    expect(isDecoding({ value: 21, max: 20 })).toBe(true); // 容错：万一超一点也当作到头
  });

  it('没有进度或 max 是 0（还没收到真的进度事件）：不是解码阶段', () => {
    expect(isDecoding(undefined)).toBe(false);
    expect(isDecoding({ value: 0, max: 0 })).toBe(false);
  });
});

describe('invalidationsFor', () => {
  it('任务事件总是刷新任务列表、队列和这个任务', () => {
    expect(invalidationsFor({ type: 'job', job: makeJob({ id: 3, status: 'running' }) })).toEqual([
      ['jobs'],
      ['queue'],
      ['job', 3],
    ]);
  });

  it('任务结束时刷新剧集和成片；剧本/分镜结束时还要刷新角色、场景、风格', () => {
    const keysDone = invalidationsFor({ type: 'job', job: makeJob({ id: 3, type: 'export.episode', status: 'done' }) });
    expect(keysDone).toContainEqual(['episode']);
    expect(keysDone).toContainEqual(['exports']);
    // 导出任务结束会把这一集的状态改成 exported，项目页的剧集列表要跟着刷新
    expect(keysDone).toContainEqual(['episodes']);
    expect(keysDone).not.toContainEqual(['characters']);
    const keysScript = invalidationsFor({ type: 'job', job: makeJob({ id: 4, type: 'script', status: 'waiting_input' }) });
    expect(keysScript).toEqual(expect.arrayContaining([['episodes'], ['characters'], ['locations'], ['style']]));
  });

  it('抽卡、推荐造型和三视图任务一有状态变化就刷新角色（显示进行中）', () => {
    for (const type of ['image.portrait', 'character.suggest', 'image.turnaround', 'character.turnaround_look']) {
      expect(invalidationsFor({ type: 'job', job: makeJob({ id: 5, type, status: 'running' }) })).toContainEqual(['characters']);
    }
    expect(invalidationsFor({ type: 'job', job: makeJob({ id: 5, type: 'image.keyframe', status: 'running' }) })).not.toContainEqual([
      'characters',
    ]);
  });

  it('日志事件只刷新那个任务；进度和队列事件不刷新任何查询', () => {
    expect(invalidationsFor({ type: 'log', job_id: 9, line: 'x' })).toEqual([['job', 9]]);
    expect(invalidationsFor({ type: 'progress', job_id: 9, value: 1, max: 2 })).toEqual([]);
    expect(
      invalidationsFor({ type: 'queue', paused: false, counts: { queued: 0, running: 0, waiting_input: 0 }, current_family: null }),
    ).toEqual([]);
  });
});
