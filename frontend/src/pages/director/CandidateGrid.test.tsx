import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { makeCandidate, makeSlot } from '../../test/factories';
import { CandidateGrid } from './CandidateGrid';

describe('CandidateGrid', () => {
  it('列出所有候选，已选定的标出来，点"选定"回调候选 id', async () => {
    const onSelect = vi.fn();
    const slot = makeSlot({
      id: 5, kind: 'first_frame', selected_candidate_id: 101,
      candidates: [makeCandidate({ id: 101 }), makeCandidate({ id: 102, params: { seed: 9 } })],
    });
    render(<CandidateGrid slot={slot} onSelect={onSelect} />);
    const first = screen.getByTestId('candidate-101');
    expect(within(first).getByText('已选定')).toBeTruthy();
    expect(within(first).queryByRole('button', { name: '选定' })).toBeNull();
    const second = screen.getByTestId('candidate-102');
    // 默认只写引擎中文名和时间；编号、种子、参数收在「高级」里
    expect(within(second).getByText('Z-Image 快速')).toBeTruthy();
    const adv = within(second).getByText('高级').closest('details') as HTMLDetailsElement;
    expect(adv.open).toBe(false);
    expect(within(adv).getByText(/#102 · zimage · seed 9/)).toBeTruthy();
    await userEvent.setup().click(within(second).getByRole('button', { name: '选定' }));
    expect(onSelect).toHaveBeenCalledWith(102);
    // 新的在前
    expect(screen.getAllByTestId(/^candidate-/).map((el) => el.dataset.testid)).toEqual(['candidate-102', 'candidate-101']);
  });

  it('视频槽位用 video 预览；有工作流记录时给出导出到 ComfyUI 的按钮', () => {
    const slot = makeSlot({
      kind: 'video',
      candidates: [makeCandidate({ id: 201, url: '/media/x.mp4' }), makeCandidate({ id: 202, params: {} })],
    });
    const { container } = render(<CandidateGrid slot={slot} onSelect={() => {}} />);
    expect(container.querySelectorAll('video')).toHaveLength(2);
    expect(within(screen.getByTestId('candidate-201')).getByRole('button', { name: '导出到 ComfyUI' })).toBeTruthy();
    expect(within(screen.getByTestId('candidate-202')).queryByRole('button', { name: '导出到 ComfyUI' })).toBeNull();
  });

  it('没有候选时说明', () => {
    render(<CandidateGrid slot={makeSlot()} onSelect={() => {}} />);
    expect(screen.getByText('还没有候选')).toBeTruthy();
  });

  it('导出到 ComfyUI：成功时请求工作流并触发下载', async () => {
    const blob = new Blob([JSON.stringify({ nodes: [] })], { type: 'application/json' });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(blob, {
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="zimage_201.json"' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const createObjectURL = vi.fn().mockReturnValue('blob:mock-url');
    const revokeObjectURL = vi.fn();
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;

    const clickSpy = vi.fn();
    const realCreateElement = document.createElement.bind(document);
    const createElementSpy = vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = realCreateElement(tag);
      if (tag === 'a') (el as HTMLAnchorElement).click = clickSpy;
      return el;
    });

    const slot = makeSlot({ kind: 'video', candidates: [makeCandidate({ id: 201, url: '/media/x.mp4' })] });
    render(<CandidateGrid slot={slot} onSelect={() => {}} />);
    await userEvent
      .setup()
      .click(within(screen.getByTestId('candidate-201')).getByRole('button', { name: '导出到 ComfyUI' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/candidates/201/comfy-workflow'));
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    expect(createObjectURL).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url');
    const anchor = createElementSpy.mock.results.map((r) => r.value as HTMLElement).find((el) => el.tagName === 'A');
    expect((anchor as HTMLAnchorElement).download).toBe('zimage_201.json');

    createElementSpy.mockRestore();
  });

  it('导出到 ComfyUI：失败时就地显示后端返回的 detail（如缺失的节点名）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '缺少节点：RefNode' }), { status: 400 })),
    );
    const slot = makeSlot({ kind: 'video', candidates: [makeCandidate({ id: 201, url: '/media/x.mp4' })] });
    render(<CandidateGrid slot={slot} onSelect={() => {}} />);
    await userEvent
      .setup()
      .click(within(screen.getByTestId('candidate-201')).getByRole('button', { name: '导出到 ComfyUI' }));
    await waitFor(() => expect(screen.getByText(/导出失败/)).toBeTruthy());
    expect(screen.getByText(/RefNode/)).toBeTruthy();
  });
});
