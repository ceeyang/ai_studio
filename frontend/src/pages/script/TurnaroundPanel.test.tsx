import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Character, Turnaround, TurnaroundCell, TurnaroundSheet, TurnaroundView } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeCandidate } from '../../test/factories';
import { renderAt } from '../../test/render';
import { EventsContext } from '../../ws/EventsContext';
import { initialEvents } from '../../ws/events';
import { TurnaroundPanel } from './TurnaroundPanel';

const BODY_VIEWS: [TurnaroundView, string][] = [
  ['front_full', '正面全身'],
  ['three_quarter', '3/4 侧'],
  ['profile', '侧面'],
  ['back', '背面'],
];
const FACE_VIEWS: [TurnaroundView, string][] = [
  ['face_front', '正脸'],
  ['face_three_quarter', '3/4 脸'],
  ['face_profile', '侧脸'],
];

function sheet(key: 'body' | 'face', over: Partial<TurnaroundSheet> = {}): TurnaroundSheet {
  return {
    sheet: key,
    label: key === 'body' ? '全身三视图' : '头像',
    views: (key === 'body' ? BODY_VIEWS : FACE_VIEWS).map(([v]) => v),
    selected_candidate_id: null,
    url: null,
    stale: false,
    crop_method: null,
    candidates: [],
    ...over,
  };
}

function cells(fill: (view: TurnaroundView) => Partial<TurnaroundCell> = () => ({})): TurnaroundCell[] {
  return [
    ...BODY_VIEWS.map(([view, label]) => ({ view, label, sheet: 'body' as const })),
    ...FACE_VIEWS.map(([view, label]) => ({ view, label, sheet: 'face' as const })),
  ].map((c) => ({ ...c, selected_candidate_id: null, url: null, stale: false, ...fill(c.view) }));
}

const SHEET_A = makeCandidate({ id: 61, url: '/media/p/sheet_a.png', engine: 'h3_image' });
const SHEET_B = makeCandidate({ id: 62, url: '/media/p/sheet_b.png', engine: 'h3_image' });
const FACE = makeCandidate({ id: 63, url: '/media/p/face.png', engine: 'h3_image' });

const EMPTY: Turnaround = { look: '', default_engine: 'h3_image', sheets: [sheet('body'), sheet('face')], cells: cells() };
const CHEN: Character = {
  id: 6, project_id: 1, name: '陈师傅', description: '', visual_prompt: 'lean mechanic', core_features: '',
  voice: null, portrait_candidate_id: 19, portrait_url: '/media/p/up.png', portraits: [], design_options: [],
  active_jobs: [], turnaround: EMPTY,
};
const DONE: Character = {
  ...CHEN,
  turnaround: {
    look: 'A lean 45-year-old mechanic in a navy work jacket',
    default_engine: 'h3_image',
    sheets: [
      sheet('body', { selected_candidate_id: 61, url: SHEET_A.url, crop_method: 'segment', candidates: [SHEET_A, SHEET_B] }),
      sheet('face', { selected_candidate_id: 63, url: FACE.url, stale: true, crop_method: 'equal', candidates: [FACE] }),
    ],
    cells: cells((v) =>
      v.startsWith('face_')
        ? { selected_candidate_id: 90, url: `/media/p/${v}.png`, stale: true }
        : { selected_candidate_id: 80, url: `/media/p/${v}.png` },
    ),
  },
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
  it('还没出图：两种设定图和 7 个视角都显示未生成，可以全部生成', async () => {
    const { calls, user } = setup(CHEN, { 'POST /api/characters/6/turnaround/generate': { job_ids: [70, 71] } });
    const panel = screen.getByRole('region', { name: '陈师傅 的三视图' });
    for (const key of ['body', 'face']) {
      expect(within(within(panel).getByTestId(`sheet-${key}`)).getByText('未生成')).toBeTruthy();
    }
    for (const [view, label] of [...BODY_VIEWS, ...FACE_VIEWS]) {
      const cell = within(panel).getByTestId(`turn-${view}`);
      expect(within(cell).getByText(label)).toBeTruthy();
    }
    // 不再有逐格视角描述
    expect(within(panel).queryByLabelText(/视角描述/)).toBeNull();
    await user.click(within(panel).getByRole('button', { name: '全部生成' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ url: '/api/characters/6/turnaround/generate', body: {} });
  });

  it('显示设定图大图、切好的格子和过期；单种重新生成', async () => {
    const { calls, user } = setup(DONE, { 'POST /api/characters/6/turnaround/generate': { job_ids: [72] } });
    const body = screen.getByTestId('sheet-body');
    expect((within(body).getByRole('img', { name: '陈师傅 全身三视图' }) as HTMLImageElement).src).toContain('/media/p/sheet_a.png');
    expect(within(body).getByText('已生成')).toBeTruthy();
    const back = screen.getByTestId('turn-back');
    expect((within(back).getByRole('img', { name: '陈师傅 背面' }) as HTMLImageElement).src).toContain('/media/p/back.png');
    const face = screen.getByTestId('sheet-face');
    expect(within(within(face).getByText('头像').parentElement!).getByText('过期')).toBeTruthy();
    expect(face.className).toContain('is-stale');
    expect(screen.getByTestId('turn-face_profile').className).toContain('is-stale');
    // 等宽切的给出提示
    expect(within(face).getByText(/现在是等宽切的/)).toBeTruthy();
    expect(within(body).queryByText(/现在是等宽切的/)).toBeNull();

    await user.click(within(face).getByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ body: { sheets: ['face'] } });
  });

  it('点候选切换设定图；重新切图', async () => {
    const { calls, user } = setup(DONE, {
      'POST /api/characters/6/turnaround/sheets/body/select': DONE,
      'POST /api/characters/6/turnaround/sheets/body/crop': DONE,
    });
    const body = screen.getByTestId('sheet-body');
    const current = within(body).getByRole('button', { name: '全身三视图 候选 #61（当前）' });
    expect(current.getAttribute('aria-pressed')).toBe('true');
    await user.click(current);
    await user.click(within(body).getByRole('button', { name: '全身三视图 候选 #62' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({
      url: '/api/characters/6/turnaround/sheets/body/select', body: { candidate_id: 62 },
    });
    await user.click(within(body).getByRole('button', { name: '重新切图' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[1]).toMatchObject({ url: '/api/characters/6/turnaround/sheets/body/crop', body: { method: 'auto' } });
    await user.click(within(body).getByRole('button', { name: '等宽切图' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(3));
    expect(posts(calls)[2]).toMatchObject({ body: { method: 'equal' } });
  });

  it('全身描述：AI 整理和手改保存', async () => {
    const { calls, user } = setup(DONE, {
      'POST /api/characters/6/turnaround/look': { job_ids: [73] },
      'PATCH /api/characters/6/turnaround/look': DONE,
    });
    const ta = screen.getByLabelText('全身描述');
    expect((ta as HTMLTextAreaElement).value).toContain('navy work jacket');
    expect(screen.queryByRole('button', { name: '保存描述' })).toBeNull();
    await user.clear(ta);
    await user.type(ta, 'navy jacket, black shoes');
    await user.click(screen.getByRole('button', { name: '保存描述' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({
      method: 'PATCH', url: '/api/characters/6/turnaround/look', body: { look: 'navy jacket, black shoes' },
    });
    await user.click(screen.getByRole('button', { name: '重写全身描述' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[1]).toMatchObject({ url: '/api/characters/6/turnaround/look' });
  });

  it('进行中的任务：设定图显示生成中和进度，按钮禁用', () => {
    const busy: Character = {
      ...DONE,
      active_jobs: [
        { id: 80, type: 'image.turnaround', status: 'running', sheet: 'body' },
        { id: 81, type: 'image.turnaround', status: 'queued', sheet: 'face' },
        { id: 82, type: 'character.turnaround_look', status: 'running' },
      ],
    };
    setup(busy, {}, { 80: { value: 5, max: 20 } });
    const body = screen.getByTestId('sheet-body');
    expect(within(body).getByTestId('sheet-pending').textContent).toContain('生成中');
    expect(within(body).getByTestId('sheet-pending').textContent).toContain('25%');
    expect(within(screen.getByTestId('sheet-face')).getByTestId('sheet-pending').textContent).toContain('排队中');
    expect((within(body).getByRole('button', { name: '重新生成' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '整理中…' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('设定图采样完成（value>=max）但任务还在跑：显示"解码保存中"而不是卡在 100%', () => {
    const busy: Character = {
      ...DONE,
      active_jobs: [{ id: 80, type: 'image.turnaround', status: 'running', sheet: 'body' }],
    };
    setup(busy, {}, { 80: { value: 20, max: 20 } });
    const pending = within(screen.getByTestId('sheet-body')).getByTestId('sheet-pending');
    expect(pending.textContent).toContain('解码保存中');
    expect(pending.textContent).not.toContain('100%');
    expect(pending.querySelector('.spinner')).toBeTruthy();
  });

  it('可以选别的引擎（浏览器）出设定图，请求带上 engine', async () => {
    const { calls, user } = setup(CHEN, {
      'GET /api/engines': { image: [{ name: 'zimage', family: 'zimage', runtime: 'comfyui', caps: { max_refs: 0 } }, { name: 'gpt_image', family: 'chatgpt_web', runtime: 'browser', caps: { max_refs: 4 } }], video: [] },
      'POST /api/characters/6/turnaround/generate': { job_ids: [70, 71] },
    });
    const pick = screen.getByLabelText('三视图引擎');
    expect(within(pick).getByRole('option', { name: '默认（H3 图像）' })).toBeTruthy();
    await waitFor(() => expect(within(pick).getByRole('option', { name: 'ChatGPT 网页' })).toBeTruthy());
    await user.selectOptions(pick, 'gpt_image');
    await user.click(screen.getByRole('button', { name: '全部生成' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ url: '/api/characters/6/turnaround/generate', body: { engine: 'gpt_image' } });
  });

  it('「默认（…）」按设置页三视图这一步算，请求里不带 engine（让后端按设置选）', async () => {
    const { calls, user } = setup(CHEN, {
      'GET /api/settings': { steps: [{ key: 'turnaround', label: '三视图', kind: 'image', value: 'gpt_image', effective: 'gpt_image', fallback: 'h3_image', source: 'step' }] },
      'POST /api/characters/6/turnaround/generate': { job_ids: [70, 71] },
    });
    const pick = screen.getByLabelText('三视图引擎');
    await waitFor(() => expect(within(pick).getByRole('option', { name: '默认（ChatGPT 网页）' })).toBeTruthy());
    expect(calls.some((c) => c.url === '/api/settings?project=demo')).toBe(true);
    await user.click(screen.getByRole('button', { name: '全部生成' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect((posts(calls)[0].body as { engine?: string }).engine).toBeUndefined();
  });

  it('默认引擎要定妆照锁脸：没有定妆照时不能出图', () => {
    setup({ ...CHEN, portrait_candidate_id: null, portrait_url: null });
    expect((screen.getByRole('button', { name: '全部生成' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/先选定定妆照/)).toBeTruthy();
  });

  it('全身描述改了没保存：「全部生成」「生成」变「保存描述并生成」，先保存再出图', async () => {
    const { calls, user } = setup(CHEN, {
      'PATCH /api/characters/6/turnaround/look': CHEN,
      'POST /api/characters/6/turnaround/generate': { job_ids: [70] },
    });
    await user.type(screen.getByLabelText('全身描述'), 'navy jacket, black boots');
    expect(screen.queryByRole('button', { name: '全部生成' })).toBeNull();
    const face = screen.getByTestId('sheet-face');
    await user.click(within(face).getByRole('button', { name: '保存描述并生成' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[0]).toMatchObject({ method: 'PATCH', url: '/api/characters/6/turnaround/look', body: { look: 'navy jacket, black boots' } });
    expect(posts(calls)[1]).toMatchObject({ url: '/api/characters/6/turnaround/generate', body: { sheets: ['face'] } });
  });

  it('全部生成：保存描述并生成也走同一条路', async () => {
    const { calls, user } = setup(CHEN, {
      'PATCH /api/characters/6/turnaround/look': CHEN,
      'POST /api/characters/6/turnaround/generate': { job_ids: [70, 71] },
    });
    await user.type(screen.getByLabelText('全身描述'), 'navy jacket');
    const head = screen.getByRole('region', { name: '陈师傅 的三视图' }).querySelector('.turn-head') as HTMLElement;
    await user.click(within(head).getByRole('button', { name: '保存描述并生成' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[0].body).toEqual({ look: 'navy jacket' });
    expect(posts(calls)[1].body).toEqual({});
  });

  it('全部生成只提交没在跑的设定图；全在跑时禁用并写明原因', async () => {
    const one: Character = { ...CHEN, active_jobs: [{ id: 80, type: 'image.turnaround', status: 'running', sheet: 'body' }] };
    const { calls, user } = setup(one, { 'POST /api/characters/6/turnaround/generate': { job_ids: [71] } });
    await user.click(screen.getByRole('button', { name: /^全部生成/ }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0].body).toEqual({ sheets: ['face'] });
  });

  it('两张都在跑：全部生成禁用，旁边写原因', () => {
    const both: Character = {
      ...CHEN,
      active_jobs: [
        { id: 80, type: 'image.turnaround', status: 'running', sheet: 'body' },
        { id: 81, type: 'image.turnaround', status: 'queued', sheet: 'face' },
      ],
    };
    setup(both);
    expect((screen.getByRole('button', { name: /^全部生成/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('两张设定图都在生成')).toBeTruthy();
  });

  it('能不能出图看已保存的描述：没外观提示词也没保存的描述时禁用并写明原因', () => {
    setup({ ...CHEN, visual_prompt: '' });
    expect((screen.getByRole('button', { name: '全部生成' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('先写全身描述或外观提示词')).toBeTruthy();
  });

  it('全身描述改了没保存时换页面先问', async () => {
    installFetch({});
    const { router } = renderAt(
      <EventsContext.Provider value={{ ...initialEvents, progress: {} }}>
        <TurnaroundPanel c={DONE} slug="demo" />
      </EventsContext.Provider>,
      { route: '/', path: '/' },
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('全身描述'), '，戴手套');
    act(() => {
      void router.navigate('/elsewhere');
    });
    const dlg = await screen.findByRole('dialog', { name: '有未保存的修改，确定离开？' });
    await user.click(within(dlg).getByRole('button', { name: '留下' }));
    expect(screen.getByTestId('location').textContent).toBe('/');
  });
});
