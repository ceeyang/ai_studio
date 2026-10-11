// 各处点图片 / 视频都在当前页的看图弹窗里打开，不再开新标签。
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Asset, Character, ExportRow, JobTrace } from '../api/types';
import { AssetPicker } from '../components/AssetPicker';
import { installFetch } from '../test/fetch';
import { makeCandidate, makeEpisode, makeShot, makeSlot } from '../test/factories';
import { renderAt } from '../test/render';
import { EventsContext } from '../ws/EventsContext';
import { initialEvents } from '../ws/events';
import { ArchivedCandidates } from './director/ArchivedCandidates';
import { CandidateGrid } from './director/CandidateGrid';
import { JobTraces } from './jobs/JobTraces';
import { TurnaroundPanel } from './script/TurnaroundPanel';
import { CastStage } from './workspace/CastStage';
import { ExportStage } from './workspace/ExportStage';

let winOpen: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  winOpen = vi.spyOn(window, 'open').mockImplementation(() => null);
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
});
afterEach(() => {
  expect(winOpen).not.toHaveBeenCalled();
  vi.restoreAllMocks();
});

/** 页面上（弹窗外）不再有开新标签的媒体链接 */
function noBlankLinks(root: HTMLElement) {
  const links = Array.from(root.querySelectorAll('a[target="_blank"]')).filter((a) => !a.closest('[role="dialog"].media-viewer'));
  expect(links).toHaveLength(0);
}

const viewer = (name: string | RegExp) => screen.findByRole('dialog', { name });
const shown = (d: HTMLElement) => (d.querySelector('.mv-viewport img, .mv-viewport video') as HTMLElement).getAttribute('src');

describe('看图弹窗接入点', () => {
  it('镜头抽屉的首帧候选：点缩略图弹窗看大图，同一格候选左右切换，弹窗里能直接选定', async () => {
    const onSelect = vi.fn();
    const slot = makeSlot({
      id: 5, kind: 'first_frame', selected_candidate_id: 101,
      candidates: [
        makeCandidate({ id: 101, url: '/media/a.png', engine: 'web_gemini', created_at: '2026-10-11T21:42:40' }),
        makeCandidate({ id: 102, url: '/media/b.png' }),
      ],
    });
    const { container } = renderAt(<CandidateGrid slot={slot} shotCode="S01" onSelect={onSelect} />);
    noBlankLinks(container);
    const user = userEvent.setup();
    // 新的在前：#102 是第一张
    await user.click(screen.getByRole('button', { name: '看大图：候选 #101' }));
    const d = await viewer('S01 首帧候选');
    expect(d.getAttribute('aria-modal')).toBe('true');
    expect(shown(d)).toBe('/media/a.png');
    expect(within(d).getByText('2 / 2')).toBeTruthy();
    expect(within(d).getByText(/^S01 首帧候选 · .+ · /)).toBeTruthy();
    expect(within(d).getByText('已选定')).toBeTruthy();
    await user.keyboard('{ArrowRight}');
    expect(shown(d)).toBe('/media/b.png');
    await user.click(within(d).getByRole('button', { name: '选定' }));
    expect(onSelect).toHaveBeenCalledWith(102);
  });

  it('视频候选：点缩略图在弹窗里播放，不再内嵌播放器', async () => {
    const slot = makeSlot({ kind: 'video', candidates: [makeCandidate({ id: 201, url: '/media/x.mp4' })] });
    renderAt(<CandidateGrid slot={slot} shotCode="S02" onSelect={() => {}} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '播放候选 #201' }));
    const d = await viewer('S02 视频候选');
    const v = d.querySelector('video') as HTMLVideoElement;
    expect(v.getAttribute('src')).toBe('/media/x.mp4');
    expect(v.controls).toBe(true);
  });

  it('定妆照：点开是看图弹窗（适应窗口 / 原始大小、序号），不换造型', async () => {
    const UPLOAD = makeCandidate({ id: 19, engine: 'upload', url: '/media/p/up.png' });
    const DRAWN = makeCandidate({ id: 46, engine: 'h3_image', url: '/media/p/ai.png' });
    const CHEN: Character = {
      id: 6, project_id: 1, name: '陈师傅', description: '', visual_prompt: 'lean mechanic', core_features: '',
      voice: null, portrait_candidate_id: 19, portrait_url: '/media/p/up.png', portraits: [UPLOAD, DRAWN],
      design_options: [], active_jobs: [], turnaround: { look: '', default_engine: 'h3_image', sheets: [], cells: [] },
    };
    const calls = installFetch({
      'GET /api/episodes/5': makeEpisode({ id: 5, shots: [makeShot({ id: 1, cast: [{ character_id: 6, variation_id: null }] })] }),
      'GET /api/projects/demo/locations': [],
      'GET /api/projects/demo/style': null,
      'GET /api/projects/demo/characters': [CHEN],
    });
    renderAt(<CastStage />, { route: '/projects/demo/episodes/5?stage=cast&char=6', path: '/projects/:slug/episodes/:episodeId' });
    const drawer = await screen.findByRole('dialog', { name: '角色 陈师傅' });
    const user = userEvent.setup();
    await user.click(within(drawer).getByRole('button', { name: '定妆照 #46' }));
    const d = await viewer('陈师傅 定妆照');
    expect(d.closest('.modal-scrim')!.className).toContain('media-viewer-scrim');
    expect(shown(d)).toBe('/media/p/ai.png');
    expect(within(d).getByText('2 / 2')).toBeTruthy();
    await user.click(within(d).getByRole('button', { name: '原始大小' }));
    expect(screen.getByTestId('media-viewer-stage').className).toContain('is-actual');
    expect(calls.filter((c) => c.method !== 'GET')).toHaveLength(0);
  });

  it('三视图：设定图和切图一组，点切图从那一格开始看', async () => {
    const SHEET = makeCandidate({ id: 61, url: '/media/p/sheet_a.png', engine: 'h3_image' });
    const C: Character = {
      id: 6, project_id: 1, name: '陈师傅', description: '', visual_prompt: 'x', core_features: '',
      voice: null, portrait_candidate_id: 19, portrait_url: '/media/p/up.png', portraits: [], design_options: [], active_jobs: [],
      turnaround: {
        look: 'x', default_engine: 'h3_image',
        sheets: [{ sheet: 'body', label: '全身三视图', views: ['front_full', 'back'], selected_candidate_id: 61, url: SHEET.url, stale: false, crop_method: 'segment', candidates: [SHEET] }],
        cells: [
          { view: 'front_full', label: '正面全身', sheet: 'body', selected_candidate_id: 80, url: '/media/p/front.png', stale: false },
          { view: 'back', label: '背面', sheet: 'body', selected_candidate_id: 80, url: '/media/p/back.png', stale: false },
        ],
      },
    };
    installFetch({});
    const { container } = renderAt(
      <EventsContext.Provider value={initialEvents}>
        <TurnaroundPanel c={C} slug="demo" />
      </EventsContext.Provider>,
    );
    noBlankLinks(container);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '看大图：陈师傅 背面' }));
    const d = await viewer('陈师傅 全身三视图');
    expect(shown(d)).toBe('/media/p/back.png');
    expect(within(d).getByText('3 / 3')).toBeTruthy();
    await user.keyboard('{ArrowRight}');
    expect(shown(d)).toBe('/media/p/sheet_a.png');
  });

  it('导出的成片：「放大播放」在弹窗里播，所有成片一组', async () => {
    const row = (p: Partial<ExportRow>): ExportRow => ({
      id: 1, episode_id: 4, variant: '9x16', file_path: 'demo/exports/a.mp4', url: '/media/a.mp4', settings: {},
      label: '第一版', is_final: false, created_at: '2026-10-08T11:36:09', ...p,
    });
    installFetch({
      'GET /api/episodes/4': makeEpisode({ id: 4, shots: [] }),
      'GET /api/episodes/4/exports': [row({ id: 3, is_final: true }), row({ id: 2, url: '/media/b.mp4', label: '第二版', file_path: 'demo/exports/b.mp4' })],
      'GET /api/projects/demo/end-card': null,
    });
    renderAt(<ExportStage />, { route: '/projects/demo/episodes/4?stage=export', path: '/projects/:slug/episodes/:episodeId' });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '放大播放 第二版' }));
    const d = await viewer('成片');
    expect(shown(d)).toBe('/media/b.mp4');
    expect(within(d).getByText('2 / 2')).toBeTruthy();
    await user.keyboard('{ArrowLeft}');
    expect(shown(d)).toBe('/media/a.mp4');
    expect(within(d).getByText('定稿')).toBeTruthy();
    // 下载链接还在
    expect(screen.getAllByTestId('export-link')[0].hasAttribute('download')).toBe(true);
  });

  it('资产选择器：点缩略图仍是选中；放大镜单独打开看图弹窗，Esc 只关看图', async () => {
    const asset = (id: string, label: string): Asset => ({
      id, kind: 'character', slot_kind: 'portrait', media: 'image', label, source: '', url: `/media/${id}.png`, thumb_url: `/media/${id}.png`,
    });
    installFetch({ 'GET /api/episodes/7/assets': [asset('cand-1', '陈师傅 定妆照'), asset('cand-2', '小李 定妆照')] });
    renderAt(<AssetPicker episodeId={7} title="选图" max={2} onConfirm={() => {}} onClose={() => {}} />);
    const user = userEvent.setup();
    const pick = await screen.findByRole('button', { name: '选择 小李 定妆照' });
    await user.click(pick);
    expect(screen.getByRole('button', { name: '取消选择 小李 定妆照' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.queryByRole('dialog', { name: '角色资产' })).toBeNull();
    await user.click(screen.getByRole('button', { name: '看大图：陈师傅 定妆照' }));
    const d = await viewer('角色资产');
    expect(shown(d)).toBe('/media/cand-1.png');
    // 看图弹窗叠在选择器上面
    const z = (el: HTMLElement) => Number((el.closest('.modal-scrim') as HTMLElement).style.zIndex || 40);
    expect(z(d)).toBeGreaterThan(z(screen.getByRole('dialog', { name: '选图' })));
    await user.click(within(d).getByRole('button', { name: '选择' }));
    expect(within(d).getByRole('button', { name: '取消选择' })).toBeTruthy();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '角色资产' })).toBeNull());
    expect(screen.getByRole('dialog', { name: '选图' })).toBeTruthy();
    expect(screen.getByTestId('asset-picked-count').textContent).toContain('已选 2/2 张');
  });

  it('历史候选：视频和图片一组在弹窗里看，弹窗里能恢复', async () => {
    const ROW = {
      id: 7, episode_id: 3, shot_code: 'S04', kind: 'video', file_path: 'demo/v/old.mp4', url: '/media/demo/v/old.mp4',
      engine: 'h3_video', params: {}, note: '', was_selected: true, shot_snapshot: { action: '她抬头' },
      reason: 'storyboard', created_at: '2026-10-01T10:00:00', archived_at: '2026-10-02T10:00:00',
    };
    const FF = { ...ROW, id: 8, kind: 'first_frame', url: '/media/demo/ff/old.png', was_selected: false };
    const calls = installFetch({ 'GET /api/episodes/3/archived-candidates': [ROW, FF], 'POST /api/archived-candidates/8/restore': {} });
    const { container } = renderAt(<ArchivedCandidates episodeId={3} shotId={5} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '历史候选' }));
    await user.click(await screen.findByRole('button', { name: '看大图：历史候选 #8' }));
    noBlankLinks(container);
    const d = await viewer('历史候选');
    expect(shown(d)).toBe('/media/demo/ff/old.png');
    await user.keyboard('{ArrowLeft}');
    expect(d.querySelector('video')).toBeTruthy();
    expect(within(d).getByText('当时选定')).toBeTruthy();
    await user.keyboard('{ArrowRight}');
    await user.click(within(d).getByRole('button', { name: '恢复到这个镜头' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
  });

  it('任务调用明细：产物视频也在弹窗里播，不再开新标签', async () => {
    const T: JobTrace = {
      id: 1, seq: 1, kind: 'engine', title: '视频', created_at: null,
      data: { engine: 'h3_video', output_kind: 'video', what: '视频 S01', params: {}, candidate_files: ['demo/media/video/a.mp4'] },
    };
    installFetch({ 'GET /api/jobs/7/traces': { job_id: 7, traces: [T] } });
    const { container } = renderAt(<JobTraces jobId={7} active={false} />);
    const user = userEvent.setup();
    await user.click(await screen.findByText(/视频 · h3_video/));
    noBlankLinks(container.querySelector('.trace-pics') as HTMLElement);
    await user.click(screen.getByRole('button', { name: '播放视频：a.mp4' }));
    const d = await viewer('产物');
    expect(shown(d)).toBe('/media/demo/media/video/a.mp4');
  });
});
