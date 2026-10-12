import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ClipGroup, ShotDetail } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeCandidate, makeEpisode, makeShot, makeSlot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { episodeStages } from '../stages';
import { ShotCard } from '../workspace/ShotCard';
import { cardAction, firstFrameReady, genLabel } from './shotStatus';
import { ShotDrawer } from './ShotDrawer';

const GEMINI_CAPS = { duration_control: 'fixed', allowed_seconds: [10], default_seconds: 10, max_seconds: 10, ref_mode: true, max_refs: 3 };
const ENGINES = {
  image: [{ name: 'zimage', family: 'zimage', caps: {} }],
  video: [{ name: 'gemini_video', label: 'Gemini 网页（Veo）', family: 'gemini_web', caps: GEMINI_CAPS }],
};
const PROJECT = { id: 1, slug: 'demo', title: 'demo', aspect: '9:16', image_engine: 'zimage', video_engine: 'gemini_video' };

function group(index: number, extra: Partial<ClipGroup> = {}): ClipGroup {
  return {
    lead_id: 5, lead_code: 'S01', index, size: 3, codes: ['S01', 'S02', 'S03'], shot_ids: [5, 6, 7],
    offset: [0, 3, 7][index - 1], total: 10, unit: 10, video_ready: true, ...extra,
  };
}

const VIDEO = makeSlot({ id: 50, kind: 'video', selected_candidate_id: 500, candidates: [makeCandidate({ id: 500, url: '/media/v.mp4' })] });

const handlers = () => ({
  onToggle: vi.fn(), onOpen: vi.fn(), onAction: vi.fn(), onMove: vi.fn(), onSplit: vi.fn(), onInsertAfter: vi.fn(), onDelete: vi.fn(),
});

describe('组镜：卡片', () => {
  it('显示「组镜 2/3」，成员的视频从自己的起点开始', () => {
    const shot = makeShot({ id: 6, code: 'S02', clip_group: 1, group: group(2), slots: [VIDEO] });
    render(<ShotCard shot={shot} stage="videos" selected={false} first={false} last={false} cast={[]} {...handlers()} />);
    expect(screen.getByTestId('group-S02').textContent).toBe('组镜 2/3');
    expect(screen.getByLabelText('S02 视频').getAttribute('src')).toBe('/media/v.mp4#t=3.00');
  });

  it('主镜显示「组镜 1/3」，视频从头放；不在组里没有角标', () => {
    const lead = makeShot({ id: 5, code: 'S01', clip_group: 1, group: group(1), slots: [VIDEO] });
    const r = render(<ShotCard shot={lead} stage="videos" selected={false} first={false} last={false} cast={[]} {...handlers()} />);
    expect(screen.getByTestId('group-S01').textContent).toBe('组镜 1/3');
    expect(screen.getByLabelText('S01 视频').getAttribute('src')).toBe('/media/v.mp4');
    r.unmount();
    render(<ShotCard shot={makeShot({ id: 9, code: 'S09' })} stage="videos" selected={false} first={false} last={false} cast={[]} {...handlers()} />);
    expect(screen.queryByTestId('group-S09')).toBeNull();
  });

  it('重拍按钮是「重拍整组」；成员首帧可选，等的是主镜的首帧', () => {
    const member = makeShot({ id: 6, code: 'S02', group: group(2), slots: [VIDEO] });
    expect(genLabel(member, 'videos')).toBe('重拍整组');
    expect(genLabel(member, 'keyframes')).toBe('生成首帧');
    expect(firstFrameReady(member)).toBe(true);   // 自己没有首帧也能出（看主镜）
    const waiting = makeShot({ id: 6, code: 'S02', group: group(2, { video_ready: false }) });
    expect(cardAction(waiting, 'videos')).toMatchObject({ kind: 'need_first_frame', label: '先出 S01 首帧' });
    expect(genLabel(makeShot({ id: 5, code: 'S01', group: group(1) }), 'videos')).toBe('生成整组');
  });

  it('首帧进度：成员没出首帧不算缺', () => {
    const ff = makeSlot({ id: 40, kind: 'first_frame', selected_candidate_id: 400, candidates: [makeCandidate({ id: 400 })] });
    const shots: ShotDetail[] = [
      makeShot({ id: 5, code: 'S01', order: 0, group: group(1), slots: [ff] }),
      makeShot({ id: 6, code: 'S02', order: 1, group: group(2) }),
      makeShot({ id: 7, code: 'S03', order: 2, group: group(3) }),
    ];
    const { stages } = episodeStages(makeEpisode({ shots }), [], []);
    expect(stages.find((s) => s.key === 'keyframes')?.detail).toBe('1/1');
  });
});

describe('组镜：抽屉', () => {
  it('成员：说明和谁一起生成，能拆出组镜；不显示接上一镜', async () => {
    const lead = makeShot({ id: 5, code: 'S01', order: 0, duration: 3, scene_block_id: 2, group: group(1) });
    const member = makeShot({ id: 6, code: 'S02', order: 1, duration: 4, scene_block_id: 2, group: group(2), slots: [VIDEO] });
    const last = makeShot({ id: 7, code: 'S03', order: 2, duration: 3, scene_block_id: 2, group: group(3) });
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'GET /api/projects/demo': { ...PROJECT, fixed_engine_pack: 'group' },
      'GET /api/episodes/1': makeEpisode({ shots: [lead, member, last] }),
      'POST /api/shots/6/ungroup': { ...member, group: null },
    });
    renderAt(<ShotDrawer shot={member} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    const region = await screen.findByRole('region', { name: '组镜' });
    expect(within(region).getByTestId('group-info').textContent).toContain('组镜 2/3，和 S01 一起生成');
    expect(within(region).getByTestId('group-info').textContent).toContain('这一镜从 3 秒开始');
    expect(screen.getByTestId('group-member-note')).toBeTruthy();
    expect(screen.queryByRole('region', { name: '接上一镜' })).toBeNull();
    expect(screen.queryByTestId('waste-notice')).toBeNull();
    // 组已经满 10 秒、后面没有镜头：不能再合
    await waitFor(() => expect((within(region).getByRole('button', { name: '和下一镜合成组镜' }) as HTMLButtonElement).disabled).toBe(true));
    await userEvent.setup().click(within(region).getByRole('button', { name: '拆出组镜' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/shots/6/ungroup'))).toBe(true));
    expect(screen.getByRole('button', { name: '重拍整组' })).toBeTruthy();
  });

  it('主镜：组没满时提示还能并几秒，点「和下一镜合成组镜」', async () => {
    const g = { ...group(1), size: 2, codes: ['S01', 'S02'], shot_ids: [5, 6], total: 6 };
    const lead = makeShot({ id: 5, code: 'S01', order: 0, duration: 3, scene_block_id: 2, group: g });
    const member = makeShot({ id: 6, code: 'S02', order: 1, duration: 3, scene_block_id: 2, group: { ...g, index: 2, offset: 3 } });
    const next = makeShot({ id: 7, code: 'S03', order: 2, duration: 3, scene_block_id: 2 });
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'GET /api/projects/demo': { ...PROJECT, fixed_engine_pack: 'group' },
      'GET /api/episodes/1': makeEpisode({ shots: [lead, member, next] }),
      'POST /api/shots/5/group-with-next': lead,
    });
    renderAt(<ShotDrawer shot={lead} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    const region = await screen.findByRole('region', { name: '组镜' });
    expect(within(region).getByTestId('group-info').textContent).toContain('组镜 1/2，主镜：和 S02 一起生成，视频挂在这一镜上');
    expect(within(region).getByText('这一组只用 6 秒，还能再并 4 秒。')).toBeTruthy();
    const btn = within(region).getByRole('button', { name: '和下一镜合成组镜' }) as HTMLButtonElement;
    await waitFor(() => expect(btn.disabled).toBe(false));
    await userEvent.setup().click(btn);
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/shots/5/group-with-next'))).toBe(true));
  });

  it('组镜模式的浪费提醒给「和下一镜合成组镜」', async () => {
    const shot = makeShot({ id: 5, code: 'S01', order: 0, duration: 3, scene_block_id: 2 });
    const next = makeShot({ id: 6, code: 'S02', order: 1, duration: 4, scene_block_id: 2 });
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'GET /api/projects/demo': { ...PROJECT, fixed_engine_pack: 'group' },
      'GET /api/episodes/1': makeEpisode({ shots: [shot, next] }),
      'POST /api/shots/5/group-with-next': shot,
    });
    renderAt(<ShotDrawer shot={shot} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    await screen.findByTestId('waste-notice');
    const btn = (await screen.findByRole('button', { name: '和下一镜合成组镜' })) as HTMLButtonElement;
    expect(screen.queryByRole('button', { name: '并进下一镜做成多拍镜头' })).toBeNull();
    await waitFor(() => expect(btn.disabled).toBe(false));
    await userEvent.setup().click(btn);
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/group-with-next'))).toBe(true));
  });
});
