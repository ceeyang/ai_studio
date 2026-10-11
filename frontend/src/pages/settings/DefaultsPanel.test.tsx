import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { AppSettings } from '../../api/types';
import { installFetch, json } from '../../test/fetch';
import { renderAt } from '../../test/render';
import { SettingsPage } from '../SettingsPage';
import { settingsPatch } from './DefaultsPanel';

const STEP = (key: string, label: string, kind: 'llm' | 'image' | 'video', effective: string, value: string | null = null, fallback = effective) =>
  ({ key, label, kind, value, effective, fallback, source: value ? 'step' : 'default' });

function settings(over: Partial<AppSettings> = {}): AppSettings {
  return {
    text_model: { value: null, effective: 'claude', effective_label: 'Claude（本机 Claude Code）', fallback: 'claude', fallback_label: 'Claude（本机 Claude Code）', source: 'config' },
    image_engine: { value: null, effective: 'zimage', fallback: 'zimage', source: 'default' },
    video_engine: { value: null, effective: 'h3_video', fallback: 'h3_video', source: 'default' },
    steps: [
      STEP('script', '剧本', 'llm', 'claude'),
      STEP('storyboard', '分镜', 'llm', 'claude'),
      STEP('subtitles.translate', '字幕翻译（配音前）', 'llm', 'claude'),
      STEP('portrait', '角色定妆照', 'image', 'zimage'),
      STEP('turnaround', '三视图', 'image', 'h3_image'),
    ],
    project: null,
    choices: {
      llm: [
        { id: 'claude', label: 'Claude（本机 Claude Code）', kind: 'claude' },
        { id: 'lmstudio', label: '本机 LM Studio（lmstudio，占显卡）', kind: 'openai' },
      ],
      image: [
        { name: 'zimage', label: 'Z-Image 快速', runtime: 'comfyui', family: 'zimage', enabled: true, ready: true },
        { name: 'krea2', label: 'Krea2 人像', runtime: 'comfyui', family: 'krea2', enabled: true, ready: true },
        { name: 'h3_image', label: 'H3 图像', runtime: 'comfyui', family: 'h3', enabled: true, ready: true },
        { name: 'gpt_image', label: 'ChatGPT 网页', runtime: 'browser', family: 'chatgpt_web', enabled: true, ready: true },
        {
          name: 'rh_flux', label: 'Flux', runtime: 'runninghub', family: 'runninghub', enabled: true, ready: false,
          note: 'Flux（RunningHub 云端）不能用：没有设置 RunningHub API key',
        },
      ],
      video: [
        { name: 'h3_video', label: 'H3 视频（有声）', runtime: 'comfyui', family: 'h3', enabled: true, ready: true },
        { name: 'wan22_i2v', label: 'Wan2.2', runtime: 'comfyui', family: 'wan22', enabled: true, ready: true },
      ],
    },
    ...over,
  };
}

function base(extra: Record<string, unknown> = {}) {
  return installFetch({
    'GET /api/doctor': [],
    'GET /api/engines': { image: [], video: [] },
    'GET /api/config': {},
    'GET /api/settings': settings(),
    'GET /api/api-services': { services: [] },
    ...extra,
  });
}

describe('设置页「默认模型」', () => {
  it('在页面最上面：文本模型 / 新项目的图片来源 / 新项目的视频来源，中文名，默认项写明现在用的', async () => {
    base();
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const panel = await screen.findByRole('region', { name: '默认模型' });
    const doctor = screen.getByRole('heading', { name: '环境检查' });
    expect(panel.compareDocumentPosition(doctor) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const text = (await screen.findByLabelText('文本模型')) as HTMLSelectElement;
    expect(text.value).toBe('');
    expect(within(text).getByRole('option', { name: '按配置文件（Claude（本机 Claude Code））' })).toBeTruthy();
    expect(within(text).getByRole('option', { name: '本机 LM Studio（lmstudio，占显卡）' })).toBeTruthy();
    const img = screen.getByLabelText('新项目的图片来源');
    expect(within(img).getByRole('option', { name: '内置默认（Z-Image 快速）' })).toBeTruthy();
    expect(within(img).getByRole('option', { name: 'Krea2 人像' })).toBeTruthy();
    expect(within(img).getByRole('option', { name: 'ChatGPT 网页' })).toBeTruthy();
    expect(within(img).getByRole('option', { name: 'Flux（RunningHub 云端）（现在不可用）' })).toBeTruthy();
    expect(screen.getByText(/没有设置 RunningHub API key/)).toBeTruthy();
    const vid = screen.getByLabelText('新项目的视频来源');
    expect(within(vid).getByRole('option', { name: '内置默认（H3 视频（有声））' })).toBeTruthy();
    expect(screen.getAllByText('只影响之后新建的项目；已有项目在项目里改图像/视频引擎')).toHaveLength(2);
    expect(screen.getByText('设置页只管默认，项目和镜头上选的总是优先。改了马上生效，不用重启。')).toBeTruthy();
    // 整页只有一个主按钮
    expect(document.querySelectorAll('button.primary')).toHaveLength(1);
    expect((screen.getByRole('button', { name: '保存' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('改了才能保存；只提交改动的字段；保存后显示「已保存」', async () => {
    const saved = settings({ image_engine: { value: 'krea2', effective: 'krea2', fallback: 'zimage', source: 'settings' } });
    const calls = base({ 'PUT /api/settings': saved });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText('新项目的图片来源'), 'krea2');
    const state = screen.getByTestId('defaults-state');
    expect(state.getAttribute('role')).toBe('status');
    expect(state.getAttribute('aria-live')).toBe('polite');
    expect(state.textContent).toBe('有改动，还没保存');
    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ image_engine: 'krea2' });
    await waitFor(() => expect(screen.getByTestId('defaults-state').textContent).toBe('已保存，马上生效'));
  });

  it('「按步骤单独设置」默认收起；每行「跟随默认（…）」或指定一个；改回跟随默认提交 null', async () => {
    const st = settings();
    st.steps = st.steps.map((s) => (s.key === 'script' ? { ...s, value: 'lmstudio', effective: 'lmstudio', source: 'step' } : s));
    const calls = base({ 'GET /api/settings': st, 'PUT /api/settings': settings() });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const user = userEvent.setup();
    const details = (await screen.findByText(/按步骤单独设置/)).closest('details')!;
    expect(details.open).toBe(false);
    expect(details.querySelector('summary')?.textContent).toBe('按步骤单独设置（已单独设置 1 步）');
    await user.click(details.querySelector('summary')!);
    const rows = details.querySelectorAll('tbody tr');
    expect([...rows].map((r) => r.querySelector('td')?.firstChild?.textContent)).toEqual(
      ['剧本', '分镜', '字幕翻译（配音前）', '角色定妆照', '三视图']);
    expect(details.textContent).toContain('首帧、视频没有单独设置：跟项目和镜头上选的引擎走');
    const script = screen.getByLabelText('剧本用什么') as HTMLSelectElement;
    expect(script.value).toBe('lmstudio');
    expect(screen.getByTestId('effective-script').textContent).toBe('本机 LM Studio（lmstudio，占显卡）');
    expect(within(screen.getByLabelText('三视图用什么')).getByRole('option', { name: '跟随默认（H3 图像）' })).toBeTruthy();
    await user.selectOptions(script, '');
    await user.selectOptions(screen.getByLabelText('角色定妆照用什么'), 'krea2');
    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ steps: { script: null, portrait: 'krea2' } });
  });

  it('保存失败显示后端的中文错误', async () => {
    base({ 'PUT /api/settings': () => json({ detail: '默认图片来源：图片来源 x 不存在或未启用' }, 400) });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText('新项目的视频来源'), 'wan22_i2v');
    await user.click(screen.getByRole('button', { name: '保存' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/不存在或未启用/);
  });

  it('存的设置已经失效：下拉框留着这一项，旁边写「已失效（配置里没有 X），现在用 Y」', async () => {
    const st = settings({
      text_model: { value: 'gone', effective: 'claude', fallback: 'claude', invalid: true, source: 'config' },
      image_engine: { value: 'old_engine', effective: 'zimage', fallback: 'zimage', invalid: true, source: 'default' },
    });
    st.steps = st.steps.map((s) => (s.key === 'portrait' ? { ...s, value: 'old_engine', invalid: true } : s));
    base({ 'GET /api/settings': st });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    expect((await screen.findByTestId('invalid-text_model')).textContent).toBe('已失效（配置里没有 gone），现在用 Claude（本机 Claude Code）');
    expect((screen.getByLabelText('文本模型') as HTMLSelectElement).value).toBe('gone');
    expect(within(screen.getByLabelText('文本模型')).getByRole('option', { name: '已失效：gone' })).toBeTruthy();
    expect(screen.getByTestId('invalid-image_engine').textContent).toBe('已失效（配置里没有 old_engine），现在用 Z-Image 快速');
    expect(screen.getByTestId('invalid-portrait').textContent).toContain('现在用 Z-Image 快速');
    expect(screen.queryByTestId('invalid-turnaround')).toBeNull();
  });

  it('设置读不到：错误用 role=alert', async () => {
    base({ 'GET /api/settings': () => json({ detail: '数据库坏了' }, 500) });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const panel = await screen.findByRole('region', { name: '默认模型' });
    await waitFor(() => expect(within(panel).getByRole('alert').textContent).toContain('数据库坏了'));
  });

  it('settingsPatch：没改动是空对象，空串变 null', () => {
    const d = { text_model: '', image_engine: 'krea2', video_engine: '', steps: { script: 'a', videos: '' } };
    expect(settingsPatch(d, d)).toEqual({});
    expect(settingsPatch(d, { ...d, image_engine: '', steps: { script: '', videos: 'wan' } })).toEqual({
      image_engine: null,
      steps: { script: null, videos: 'wan' },
    });
  });

  it('?step=：展开「按步骤单独设置」并把焦点放到这一步的下拉框（从失败提示跳过来）', async () => {
    base();
    renderAt(<SettingsPage />, { route: '/settings?step=storyboard', path: '/settings' });
    const sel = await screen.findByLabelText('分镜用什么');
    await waitFor(() => expect(document.activeElement).toBe(sel));
    expect((sel.closest('details') as HTMLDetailsElement).open).toBe(true);
  });

  it('全局页上跟项目图片引擎走的步骤（场景图、定妆照）写「跟随项目图片引擎」，不写一个具体引擎', async () => {
    const loc = { ...STEP('location', '场景图', 'image', 'zimage'), follows_project: true };
    base({ 'GET /api/settings': settings({ steps: [STEP('script', '剧本', 'llm', 'claude'), loc] }) });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const cell = await screen.findByTestId('effective-location');
    expect(cell.textContent).toBe('跟随项目图片引擎');
    const sel = screen.getByLabelText('场景图用什么') as HTMLSelectElement;
    expect(sel.options[0].textContent).toBe('跟随默认（项目图片引擎）');
  });

  it('定妆照跟项目走时写明按风格预设推荐；说明里讲清全局设置和预设合不来时不用', async () => {
    const portrait = { ...STEP('portrait', '角色定妆照', 'image', 'zimage'), follows_project: true };
    base({ 'GET /api/settings': settings({ steps: [STEP('script', '剧本', 'llm', 'claude'), portrait] }) });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const cell = await screen.findByTestId('effective-portrait');
    expect(cell.textContent).toBe('跟随项目（风格预设推荐 / 图片引擎）');
    const sel = screen.getByLabelText('角色定妆照用什么') as HTMLSelectElement;
    expect(sel.options[0].textContent).toBe('跟随默认（项目风格预设推荐 / 图片引擎）');
    const help = screen.getByText(/定妆照：抽卡时手选的/);
    expect(help.textContent).toContain('和项目的风格预设合得来');
    expect(help.textContent).toContain('真人写实、商业广告推荐 krea2');
  });
});

