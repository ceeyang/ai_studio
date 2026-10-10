import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { installFetch } from '../../test/fetch';
import { NEXT_ACTION_EVENT } from '../../components/EpisodeHeader';
import { makeEpisode, makeShot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { CastStage } from './CastStage';

const LIN = { id: 8, project_id: 1, name: '林夏', description: '女主播', visual_prompt: 'young woman', core_features: '', voice: null };
const ROUTES = {
  'GET /api/episodes/4': makeEpisode({ id: 4, title: '提词器' }),
  'GET /api/projects/demo/locations': [
    { id: 3, project_id: 1, name: '直播间', time_of_day: '夜', atmosphere: '压抑', visual_prompt: 'a dim studio' },
  ],
  'GET /api/projects/demo/characters': [LIN],
  'GET /api/projects/demo/style': null,
};
const AT = { route: '/projects/demo/episodes/4?stage=cast', path: '/projects/:slug/episodes/:episodeId' };
const writes = <T extends { method: string }>(calls: T[]) => calls.filter((c) => c.method !== 'GET');

describe('CastStage', () => {
  it('三个页签：角色 / 场景 / 风格；不带页头', async () => {
    installFetch(ROUTES);
    renderAt(<CastStage />, AT);
    const user = userEvent.setup();
    expect(await screen.findByTestId('character-8')).toBeTruthy();
    expect(screen.queryByRole('heading', { level: 2 })).toBeNull();
    expect(screen.getByRole('tab', { name: '角色（1）' }).getAttribute('aria-selected')).toBe('true');
    await user.click(screen.getByRole('tab', { name: '场景（1）' }));
    expect(screen.getByTestId('character-8').closest('[hidden]')).toBeTruthy();
    expect(screen.getByTestId('location-3').closest('[hidden]')).toBeNull();
    await user.click(screen.getByRole('tab', { name: '风格' }));
    expect(screen.getByLabelText(/风格锚/).closest('[hidden]')).toBeNull();
    expect(screen.getByTestId('location-3').closest('[hidden]')).toBeTruthy();
  });

  it('切换页签不丢正在编辑的内容', async () => {
    installFetch(ROUTES);
    renderAt(<CastStage />, AT);
    const user = userEvent.setup();
    const card = await screen.findByTestId('character-8');
    const desc = within(card).getByLabelText('人物描述');
    await user.type(desc, '（夜班）');
    await user.click(screen.getByRole('tab', { name: '风格' }));
    await user.type(screen.getByLabelText('色板'), '冷蓝');
    await user.click(screen.getByRole('tab', { name: '角色（1）' }));
    expect((within(screen.getByTestId('character-8')).getByLabelText('人物描述') as HTMLTextAreaElement).value).toBe('女主播（夜班）');
    await user.click(screen.getByRole('tab', { name: '风格' }));
    expect((screen.getByLabelText('色板') as HTMLInputElement).value).toBe('冷蓝');
  });

  it('角色卡可编辑；音色是下拉，「其他（手填）」切成输入框', async () => {
    const calls = installFetch({ ...ROUTES, 'PATCH /api/characters/8': LIN });
    renderAt(<CastStage />, AT);
    const user = userEvent.setup();
    const card = await screen.findByTestId('character-8');
    const voice = within(card).getByLabelText(/配音音色/) as HTMLSelectElement;
    expect(voice.tagName).toBe('SELECT');
    expect(within(voice).getByRole('option', { name: '高棉语 · 女声' })).toBeTruthy();
    await user.selectOptions(voice, 'km-KH-SreymomNeural');
    await user.click(within(card).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0].body).toEqual({ voice: 'km-KH-SreymomNeural' });

    await user.selectOptions(voice, '其他（手填）');
    const custom = within(card).getByLabelText('配音音色（手填）');
    await user.type(custom, 'en-US-AriaNeural');
    await user.click(within(card).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    expect(writes(calls)[1].body).toEqual({ voice: 'en-US-AriaNeural' });
  });

  it('不在常用列表里的音色：直接显示成手填', async () => {
    installFetch({ ...ROUTES, 'GET /api/projects/demo/characters': [{ ...LIN, voice: 'en-US-AriaNeural' }] });
    renderAt(<CastStage />, AT);
    const card = await screen.findByTestId('character-8');
    expect((within(card).getByLabelText('配音音色（手填）') as HTMLInputElement).value).toBe('en-US-AriaNeural');
  });

  it('角色定妆照：上传后提交 multipart 并刷新', async () => {
    const withPortrait = { ...LIN, portrait_candidate_id: 31, portrait_url: '/media/demo/media/portrait/a.png' };
    let uploaded = false;
    const calls = installFetch({
      ...ROUTES,
      'GET /api/projects/demo/characters': () => [uploaded ? withPortrait : { ...withPortrait, portrait_candidate_id: null, portrait_url: null }],
      'POST /api/characters/8/portrait': () => {
        uploaded = true;
        return withPortrait;
      },
    });
    renderAt(<CastStage />, AT);
    const user = userEvent.setup();
    const card = await screen.findByTestId('character-8');
    expect(within(card).getByText('还没有定妆照')).toBeTruthy();
    await user.upload(within(card).getByLabelText('上传定妆照'), new File(['png'], 'lin.png', { type: 'image/png' }));
    const img = (await within(card).findByRole('img', { name: '林夏 定妆照' })) as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('/media/demo/media/portrait/a.png');
    expect(writes(calls)[0].url).toBe('/api/characters/8/portrait');
    const init = vi.mocked(fetch).mock.calls.find(([, i]) => i?.method === 'POST')?.[1];
    expect(init?.body).toBeInstanceOf(FormData);
  });

  it('场景卡可编辑', async () => {
    const calls = installFetch({ ...ROUTES, 'PATCH /api/locations/3': ROUTES['GET /api/projects/demo/locations'][0] });
    renderAt(<CastStage />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('tab', { name: '场景（1）' }));
    const loc = screen.getByTestId('location-3');
    await user.type(within(loc).getByLabelText('氛围'), '，安静');
    await user.click(within(loc).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]).toMatchObject({ url: '/api/locations/3', body: { atmosphere: '压抑，安静' } });
  });

  it('场景或风格改了没保存时换页面先问', async () => {
    installFetch(ROUTES);
    const { router } = renderAt(<CastStage />, AT);
    const user = userEvent.setup();
    const leave = () => act(() => void router.navigate('/jobs'));
    await user.click(await screen.findByRole('tab', { name: '风格' }));
    await user.type(screen.getByLabelText('色板'), '冷色');
    await leave();
    const dlg = await screen.findByRole('dialog', { name: '有未保存的修改，确定离开？' });
    await user.click(within(dlg).getByRole('button', { name: '留下' }));
    expect(screen.getByTestId('location').textContent).toBe('/projects/demo/episodes/4?stage=cast');
    await user.clear(screen.getByLabelText('色板'));
    await user.click(screen.getByRole('tab', { name: '场景（1）' }));
    await user.type(within(screen.getByTestId('location-3')).getByLabelText('时间'), '晚');
    await leave();
    expect(await screen.findByRole('dialog', { name: '有未保存的修改，确定离开？' })).toBeTruthy();
  });

  it('地址里带 ?char=：切回角色页签', async () => {
    installFetch(ROUTES);
    const { router } = renderAt(<CastStage />, AT);
    const user = userEvent.setup();
    await screen.findByTestId('character-8');
    await user.click(screen.getByRole('tab', { name: '风格' }));
    await act(() => void router.navigate('/projects/demo/episodes/4?stage=cast&char=8'));
    await waitFor(() => expect(screen.getByRole('tab', { name: /角色/ }).getAttribute('aria-selected')).toBe('true'));
    expect(screen.getByTestId('character-8').closest('[hidden]')).toBeNull();
  });

  it('下一步是抽定妆照：第一个缺定妆照的出场角色的「抽卡」打上 data-next-action（不是主按钮）；收到下一步事件切回角色页签', async () => {
    const LAO = { ...LIN, id: 9, name: '老周', portrait_url: null };
    installFetch({
      ...ROUTES,
      'GET /api/episodes/4': makeEpisode({
        id: 4,
        title: '提词器',
        scene_blocks: [{ id: 1, order: 0, location_id: null, text: 'a' }],
        shots: [makeShot({ id: 1, cast: [{ character_id: 8, variation_id: null }, { character_id: 9, variation_id: null }] })],
      }),
      'GET /api/projects/demo/characters': [{ ...LIN, portrait_url: '/media/p.png' }, LAO],
    });
    const { container } = renderAt(<CastStage />, AT);
    const user = userEvent.setup();
    const lao = await screen.findByTestId('character-9');
    const draw = within(lao).getByRole('button', { name: '抽卡' });
    await waitFor(() => expect(draw.hasAttribute('data-next-action')).toBe(true));
    expect(within(screen.getByTestId('character-8')).getByRole('button', { name: '抽卡' }).hasAttribute('data-next-action')).toBe(false);
    expect(container.querySelectorAll('[data-next-action]')).toHaveLength(1);
    expect(container.querySelectorAll('.primary')).toHaveLength(0);
    await user.click(screen.getByRole('tab', { name: '风格' }));
    // 下一步在别的阶段：不理
    act(() => {
      window.dispatchEvent(new CustomEvent(NEXT_ACTION_EVENT, { detail: { panel: 'script' } }));
    });
    expect(screen.getByRole('tab', { name: '风格' }).getAttribute('aria-selected')).toBe('true');
    act(() => {
      window.dispatchEvent(new CustomEvent(NEXT_ACTION_EVENT, { detail: { panel: 'cast' } }));
    });
    expect(screen.getByRole('tab', { name: /角色/ }).getAttribute('aria-selected')).toBe('true');
  });
});
