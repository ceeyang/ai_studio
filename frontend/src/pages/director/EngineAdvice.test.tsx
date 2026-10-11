import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { EpisodeEngineAdvice, ShotEngineAdvice } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeShot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { EngineAdviceChip, adviceFor } from './EngineAdvice';
import { ShotDrawer } from './ShotDrawer';

const ENGINES = {
  image: [{ name: 'zimage', family: 'zimage', caps: {} }],
  video: [
    { name: 'h3_video', family: 'h3', caps: { has_audio: true } },
    { name: 'wan22_i2v', family: 'wan', caps: { has_audio: false } },
    { name: 'jimeng_video', label: '即梦网页（Seedance）', family: 'jimeng_web', runtime: 'browser', caps: { has_audio: true } },
  ],
};
const PROJECT = { id: 1, slug: 'demo', title: 'demo', aspect: '9:16', image_engine: 'zimage', video_engine: 'h3_video' };

function check(engine: string, label: string, ok: boolean, problems: string[] = [], ready = true) {
  return { engine, label, tier: 'standard' as const, runtime: 'comfyui', ready, note: ready ? null : '用不了', ok, problems };
}

function advice(over: Partial<ShotEngineAdvice> = {}): ShotEngineAdvice {
  return {
    shot_id: 5,
    code: 'S01',
    difficulty: 'hard',
    score: 5,
    reasons: ['多人同框（3 人），容易串脸、动作对不上', '大动作（打斗），容易糊、肢体变形'],
    requirements: [],
    current_engine: 'h3_video',
    current_ok: true,
    current_problems: [],
    recommended: [
      { engine: 'jimeng_video', label: '即梦网页（Seedance）', why: '难镜头用强模型更稳，用网页会员 / 免费额度' },
      { engine: 'h3_video', label: 'H3 视频（有声）', why: '没有可用的强模型，退而用这个' },
    ],
    suggest_switch: true,
    switch_reason: '难镜头用标准档引擎容易废片，换强模型更省重抽',
    checks: [
      check('h3_video', 'H3 视频（有声）', true),
      check('wan22_i2v', 'Wan 2.2', false, ['没有声音，台词出不来、对不上口型']),
      check('jimeng_video', '即梦网页（Seedance）', true),
    ],
    ...over,
  };
}

function episodeAdvice(a: ShotEngineAdvice): EpisodeEngineAdvice {
  return { episode_id: 1, shots: [a], switch_count: a.suggest_switch ? 1 : 0 };
}

function videoSelect() {
  return document.querySelector('select[name="video_engine"]') as HTMLSelectElement;
}

describe('adviceFor', () => {
  it('草稿就是保存的引擎：按 suggest_switch；推荐第一个就是草稿时不建议', () => {
    expect(adviceFor(advice(), 'h3_video').suggestion?.engine).toBe('jimeng_video');
    expect(adviceFor(advice({ suggest_switch: false }), 'h3_video').suggestion).toBeNull();
    expect(adviceFor(advice(), 'jimeng_video').suggestion).toBeNull();
  });

  it('草稿改成了不满足要求的引擎：列问题并建议换', () => {
    const r = adviceFor(advice({ suggest_switch: false }), 'wan22_i2v');
    expect(r.problems).toEqual(['没有声音，台词出不来、对不上口型']);
    expect(r.suggestion?.engine).toBe('jimeng_video');
  });

  it('没有建议数据时什么都不显示', () => {
    expect(adviceFor(undefined, 'h3_video')).toEqual({ suggestion: null, problems: [], note: null });
  });
});

describe('ShotDrawer 引擎建议', () => {
  it('显示「建议：…」，点「用这个」只改草稿里的视频引擎，不保存', async () => {
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'GET /api/projects/demo': PROJECT,
      'GET /api/episodes/1/engine-advice': episodeAdvice(advice()),
    });
    const shot = makeShot({ id: 5, code: 'S01' });
    renderAt(<ShotDrawer shot={shot} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    const hint = await screen.findByTestId('engine-advice');
    expect(hint.textContent).toContain('建议：即梦网页');
    expect(hint.textContent).toContain('难镜头用强模型更稳');
    expect(screen.queryByTestId('engine-advice-warn')).toBeNull();
    await waitFor(() => expect(videoSelect().querySelector('option[value="jimeng_video"]')).toBeTruthy());
    expect(videoSelect().value).toBe('');
    await userEvent.setup().click(screen.getByRole('button', { name: '用这个' }));
    await waitFor(() => expect(videoSelect().value).toBe('jimeng_video'));
    expect(screen.getByText('有未保存的修改')).toBeTruthy();
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false);
    // 换成推荐的之后建议收起
    expect(screen.queryByTestId('engine-advice')).toBeNull();
  });

  it('当前引擎不满足硬性要求：一行警告', async () => {
    installFetch({
      'GET /api/engines': ENGINES,
      'GET /api/projects/demo': PROJECT,
      'GET /api/episodes/1/engine-advice': episodeAdvice(
        advice({
          current_engine: 'wan22_i2v',
          current_ok: false,
          current_problems: ['没有声音，台词出不来、对不上口型'],
          switch_reason: '当前引擎：没有声音，台词出不来、对不上口型',
        }),
      ),
    });
    const shot = makeShot({ id: 5, code: 'S01', video_engine: 'wan22_i2v' });
    renderAt(<ShotDrawer shot={shot} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    const warn = await screen.findByTestId('engine-advice-warn');
    expect(warn.textContent).toContain('不适合这一镜');
    expect(warn.textContent).toContain('没有声音');
    expect(screen.getByRole('button', { name: '用这个' })).toBeTruthy();
  });
});

describe('EngineAdviceChip', () => {
  it('有建议换的镜头时显示「N 镜建议换引擎」，没有时不显示', () => {
    const { rerender } = render(<EngineAdviceChip advice={episodeAdvice(advice())} />);
    expect(screen.getByTestId('engine-advice-summary').textContent).toBe('1 镜建议换引擎');
    expect(screen.getByTestId('engine-advice-summary').getAttribute('title')).toContain('S01');
    rerender(<EngineAdviceChip advice={episodeAdvice(advice({ suggest_switch: false }))} />);
    expect(screen.queryByTestId('engine-advice-summary')).toBeNull();
  });
});
