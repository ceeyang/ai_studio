import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { installFetch } from '../../test/fetch';
import { makeCandidate, makeEpisode, makeShot, makeSlot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { ExportStage, OPTIONS_KEY, exportTags } from './ExportStage';

function withVideo(id: number, code: string) {
  return makeShot({
    id,
    code,
    order: id,
    slots: [
      makeSlot({
        id: id * 10, kind: 'video', selected_candidate_id: id * 100,
        candidates: [makeCandidate({ id: id * 100, url: `/media/v${id}.mp4` })],
      }),
    ],
  });
}

const AT = { route: '/projects/demo/episodes/4?stage=export', path: '/projects/:slug/episodes/:episodeId' };

function routes(extra: Record<string, unknown> = {}) {
  return {
    'GET /api/episodes/4': makeEpisode({ id: 4, shots: [withVideo(11, 'S01')] }),
    'GET /api/episodes/4/exports': [],
    'GET /api/projects/demo/end-card': null,
    'GET /api/projects/demo/aigc': { label_text: '本片由 AI 辅助制作', producer: 'AI Studio' },
    'POST /api/episodes/4/run': { job_ids: [51] },
    ...extra,
  };
}

const group = () => screen.getByRole('group', { name: 'AI 标识' });

describe('ExportStage · AI 标识', () => {
  beforeEach(() => localStorage.clear());

  it('默认带画面角标（左上角、片头 3 秒）和元数据', async () => {
    const calls = installFetch(routes());
    renderAt(<ExportStage />, AT);
    const user = userEvent.setup();
    await screen.findByTestId('readiness');
    expect((screen.getByLabelText('画面角标') as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText('写入隐式标识') as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText('角标位置') as HTMLSelectElement).value).toBe('top_left');
    expect((screen.getByLabelText('片头显示（秒）') as HTMLInputElement).value).toBe('3');
    await user.click(screen.getByRole('button', { name: '合成导出' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({
      export: { aigc_label: true, aigc_label_position: 'top_left', aigc_label_seconds: 3, aigc_label_always: false, aigc_metadata: true },
    });
  });

  it('改位置、整集常显、关元数据都带进导出选项；整集常显时秒数不可改', async () => {
    const calls = installFetch(routes());
    renderAt(<ExportStage />, AT);
    const user = userEvent.setup();
    await screen.findByTestId('readiness');
    await user.selectOptions(screen.getByLabelText('角标位置'), '右下角');
    const secs = screen.getByLabelText('片头显示（秒）') as HTMLInputElement;
    await user.clear(secs);
    await user.type(secs, '5');
    await user.click(screen.getByLabelText('整集常显'));
    expect(secs.disabled).toBe(true);
    await user.click(screen.getByLabelText('写入隐式标识'));
    await user.click(screen.getByRole('button', { name: '合成导出' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({
      export: { aigc_label: true, aigc_label_position: 'bottom_right', aigc_label_seconds: 5, aigc_label_always: true, aigc_metadata: false },
    });
    // 选项按剧集记在浏览器里
    expect(JSON.parse(localStorage.getItem(OPTIONS_KEY(4)) ?? '{}')).toMatchObject({ aigcPos: 'bottom_right', aigcAlways: true, aigcMeta: false });
  });

  it('关掉角标：位置不可选，并提示不合规', async () => {
    installFetch(routes());
    renderAt(<ExportStage />, AT);
    const user = userEvent.setup();
    await screen.findByTestId('readiness');
    await user.click(screen.getByLabelText('画面角标'));
    expect((screen.getByLabelText('角标位置') as HTMLSelectElement).disabled).toBe(true);
    expect(within(group()).getByText(/不符合 AI 微短剧的标识要求/)).toBeTruthy();
  });

  it('读回本地保存的位置；秒数超范围夹到 60', async () => {
    localStorage.setItem(OPTIONS_KEY(4), JSON.stringify({ aigcPos: 'top_right', aigcSeconds: 999 }));
    installFetch(routes());
    renderAt(<ExportStage />, AT);
    await screen.findByTestId('readiness');
    expect((screen.getByLabelText('角标位置') as HTMLSelectElement).value).toBe('top_right');
    expect((screen.getByLabelText('片头显示（秒）') as HTMLInputElement).value).toBe('60');
  });

  it('本地存的位置是坏值时用默认左上角', async () => {
    localStorage.setItem(OPTIONS_KEY(4), JSON.stringify({ aigcPos: 'middle' }));
    installFetch(routes());
    renderAt(<ExportStage />, AT);
    await screen.findByTestId('readiness');
    expect((screen.getByLabelText('角标位置') as HTMLSelectElement).value).toBe('top_left');
  });

  it('项目的角标文字和出品方：改了保存走 PUT', async () => {
    const calls = installFetch(routes({ 'PUT /api/projects/demo/aigc': (c: { body: unknown }) => c.body }));
    renderAt(<ExportStage />, AT);
    const user = userEvent.setup();
    const producer = (await screen.findByLabelText('出品方')) as HTMLInputElement;
    await waitFor(() => expect(producer.value).toBe('AI Studio'));
    expect((screen.getByLabelText('角标文字') as HTMLInputElement).value).toBe('本片由 AI 辅助制作');
    const save = screen.getByRole('button', { name: '保存标识设置' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    await user.clear(producer);
    await user.type(producer, '星河影业');
    await user.click(save);
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.find((c) => c.method === 'PUT')).toMatchObject({
      url: '/api/projects/demo/aigc',
      body: { label_text: '本片由 AI 辅助制作', producer: '星河影业' },
    });
  });

  it('成片标签：关了角标、角标没烧上时标出来', () => {
    expect(exportTags({ aigc_label: false })).toEqual(['无 AI 角标']);
    expect(exportTags({ aigc_label: true, aigc_label_burned: false })).toEqual(['AI 角标没烧上']);
    expect(exportTags({ aigc_label: true, aigc_label_burned: true })).toEqual([]);
  });
});
