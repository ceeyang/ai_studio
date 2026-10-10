import { type ReactNode, createContext, useCallback, useContext, useRef, useState } from 'react';
import { Dialog } from './Dialog';

export interface ConfirmOptions {
  title: string;
  body: ReactNode;
  /** 确认按钮的字，默认「确定」 */
  confirmText?: string;
  /** 不确认按钮的字，默认「取消」（确认的动作本身就叫「取消…」时换个说法，免得两个按钮都叫取消） */
  cancelText?: string;
  /** 删除、取消任务这类不可撤销的操作：确认按钮用危险色，默认焦点放在「取消」上 */
  danger?: boolean;
}

export type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

interface Pending extends ConfirmOptions {
  resolve: (ok: boolean) => void;
}

/** 全局确认框：替代 window.confirm。挂在 App 根上，页面里用 useConfirm() 拿到 confirm()。 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const current = useRef<Pending | null>(null);

  const confirm = useCallback<ConfirmFn>(
    (opts) =>
      new Promise<boolean>((resolve) => {
        // 上一个还没回答就来了新的：旧的算取消
        current.current?.resolve(false);
        const next = { ...opts, resolve };
        current.current = next;
        setPending(next);
      }),
    [],
  );

  const answer = (ok: boolean) => {
    const p = current.current;
    current.current = null;
    setPending(null);
    p?.resolve(ok);
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && (
        <Dialog title={pending.title} onClose={() => answer(false)}>
          <div className="confirm-body">{pending.body}</div>
          <div className="modal-foot">
            <button type="button" data-autofocus={pending.danger ? true : undefined} onClick={() => answer(false)}>
              {pending.cancelText ?? '取消'}
            </button>
            <button
              type="button"
              className={pending.danger ? 'danger' : 'primary'}
              data-autofocus={pending.danger ? undefined : true}
              onClick={() => answer(true)}
            >
              {pending.confirmText ?? '确定'}
            </button>
          </div>
        </Dialog>
      )}
    </ConfirmContext.Provider>
  );
}

/** 拿到 confirm(opts) → Promise<boolean>。没挂 ConfirmProvider 时退回浏览器自带的 window.confirm。 */
export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  return ctx ?? fallbackConfirm;
}

const fallbackConfirm: ConfirmFn = async ({ title }) => window.confirm(title);
