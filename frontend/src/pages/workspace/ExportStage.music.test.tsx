// 成片阶段的配乐（E）：上传 / 替换 / 去掉、试听、导出选项带上配乐参数、标签里有「配乐」。
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { installFetch } from '../../test/fetch';
import { makeCandidate, makeEpisode, makeShot, makeSlot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { ExportStage, OPTIONS_KEY, exportTags, toOptions } from './ExportStage';

const SHOT = makeShot({
  id: 11,
  code: 'S01',
  slots: [makeSlot({ id: 1, kind: 'video', selected_candidate_id: 5, candidates: [makeCandidate({ id: 5, url: '/media/v.mp4' })] })],
});
const EP = makeEpisode({ id: 4, shots: [SHOT] });
const AT = { route: '/projects/demo/episodes/4?stage=export', path: '/projects/:slug/episodes/:episodeId' };
const BGM = { candidate_id: 77, url: '/media/demo/media/bgm/a.mp3', filename: '夜色.mp3', created_at: '2026-10-11T10:00:00' };

function routes(extra: Record<string, unknown> = {}) {
  return {
    'GET /api/episodes/4': EP,
    'GET /api/episodes/4/exports': [],
    'GET /api/projects/demo/end-card': null,
    'GET /api/projects/demo/aigc': { label_text: '本片由 AI 辅助制作', producer: 'AI Studio' },
    'GET /api/episodes/4/bgm': null,
    ...extra,
  };
}

describe('ExportStage 配乐', () => {
  beforeEach(() => localStorage.clear());

  it('还没有配乐：说明 + 上传；勾选框不能勾', async () => {
    let uploaded = false;
    const calls = installFetch(routes({
      'GET /api/episodes/4/bgm': () => (uploaded ? BGM : null),
      'POST /api/episodes/4/bgm': () => {
        uploaded = true;
        return BGM;
      },
    }));
    renderAt(<ExportStage />, AT);
    const user = userEvent.setup();
    const group = await screen.findByRole('group', { name: '配乐' });
    expect(within(group).getByText(/这一集还没有配乐/)).toBeTruthy();
    expect((within(group).getByRole('checkbox', { name: '成片垫配乐' }) as HTMLInputElement).disabled).toBe(true);
    await user.upload(within(group).getByLabelText('上传配乐'), new File(['id3'], '夜色.mp3', { type: 'audio/mpeg' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/episodes/4/bgm')).toBe(true));
    // 上传成功后自动勾上，试听播放器出现
    expect(await within(group).findByTestId('bgm-player')).toBeTruthy();
    expect((within(group).getByRole('checkbox', { name: '成片垫配乐' }) as HTMLInputElement).checked).toBe(true);
  });

  it('有配乐：试听、去掉；勾上后导出带音量、淡入淡出、压低', async () => {
    let removed = false;
    const calls = installFetch(routes({
      'GET /api/episodes/4/bgm': () => (removed ? null : BGM),
      'DELETE /api/episodes/4/bgm': () => {
        removed = true;
        return new Response(null, { status: 204 });
      },
      'POST /api/episodes/4/run': { job_ids: [9] },
    }));
    renderAt(<ExportStage />, AT);
    const user = userEvent.setup();
    const group = await screen.findByRole('group', { name: '配乐' });
    const player = (await within(group).findByTestId('bgm-player')) as HTMLAudioElement;
    expect(player.getAttribute('src')).toBe('/media/demo/media/bgm/a.mp3');
    expect(within(group).getByText('夜色.mp3')).toBeTruthy();
    await user.click(within(group).getByRole('checkbox', { name: '成片垫配乐' }));
    const vol = within(group).getByLabelText('音量（dB）');
    await user.clear(vol);
    await user.type(vol, '-24');
    await user.click(within(group).getByRole('checkbox', { name: '有台词时自动压低' }));
    await user.click(screen.getByRole('button', { name: '合成导出' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/episodes/4/run')).toBe(true));
    const run = calls.find((c) => c.url === '/api/episodes/4/run');
    expect(run?.body).toMatchObject({
      stage: 'export',
      export: { music: true, music_db: -24, music_fade_in: 1, music_fade_out: 2, music_duck: false },
    });
    expect(JSON.parse(localStorage.getItem(OPTIONS_KEY(4)) ?? '{}')).toMatchObject({ music: true, musicDb: -24, duck: false });
    await user.click(within(group).getByRole('button', { name: '去掉配乐' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/episodes/4/bgm')).toBe(true));
    expect(await within(group).findByText(/这一集还没有配乐/)).toBeTruthy();
  });

  it('选项换算：没有配乐时不带配乐；标签里有「配乐」', () => {
    const c = {
      subZh: false, subKm: false, dubKm: false, endCard: false, bed: true, alignDetected: false,
      music: true, musicDb: -18, fadeIn: 1, fadeOut: 2, duck: true,
      aigcLabel: true, aigcPos: 'top_left' as const, aigcSeconds: 3, aigcAlways: false, aigcMeta: true,
    };
    expect(toOptions(c, false).music).toBeUndefined();
    expect(toOptions(c, true)).toMatchObject({ music: true, music_db: -18, music_duck: true });
    expect(exportTags({ music: true, end_card: true })).toEqual(['配乐', '片尾卡']);
  });
});
