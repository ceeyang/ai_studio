import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { installFetch } from '../test/fetch';
import { renderAt } from '../test/render';
import { RegenerateDialog, impactText } from './RegenerateDialog';

const IMPACT = {
  stage: 'storyboard', will_run: true, blocks: [3, 5], shots_deleted: 12, selected_first_frames: 10,
  selected_videos: 8, manual_kept: 2, overwrites: ['角色描述', '场景氛围'],
};
const MILD = { ...IMPACT, blocks: [2], shots_deleted: 3, selected_first_frames: 0, selected_videos: 0, manual_kept: 0, overwrites: [] };
const NOTHING = { ...IMPACT, stage: 'script', blocks: [], shots_deleted: 0, selected_first_frames: 0, selected_videos: 0, manual_kept: 0, overwrites: [] };

const writes = <T extends { method: string }>(calls: T[]) => calls.filter((c) => c.method !== 'GET');

function setup(props: Partial<Parameters<typeof RegenerateDialog>[0]> = {}, routes: Record<string, unknown> = {}) {
  const calls = installFetch({
    'GET /api/episodes/4/rerun-impact': IMPACT,
    'POST /api/episodes/4/run': { job_ids: [9, 10] },
    ...routes,
  });
  const onClose = vi.fn();
  const onSubmitted = vi.fn();
  renderAt(
    <RegenerateDialog
      open
      onClose={onClose}
      stage="storyboard"
      episodeId={4}
      scopeLabel="整集"
      onSubmitted={onSubmitted}
      {...props}
    />,
  );
  return { calls, onClose, onSubmitted, user: userEvent.setup() };
}

describe('RegenerateDialog', () => {
  it('关着时什么都不渲染、不查影响', () => {
    const calls = installFetch({});
    renderAt(<RegenerateDialog open={false} onClose={() => {}} stage="script" episodeId={4} scopeLabel="整集" onSubmitted={() => {}} />);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it('写明范围，按强制查影响并原样列出；会删有选定视频的镜头时「重新生成」是危险样式', async () => {
    const { calls } = setup();
    const dlg = await screen.findByRole('dialog', { name: '重新生成分镜' });
    expect(dlg.textContent).toContain('范围：整集');
    expect(
      await within(dlg).findByText('将重建段落 3、5 的镜头：删除 12 个镜头（其中 8 个有选定视频），保留 2 个手工镜头；会覆盖角色描述、场景氛围'),
    ).toBeTruthy();
    expect(calls.find((c) => c.url.includes('rerun-impact'))?.url).toBe('/api/episodes/4/rerun-impact?stage=storyboard&force=1');
    const ta = within(dlg).getByLabelText('修改意见（可选）') as HTMLTextAreaElement;
    expect(ta.maxLength).toBe(500);
    expect(ta.placeholder).toBe('例如「第 3 段改成在车底对话」「镜头更紧凑，少用远景」');
    expect(within(dlg).getByRole('button', { name: '重新生成' }).className).toContain('danger');
  });

  it('会删掉有选定首帧的镜头（没有选定视频）时也是危险样式', async () => {
    setup({}, { 'GET /api/episodes/4/rerun-impact': { ...MILD, selected_first_frames: 1 } });
    const dlg = await screen.findByRole('dialog');
    await within(dlg).findByText('将重建段落 2 的镜头：删除 3 个镜头（其中 1 个有选定首帧）');
    expect(within(dlg).getByRole('button', { name: '重新生成' }).className).toContain('danger');
  });

  it('没有意见：提交 force=true，不带 note；成功后回调任务 id 并关闭', async () => {
    const { calls, onClose, onSubmitted, user } = setup({}, { 'GET /api/episodes/4/rerun-impact': MILD });
    const dlg = await screen.findByRole('dialog');
    await within(dlg).findByText(/删除 3 个镜头/);
    expect(within(dlg).getByRole('button', { name: '重新生成' }).className).not.toContain('danger');
    await user.click(within(dlg).getByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(onSubmitted).toHaveBeenCalledWith([9, 10]));
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0]).toMatchObject({ method: 'POST', url: '/api/episodes/4/run', body: { stage: 'storyboard', force: true } });
    expect(onClose).toHaveBeenCalled();
  });

  it('有意见：note 一起提交（去掉首尾空白）；输入意见不会每个字都重新查影响', async () => {
    const { calls, onSubmitted, user } = setup({ stage: 'script' }, { 'GET /api/episodes/4/rerun-impact': NOTHING });
    const dlg = await screen.findByRole('dialog', { name: '重新生成剧本' });
    expect(await within(dlg).findByText('不会删除镜头，也不会覆盖已有设定')).toBeTruthy();
    await user.type(within(dlg).getByLabelText('修改意见（可选）'), '  第 3 段改成在车底对话 ');
    await waitFor(() => expect(calls.filter((c) => c.url.includes('rerun-impact')).length).toBeLessThanOrEqual(2));
    await user.click(within(dlg).getByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(onSubmitted).toHaveBeenCalled());
    expect(writes(calls)[0].body).toEqual({ stage: 'script', force: true, note: '第 3 段改成在车底对话' });
  });

  it('按段：查影响和提交都带 scene_block_ids', async () => {
    const { calls, onSubmitted, user } = setup({ blockIds: [21, 23], scopeLabel: '第 2 段' });
    const dlg = await screen.findByRole('dialog');
    expect(dlg.textContent).toContain('范围：第 2 段');
    await within(dlg).findByText(/删除 12 个镜头/);
    expect(calls.find((c) => c.url.includes('rerun-impact'))?.url).toBe(
      '/api/episodes/4/rerun-impact?stage=storyboard&force=1&scene_block_ids=21,23',
    );
    await user.click(within(dlg).getByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(onSubmitted).toHaveBeenCalled());
    expect(writes(calls)[0].body).toEqual({ stage: 'storyboard', force: true, scene_block_ids: [21, 23] });
  });

  it('先 beforeRun（保存未保存的修改）；返回 false 就不提交、不关', async () => {
    const beforeRun = vi.fn(async () => false);
    const { calls, onClose, onSubmitted, user } = setup({ beforeRun, unsavedNote: '有未保存的修改，重新生成前会先保存' });
    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).getByText('有未保存的修改，重新生成前会先保存')).toBeTruthy();
    await user.click(within(dlg).getByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(beforeRun).toHaveBeenCalled());
    expect(writes(calls)).toHaveLength(0);
    expect(onSubmitted).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(within(dlg).getByText('保存没成功，没有重新生成')).toBeTruthy();
  });

  it('beforeRun 返回 true 才提交', async () => {
    const order: string[] = [];
    const beforeRun = vi.fn(async () => {
      order.push('save');
      return true;
    });
    const { onSubmitted, user } = setup({ beforeRun, onSubmitted: vi.fn(() => order.push('run')) });
    const dlg = await screen.findByRole('dialog');
    await user.click(within(dlg).getByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(order).toEqual(['save', 'run']));
    expect(onSubmitted).toBeDefined();
  });

  it('提交被拒：错误显示在对话框里，不关', async () => {
    const { onClose, user } = setup(
      {},
      { 'POST /api/episodes/4/run': new Response(JSON.stringify({ detail: '修改意见太长' }), { status: 400, headers: { 'Content-Type': 'application/json' } }) },
    );
    const dlg = await screen.findByRole('dialog');
    await user.click(within(dlg).getByRole('button', { name: '重新生成' }));
    expect(await within(dlg).findByText(/修改意见太长/)).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('会删改过的镜头、分镜规则已更新：一并写明，改过的镜头也算危险', async () => {
    setup({}, {
      'GET /api/episodes/4/rerun-impact': { ...MILD, edited_shots: 2, rules_outdated: true },
    });
    const dlg = await screen.findByRole('dialog');
    expect(await within(dlg).findByText('将重建段落 2 的镜头：删除 3 个镜头（其中 2 个生成后改过）')).toBeTruthy();
    expect(within(dlg).getByText('分镜规则已更新，现有镜头是按旧规则出的；这次重新生成会按新规则出')).toBeTruthy();
    expect(within(dlg).getByRole('button', { name: '重新生成' }).className).toContain('danger');
  });

  it('impactText：选定和改过的一起列', () => {
    expect(impactText({ ...IMPACT, edited_shots: 3 } as never)).toBe(
      '将重建段落 3、5 的镜头：删除 12 个镜头（其中 8 个有选定视频、3 个生成后改过），保留 2 个手工镜头；会覆盖角色描述、场景氛围',
    );
  });

  it('取消：不提交', async () => {
    const { calls, onClose, user } = setup();
    const dlg = await screen.findByRole('dialog');
    await user.click(within(dlg).getByRole('button', { name: '取消' }));
    expect(onClose).toHaveBeenCalled();
    expect(writes(calls)).toHaveLength(0);
  });
});
