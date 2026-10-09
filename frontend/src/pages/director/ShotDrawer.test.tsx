import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { installFetch } from '../../test/fetch';
import { makeCandidate, makeShot, makeSlot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { ShotDrawer, toShotPatch } from './ShotDrawer';

const ENGINES = {
  image: [{ name: 'zimage', family: 'zimage', caps: {} }],
  video: [{ name: 'h3_video', family: 'h3', caps: { has_audio: true } }],
};
const SHOT = makeShot({
  id: 5,
  code: 'S03',
  image_prompt: 'old prompt',
  duration: 5,
  slots: [
    makeSlot({
      id: 9, kind: 'first_frame', selected_candidate_id: 101, stale: true,
      candidates: [makeCandidate({ id: 101 }), makeCandidate({ id: 102 })],
    }),
  ],
});

describe('ShotDrawer', () => {
  it('改提示词只提交改动的字段', async () => {
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'PATCH /api/shots/5': (c: { body: unknown }) => ({ ...SHOT, ...(c.body as object) }),
    });
    renderAt(<ShotDrawer shot={SHOT} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    const user = userEvent.setup();
    const ta = screen.getByLabelText('图像提示词（英文）');
    await user.clear(ta);
    await user.type(ta, 'new prompt');
    const dur = screen.getByLabelText('时长（秒）');
    await user.clear(dur);
    await user.type(dur, '7');
    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    const patch = calls.find((c) => c.method === 'PATCH');
    expect(patch?.url).toBe('/api/shots/5');
    expect(patch?.body).toEqual({ image_prompt: 'new prompt', duration: 7 });
  });

  it('选定另一个候选；槽位过期时标题旁有标记', async () => {
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'POST /api/candidates/102/select': makeCandidate({ id: 102 }),
    });
    renderAt(<ShotDrawer shot={SHOT} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    expect(screen.getByRole('dialog', { name: '镜头 S03 详情' })).toBeTruthy();
    expect(within(screen.getByRole('heading', { name: /首帧候选/ })).getByText('已过期')).toBeTruthy();
    await userEvent.setup().click(within(screen.getByTestId('candidate-102')).getByRole('button', { name: '选定' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/candidates/102/select')).toBe(true));
  });

  it('生成与关闭交给父组件', async () => {
    installFetch({ 'GET /api/engines': ENGINES });
    const onClose = vi.fn();
    const onGenerate = vi.fn();
    renderAt(<ShotDrawer shot={SHOT} episodeId={1} onClose={onClose} onGenerate={onGenerate} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '生成视频' }));
    expect(onGenerate).toHaveBeenCalledWith('videos');
    await user.click(screen.getByRole('button', { name: '关闭' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('提交运行中时，生成按钮禁用，点击不触发回调', async () => {
    installFetch({ 'GET /api/engines': ENGINES });
    const onGenerate = vi.fn();
    renderAt(<ShotDrawer shot={SHOT} episodeId={1} onClose={() => {}} onGenerate={onGenerate} generating />);
    const user = userEvent.setup();
    const genFrame = screen.getByRole('button', { name: '生成首帧' }) as HTMLButtonElement;
    const genVideo = screen.getByRole('button', { name: '生成视频' }) as HTMLButtonElement;
    expect(genFrame.disabled).toBe(true);
    expect(genVideo.disabled).toBe(true);
    await user.click(genFrame);
    await user.click(genVideo);
    expect(onGenerate).not.toHaveBeenCalled();
  });

  it('toShotPatch：时长转数字，引擎留空表示回到项目默认', () => {
    expect(toShotPatch({ duration: '6.5', image_engine: '', video_engine: 'h3_video', action: '抬头' })).toEqual({
      duration: 6.5,
      image_engine: null,
      video_engine: 'h3_video',
      action: '抬头',
    });
  });

  it('对白里填高棉语，保存时 PATCH 整段对白', async () => {
    const shot = makeShot({ id: 6, code: 'S02', dialogue: [{ speaker: '陈师傅', zh: '换？', start: 2, end: 2.6 }] });
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'PATCH /api/shots/6': (c: { body: unknown }) => ({ ...shot, ...(c.body as object) }),
    });
    renderAt(<ShotDrawer shot={shot} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('第 1 句高棉语'), 'ដូរ?');
    await user.click(screen.getByRole('button', { name: '保存对白' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({
      dialogue: [{ speaker: '陈师傅', zh: '换？', start: 2, end: 2.6, km: 'ដូរ?' }],
    });
  });
});
