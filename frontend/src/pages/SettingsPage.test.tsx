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

  it('浏览器引擎标「浏览器」；点「打开浏览器登录」提示去窗口里登录', async () => {
    const engines = {
      image: [
        ...ENGINES.image,
        { name: 'gpt_image', family: 'chatgpt_web', runtime: 'browser', caps: { max_refs: 4, supports_negative: false, sizes: { '9:16': [1080, 1920] } } },
      ],
      video: [],
    };
    const calls = installFetch({
      'GET /api/doctor': [{ group: '浏览器', name: 'ChatGPT 登录（gpt_image）', status: 'warn', detail: '没登录 chatgpt.com。在设置页点“打开浏览器登录”' }],
      'GET /api/engines': engines,
      'GET /api/config': {},
      'POST /api/browser/login': { launched: true, urls: ['https://chatgpt.com/'], endpoint: 'http://127.0.0.1:9333', user_data_dir: 'D:/works/ai_studio/browser-profile' },
    });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    const user = userEvent.setup();
    expect(await screen.findByText('浏览器', { selector: '.pill' })).toBeTruthy();
    expect(screen.getByText(/没有种子，每次结果不同/)).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '打开浏览器登录' }));
    const notice = await screen.findByTestId('browser-login-notice');
    expect(notice.textContent).toContain('已启动浏览器');
    expect(notice.textContent).toContain('browser-profile');
    expect(calls.some((c) => c.method === 'POST' && c.url === '/api/browser/login')).toBe(true);
  });

  it('没有浏览器引擎时不显示登录按钮', async () => {
    installFetch({ 'GET /api/doctor': [DOCTOR[0]], 'GET /api/engines': ENGINES, 'GET /api/config': {} });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    expect((await screen.findAllByText('zimage')).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: '打开浏览器登录' })).toBeNull();
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
});
