import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Project } from '../api/types';
import { installFetch } from '../test/fetch';
import { renderAt } from '../test/render';
import { ProjectSettingsDialog } from './ProjectSettingsDialog';

const PROJECT: Project = {
  id: 1, slug: 'demo', title: '海风', aspect: '9:16', image_engine: 'zimage', video_engine: 'gemini_video', seed: 1,
  negative: '', style_preset: 'live_action', style_bible_id: null, archived: false, created_at: '2026-10-12T00:00:00Z',
};

describe('ProjectSettingsDialog 固定时长视频引擎', () => {
  it('默认多拍镜头；组镜拆分列出但不能选；改成不合并只发这一项', async () => {
    const calls = installFetch({ 'PATCH /api/projects/demo': (c: { body: unknown }) => ({ ...PROJECT, ...(c.body as object) }) });
    renderAt(<ProjectSettingsDialog project={PROJECT} onClose={() => {}} />);
    const select = screen.getByRole('combobox', { name: '固定时长视频引擎怎么用满' }) as HTMLSelectElement;
    expect(select.value).toBe('beats');
    const group = screen.getByRole('option', { name: '组镜拆分（即将支持）' }) as HTMLOptionElement;
    expect(group.disabled).toBe(true);
    expect(screen.getByRole('option', { name: '多拍镜头（一次生成里切 2–4 拍）' })).toBeTruthy();
    const user = userEvent.setup();
    await user.selectOptions(select, 'off');
    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ fixed_engine_pack: 'off' });
  });

  it('老项目已经选了不合并：下拉显示当前值', () => {
    installFetch({});
    renderAt(<ProjectSettingsDialog project={{ ...PROJECT, fixed_engine_pack: 'off' }} onClose={() => {}} />);
    expect((screen.getByRole('combobox', { name: '固定时长视频引擎怎么用满' }) as HTMLSelectElement).value).toBe('off');
  });
});
