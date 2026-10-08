import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { EditableFields, type FieldSpec } from './EditableFields';

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
});
