import { screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { KindTakes, ShotTakes, TakeStats } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeEpisode, makeShot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { ShotStage } from './ShotStage';
import { shotTakesText, takeStatsSummary } from './TakeStats';

const kind = (k: Partial<KindTakes> = {}): KindTakes => ({ takes: 0, failed: 0, selected_index: null, engines: {}, ...k });
const shotTakes = (id: number, code: string, ff: Partial<KindTakes>, video: Partial<KindTakes>): ShotTakes => ({
  shot_id: id,
  code,
  first_frame: kind(ff),
  video: kind(video),
});

const STATS: TakeStats = {
  episode_id: 4,
  shots: [
    shotTakes(11, 'S01', { takes: 3, selected_index: 3 }, { takes: 5, failed: 1, selected_index: 4 }),
    shotTakes(12, 'S02', {}, {}),
  ],
  total_takes: 8,
  failed: 1,
  first_frame_takes: 3,
  video_takes: 5,
  shots_with_video: 1,
  usable_rate: 0.2,
  avg_takes_per_finished_shot: 9,
  engines: [
    { engine: 'zimage', kind: 'first_frame', takes: 3, failed: 0 },
    { engine: 'h3_video', kind: 'video', takes: 5, failed: 1 },
  ],
  archived_takes: { video: 2 },
};

describe('抽卡统计文案', () => {
  it('镜头一行：抽数、失败、选第几条；只抽过一条不说选第几条；都没抽过为 null', () => {
    expect(shotTakesText(STATS.shots[0])).toBe('首帧 3 抽（选第 3 条） · 视频 5 抽，失败 1（选第 4 条）');
    expect(shotTakesText(shotTakes(1, 'S09', { takes: 1, selected_index: 1 }, {}))).toBe('首帧 1 抽');
    expect(shotTakesText(STATS.shots[1])).toBeNull();
    expect(shotTakesText(undefined)).toBeNull();
  });

  it('本集合计：总抽数、失败、可用率、平均每镜、按引擎、废弃镜头', () => {
    expect(takeStatsSummary(STATS)).toBe(
      '本集共抽 8 次，失败 1 次，可用率 20%，平均每镜 9.0 抽；按引擎：首帧 Z-Image 快速 3 抽、视频 H3 视频（有声） 5 抽（失败 1）；废弃镜头的抽卡 2 条',
    );
    const fresh = { ...STATS, total_takes: 0, failed: 0, usable_rate: null, avg_takes_per_finished_shot: null, engines: [], archived_takes: {} };
    expect(takeStatsSummary(fresh)).toBeNull();
    expect(takeStatsSummary({ ...fresh, total_takes: 2, first_frame_takes: 2 })).toBe('本集共抽 2 次，失败 0 次');
  });
});

describe('ShotStage 里的抽卡统计', () => {
  function Harness() {
    const [selected, setSelected] = useState<Set<number>>(() => new Set());
    const [count, setCount] = useState(1);
    const [submitting, setSubmitting] = useState(false);
    return (
      <ShotStage work={{ stage: 'videos', gate: null, selected, setSelected, count, setCount, submitting, setSubmitting, goStage: vi.fn() }} />
    );
  }

  it('工具栏下面一行本集合计，卡片上一行这一镜的抽数；没抽过的镜头不显示', async () => {
    installFetch({
      'GET /api/episodes/4': makeEpisode({
        id: 4,
        shots: [makeShot({ id: 11, code: 'S01' }), makeShot({ id: 12, code: 'S02', order: 1 })],
      }),
      'GET /api/episodes/4/take-stats': STATS,
      'GET /api/engines': { image: [], video: [] },
      'GET /api/projects/demo/locations': [],
      'GET /api/projects/demo/characters': [],
      'GET /api/jobs': [],
    });
    renderAt(<Harness />, { route: '/projects/demo/episodes/4?stage=videos', path: '/projects/:slug/episodes/:episodeId' });
    expect((await screen.findByTestId('take-stats')).textContent).toContain('本集共抽 8 次，失败 1 次，可用率 20%');
    expect(screen.getByTestId('takes-S01').textContent).toBe('首帧 3 抽（选第 3 条） · 视频 5 抽，失败 1（选第 4 条）');
    expect(screen.queryByTestId('takes-S02')).toBeNull();
  });

  it('统计接口失败时不显示，不影响镜头卡片', async () => {
    installFetch({
      'GET /api/episodes/4': makeEpisode({ id: 4, shots: [makeShot({ id: 11, code: 'S01' })] }),
      'GET /api/engines': { image: [], video: [] },
      'GET /api/projects/demo/locations': [],
      'GET /api/projects/demo/characters': [],
      'GET /api/jobs': [],
    });
    renderAt(<Harness />, { route: '/projects/demo/episodes/4?stage=videos', path: '/projects/:slug/episodes/:episodeId' });
    expect(await screen.findByTestId('shot-S01')).toBeTruthy();
    expect(screen.queryByTestId('take-stats')).toBeNull();
  });
});
