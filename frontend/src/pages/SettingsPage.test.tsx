import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFetch } from '../test/fetch';
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

  it('全部通过时说明没有失败项', async () => {
    installFetch({ 'GET /api/doctor': [DOCTOR[0]], 'GET /api/engines': ENGINES, 'GET /api/config': {} });
    renderAt(<SettingsPage />, { route: '/settings', path: '/settings' });
    expect((await screen.findByTestId('doctor-summary')).textContent).toBe('全部通过');
  });
});
