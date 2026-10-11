import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Character } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { renderAt } from '../../test/render';
import { CharacterProfile } from './CharacterProfile';

const ZHAN: Character = {
  id: 9, project_id: 1, name: '站长', description: '外卖站站长，电话里催单', visual_prompt: '', core_features: '',
  voice: null, voice_only: false,
};

describe('CharacterProfile 只出声', () => {
  it('勾选「只出声，不出镜」立即保存 voice_only', async () => {
    const calls = installFetch({ 'PATCH /api/characters/9': { ...ZHAN, voice_only: true } });
    renderAt(<CharacterProfile c={ZHAN} slug="demo" onDirtyChange={() => {}} />);
    const box = screen.getByRole('checkbox', { name: /只出声，不出镜/ });
    expect((box as HTMLInputElement).checked).toBe(false);
    await userEvent.click(box);
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ voice_only: true });
  });

  it('已经是只出声的角色：复选框是勾上的，取消勾选保存 false', async () => {
    const calls = installFetch({ 'PATCH /api/characters/9': ZHAN });
    renderAt(<CharacterProfile c={{ ...ZHAN, voice_only: true }} slug="demo" onDirtyChange={() => {}} />);
    const box = screen.getByRole('checkbox', { name: /只出声，不出镜/ }) as HTMLInputElement;
    expect(box.checked).toBe(true);
    await userEvent.click(box);
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ voice_only: false }));
  });
});
