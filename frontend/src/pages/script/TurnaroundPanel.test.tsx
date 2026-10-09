import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Character, TurnaroundCell, TurnaroundView } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeCandidate } from '../../test/factories';
import { renderAt } from '../../test/render';
import { EventsContext } from '../../ws/EventsContext';
import { initialEvents } from '../../ws/events';
import { TurnaroundPanel } from './TurnaroundPanel';

const VIEWS: [TurnaroundView, string][] = [
  ['front_full', '正面全身'],
  ['front_half', '正面半身'],
  ['face_closeup', '脸部特写'],
  ['left_profile', '左侧'],
  ['right_profile', '右侧'],
  ['three_quarter', '3/4 侧'],
  ['back', '背面'],
  ['low_angle', '仰视'],
  ['high_angle', '俯视'],
];

function cells(fill: (view: TurnaroundView) => Partial<TurnaroundCell> = () => ({})): TurnaroundCell[] {
  return VIEWS.map(([view, label]) => ({
    view, label, description: '', selected_candidate_id: null, url: null, stale: false, candidates: [], ...fill(view),
  }));
}

const BACK_A = makeCandidate({ id: 61, url: '/media/p/back_a.png', engine: 'h3_image' });
const BACK_B = makeCandidate({ id: 62, url: '/media/p/back_b.png', engine: 'h3_image' });
const FACE = makeCandidate({ id: 63, url: '/media/p/face.png', engine: 'h3_image' });

const CHEN: Character = {
  id: 6, project_id: 1, name: '陈师傅', description: '', visual_prompt: 'lean mechanic', core_features: '',
  voice: null, portrait_candidate_id: 19, portrait_url: '/media/p/up.png', portraits: [], design_options: [],
  active_jobs: [], turnaround: cells(),
};
const PLANNED: Character = {
  ...CHEN,
  turnaround: cells((v) => {
    const base = { description: `${v} view description` };
    if (v === 'back') return { ...base, selected_candidate_id: 61, url: BACK_A.url, candidates: [BACK_A, BACK_B] };
    if (v === 'face_closeup') return { ...base, selected_candidate_id: 63, url: FACE.url, stale: true, candidates: [FACE] };
    return base;
  }),
};

function setup(c: Character, routes: Record<string, unknown> = {}, progress = {}) {
  const calls = installFetch(routes);
  renderAt(
    <EventsContext.Provider value={{ ...initialEvents, progress }}>
      <TurnaroundPanel c={c} slug="demo" />
    </EventsContext.Provider>,
    { route: '/', path: '/' },
  );
  return { calls, user: userEvent.setup() };
}

const posts = (calls: { method: string; url: string; body: unknown }[]) => calls.filter((c) => c.method !== 'GET');

describe('三视图', () => {
  it('没有视角描述时 9 格都显示，只能先生成视角描述', async () => {
    const { calls, user } = setup(CHEN, { 'POST /api/characters/6/turnaround/plan': { job_ids: [70] } });
    const panel = screen.getByRole('region', { name: '陈师傅 的三视图' });
    for (const [view, label] of VIEWS) {
      const cell = within(panel).getByTestId(`turn-${view}`);
      expect(within(cell).getByText(label)).toBeTruthy();
      expect(within(cell).getByText('未生成')).toBeTruthy();
    }
    expect((within(panel).getByRole('button', { name: '全部生成' }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(within(panel).getByRole('button', { name: '生成视角描述' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ url: '/api/characters/6/turnaround/plan' });
  });

  it('显示已出图、过期和未生成三种状态；全部生成和单格重新生成', async () => {
    const { calls, user } = setup(PLANNED, { 'POST /api/characters/6/turnaround/generate': { job_ids: [71] } });
    const back = screen.getByTestId('turn-back');
    expect((within(back).getByRole('img', { name: '陈师傅 背面' }) as HTMLImageElement).src).toContain('/media/p/back_a.png');
    expect(within(back).getByText('已生成')).toBeTruthy();
    const face = screen.getByTestId('turn-face_closeup');
    expect(within(face).getByText('过期')).toBeTruthy();
    expect(face.className).toContain('is-stale');
    expect(within(screen.getByTestId('turn-low_angle')).getByText('未生成')).toBeTruthy();
    expect(screen.getByRole('button', { name: '重写视角描述' })).toBeTruthy();

    await user.click(screen.getByRole('button', { name: '全部生成' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ url: '/api/characters/6/turnaround/generate', body: {} });
    await user.click(within(back).getByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[1]).toMatchObject({ body: { views: ['back'] } });
    await user.click(within(screen.getByTestId('turn-low_angle')).getByRole('button', { name: '生成' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(3));
    expect(posts(calls)[2]).toMatchObject({ body: { views: ['low_angle'] } });
  });

  it('改一格的视角描述后保存', async () => {
    const { calls, user } = setup(PLANNED, { 'PATCH /api/characters/6/turnaround/plan': PLANNED });
    const back = screen.getByTestId('turn-back');
    expect(within(back).queryByRole('button', { name: '保存描述' })).toBeNull();
    const ta = within(back).getByLabelText('背面 视角描述');
    await user.clear(ta);
    await user.type(ta, 'walking away, back to camera');
    await user.click(within(back).getByRole('button', { name: '保存描述' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({
      method: 'PATCH', url: '/api/characters/6/turnaround/plan', body: { views: { back: 'walking away, back to camera' } },
    });
  });

  it('点候选切换这一格的选定', async () => {
    const { calls, user } = setup(PLANNED, { 'POST /api/candidates/62/select': BACK_B });
    const back = screen.getByTestId('turn-back');
    const current = within(back).getByRole('button', { name: '背面 候选 #61（当前）' });
    expect(current.getAttribute('aria-pressed')).toBe('true');
    await user.click(current);
    await user.click(within(back).getByRole('button', { name: '背面 候选 #62' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ url: '/api/candidates/62/select' });
  });

  it('进行中的任务：格子显示生成中/排队中和进度，写描述时按钮禁用', () => {
    const busy: Character = {
      ...PLANNED,
      active_jobs: [
        { id: 80, type: 'image.turnaround', status: 'running', view: 'back' },
        { id: 81, type: 'image.turnaround', status: 'queued', view: 'low_angle' },
        { id: 82, type: 'character.turnaround_plan', status: 'running' },
      ],
    };
    setup(busy, {}, { 80: { value: 5, max: 20 } });
    const back = screen.getByTestId('turn-back');
    expect(within(back).getByTestId('turn-pending').textContent).toContain('生成中');
    expect(within(back).getByTestId('turn-pending').textContent).toContain('25%');
    expect(within(screen.getByTestId('turn-low_angle')).getByTestId('turn-pending').textContent).toContain('排队中');
    expect((screen.getByRole('button', { name: '写描述中…' }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(back).getByRole('button', { name: '重新生成' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('没有定妆照时不能出图', () => {
    setup({ ...PLANNED, portrait_candidate_id: null, portrait_url: null });
    expect((screen.getByRole('button', { name: '全部生成' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/先选定定妆照/)).toBeTruthy();
  });
});
