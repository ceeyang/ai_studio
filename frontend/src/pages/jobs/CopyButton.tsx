import { useEffect, useState } from 'react';

/** 小号「复制」按钮：复制成功后 2 秒内显示「已复制」。剪贴板不可用时显示「复制失败」。 */
export function CopyButton({ text, label = '复制', what }: { text: string; label?: string; what?: string }) {
  const [state, setState] = useState<'idle' | 'ok' | 'fail'>('idle');
  useEffect(() => {
    if (state === 'idle') return;
    const t = setTimeout(() => setState('idle'), 2000);
    return () => clearTimeout(t);
  }, [state]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState('ok');
    } catch {
      setState('fail');
    }
  };
  return (
    <button
      type="button"
      className="ghost sm"
      aria-label={what ? `${label}${what}` : label}
      onClick={() => void copy()}
    >
      {state === 'ok' ? '已复制' : state === 'fail' ? '复制失败' : label}
    </button>
  );
}
