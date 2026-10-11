import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Prop } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeCandidate } from '../../test/factories';
import { renderAt } from '../../test/render';
import { PropCard, propDrawable, propUses } from './PropImages';

const ENGINES = { image: [{ name: 'zimage', family: 'zimage', caps: {} }], video: [] };

function prop(p: Partial<Prop> = {}): Prop {
  return {
    id: 3,
    project_id: 1,
    name: 'a dented red thermos',
    description: '',
    visual_prompt: '',
    images: [],
    active_jobs: [],
    uses: [
      { episode_number: 1, shot_code: 'S01' },
      { episode_number: 1, shot_code: 'S04' },
      { episode_number: 2, shot_code: 'S02' },
    ],
    ...p,
  };
}

describe('PropCard', () => {
  it('列出用到的镜头；出道具图、上传、换选定', async () => {
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'POST /api/props/3/images/draw': { job_ids: [1] },
      'POST /api/props/3/images/41/use': prop(),
    });
    const p = prop({
      image_candidate_id: 40,
      images: [makeCandidate({ id: 40, engine: 'upload' }), makeCandidate({ id: 41, engine: 'zimage' })],
    });
    renderAt(<PropCard prop={p} slug="demo" />);
    expect(screen.getByText('用在：第1集 S01、S04；第2集 S02')).toBeTruthy();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '出道具图' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/props/3/images/draw')).toBe(true));
    expect(calls.find((c) => c.url === '/api/props/3/images/draw')?.body).toEqual({ count: 1 });
    const strip = screen.getByRole('list', { name: 'a dented red thermos 的道具图候选' });
    expect(within(strip).getByText('当前')).toBeTruthy();
    await user.click(within(strip).getByRole('button', { name: '把道具图 #41 设为道具图' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/props/3/images/41/use')).toBe(true));
  });

  it('没有英文说法时不能出图', () => {
    installFetch({ 'GET /api/engines': ENGINES });
    renderAt(<PropCard prop={prop({ name: '红色保温杯' })} slug="demo" />);
    expect((screen.getByRole('button', { name: '出道具图' }) as HTMLButtonElement).disabled).toBe(true);
    expect(propDrawable(prop({ name: '红色保温杯', visual_prompt: 'a red thermos' }))).toBe(true);
    expect(propUses(prop({ uses: [] }))).toBe('');
  });
});
