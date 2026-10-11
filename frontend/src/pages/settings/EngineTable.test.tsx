import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import type { DoctorCheck, EngineRow } from '../../api/types';
import { EngineTable, unavailableReason } from './EngineTable';
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
});
