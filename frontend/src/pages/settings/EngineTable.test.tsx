import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import type { DoctorCheck, EngineRow } from '../../api/types';
import { EngineTable, durationText, unavailableReason } from './EngineTable';
import { missingText } from './BrowserPanel';
import { FoldPanel } from './FoldPanel';

const img = (name: string, extra: Partial<EngineRow> = {}): EngineRow =>
  ({ name, family: name, caps: { max_refs: 0, supports_negative: false, sizes: { '9:16': [1, 1] } }, ...extra }) as EngineRow;
const vid = (name: string, extra: Partial<EngineRow> = {}): EngineRow =>
  ({ name, family: name, caps: { max_refs: 0, has_audio: true, fps: 24, sizes: {}, default_seconds: 5, max_seconds: 10 }, ...extra }) as EngineRow;

const IMAGE = [
  img('zimage'),
  img('h3_image'),
  img('gpt_image', { runtime: 'browser' }),
  img('openai_image', { runtime: 'api', ready: false, note: 'OpenAI 图像（API）不能用：还没有配置 Key' }),
];
const VIDEO = [vid('h3_video'), vid('veo_video', { runtime: 'api', ready: true })];
const DOCTOR: DoctorCheck[] = [
  { group: 'ComfyUI', name: '服务', status: 'ok', detail: 'ok' },
  { group: '引擎', name: 'h3_image 模型', status: 'fail', detail: '缺模型 h3.safetensors' },
  { group: '浏览器', name: 'ChatGPT 登录 (gpt_image)', status: 'fail', detail: '没登录' },
] as DoctorCheck[];

describe('设置页引擎表', () => {
  it('按来源分组；用不了的灰掉、写原因、排在同组后面', () => {
    render(<EngineTable image={IMAGE} video={VIDEO} doctor={DOCTOR} />);
    const local = screen.getByTestId('engine-group-comfyui');
    expect(within(local).getByText(/本地 ComfyUI/).textContent).toContain('2 个能用 / 共 3 个');
    const rows = within(local).getAllByRole('row').slice(1);
    expect(rows[rows.length - 1].textContent).toContain('h3_image');
    expect(rows[rows.length - 1].className).toBe('engine-off');
    expect(rows[rows.length - 1].textContent).toContain('不能用：缺模型 h3.safetensors');
    const web = screen.getByTestId('engine-group-browser');
    expect(within(web).getByText(/不能用：ChatGPT 登录 \(gpt_image\) 没登录/)).toBeTruthy();
    const api = screen.getByTestId('engine-group-api');
    expect(within(api).getByText('OpenAI 图像（API）不能用：还没有配置 Key')).toBeTruthy();
    expect(screen.queryByTestId('engine-group-runninghub')).toBeNull();
  });

  it('按类型切换；只看能用的', async () => {
    const user = userEvent.setup();
    render(<EngineTable image={IMAGE} video={VIDEO} doctor={DOCTOR} />);
    expect(screen.getByRole('tab', { name: '全部3/6' })).toBeTruthy();
    await user.click(screen.getByRole('tab', { name: '视频2/2' }));
    expect(screen.queryByText('zimage')).toBeNull();
    expect(screen.getAllByText('h3_video').length).toBeGreaterThan(0);
    await user.click(screen.getByRole('tab', { name: /全部/ }));
    await user.click(screen.getByLabelText('只看能用的'));
    expect(document.querySelectorAll('tr.engine-off')).toHaveLength(0);
    expect(screen.queryByTestId('engine-group-browser')).toBeNull();
  });

  it('ComfyUI 连不上时本地引擎全灰', () => {
    const down = [{ group: 'ComfyUI', name: '服务', status: 'fail', detail: '连不上 8188' }] as DoctorCheck[];
    expect(unavailableReason(img('zimage'), down)).toBe('不能用：ComfyUI 连不上（连不上 8188）');
    expect(unavailableReason(img('gpt_image', { runtime: 'browser' }), down)).toBeNull();
  });
});

describe('设置页折叠块', () => {
  afterEach(() => window.localStorage.clear());

  it('点标题折叠 / 展开并记住；默认折叠的块记住展开后下次直接展开', async () => {
    const user = userEvent.setup();
    const { unmount } = render(
      <FoldPanel id="t" className="x" title="块" defaultOpen={false} actions={<button type="button">动作</button>}>
        <p>内容</p>
      </FoldPanel>,
    );
    const toggle = screen.getByRole('button', { name: '块' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByText('内容').closest('.fold-body')?.hasAttribute('hidden')).toBe(true);
    // 折叠时标题右边的动作还在
    expect(screen.getByRole('button', { name: '动作' })).toBeTruthy();
    await user.click(toggle);
    expect(screen.getByText('内容').closest('.fold-body')?.hasAttribute('hidden')).toBe(false);
    unmount();
    render(
      <FoldPanel id="t" className="x" title="块" defaultOpen={false}>
        <p>内容</p>
      </FoldPanel>,
    );
    expect(screen.getByText('内容').closest('.fold-body')?.hasAttribute('hidden')).toBe(false);
  });

  it('网页视频：同一网站出图、出视频合在一行的登录检查对两个引擎都算；时长按实测写明能不能指定', () => {
    const doctor = [
      { group: '浏览器', name: 'Gemini 登录（gemini_image、gemini_video）', status: 'fail', detail: '没登录' },
    ] as DoctorCheck[];
    const gv = vid('gemini_video', {
      runtime: 'browser',
      caps: { max_refs: 0, has_audio: true, fps: 24, sizes: { '9:16': [720, 1280], '16:9': [1280, 720] },
        default_seconds: 10, max_seconds: 10, duration_control: 'fixed', allowed_seconds: [10] },
    });
    expect(unavailableReason(gv, doctor)).toContain('Gemini 登录');
    expect(unavailableReason(img('gemini_image', { runtime: 'browser' }), doctor)).toContain('Gemini 登录');
    expect(unavailableReason(img('gemini', { runtime: 'browser' }), doctor)).toBeNull();
    render(<EngineTable image={[]} video={[gv]} doctor={[]} />);
    const web = screen.getByTestId('engine-group-browser');
    expect(web.textContent).toContain('首帧图生视频');
    expect(web.textContent).toContain('时长不能指定，总是约 10 秒');
    expect(durationText({ duration_control: 'prompt', allowed_seconds: [3, 5, 10], default_seconds: 10 })).toBe(
      '时长写进提示词（只认 3 / 5 / 10 秒），不一定照做；不指定时约 10 秒');
    expect(durationText({ allowed_seconds: [4, 6, 8] })).toBe('时长可选 4 / 6 / 8 秒');
    expect(durationText({ default_seconds: 5, max_seconds: 10 })).toBe('默认 5 秒 / 最长 10 秒');
  });

  it('老扩展缺的能力写成中文', () => {
    expect(missingText(['video'])).toBe('网页出视频（Gemini / Muse）');
    expect(missingText(undefined)).toBe('网页文本模型（ChatGPT / Gemini / Claude 网页）');
  });
});
