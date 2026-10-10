// 抽屉里有没保存的修改、要关掉 / 换走 / 切阶段时的统一确认：「放弃修改」继续，「继续编辑」留下。
import type { ConfirmFn } from './ConfirmDialog';

/** what 如「这个角色」，then 如「关掉抽屉」：正文是「这个角色有改过但没保存的内容，关掉抽屉就没了。」 */
export function confirmDiscard(confirm: ConfirmFn, what: string, then: string): Promise<boolean> {
  return confirm({
    title: '放弃没保存的修改？',
    body: `${what}有改过但没保存的内容，${then}就没了。`,
    confirmText: '放弃修改',
    cancelText: '继续编辑',
    danger: true,
  });
}

/** 阶段内容登记给工作台的「能不能离开」检查：没有未保存的修改直接 true，有就问一句 */
export type LeaveCheck = () => Promise<boolean>;
