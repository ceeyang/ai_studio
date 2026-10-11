import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { NextStep, Stage } from '../stages';
import { isSceneSkipped, setSceneSkipped } from './sceneSkip';
import { StageBar } from './StageBar';

const STAGES: Stage[] = [
  { key: 'script', label: '剧本', state: 'done', detail: '1 段 · 2 镜' },
  { key: 'cast', label: '角色', state: 'active', detail: '1/1 · 场景图 0/1' },
  { key: 'keyframes', label: '首帧', state: 'todo', detail: '0/2' },
  { key: 'videos', label: '视频', state: 'todo', detail: '0/2' },
  { key: 'export', label: '成片', state: 'todo', detail: '还没有成片' },
];
const SCENES: NextStep = { label: '生成场景图（1 个）', stage: 'cast', action: 'run', count: 1, stale: false, skippable: true, kind: 'scenes' };

describe('StageBar', () => {
  it('可跳过的下一步（场景图）：主按钮旁有「跳过」，点了回调', async () => {
    const onSkip = vi.fn();
    render(<StageBar stages={STAGES} current="cast" next={SCENES} onSelect={vi.fn()} onRun={vi.fn()} onSkip={onSkip} />);
    expect(screen.getByRole('button', { name: '下一步：生成场景图（1 个）' })).toBeTruthy();
    expect(screen.getByText('1/1 · 场景图 0/1')).toBeTruthy();
    await userEvent.setup().click(screen.getByRole('button', { name: '跳过' }));
    expect(onSkip).toHaveBeenCalledOnce();
  });

  it('不可跳过的下一步没有「跳过」', () => {
    const next: NextStep = { label: '抽定妆照（1 个角色缺）', stage: 'cast', action: 'run', count: 1, stale: false };
    render(<StageBar stages={STAGES} current="cast" next={next} onSelect={vi.fn()} onRun={vi.fn()} onSkip={vi.fn()} />);
    expect(screen.queryByRole('button', { name: '跳过' })).toBeNull();
  });

  it('跳过按集记下（存不了时也在这次打开的页面里算跳过）', () => {
    expect(isSceneSkipped(4321)).toBe(false);
    setSceneSkipped(4321, true);
    expect(isSceneSkipped(4321)).toBe(true);
    expect(isSceneSkipped(4322)).toBe(false);
    setSceneSkipped(4321, false);
    expect(isSceneSkipped(4321)).toBe(false);
  });
});
