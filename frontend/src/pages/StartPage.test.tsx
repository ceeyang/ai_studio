import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import type { IdeaOption } from '../api/types';
import { type FetchCall, installFetch, json } from '../test/fetch';
import { renderAt } from '../test/render';
import { DRAFT_KEY, StartPage } from './StartPage';

const ENGINES = {
  image: [
    { name: 'h3_image', family: 'h3', caps: {} },
    { name: 'zimage', family: 'zimage', caps: {} },
  ],
  video: [{ name: 'h3_video', family: 'h3', caps: { has_audio: true } }],
};

function opt(title: string, genre = '悬疑'): IdeaOption {
  return { title, logline: `${title}的一句话`, hook: `${title}的钩子`, twist: `${title}的反转`, ending: `${title}的结尾`, genre };
}

const FIRST = [opt('错送的外卖'), opt('十年后的我', '科幻'), opt('最后一单', '温情'), opt('门后的人')];
const MORE = [opt('外卖箱里的信'), opt('迟到的晚餐', '喜剧'), opt('楼道灯'), opt('备注栏')];

const where = () => decodeURIComponent(screen.getByTestId('location').textContent ?? '');

beforeEach(() => localStorage.clear());

describe('StartPage（从一个点子开始）', () => {
  it('点子 → 方向（再来 4 个接在后面）→ 合并两个写梗概 → 按要求改写 → 开始制作，跳到工作台带 ?autopilot=1', async () => {
    let asked = 0;
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'POST /api/ideas/options': () => ({ options: asked++ === 0 ? FIRST : MORE }),
      'POST /api/ideas/expand': (c: FetchCall) =>
        (c.body as { instruction?: string }).instruction
          ? { title: '不该用', synopsis: '改写后的梗概：主角是女生。' }
          : { title: '错送的十年', synopsis: '外卖员送错一单，开门的是十年后的自己。' },
      'POST /api/ideas/start': json({ slug: '错送的十年', episode_id: 7, episode_number: 1, job_ids: [1, 2] }, 201),
    });
    renderAt(<StartPage />, { route: '/start', path: '/start' });
    const user = userEvent.setup();

    // ① 点子：默认 15 秒、9:16、真人写实
    expect((screen.getByRole('radio', { name: '15 秒' }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('radio', { name: '9:16 竖屏' }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('radio', { name: /真人写实/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('button', { name: '出几个方向' }) as HTMLButtonElement).disabled).toBe(true);
    await user.type(screen.getByLabelText('一句话点子'), '外卖员送错了一单');
    await user.click(screen.getByRole('radio', { name: '30 秒' }));
    await user.click(screen.getByRole('radio', { name: '16:9 横屏' }));
    await user.click(screen.getByRole('button', { name: '出几个方向' }));

    // ② 方向
    await screen.findByText('错送的外卖');
    expect(calls.find((c) => c.url.endsWith('/api/ideas/options'))?.body).toEqual({
      idea: '外卖员送错了一单', target_seconds: 30, style_preset: 'live_action', count: 4,
    });
    const cards = screen.getByRole('group', { name: '方向' });
    expect(within(cards).getByText('错送的外卖的钩子')).toBeTruthy();
    expect(within(cards).getByText('科幻')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '再来 4 个' }));
    await screen.findByText('外卖箱里的信');
    const more = calls.filter((c) => c.url.endsWith('/api/ideas/options'))[1];
    expect((more.body as { exclude: string[] }).exclude).toEqual(FIRST.map((o) => o.title));
    // 之前的卡片还在
    expect(within(cards).getAllByRole('checkbox')).toHaveLength(8);
    expect(screen.getByText('错送的外卖')).toBeTruthy();

    await user.click(screen.getByRole('checkbox', { name: '选「错送的外卖」' }));
    expect(screen.getByRole('button', { name: '用这个方向写梗概' })).toBeTruthy();
    await user.click(screen.getByRole('checkbox', { name: '选「十年后的我」' }));
    await user.click(screen.getByRole('button', { name: '合并这两个写梗概' }));

    // ③ 梗概
    const synopsis = (await screen.findByLabelText('梗概')) as HTMLTextAreaElement;
    expect(synopsis.value).toBe('外卖员送错一单，开门的是十年后的自己。');
    expect((screen.getByLabelText('片名') as HTMLInputElement).value).toBe('错送的十年');
    expect(calls.find((c) => c.url.endsWith('/api/ideas/expand'))?.body).toEqual({
      idea: '外卖员送错了一单', target_seconds: 30, style_preset: 'live_action', options: [FIRST[0], FIRST[1]],
    });
    await user.type(screen.getByLabelText('修改要求'), '主角换成女生');
    await user.click(screen.getByRole('button', { name: '按要求改写' }));
    await waitFor(() => expect((screen.getByLabelText('梗概') as HTMLTextAreaElement).value).toBe('改写后的梗概：主角是女生。'));
    const rewrite = calls.filter((c) => c.url.endsWith('/api/ideas/expand'))[1];
    expect(rewrite.body).toMatchObject({
      options: [FIRST[0], FIRST[1]], current: '外卖员送错一单，开门的是十年后的自己。', instruction: '主角换成女生',
    });
    // 片名用户可能改过：改写只换梗概
    expect((screen.getByLabelText('片名') as HTMLInputElement).value).toBe('错送的十年');
    await user.click(screen.getByRole('button', { name: '下一步：确认' }));

    // ④ 确认
    const name = screen.getByLabelText('项目名') as HTMLInputElement;
    expect(name.value).toBe('错送的十年');
    await screen.findByRole('option', { name: 'Z-Image 快速' });
    await user.selectOptions(screen.getByLabelText('图像引擎'), 'h3_image');
    await user.click(screen.getByRole('button', { name: '开始制作' }));
    await waitFor(() => expect(where()).toBe('/projects/错送的十年/episodes/7?autopilot=1'));
    expect(calls.find((c) => c.url.endsWith('/api/ideas/start'))?.body).toEqual({
      title: '错送的十年',
      synopsis: '改写后的梗概：主角是女生。',
      target_seconds: 30,
      aspect: '16:9',
      style_preset: 'live_action',
      image_engine: 'h3_image',
      video_engine: 'h3_video',
    });
    // 建好了：草稿清掉
    expect(localStorage.getItem(DRAFT_KEY)).toBeNull();
  });

  it('刷新后从本机草稿接着来；「重新开始」清掉', async () => {
    localStorage.setItem(
      DRAFT_KEY,
      JSON.stringify({
        step: 3, idea: '一个点子', seconds: 60, aspect: '9:16', preset: 'anime', options: FIRST, picked: [2],
        title: '最后一单', synopsis: '草稿里的梗概',
      }),
    );
    installFetch({ 'GET /api/engines': ENGINES });
    renderAt(<StartPage />, { route: '/start', path: '/start' });
    const user = userEvent.setup();
    expect((screen.getByLabelText('梗概') as HTMLTextAreaElement).value).toBe('草稿里的梗概');
    expect((screen.getByLabelText('片名') as HTMLInputElement).value).toBe('最后一单');
    // 回到方向：选中的卡片还在
    await user.click(screen.getByRole('button', { name: '换个方向' }));
    expect((screen.getByRole('checkbox', { name: '选「最后一单」' }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/60 秒 · 9:16 · 日系动漫/)).toBeTruthy();
    // 改动也记下来
    await user.click(screen.getByRole('checkbox', { name: '选「门后的人」' }));
    expect(JSON.parse(localStorage.getItem(DRAFT_KEY)!).picked).toEqual([2, 3]);

    await user.click(screen.getByRole('button', { name: '重新开始' }));
    expect((screen.getByLabelText('一句话点子') as HTMLTextAreaElement).value).toBe('');
    expect((screen.getByRole('radio', { name: '15 秒' }) as HTMLInputElement).checked).toBe(true);
    expect(JSON.parse(localStorage.getItem(DRAFT_KEY)!).options).toEqual([]);
  });

  it('文本模型失败（502）：把后端的说明显示出来，还停在第一步', async () => {
    installFetch({
      'GET /api/engines': ENGINES,
      'POST /api/ideas/options': json({ detail: '文本模型没按格式回答：缺少 options' }, 502),
    });
    renderAt(<StartPage />, { route: '/start', path: '/start' });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('一句话点子'), '点子');
    await user.click(screen.getByRole('button', { name: '出几个方向' }));
    expect(await screen.findByText(/文本模型没按格式回答：缺少 options/)).toBeTruthy();
    expect(screen.getByLabelText('一句话点子')).toBeTruthy();
    expect((screen.getByRole('button', { name: '出几个方向' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('等文本模型时按钮显示「正在想…」', async () => {
    let release: (v: unknown) => void = () => {};
    installFetch({
      'GET /api/engines': ENGINES,
      'POST /api/ideas/options': () => new Promise((r) => (release = r)),
    });
    renderAt(<StartPage />, { route: '/start', path: '/start' });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('一句话点子'), '点子');
    await user.click(screen.getByRole('button', { name: '出几个方向' }));
    expect(((await screen.findByRole('button', { name: '正在想…' })) as HTMLButtonElement).disabled).toBe(true);
    release({ options: FIRST });
    expect(await screen.findByText('错送的外卖')).toBeTruthy();
  });
});
