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
});
