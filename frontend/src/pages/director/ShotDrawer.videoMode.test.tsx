import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFetch } from '../../test/fetch';
import { makeCandidate, makeShot, makeSlot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { ShotDrawer } from './ShotDrawer';
import { cardAction, firstFrameReady, refsMode } from './shotStatus';

const ENGINES = {
  image: [{ name: 'zimage', family: 'zimage', caps: {} }],
  video: [
    { name: 'h3_video', family: 'h3', caps: { has_audio: true, ref_mode: false } },
    { name: 'seedance_video', label: 'Seedance', family: 'seedance', caps: { has_audio: true, ref_mode: true, max_refs: 9 } },
  ],
};
const PROJECT = { id: 1, slug: 'demo', title: 'demo', aspect: '9:16', image_engine: 'zimage', video_engine: 'h3_video' };
const FF = makeSlot({ id: 9, kind: 'first_frame', selected_candidate_id: 101, candidates: [makeCandidate({ id: 101 })] });

function modeSelect() {
  return screen.getByRole('combobox', { name: '生成方式' }) as HTMLSelectElement;
}

describe('ShotDrawer 生成方式', () => {
  it('视频引擎不支持参考图直出：下拉禁用并说明', async () => {
    installFetch({ 'GET /api/engines': ENGINES, 'GET /api/projects/demo': PROJECT });
    const shot = makeShot({ id: 5, code: 'S01', slots: [FF] });
    renderAt(<ShotDrawer shot={shot} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('video-mode-unsupported')).toBeTruthy());
    expect(modeSelect().disabled).toBe(true);
    expect(modeSelect().value).toBe('first_frame');
    expect(screen.getByTestId('video-mode-unsupported').textContent).toContain('不支持参考图直出');
  });

  it('支持参考图直出的引擎：可选，切换后提交 video_mode', async () => {
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'GET /api/projects/demo': PROJECT,
      'PATCH /api/shots/5': (c: { body: unknown }) => ({ ...shot, ...(c.body as object) }),
    });
    const shot = makeShot({ id: 5, code: 'S01', video_engine: 'seedance_video', slots: [FF] });
    renderAt(<ShotDrawer shot={shot} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    await waitFor(() => expect(modeSelect().disabled).toBe(false));
    expect(screen.queryByTestId('video-mode-unsupported')).toBeNull();
    await userEvent.setup().selectOptions(modeSelect(), 'refs');
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ video_mode: 'refs' });
  });

  it('参考图直出：首帧区折叠成「用到的参考图」，首帧按钮禁用；改回首帧提交 null', async () => {
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'GET /api/projects/demo': PROJECT,
      'PATCH /api/shots/5': (c: { body: unknown }) => ({ ...shot, ...(c.body as object) }),
    });
    const shot = makeShot({
      id: 5,
      code: 'S01',
      video_engine: 'seedance_video',
      video_mode: 'refs',
      slots: [FF],
      video_refs: [
        { candidate_id: 1, label: '场景图·修车行', kind: 'location', url: '/media/a.png', used: true },
        { candidate_id: 2, label: '陈师傅 定妆照', kind: 'portrait', url: '/media/b.png', used: true },
        { candidate_id: 3, label: '道具·扳手', kind: 'prop', url: '/media/c.png', used: false },
      ],
    });
    renderAt(<ShotDrawer shot={shot} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    expect(screen.getByRole('list', { name: '用到的参考图' })).toBeTruthy();
    expect(screen.getByAltText('陈师傅 定妆照')).toBeTruthy();
    expect(screen.getByText(/道具·扳手（超出视频引擎上限，带不上）/)).toBeTruthy();
    expect(screen.getByTestId('first-frame-collapsed')).toBeTruthy();
    expect((screen.getByRole('button', { name: '参考图直出，不出首帧' }) as HTMLButtonElement).disabled).toBe(true);
    // 视频这一格不要求首帧
    expect(screen.getByRole('button', { name: '生成视频' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '先出首帧' })).toBeNull();
    await waitFor(() => expect(modeSelect().disabled).toBe(false));
    expect(modeSelect().value).toBe('refs');
    await userEvent.setup().selectOptions(modeSelect(), 'first_frame');
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ video_mode: null });
  });

  it('选了参考图直出、引擎又不支持：仍能改回首帧，并提示会被拒绝', async () => {
    installFetch({ 'GET /api/engines': ENGINES, 'GET /api/projects/demo': PROJECT });
    const shot = makeShot({ id: 5, code: 'S01', video_mode: 'refs', slots: [] });
    renderAt(<ShotDrawer shot={shot} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('video-mode-unsupported').textContent).toContain('会被拒绝'));
    expect(modeSelect().disabled).toBe(false);
    expect((screen.getByRole('option', { name: '参考图直出' }) as HTMLOptionElement).disabled).toBe(true);
  });

  it('开了接上一镜：参考图直出不生效，按首帧模式', () => {
    const shot = makeShot({ id: 5, code: 'S02', video_mode: 'refs', continue_from_prev: true, slots: [] });
    expect(refsMode(shot)).toBe(false);
    expect(refsMode({ ...shot, continue_from_prev: false })).toBe(true);
    const direct = { ...shot, continue_from_prev: false };
    expect(firstFrameReady(direct)).toBe(true);
    expect(cardAction(direct, 'keyframes').kind).toBe('refs_direct');
    expect(cardAction(direct, 'videos').kind).toBe('generate');
  });
});
