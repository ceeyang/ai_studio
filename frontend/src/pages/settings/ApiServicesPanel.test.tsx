import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { ApiService, AppSettings } from '../../api/types';
import { installFetch, json } from '../../test/fetch';
import { renderAt } from '../../test/render';
import { SettingsPage } from '../SettingsPage';
import { servicePatch } from './ApiServicesPanel';

function svc(id: string, over: Partial<ApiService> = {}): ApiService {
  const label = { openai: 'OpenAI（GPT）', anthropic: 'Anthropic（Claude）', gemini: 'Google（Gemini）' }[id] ?? id;
  const models =
    id === 'anthropic'
      ? { text: { value: null, effective: 'claude-sonnet-4-5', default: 'claude-sonnet-4-5', label: '文本模型', suggestions: ['claude-sonnet-4-5'] } }
      : {
          text: { value: null, effective: 'gpt-4.1', default: 'gpt-4.1', label: '文本模型', suggestions: ['gpt-4.1', 'gpt-5'] },
          image: { value: null, effective: 'gpt-image-1', default: 'gpt-image-1', label: '出图模型', suggestions: ['gpt-image-1'] },
          video: { value: null, effective: 'sora-2', default: 'sora-2', label: '视频模型', suggestions: ['sora-2'] },
        };
  return {
    id,
    label,
    configured: false,
    source: null,
    env_name: null,
    env_keys: id === 'gemini' ? ['GEMINI_API_KEY', 'GOOGLE_API_KEY'] : [`${id.toUpperCase()}_API_KEY`],
    masked: null,
    has_saved_key: false,
    base_url: null,
    effective_base_url: 'https://api.openai.com/v1',
    default_base_url: 'https://api.openai.com/v1',
    base_hint: 'OpenAI 兼容代理 / 中转填到 /v1 为止',
    models,
    llm_provider: `${id}_api`,
    engines: [],
    last_test: null,
    ...over,
  };
}

const SAVED = svc('openai', { configured: true, source: 'settings', masked: 'sk-…abcd', has_saved_key: true });

function settings(): AppSettings {
  return {
    text_model: { value: null, effective: 'claude', effective_label: 'Claude', fallback: 'claude', fallback_label: 'Claude', source: 'config' },
    image_engine: { value: null, effective: 'zimage', fallback: 'zimage', source: 'default' },
    video_engine: { value: null, effective: 'h3_video', fallback: 'h3_video', source: 'default' },
    steps: [{ key: 'script', label: '剧本', kind: 'llm', value: null, effective: 'claude', fallback: 'claude', source: 'default' }],
    project: null,
    choices: {
      llm: [
        { id: 'claude', label: 'Claude', kind: 'claude', ready: true },
        { id: 'openai_api', label: 'OpenAI GPT（API · gpt-4.1）', kind: 'openai_api', ready: false, note: '还没有配置 OpenAI（GPT） 的 API Key' },
        { id: 'gemini_api', label: 'Google Gemini（API · gemini-2.5-flash）', kind: 'gemini', ready: true },
      ],
      image: [
        { name: 'zimage', label: 'Z-Image 快速', runtime: 'comfyui', family: 'zimage', enabled: true, ready: true },
        { name: 'openai_image', label: 'OpenAI 图像（API）', runtime: 'api', family: 'openai_api_image', enabled: true, ready: false, note: '不能用：还没有配置 Key' },
        { name: 'gemini_api_image', label: 'Gemini 图像（API）', runtime: 'api', family: 'gemini_api_image', enabled: true, ready: true },
      ],
      video: [
        { name: 'h3_video', label: 'H3 视频（有声）', runtime: 'comfyui', family: 'h3', enabled: true, ready: true },
        { name: 'veo_video', label: 'Google Veo（API）', runtime: 'api', family: 'veo_api', enabled: true, ready: false, note: '没 Key' },
      ],
    },
  };
}

function base(extra: Record<string, unknown> = {}) {
  return installFetch({
    'GET /api/doctor': [],
    'GET /api/engines': {
      image: [
        {
          name: 'openai_image', label: 'OpenAI 图像（API）', family: 'openai_api_image', runtime: 'api', ready: false, note: 'OpenAI 图像（API）不能用：还没有配置 Key', model: 'gpt-image-1',
          caps: { max_refs: 8, supports_negative: false, sizes: { '9:16': [864, 1536], '16:9': [1536, 864] } },
        },
      ],
      video: [],
    },
    'GET /api/config': {},
    'GET /api/settings': settings(),
    'GET /api/api-services': { services: [svc('openai'), svc('anthropic'), svc('gemini')] },
    ...extra,
  });
}

describe('设置页「API 服务」', () => {
  it('三家各一块；没配的写「未配置」；Key 输入框是 password，可显示 / 隐藏；整页仍只有一个主按钮', async () => {
    base();
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const panel = await screen.findByRole('region', { name: 'API 服务' });
    expect(await within(panel).findByTestId('api-openai')).toBeTruthy();
    expect(within(panel).getByTestId('api-anthropic')).toBeTruthy();
    expect(within(panel).getByTestId('api-gemini')).toBeTruthy();
    expect(within(panel).getByTestId('api-openai-status').textContent).toBe('未配置');
    const key = within(panel).getByLabelText('OpenAI（GPT） API Key') as HTMLInputElement;
    expect(key.type).toBe('password');
    const user = userEvent.setup();
    await user.click(within(screen.getByTestId('api-openai')).getByRole('button', { name: '显示 Key' }));
    expect(key.type).toBe('text');
    await user.click(within(screen.getByTestId('api-openai')).getByRole('button', { name: '隐藏 Key' }));
    expect(key.type).toBe('password');
    // Claude 只有文本模型
    expect(within(screen.getByTestId('api-anthropic')).queryByLabelText('Anthropic（Claude） 出图模型')).toBeNull();
    // 没 Key 不能测试；没存过 Key 没有「清除」
    expect((within(screen.getByTestId('api-openai')).getByRole('button', { name: '测试连接' }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(screen.getByTestId('api-openai')).queryByRole('button', { name: '清除' })).toBeNull();
    expect(document.querySelectorAll('button.primary')).toHaveLength(1);
    // 引擎表：API 标签、模型名、没 Key 的原因
    expect(await screen.findByText('API')).toBeTruthy();
    expect(screen.getByText(/模型 gpt-image-1 · 没有种子/)).toBeTruthy();
    expect(screen.getByText('OpenAI 图像（API）不能用：还没有配置 Key')).toBeTruthy();
  });

  it('已配置只显示掩码，从不显示明文；保存只发改了的字段；Key 留空不发', async () => {
    const calls = base({
      'GET /api/api-services': { services: [SAVED, svc('anthropic'), svc('gemini')] },
      'PUT /api/api-services/openai': { ...SAVED, models: { ...SAVED.models, text: { ...SAVED.models.text!, value: 'gpt-5', effective: 'gpt-5' } } },
    });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const card = await screen.findByTestId('api-openai');
    expect(within(card).getByTestId('api-openai-status').textContent).toBe('已配置 · 设置页填的 · sk-…abcd');
    const key = within(card).getByLabelText('OpenAI（GPT） API Key') as HTMLInputElement;
    expect(key.value).toBe('');
    expect(key.placeholder).toContain('sk-…abcd');
    const user = userEvent.setup();
    const save = within(card).getByRole('button', { name: '保存' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    await user.type(within(card).getByLabelText('OpenAI（GPT） 文本模型'), 'gpt-5');
    expect(within(card).getByTestId('api-openai-result').textContent).toBe('有改动，还没保存');
    await user.click(save);
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ models: { text: 'gpt-5' } });
    await waitFor(() => expect(within(card).getByTestId('api-openai-result').textContent).toBe('已保存，马上生效'));
  });

  it('填新 Key 保存；清除发 clear_key', async () => {
    const calls = base({
      'GET /api/api-services': { services: [svc('openai'), svc('anthropic'), svc('gemini', { configured: true, source: 'env', env_name: 'GEMINI_API_KEY', masked: 'AIz…9876' })] },
      'PUT /api/api-services/openai': SAVED,
    });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const card = await screen.findByTestId('api-openai');
    expect(screen.getByTestId('api-gemini-status').textContent).toBe('已配置 · 环境变量 GEMINI_API_KEY · AIz…9876');
    // 环境变量来的 Key 不能在这里清除
    expect(within(screen.getByTestId('api-gemini')).queryByRole('button', { name: '清除' })).toBeNull();
    const user = userEvent.setup();
    await user.type(within(card).getByLabelText('OpenAI（GPT） API Key'), '  sk-new-key-123456789  ');
    await user.click(within(card).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(1));
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ api_key: 'sk-new-key-123456789' });
    // 保存后输入框清空，显示掩码
    await waitFor(() => expect(screen.getByTestId('api-openai-status').textContent).toContain('sk-…abcd'));
    expect((within(card).getByLabelText('OpenAI（GPT） API Key') as HTMLInputElement).value).toBe('');
    await user.click(within(card).getByRole('button', { name: '清除' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(2));
    expect(calls.filter((c) => c.method === 'PUT')[1].body).toEqual({ clear_key: true });
  });

  it('测试连接：显示中文结果；失败也显示原因', async () => {
    const calls = base({
      'GET /api/api-services': { services: [SAVED, svc('anthropic'), svc('gemini')] },
      'POST /api/api-services/openai/test': { ok: false, message: 'OpenAI 拒绝了 API Key（HTTP 401）', service: { ...SAVED, last_test: { ok: false, message: 'OpenAI 拒绝了 API Key（HTTP 401）' } } },
    });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const card = await screen.findByTestId('api-openai');
    const user = userEvent.setup();
    await user.click(within(card).getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(within(card).getByTestId('api-openai-result').textContent).toBe('测试失败：OpenAI 拒绝了 API Key（HTTP 401）'));
    expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/api/api-services/openai/test'))).toBe(true);
  });

  it('保存被拒：显示后端的中文错误', async () => {
    base({
      'GET /api/api-services': { services: [svc('openai'), svc('anthropic'), svc('gemini')] },
      'PUT /api/api-services/openai': () => json({ detail: 'Base URL 要以 http:// 或 https:// 开头' }, 400),
    });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const card = await screen.findByTestId('api-openai');
    const user = userEvent.setup();
    await user.type(within(card).getByLabelText('OpenAI（GPT） Base URL'), 'ftp://x');
    await user.click(within(card).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(within(card).getByTestId('api-openai-result').textContent).toContain('Base URL 要以 http://'));
  });

  it('默认模型下拉：没配 Key 的 API 文本模型和引擎禁用并写明', async () => {
    base();
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const text = await screen.findByLabelText('文本模型');
    const gpt = within(text).getByRole('option', { name: 'OpenAI GPT（API · gpt-4.1）（先在 API 服务里填 Key）' }) as HTMLOptionElement;
    expect(gpt.disabled).toBe(true);
    expect((within(text).getByRole('option', { name: 'Google Gemini（API · gemini-2.5-flash）' }) as HTMLOptionElement).disabled).toBe(false);
    const img = screen.getByLabelText('新项目的图片来源');
    expect((within(img).getByRole('option', { name: 'OpenAI 图像（API）（先在 API 服务里填 Key）' }) as HTMLOptionElement).disabled).toBe(true);
    expect((within(img).getByRole('option', { name: 'Gemini 图像（API）' }) as HTMLOptionElement).disabled).toBe(false);
    const vid = screen.getByLabelText('新项目的视频来源');
    expect((within(vid).getByRole('option', { name: 'Google Veo（API · 有声）（先在 API 服务里填 Key）' }) as HTMLOptionElement).disabled).toBe(true);
    // 按步骤设置里也一样
    const step = screen.getByLabelText('剧本用什么');
    expect((within(step).getByRole('option', { name: 'OpenAI GPT（API · gpt-4.1）（先在 API 服务里填 Key）' }) as HTMLOptionElement).disabled).toBe(true);
    // API 引擎没 Key 不在默认模型下面一条条列
    expect(screen.queryByText(/OpenAI 图像（API）：不能用/)).toBeNull();
  });

  it('servicePatch：没改动是空对象；清空 Base URL / 模型名发空串（恢复默认）', () => {
    const s = svc('openai', { base_url: 'https://proxy/v1', models: { ...svc('openai').models, text: { ...svc('openai').models.text!, value: 'gpt-5' } } });
    const d = { key: '', base: 'https://proxy/v1', models: { text: 'gpt-5', image: '', video: '' } };
    expect(servicePatch(s, d)).toEqual({});
    expect(servicePatch(s, { ...d, key: ' k ', base: '', models: { ...d.models, text: '' } })).toEqual({ api_key: 'k', base_url: '', models: { text: '' } });
  });
});
