import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { installFetch } from '../../test/fetch';
import { makeCandidate, makeShot, makeSlot } from '../../test/factories';
import { Link } from 'react-router-dom';
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
/** 首帧选定且没过期：视频那一格可以直接生成 */
const READY = { ...SHOT, slots: [{ ...SHOT.slots[0], stale: false }] };

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

  it('图像提示词里有外貌占位符：显示出图时实际用的文字；没有占位符不显示', async () => {
    installFetch({ 'GET /api/engines': ENGINES });
    const shot = {
      ...SHOT,
      image_prompt: 'wide. {{char:3}} He waits.',
      image_prompt_resolved: 'wide. Chinese man in his fifties. He waits.',
      placeholder_names: { '{{char:3}}': '老周' },
    };
    const { unmount } = renderAt(<ShotDrawer shot={shot} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    expect(screen.getByText('wide. Chinese man in his fifties. He waits.')).toBeTruthy();
    expect(screen.getByTestId('placeholder-legend').textContent).toBe('外貌占位符：{{char:3}} = 老周');
    unmount();
    renderAt(<ShotDrawer shot={SHOT} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    expect(screen.queryByText(/角色外貌占位符/)).toBeNull();
  });

  it('引擎下拉框：留空写成「默认（项目现在用的引擎）」', async () => {
    installFetch({
      'GET /api/engines': { image: [...ENGINES.image, { name: 'krea2', family: 'krea2', caps: {} }], video: ENGINES.video },
      'GET /api/projects/demo': { id: 1, slug: 'demo', title: 'demo', aspect: '9:16', image_engine: 'krea2', video_engine: 'h3_video' },
    });
    renderAt(<ShotDrawer shot={SHOT} episodeId={1} slug="demo" onClose={() => {}} onGenerate={() => {}} />);
    await waitFor(() => expect(within(screen.getByLabelText('图像引擎')).getByRole('option', { name: '默认（Krea2 人像）' })).toBeTruthy());
    expect(within(screen.getByLabelText('视频引擎')).getByRole('option', { name: '默认（H3 视频（有声））' })).toBeTruthy();
  });

  it('没有项目 slug 时引擎留空项叫「项目默认」', async () => {
    installFetch({ 'GET /api/engines': ENGINES });
    renderAt(<ShotDrawer shot={SHOT} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    expect(within(screen.getByLabelText('图像引擎')).getByRole('option', { name: '项目默认' })).toBeTruthy();
  });

  it('候选排在提示词前面；英文提示词收在「高级」里', async () => {
    installFetch({ 'GET /api/engines': ENGINES });
    renderAt(<ShotDrawer shot={SHOT} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    const cands = screen.getByRole('heading', { name: /首帧候选/ });
    const params = screen.getByRole('heading', { name: '提示词与参数' });
    expect(cands.compareDocumentPosition(params) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const adv = screen.getByLabelText('图像提示词（英文）').closest('details');
    expect(adv?.querySelector('summary')?.textContent).toBe('高级');
    expect(adv?.open).toBe(false);
    expect(screen.getByLabelText('动作').closest('details')).toBeNull();
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

  it('生成与关闭交给父组件；已有选定首帧的叫「重拍首帧」，没出过视频的叫「生成视频」', async () => {
    installFetch({ 'GET /api/engines': ENGINES });
    const onClose = vi.fn();
    const onGenerate = vi.fn();
    renderAt(<ShotDrawer shot={READY} episodeId={1} onClose={onClose} onGenerate={onGenerate} />);
    const user = userEvent.setup();
    expect(screen.getByRole('button', { name: '重拍首帧' })).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '生成视频' }));
    expect(onGenerate).toHaveBeenCalledWith('videos');
    await user.click(screen.getByRole('button', { name: '关闭' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('首帧缺少或过期时，视频那一格是「先出首帧」/「先重拍首帧」（和卡片一致）：点了交给父组件切到首帧阶段，不出视频', async () => {
    installFetch({ 'GET /api/engines': ENGINES });
    const onGenerate = vi.fn();
    const onNeed = vi.fn();
    const { unmount } = renderAt(
      <ShotDrawer shot={SHOT} episodeId={1} onClose={() => {}} onGenerate={onGenerate} onNeedFirstFrame={onNeed} />,
    );
    const user = userEvent.setup();
    expect(screen.queryByRole('button', { name: /生成视频/ })).toBeNull();
    await user.click(screen.getByRole('button', { name: '先重拍首帧' }));
    expect(onNeed).toHaveBeenCalledTimes(1);
    expect(onGenerate).not.toHaveBeenCalled();
    // 焦点落到首帧那一格的按钮上
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '重拍首帧' }));
    unmount();
    renderAt(<ShotDrawer shot={makeShot({ id: 7, code: 'S04' })} episodeId={1} onClose={() => {}} onGenerate={onGenerate} onNeedFirstFrame={onNeed} />);
    expect(screen.getByRole('button', { name: '先出首帧' })).toBeTruthy();
  });

  it('提交运行中时，生成按钮禁用，点击不触发回调', async () => {
    installFetch({ 'GET /api/engines': ENGINES });
    const onGenerate = vi.fn();
    renderAt(<ShotDrawer shot={READY} episodeId={1} onClose={() => {}} onGenerate={onGenerate} generating />);
    const user = userEvent.setup();
    const genFrame = screen.getByRole('button', { name: '重拍首帧' }) as HTMLButtonElement;
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

  it('是模态对话框：打开就聚焦，Esc 和点遮罩都能关', async () => {
    installFetch({ 'GET /api/engines': ENGINES });
    const onClose = vi.fn();
    renderAt(<ShotDrawer shot={SHOT} episodeId={1} onClose={onClose} onGenerate={() => {}} />);
    const dlg = screen.getByRole('dialog', { name: '镜头 S03 详情' });
    expect(dlg.getAttribute('aria-modal')).toBe('true');
    expect(document.activeElement).toBe(dlg);
    const user = userEvent.setup();
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
    await user.click(screen.getByTestId('drawer-scrim'));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('有没保存的修改时关抽屉先确认，选继续编辑就不关', async () => {
    installFetch({ 'GET /api/engines': ENGINES });
    const onClose = vi.fn();
    renderAt(<ShotDrawer shot={SHOT} episodeId={1} onClose={onClose} onGenerate={() => {}} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('动作'), '再加一句');
    await user.click(screen.getByTestId('drawer-scrim'));
    const confirm = await screen.findByRole('dialog', { name: '放弃没保存的修改？' });
    await user.click(within(confirm).getByRole('button', { name: '继续编辑' }));
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '关闭' }));
    await user.click(within(await screen.findByRole('dialog', { name: '放弃没保存的修改？' })).getByRole('button', { name: '放弃修改' }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it('改了提示词没保存时，生成按钮变成「保存并…」：先保存再生成', async () => {
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'PATCH /api/shots/5': (c: { body: unknown }) => ({ ...SHOT, ...(c.body as object) }),
    });
    const onGenerate = vi.fn(() => {
      // 生成时保存已经完成
      expect(calls.some((c) => c.method === 'PATCH')).toBe(true);
    });
    renderAt(<ShotDrawer shot={SHOT} episodeId={1} onClose={() => {}} onGenerate={onGenerate} />);
    const user = userEvent.setup();
    expect(screen.getByRole('button', { name: '重拍首帧' })).toBeTruthy();
    const ta = screen.getByLabelText('图像提示词（英文）');
    await user.clear(ta);
    await user.type(ta, 'new prompt');
    await user.click(screen.getByRole('button', { name: '保存并重拍首帧' }));
    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith('keyframes', true));
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ image_prompt: 'new prompt' });
  });

  it('候选数在抽屉里也能改（和导演台工具栏共用）；没有强制重跑勾选框', async () => {
    installFetch({ 'GET /api/engines': ENGINES });
    const onCountChange = vi.fn();

    renderAt(
      <ShotDrawer shot={SHOT} episodeId={1} onClose={() => {}} onGenerate={() => {}} count={2}
        onCountChange={onCountChange} />,
    );
    const user = userEvent.setup();
    const sel = screen.getByLabelText('候选数') as HTMLSelectElement;
    expect(sel.value).toBe('2');
    await user.selectOptions(sel, '3');
    expect(onCountChange).toHaveBeenCalledWith(3);
    expect(screen.queryByLabelText('强制重跑')).toBeNull();
    expect(screen.queryByText(/导演台工具栏上的设置/)).toBeNull();
  });

  it('写出过期原因、上次失败的原因，进行中的那一格按钮显示生成中', () => {
    installFetch({ 'GET /api/engines': ENGINES });
    const shot = makeShot({
      ...SHOT,
      slots: [{ ...SHOT.slots[0], stale_reason: '林夏 定妆照换了' }],
      failures: [{ id: 80, type: 'video.shot', status: 'failed', error: '显存不足' }],
      active_jobs: [{ id: 81, type: 'image.keyframe', status: 'running', progress: 0.5 }],
    });
    renderAt(<ShotDrawer shot={shot} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    expect(screen.getByText('过期原因：林夏 定妆照换了')).toBeTruthy();
    expect(screen.getByText('视频上次生成失败：显存不足')).toBeTruthy();
    const gen = screen.getByRole('button', { name: '首帧生成中 50%' }) as HTMLButtonElement;
    expect(gen.disabled).toBe(true);
  });

  it('对白改了没保存时也变「保存并生成」：对白和提示词一起先保存', async () => {
    const shot = makeShot({ id: 6, code: 'S02', dialogue: [{ speaker: '陈师傅', zh: '换？', start: 2, end: 2.6 }] });
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'PATCH /api/shots/6': (c: { body: unknown }) => ({ ...shot, ...(c.body as object) }),
    });
    const onGenerate = vi.fn();
    renderAt(<ShotDrawer shot={shot} episodeId={1} onClose={() => {}} onGenerate={onGenerate} />);
    const user = userEvent.setup();
    const zh = screen.getByLabelText('第 1 句中文');
    await user.clear(zh);
    await user.type(zh, '换什么？');
    await user.click(screen.getByRole('button', { name: '保存并生成首帧' }));
    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith('keyframes', true));
    const patches = calls.filter((c) => c.method === 'PATCH');
    expect(patches).toHaveLength(1);
    expect(patches[0].body).toEqual({ dialogue: [{ speaker: '陈师傅', zh: '换什么？', start: 2, end: 2.6 }] });
  });

  it('保存失败就不生成', async () => {
    const shot = makeShot({ id: 6, code: 'S02', dialogue: [{ speaker: '陈师傅', zh: '换？', start: 2, end: 2.6 }] });
    installFetch({
      'GET /api/engines': ENGINES,
      'PATCH /api/shots/6': () => new Response(JSON.stringify({ detail: '时长不对' }), { status: 400 }),
    });
    const onGenerate = vi.fn();
    renderAt(<ShotDrawer shot={shot} episodeId={1} onClose={() => {}} onGenerate={onGenerate} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('第 1 句中文'), '吗');
    await user.click(screen.getByRole('button', { name: '保存并生成首帧' }));
    expect(await screen.findByText(/时长不对/)).toBeTruthy();
    expect(onGenerate).not.toHaveBeenCalled();
  });

  it('提示词或对白改了没保存时换页面先问', async () => {
    const shot = makeShot({ id: 6, code: 'S02', dialogue: [{ speaker: '陈师傅', zh: '换？', start: 2, end: 2.6 }] });
    installFetch({ 'GET /api/engines': ENGINES });
    renderAt(
      <>
        <ShotDrawer shot={shot} episodeId={1} onClose={() => {}} onGenerate={() => {}} />
        <Link to="/jobs">去任务页</Link>
      </>,
      { route: '/d', path: '/d' },
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('动作'), '！');
    await user.click(screen.getByRole('link', { name: '去任务页' }));
    const dlg = await screen.findByRole('dialog', { name: '有未保存的修改，确定离开？' });
    await user.click(within(dlg).getByRole('button', { name: '留下' }));
    expect(screen.getByTestId('location').textContent).toBe('/d');
  });
});
