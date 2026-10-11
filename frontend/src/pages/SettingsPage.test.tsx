import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFetch, json } from '../test/fetch';
import { renderAt } from '../test/render';
import { SettingsPage } from './SettingsPage';

const DOCTOR = [
  { group: 'ComfyUI', name: '服务', status: 'ok', detail: 'http://127.0.0.1:8189 · RTX 5080' },
  { group: 'LLM', name: 'lmstudio', status: 'warn', detail: '连不上 http://127.0.0.1:1234/v1' },
  { group: '引擎', name: 'h3_video', status: 'fail', detail: '缺模型 h3_int8.safetensors' },
];
const ENGINES = {
  image: [{ name: 'zimage', family: 'zimage', caps: { max_refs: 0, supports_negative: true, sizes: { '9:16': [768, 1344], '16:9': [1344, 768] } } }],
  video: [
    {
      name: 'h3_video', family: 'h3',
      caps: { max_refs: 8, supports_end_frame: false, has_audio: true, fps: 24, sizes: {}, default_seconds: 5, max_seconds: 10 },
    },
  ],
};

const BROWSER_ENGINES = {
  image: [
    ...ENGINES.image,
    { name: 'gpt_image', family: 'chatgpt_web', runtime: 'browser', caps: { max_refs: 4, supports_negative: false, sizes: { '9:16': [1080, 1920] } } },
  ],
  video: [],
};

describe('SettingsPage', () => {
  it('显示 doctor 汇总与明细、引擎能力、只读配置；可以重新检查', async () => {
    const calls = installFetch({ 'GET /api/doctor': DOCTOR, 'GET /api/engines': ENGINES, 'GET /api/config': { server: { port: 8700 } } });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const user = userEvent.setup();
    expect((await screen.findByTestId('doctor-summary')).textContent).toBe('1 项失败，1 项警告');
    expect(screen.getByText('缺模型 h3_int8.safetensors')).toBeTruthy();
    expect(await screen.findByText(/有声 · 24 fps · 默认 5 秒 \/ 最长 10 秒/)).toBeTruthy();
    expect(screen.getByText(/画幅 9:16 \/ 16:9/)).toBeTruthy();
    expect((await screen.findByTestId('config')).textContent).toContain('8700');
    await user.click(await screen.findByRole('button', { name: '重新检查' }));
    await waitFor(() => expect(calls.filter((c) => c.url === '/api/doctor')).toHaveLength(2));
  });

  it('cdp 方式：浏览器引擎标「浏览器」；点「打开浏览器登录」提示去窗口里登录', async () => {
    const calls = installFetch({
      'GET /api/doctor': [{ group: '浏览器', name: 'ChatGPT 登录（gpt_image）', status: 'warn', detail: '没登录 chatgpt.com。在设置页点“打开浏览器登录”' }],
      'GET /api/engines': BROWSER_ENGINES,
      'GET /api/config': {},
      'GET /api/browser/status': { transport: 'cdp', running: false, endpoint: 'http://127.0.0.1:9333', logged_in: { chatgpt: null } },
      'POST /api/browser/login': { transport: 'cdp', launched: true, urls: ['https://chatgpt.com/'], endpoint: 'http://127.0.0.1:9333', user_data_dir: 'D:/works/ai_studio/browser-profile' },
    });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const user = userEvent.setup();
    expect(await screen.findByText('浏览器', { selector: '.pill' })).toBeTruthy();
    expect(screen.getByText(/没有种子，每次结果不同/)).toBeTruthy();
    expect((await screen.findByTestId('cdp-status')).textContent).toContain('出图时自动启动');
    await user.click(screen.getByRole('button', { name: '打开浏览器登录' }));
    const notice = await screen.findByTestId('browser-login-notice');
    expect(notice.textContent).toContain('已启动浏览器');
    expect(notice.textContent).toContain('browser-profile');
    expect(calls.some((c) => c.method === 'POST' && c.url === '/api/browser/login')).toBe(true);
  });

  it('扩展方式没连上：显示安装步骤和扩展文件夹的完整路径，没有打开按钮', async () => {
    installFetch({
      'GET /api/doctor': [],
      'GET /api/engines': BROWSER_ENGINES,
      'GET /api/config': {},
      'GET /api/browser/status': {
        transport: 'extension', running: false, connected: false, extension: { connected: false },
        extension_dir: 'D:\works\ai_studio\browser-extension', sites: {}, logged_in: { chatgpt: null, gemini: null },
      },
    });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    expect((await screen.findByTestId('extension-status')).textContent).toContain('扩展没有连上');
    const steps = screen.getByTestId('extension-install-steps');
    expect(steps.textContent).toContain('加载已解压的扩展程序');
    expect(steps.textContent).toContain('chrome://extensions');
    expect(screen.getByTestId('extension-dir').textContent).toBe('D:\works\ai_studio\browser-extension');
    // 扩展连的是这个页面所在的地址（不写死 8700）
    expect(steps.textContent).toContain(`studio（${window.location.host}）`);
    // 引擎 id 不当主文案
    expect(screen.getByTestId('extension-status').textContent).toContain('ChatGPT 网页 / Gemini 网页出图会失败');
    expect(screen.queryByRole('button', { name: '在浏览器里打开 ChatGPT / Gemini / Muse / Claude' })).toBeNull();
    expect(screen.queryByRole('button', { name: '打开浏览器登录' })).toBeNull();
  });

  it('扩展方式已连上：显示版本、各网站登录和标签页；能让扩展打开两个网站', async () => {
    const calls = installFetch({
      'GET /api/doctor': [],
      'GET /api/engines': BROWSER_ENGINES,
      'GET /api/config': {},
      'GET /api/browser/status': {
        transport: 'extension', running: true, connected: true,
        extension: { connected: true, version: '0.1.0', browser: 'Google Chrome 141' },
        extension_dir: 'D:/works/ai_studio/browser-extension',
        sites: {
          chatgpt: { label: 'ChatGPT', host: 'chatgpt.com', tabs: 2, logged_in: true, cookie: true, page: 'logged_in' },
          gemini: { label: 'Gemini', host: 'gemini.google.com', tabs: 0, logged_in: false, cookie: false, page: null },
        },
        logged_in: { chatgpt: true, gemini: false },
      },
      'POST /api/browser/login': { transport: 'extension', launched: false, urls: [] },
    });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const user = userEvent.setup();
    expect((await screen.findByTestId('extension-status')).textContent).toContain('扩展已连上 · v0.1.0 · Google Chrome 141');
    expect(screen.getByTestId('browser-site-chatgpt').textContent).toContain('已登录');
    expect(screen.getByTestId('browser-site-chatgpt').textContent).toContain('2 个');
    expect(screen.getByTestId('browser-site-gemini').textContent).toContain('没登录');
    // 老版本扩展（v0.1.0，没有 rpc）：提示重新加载，网页文本模型才能用
    expect(screen.queryByTestId('extension-needs-reload')).toBeNull();
    await user.click(screen.getByRole('button', { name: '在浏览器里打开 ChatGPT / Gemini / Muse / Claude' }));
    expect((await screen.findByTestId('browser-login-notice')).textContent).toContain('已在你的浏览器里打开');
    expect(calls.some((c) => c.method === 'POST' && c.url === '/api/browser/login')).toBe(true);
  });

  it('没有浏览器引擎时不显示网页出图面板', async () => {
    installFetch({ 'GET /api/doctor': [DOCTOR[0]], 'GET /api/engines': ENGINES, 'GET /api/config': {} });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    expect((await screen.findAllByText('zimage')).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: '打开浏览器登录' })).toBeNull();
    expect(screen.queryByTestId('browser-panel')).toBeNull();
  });

  it('全部通过时说明没有失败项', async () => {
    installFetch({ 'GET /api/doctor': [DOCTOR[0]], 'GET /api/engines': ENGINES, 'GET /api/config': {} });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    expect((await screen.findByTestId('doctor-summary')).textContent).toBe('全部通过');
  });

  it('配置读取失败时显示错误', async () => {
    installFetch({
      'GET /api/doctor': DOCTOR,
      'GET /api/engines': ENGINES,
      'GET /api/config': () => json({ detail: '读不到配置文件' }, 500),
    });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    expect(await screen.findByText('读不到配置文件（HTTP 500）')).toBeTruthy();
  });

  it('页头「05 · 设置」；环境检查标题不带 doctor；引擎表主文案是中文名，id 小字', async () => {
    installFetch({ 'GET /api/doctor': [DOCTOR[0]], 'GET /api/engines': ENGINES, 'GET /api/config': {} });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    expect(screen.getByText('05 · 设置')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 2, name: '设置' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '环境检查' })).toBeTruthy();
    expect(screen.queryByText(/doctor/)).toBeNull();
    const table = (await screen.findByText('Z-Image 快速')).closest('table')!;
    const cell = screen.getByText('Z-Image 快速').closest('td')!;
    expect(cell.querySelector('.engine-id')?.textContent).toBe('zimage');
    expect(table.textContent).toContain('H3 视频（有声）');
  });

  it('连着的扩展太旧（不支持网页文本模型）：提示去扩展管理页重新加载', async () => {
    installFetch({
      'GET /api/doctor': [],
      'GET /api/engines': BROWSER_ENGINES,
      'GET /api/config': {},
      'GET /api/browser/status': {
        transport: 'extension', running: true, connected: true, needs_reload: true,
        extension: { connected: true, version: '0.1.0', browser: 'Google Chrome 141' },
        extension_dir: 'D:/works/ai_studio/browser-extension',
        sites: {
          chatgpt: { label: 'ChatGPT', host: 'chatgpt.com', tabs: 1, logged_in: true, cookie: true, page: 'logged_in' },
          claude: { label: 'Claude', host: 'claude.ai', tabs: null, logged_in: null, cookie: null, page: null },
        },
        logged_in: { chatgpt: true, claude: null },
      },
    });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const warn = await screen.findByTestId('extension-needs-reload');
    expect(warn.textContent).toContain('扩展需要重新加载');
    expect(warn.textContent).toContain('v0.1.0');
    expect(warn.textContent).toContain('chrome://extensions');
    expect(screen.getByTestId('browser-site-claude').textContent).toContain('未知');
  });
});
