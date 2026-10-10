import { render, renderHook, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, RouterProvider, createMemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { EditableFields, type FieldSpec, LeaveGuard, useUnsavedGuard } from './EditableFields';

const FIELDS: FieldSpec[] = [
  { name: 'name', label: '名字' },
  { name: 'desc', label: '描述', kind: 'textarea' },
  {
    name: 'loc',
    label: '场景',
    kind: 'select',
    options: [
      { value: '', label: '（未指定）' },
      { value: '3', label: '直播间' },
    ],
  },
];
const INITIAL = { name: '林夏', desc: '', loc: '' };

describe('EditableFields', () => {
  it('没改时保存不可用；改了之后 onSave 拿到全部值和改动', async () => {
    const onSave = vi.fn();
    const user = userEvent.setup();
    render(<EditableFields fields={FIELDS} initial={INITIAL} onSave={onSave} />);
    const btn = screen.getByRole('button', { name: '保存' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    await user.type(screen.getByLabelText('描述'), '女主播');
    await user.selectOptions(screen.getByLabelText('场景'), '3');
    expect(screen.getByText('有未保存的修改')).toBeTruthy();
    await user.click(btn);
    expect(onSave).toHaveBeenCalledWith({ name: '林夏', desc: '女主播', loc: '3' }, { desc: '女主播', loc: '3' });
  });

  it('父组件用相同内容重新渲染时不冲掉正在编辑的内容', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<EditableFields fields={FIELDS} initial={{ ...INITIAL }} onSave={() => {}} />);
    await user.type(screen.getByLabelText('描述'), '草稿');
    rerender(<EditableFields fields={FIELDS} initial={{ ...INITIAL }} onSave={() => {}} />);
    expect((screen.getByLabelText('描述') as HTMLTextAreaElement).value).toBe('草稿');
  });

  it('服务器内容变了就换成新内容', () => {
    const { rerender } = render(<EditableFields fields={FIELDS} initial={INITIAL} onSave={() => {}} />);
    rerender(<EditableFields fields={FIELDS} initial={{ ...INITIAL, name: '林秋' }} onSave={() => {}} />);
    expect((screen.getByLabelText('名字') as HTMLInputElement).value).toBe('林秋');
  });

  it('显示保存错误和保存中状态', () => {
    render(<EditableFields fields={FIELDS} initial={INITIAL} onSave={() => {}} saving error="名字不能为空（HTTP 422）" />);
    expect(screen.getByText('名字不能为空（HTTP 422）')).toBeTruthy();
    expect(screen.getByRole('button', { name: '保存中…' })).toBeTruthy();
  });

  it('改了 A 还没保存时，服务器上的 B 变了：A 保留、B 更新', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<EditableFields fields={FIELDS} initial={INITIAL} onSave={() => {}} />);
    await user.type(screen.getByLabelText('描述'), '草稿');
    rerender(<EditableFields fields={FIELDS} initial={{ ...INITIAL, name: '林秋' }} onSave={() => {}} />);
    expect((screen.getByLabelText('描述') as HTMLTextAreaElement).value).toBe('草稿');
    expect((screen.getByLabelText('名字') as HTMLInputElement).value).toBe('林秋');
    expect(screen.getByText('有未保存的修改')).toBeTruthy();
  });

  it('改过的字段服务器也变了：以用户正在改的为准', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<EditableFields fields={FIELDS} initial={INITIAL} onSave={() => {}} />);
    await user.clear(screen.getByLabelText('名字'));
    await user.type(screen.getByLabelText('名字'), '阿夏');
    rerender(<EditableFields fields={FIELDS} initial={{ ...INITIAL, name: '林秋' }} onSave={() => {}} />);
    expect((screen.getByLabelText('名字') as HTMLInputElement).value).toBe('阿夏');
  });

  it('onDirtyChange：改了报 true，改回去报 false', async () => {
    const user = userEvent.setup();
    const onDirty = vi.fn();
    render(<EditableFields fields={FIELDS} initial={INITIAL} onSave={() => {}} onDirtyChange={onDirty} />);
    await user.type(screen.getByLabelText('描述'), 'x');
    expect(onDirty).toHaveBeenLastCalledWith(true);
    await user.clear(screen.getByLabelText('描述'));
    expect(onDirty).toHaveBeenLastCalledWith(false);
  });
});

describe('useUnsavedGuard', () => {
  it('有未保存内容时关页面会被拦（beforeunload），没有时不拦', () => {
    const { rerender, unmount } = renderHook(({ dirty }) => useUnsavedGuard(dirty), { initialProps: { dirty: true } });
    const ev = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
    rerender({ dirty: false });
    const ev2 = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(ev2);
    expect(ev2.defaultPrevented).toBe(false);
    unmount();
  });

  it('数据路由下站内跳转先问：取消就留在原页', async () => {
    const user = userEvent.setup();
    const ask = vi.fn(() => false);
    vi.stubGlobal('confirm', ask);
    function Page() {
      useUnsavedGuard(true);
      return (
        <>
          <Link to="/other">走</Link>
          <LeaveGuard />
        </>
      );
    }
    const router = createMemoryRouter(
      [
        { path: '/', element: <Page /> },
        { path: '/other', element: <p>别处</p> },
      ],
      { initialEntries: ['/'] },
    );
    render(<RouterProvider router={router} />);
    await user.click(screen.getByRole('link', { name: '走' }));
    expect(ask).toHaveBeenCalledWith('有未保存的修改，确定离开？');
    expect(router.state.location.pathname).toBe('/');
    ask.mockReturnValue(true);
    await user.click(screen.getByRole('link', { name: '走' }));
    expect(await screen.findByText('别处')).toBeTruthy();
  });

  it('一页里有好几处在编辑：只要有一处没保存就拦（全站只有一个拦截器，不会互相覆盖）', async () => {
    const user = userEvent.setup();
    const ask = vi.fn(() => false);
    vi.stubGlobal('confirm', ask);
    function Editor({ dirty }: { dirty: boolean }) {
      useUnsavedGuard(dirty);
      return null;
    }
    const router = createMemoryRouter(
      [
        {
          path: '/',
          element: (
            <>
              <Editor dirty />
              <Editor dirty={false} />
              <Link to="/other">走</Link>
              <LeaveGuard />
            </>
          ),
        },
        { path: '/other', element: <p>别处</p> },
      ],
      { initialEntries: ['/'] },
    );
    render(<RouterProvider router={router} />);
    await user.click(screen.getByRole('link', { name: '走' }));
    expect(ask).toHaveBeenCalledTimes(1);
    expect(router.state.location.pathname).toBe('/');
  });

  it('只改地址栏查询串（如开关镜头抽屉）不拦', async () => {
    const user = userEvent.setup();
    const ask = vi.fn(() => false);
    vi.stubGlobal('confirm', ask);
    function Page() {
      useUnsavedGuard(true);
      return (
        <>
          <Link to="/?shot=3">开抽屉</Link>
          <LeaveGuard />
        </>
      );
    }
    const router = createMemoryRouter([{ path: '/', element: <Page /> }], { initialEntries: ['/'] });
    render(<RouterProvider router={router} />);
    await user.click(screen.getByRole('link', { name: '开抽屉' }));
    expect(ask).not.toHaveBeenCalled();
    expect(router.state.location.search).toBe('?shot=3');
  });
});
