import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { Beat } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeEpisode, makeShot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { ShotCard } from '../workspace/ShotCard';
import { generationUnit, wasteOf, wasteText } from './beats';
import { ShotDrawer } from './ShotDrawer';

const GEMINI_CAPS = { duration_control: 'fixed', allowed_seconds: [10], default_seconds: 10, max_seconds: 10, ref_mode: true, max_refs: 3 };
const MUSE_CAPS = { duration_control: 'prompt', allowed_seconds: [3, 5, 10], default_seconds: 10, max_seconds: 10 };
const ENGINES = {
  image: [{ name: 'zimage', family: 'zimage', caps: {} }],
  video: [
    { name: 'gemini_video', label: 'Gemini 网页（Veo）', family: 'gemini_web', caps: GEMINI_CAPS },
    { name: 'h3_video', family: 'h3', caps: { duration_control: 'param', default_seconds: 5, max_seconds: 10 } },
  ],
};
const PROJECT = { id: 1, slug: 'demo', title: 'demo', aspect: '9:16', image_engine: 'zimage', video_engine: 'gemini_video' };
const BEATS: Beat[] = [
  { start: 0, end: 3.5, shot_size: '中景', camera_move: 'static', action: 'The young woman sets the cup down' },
  { start: 3.5, end: 7, shot_size: '特写', camera_move: 'slow push in', action: 'Close on the cup' },
];

describe('固定时长引擎：生成单元和浪费', () => {
  it('和后端同一套算法', () => {
    expect(generationUnit(GEMINI_CAPS, 3)).toBe(10);
    expect(generationUnit(MUSE_CAPS)).toBe(10);
    expect(generationUnit(MUSE_CAPS, 4)).toBe(5);
    expect(generationUnit({ duration_control: 'param' })).toBeNull();
    expect(wasteOf(GEMINI_CAPS, 3)).toEqual({ unit: 10, waste: 7 });
    expect(wasteOf(GEMINI_CAPS, 9)).toBeNull();
    expect(wasteOf(GEMINI_CAPS, 3, BEATS)).toBeNull();
    expect(wasteOf(MUSE_CAPS, 3)).toBeNull();
    expect(wasteText(GEMINI_CAPS, 3, 10, 7)).toBe('这个引擎每次固定出 10 秒，这一镜只用 3 秒，会浪费 7 秒');
  });
});

describe('ShotDrawer 浪费提醒和多拍镜头', () => {
  it('固定时长引擎、短镜头：提醒浪费，点按钮并进下一镜', async () => {
    const shot = makeShot({ id: 5, code: 'S01', order: 0, duration: 3, scene_block_id: 2 });
    const next = makeShot({ id: 6, code: 'S02', order: 1, duration: 4, scene_block_id: 2 });
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'GET /api/projects/demo': PROJECT,
      'GET /api/episodes/1': makeEpisode({ shots: [shot, next] }),
      'POST /api/shots/5/merge-next-as-beats': { ...shot, duration: 7, beats: BEATS },
    });
    renderAt(<ShotDrawer shot={shot} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    const notice = await screen.findByTestId('waste-notice');
    expect(notice.textContent).toContain('这个引擎每次固定出 10 秒，这一镜只用 3 秒，会浪费 7 秒');
    const btn = screen.getByRole('button', { name: '并进下一镜做成多拍镜头' }) as HTMLButtonElement;
    await waitFor(() => expect(btn.disabled).toBe(false));
    await userEvent.setup().click(btn);
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/merge-next-as-beats'))).toBe(true));
  });

  it('下一镜在别的段落或合起来超过一次生成：按钮禁用并说明', async () => {
    const shot = makeShot({ id: 5, code: 'S01', order: 0, duration: 3, scene_block_id: 2 });
    const next = makeShot({ id: 6, code: 'S02', order: 1, duration: 4, scene_block_id: 3 });
    installFetch({
      'GET /api/engines': ENGINES,
      'GET /api/projects/demo': PROJECT,
      'GET /api/episodes/1': makeEpisode({ shots: [shot, next] }),
    });
    renderAt(<ShotDrawer shot={shot} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('waste-notice').textContent).toContain('在另一个段落'));
    expect((screen.getByRole('button', { name: '并进下一镜做成多拍镜头' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('能精确控时长的引擎、多拍镜头：不提醒', async () => {
    installFetch({ 'GET /api/engines': ENGINES, 'GET /api/projects/demo': PROJECT });
    const shot = makeShot({ id: 5, code: 'S01', duration: 3, video_engine: 'h3_video' });
    const { unmount } = renderAt(<ShotDrawer shot={shot} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    await waitFor(() => expect(screen.getByRole('combobox', { name: '生成方式' })).toBeTruthy());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId('waste-notice')).toBeNull();
    unmount();
    const beats = makeShot({ id: 5, code: 'S01', duration: 7, beats: BEATS });
    renderAt(<ShotDrawer shot={beats} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    await screen.findByRole('region', { name: '多拍镜头' });
    expect(screen.queryByTestId('waste-notice')).toBeNull();
  });

  it('多拍编辑：改一拍保存走 PATCH beats；改回普通镜头提交 null', async () => {
    const shot = makeShot({ id: 5, code: 'S01', duration: 7, beats: BEATS });
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'GET /api/projects/demo': PROJECT,
      'PATCH /api/shots/5': (c: { body: unknown }) => ({ ...shot, ...(c.body as object) }),
    });
    renderAt(<ShotDrawer shot={shot} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    const region = await screen.findByRole('region', { name: '多拍镜头' });
    expect(within(region).getByText('多拍镜头（2 拍）')).toBeTruthy();
    const user = userEvent.setup();
    const action = within(region).getByLabelText('第 2 拍动作');
    await user.clear(action);
    await user.type(action, 'Close on her hand');
    const end = within(region).getByLabelText('第 1 拍结束');
    await user.clear(end);
    await user.type(end, '3');
    await user.click(within(region).getByRole('button', { name: '保存拍' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    const body = calls.find((c) => c.method === 'PATCH')?.body as { beats: Beat[] };
    expect(body.beats[1].action).toBe('Close on her hand');
    expect(body.beats[0].end).toBe(3);
    await user.click(within(region).getByRole('button', { name: '改回普通镜头' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(2));
    expect(calls.filter((c) => c.method === 'PATCH')[1].body).toEqual({ beats: null });
  });
});

describe('ShotCard 多拍角标', () => {
  it('多拍镜头显示「N 拍」', () => {
    const h = { onToggle: vi.fn(), onOpen: vi.fn(), onAction: vi.fn(), onMove: vi.fn(), onSplit: vi.fn(), onInsertAfter: vi.fn(), onDelete: vi.fn() };
    const shot = makeShot({ id: 11, code: 'S01', duration: 7, beats: [...BEATS, { ...BEATS[1], start: 7, end: 9 }] });
    render(<ShotCard shot={shot} stage="keyframes" selected={false} first={false} last={false} cast={[]} {...h} />);
    expect(screen.getByTestId('beats-S01').textContent).toBe('3 拍');
  });

  it('普通镜头没有角标', () => {
    const h = { onToggle: vi.fn(), onOpen: vi.fn(), onAction: vi.fn(), onMove: vi.fn(), onSplit: vi.fn(), onInsertAfter: vi.fn(), onDelete: vi.fn() };
    render(<ShotCard shot={makeShot({ id: 12, code: 'S02' })} stage="keyframes" selected={false} first={false} last={false} cast={[]} {...h} />);
    expect(screen.queryByTestId('beats-S02')).toBeNull();
  });
});
