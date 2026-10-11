// 镜头抽屉里的「构图参考」（G）和「接上一镜」（D）。
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFetch } from '../../test/fetch';
import { makeCandidate, makeShot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { ShotDrawer } from './ShotDrawer';

const ENGINES = {
  image: [{ name: 'zimage', family: 'zimage', caps: {} }],
  video: [{ name: 'h3_video', family: 'h3', caps: { has_audio: true } }],
};

const ref = (id: number) => makeCandidate({ id, url: `/media/demo/media/shot_ref/${id}.png`, engine: 'upload', params: {} });

describe('ShotDrawer 构图参考', () => {
  it('上传、删除；满 2 张后不能再传', async () => {
    const shot = makeShot({ id: 5, code: 'S02', order: 1, image_max_refs: 9, shot_refs: [ref(31)] });
    const calls = installFetch({
      'GET /api/engines': ENGINES,
      'POST /api/shots/5/refs': { ...shot, shot_refs: [ref(31), ref(32)] },
      'DELETE /api/shots/5/refs/31': { ...shot, shot_refs: [] },
    });
    const { unmount } = renderAt(<ShotDrawer shot={shot} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    const user = userEvent.setup();
    const sec = screen.getByRole('region', { name: '构图参考' });
    expect(within(sec).getByAltText('构图参考 1')).toBeTruthy();
    expect(within(sec).queryByTestId('refs-unsupported')).toBeNull();
    await user.upload(within(sec).getByLabelText('上传构图参考'), new File(['png'], 'pose.png', { type: 'image/png' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/shots/5/refs')).toBe(true));
    await user.click(within(sec).getByRole('button', { name: '删除构图参考 1' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/shots/5/refs/31')).toBe(true));
    unmount();
    renderAt(<ShotDrawer shot={{ ...shot, shot_refs: [ref(31), ref(32)] }} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    const input = screen.getByLabelText('上传构图参考') as HTMLInputElement;
    expect(input.disabled).toBe(true);
    expect(screen.getByText('已有 2 张')).toBeTruthy();
  });

  it('图像引擎不吃参考图：提示构图参考用不上', () => {
    installFetch({ 'GET /api/engines': ENGINES });
    renderAt(<ShotDrawer shot={makeShot({ id: 5, image_max_refs: 0 })} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    expect(screen.getByTestId('refs-unsupported').textContent).toContain('不吃参考图');
  });
});

describe('ShotDrawer 接上一镜', () => {
  it('打开开关立即保存；上一镜没视频时两格都是「等上一镜视频」', async () => {
    const shot = makeShot({ id: 5, code: 'S02', order: 1 });
    const calls = installFetch({ 'GET /api/engines': ENGINES, 'PATCH /api/shots/5': { ...shot, continue_from_prev: true } });
    const { unmount } = renderAt(<ShotDrawer shot={shot} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('checkbox', { name: '接上一镜' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ continue_from_prev: true }));
    unmount();
    const waiting = { ...shot, continue_from_prev: true, continue_prev: { shot_id: 4, code: 'S01', has_video: false } };
    renderAt(<ShotDrawer shot={waiting} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    expect(screen.getByTestId('continue-hint').textContent).toContain('等上一镜视频：S01 还没有选定视频');
    const btns = screen.getAllByRole('button', { name: '等上一镜视频' }) as HTMLButtonElement[];
    expect(btns).toHaveLength(2);
    expect(btns.every((b) => b.disabled)).toBe(true);
  });

  it('上一镜有视频：说明首帧取哪一镜的尾帧；第一镜不能开', () => {
    installFetch({ 'GET /api/engines': ENGINES });
    const shot = makeShot({ id: 5, code: 'S02', order: 1, continue_from_prev: true, continue_prev: { shot_id: 4, code: 'S01', has_video: true } });
    const { unmount } = renderAt(<ShotDrawer shot={shot} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    expect(screen.getByTestId('continue-hint').textContent).toContain('首帧取 S01 选定视频的最后一帧');
    expect(screen.getByRole('button', { name: '生成首帧' })).toBeTruthy();
    unmount();
    renderAt(<ShotDrawer shot={makeShot({ id: 1, order: 0 })} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    expect((screen.getByRole('checkbox', { name: '接上一镜' }) as HTMLInputElement).disabled).toBe(true);
  });
});

describe('ShotDrawer 参考图提示', () => {
  it('参考图上限装不下所有定妆照：提示哪几个人的脸没锁上', () => {
    installFetch({ 'GET /api/engines': ENGINES });
    renderAt(
      <ShotDrawer
        shot={makeShot({ id: 5, image_max_refs: 3, first_frame_portraits_dropped: ['小李 定妆照'] })}
        episodeId={1}
        onClose={() => {}}
        onGenerate={() => {}}
      />,
    );
    const hint = screen.getByTestId('portraits-dropped');
    expect(hint.textContent).toContain('小李 定妆照');
    expect(hint.textContent).toContain('最多 3 张');
  });

  it('镜头所在场景还没有场景图：一行提示，链接到角色阶段的设定', () => {
    installFetch({ 'GET /api/engines': ENGINES });
    renderAt(
      <ShotDrawer
        shot={makeShot({ id: 5, image_max_refs: 9 })}
        episodeId={1}
        onClose={() => {}}
        onGenerate={() => {}}
        missingLocationImage={{ name: '修车行', to: '/projects/demo/episodes/1?stage=cast&settings=1' }}
      />,
    );
    const hint = screen.getByTestId('location-image-missing');
    expect(hint.textContent).toContain('这个场景还没有场景图，去角色阶段的设定里生成');
    expect(within(hint).getByRole('link').getAttribute('href')).toBe('/projects/demo/episodes/1?stage=cast&settings=1');
  });

  it('没传场景提示、也没丢定妆照：两条提示都不出', () => {
    installFetch({ 'GET /api/engines': ENGINES });
    renderAt(<ShotDrawer shot={makeShot({ id: 5, image_max_refs: 9 })} episodeId={1} onClose={() => {}} onGenerate={() => {}} />);
    expect(screen.queryByTestId('portraits-dropped')).toBeNull();
    expect(screen.queryByTestId('location-image-missing')).toBeNull();
  });
});
