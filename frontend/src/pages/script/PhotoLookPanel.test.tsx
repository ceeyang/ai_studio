import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Character } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeJob } from '../../test/factories';
import { renderAt } from '../../test/render';
import { CharacterProfile } from './CharacterProfile';

const NEW_LOOK = 'Chinese woman in her late twenties, oval face, blunt bangs, camel wool coat';
const BASE: Character = {
  id: 6, project_id: 1, name: '林夏', description: '主播', visual_prompt: 'old look', core_features: '齐肩发',
  voice: null, portrait_candidate_id: null, portrait_url: null, portraits: [], design_options: [], active_jobs: [],
  turnaround: null, appearance_ref: null,
};
const withRef = (p: Partial<NonNullable<Character['appearance_ref']>> = {}): Character => ({
  ...BASE,
  appearance_ref: { candidate_id: 3, url: '/media/demo/media/appearance_ref/a.png', visual_prompt: null, core_features: null, ...p },
});

function setup(c: Character, routes: Record<string, unknown> = {}) {
  const calls = installFetch(routes);
  renderAt(<CharacterProfile c={c} slug="demo" onDirtyChange={() => {}} />);
  return { calls, user: userEvent.setup() };
}
const panel = () => screen.getByRole('region', { name: '从参考图写外观' });

describe('从参考图写外观', () => {
  it('没有参考照片：只能上传，写外观按钮不可用；上传走 appearance-ref', async () => {
    const { calls, user } = setup(BASE, { 'POST /api/characters/6/appearance-ref': withRef() });
    expect((within(panel()).getByRole('button', { name: '从参考图写外观' }) as HTMLButtonElement).disabled).toBe(true);
    const file = new File(['png'], 'ref.png', { type: 'image/png' });
    await user.upload(within(panel()).getByLabelText('上传外观参考照片'), file);
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/characters/6/appearance-ref')).toBe(true));
  });

  it('有照片：点「从参考图写外观」提交任务并跟踪', async () => {
    const { calls, user } = setup(withRef(), {
      'POST /api/characters/6/appearance-from-photo': { job_ids: [41] },
      'GET /api/jobs/41': makeJob({ id: 41, type: 'character.from_photo', status: 'running' }),
    });
    expect(within(panel()).getByRole('img', { name: '林夏 的外观参考照片' })).toBeTruthy();
    await user.click(within(panel()).getByRole('button', { name: '从参考图写外观' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/characters/6/appearance-from-photo')).toBe(true));
    expect(await within(panel()).findByText(/从参考图写外观：已提交/)).toBeTruthy();
  });

  it('写好的外观先对照显示，确认后才写进外观提示词，并同步镜头里的旧外貌', async () => {
    const { calls, user } = setup(withRef({ visual_prompt: NEW_LOOK, core_features: '齐刘海、驼色大衣' }), {
      'PATCH /api/characters/6': { ...BASE, visual_prompt: NEW_LOOK },
      'POST /api/characters/6/shots/refresh-look': { updated: [], already: [], unmatched: [] },
      'DELETE /api/characters/6/appearance-from-photo': null,
    });
    const diff = within(panel()).getByLabelText('外观对照');
    expect(diff.textContent).toContain('old look');
    expect(diff.textContent).toContain(NEW_LOOK);
    expect(diff.textContent).toContain('齐刘海、驼色大衣');
    await user.click(within(panel()).getByRole('button', { name: '用这个外观' }));
    const dlg = await screen.findByRole('dialog', { name: '用参考图写的外观替换外观提示词？' });
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false);
    await user.click(within(dlg).getByRole('button', { name: '替换' }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ visual_prompt: NEW_LOOK, core_features: '齐刘海、驼色大衣' }),
    );
    await waitFor(() =>
      expect(calls.find((c) => c.url === '/api/characters/6/shots/refresh-look')?.body).toEqual({ old: ['old look'] }),
    );
    // 用了就清掉等确认的外观：之后再改外观提示词，对照不会又冒出来
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/characters/6/appearance-from-photo')).toBe(true),
    );
    const patchAt = calls.findIndex((c) => c.method === 'PATCH');
    const delAt = calls.findIndex((c) => c.method === 'DELETE');
    expect(delAt).toBeGreaterThan(patchAt);
  });

  it('资料有没保存的修改时不直接填入，先提示保存或撤销', async () => {
    const { calls, user } = setup(withRef({ visual_prompt: NEW_LOOK }));
    await user.type(screen.getByLabelText('人物描述'), '（夜班）');
    await user.click(within(panel()).getByRole('button', { name: '用这个外观' }));
    expect(within(panel()).getByRole('status').textContent).toContain('先保存或撤销');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false);
  });

  it('「不用」清掉写好的外观；和现在一样时不再显示对照', async () => {
    const { calls, user } = setup(withRef({ visual_prompt: NEW_LOOK }), {
      'DELETE /api/characters/6/appearance-from-photo': null,
    });
    await user.click(within(panel()).getByRole('button', { name: '不用' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
  });

  it('外观已经是参考图写的那个：不显示对照', () => {
    setup({ ...withRef({ visual_prompt: 'old look' }) });
    expect(within(panel()).queryByLabelText('外观对照')).toBeNull();
    expect(within(panel()).getByRole('button', { name: '重新从参考图写外观' })).toBeTruthy();
  });

  it('文本模型看不了图片：一键改用手动粘贴，并能去设置页这一步', async () => {
    const SETTINGS = {
      text_model: { value: null, effective: 'lm', fallback: 'lm', source: 'config' },
      image_engine: { value: null, effective: 'zimage', fallback: 'zimage', source: 'default' },
      video_engine: { value: null, effective: 'h3_video', fallback: 'h3_video', source: 'default' },
      steps: [],
      project: null,
      choices: { llm: [{ id: 'lm', label: 'LM Studio', kind: 'openai' }, { id: 'manual', label: '手动粘贴', kind: 'manual' }], image: [], video: [] },
    };
    const { calls, user } = setup(withRef(), {
      'POST /api/characters/6/appearance-from-photo': { job_ids: [41] },
      'GET /api/jobs/41': makeJob({
        id: 41, type: 'character.from_photo', status: 'failed',
        error: '文本模型 lm 看不了图片。到「设置 → 按步骤单独设置」把「从参考图写外观」这一步的文本模型换成能看图的',
      }),
      'GET /api/settings': SETTINGS,
      'PUT /api/settings': SETTINGS,
    });
    await user.click(within(panel()).getByRole('button', { name: '从参考图写外观' }));
    const fix = await within(panel()).findByTestId('vision-fallback');
    expect(within(fix).getByRole('link', { name: '去设置这一步' }).getAttribute('href')).toBe('/settings?step=character.from_photo');
    await user.click(within(fix).getByRole('button', { name: '改用手动粘贴' }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ steps: { 'character.from_photo': 'manual' } }),
    );
    expect(await within(panel()).findByText(/已改成手动粘贴/)).toBeTruthy();
  });

  it('别的失败（不是看不了图）不出「改用手动粘贴」', async () => {
    const { user } = setup(withRef(), {
      'POST /api/characters/6/appearance-from-photo': { job_ids: [42] },
      'GET /api/jobs/42': makeJob({ id: 42, type: 'character.from_photo', status: 'failed', error: 'lm 返回 HTTP 429：rate limited' }),
    });
    await user.click(within(panel()).getByRole('button', { name: '从参考图写外观' }));
    expect(await within(panel()).findByText(/HTTP 429/)).toBeTruthy();
    expect(within(panel()).queryByTestId('vision-fallback')).toBeNull();
  });
});
