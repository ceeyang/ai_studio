import { useState } from 'react';
import { errorText } from '../../api/client';
import { useProvideInput } from '../../api/queries';
import type { Job } from '../../api/types';

function PromptBlock({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false); // 剪贴板不可用时手动全选复制
    }
  };
  return (
    <div className="fields">
      <label className="field">
        <span>{label}</span>
        <textarea readOnly rows={8} value={text} />
      </label>
      <div className="row">
        <button type="button" onClick={() => void copy()}>
          复制
        </button>
        {copied && <span className="muted">已复制</span>}
      </div>
    </div>
  );
}

export interface ManualInputFormProps {
  job: Job;
  onSubmitted?: (job: Job) => void;
}

export function ManualInputForm({ job, onSubmitted }: ManualInputFormProps) {
  const input = useProvideInput();
  const [text, setText] = useState('');
  const req = job.result?.input_request;
  if (!req) return null;

  const submit = () =>
    input.mutate(
      { id: job.id, text },
      {
        onSuccess: (j) => {
          setText('');
          onSubmitted?.(j);
        },
      },
    );

  return (
    <section className="manual">
      <h4>
        等待人工输入：<code data-testid="input-key">{req.key}</code>
      </h4>
      <p className="muted">
        这一步配置成了 manual：把下面两段提示词交给任意模型（或另一个 Claude 会话），把它输出的 JSON 原样粘贴到最下面，提交后任务会重新排队。
      </p>
      {req.error && <p className="error">上次粘贴的结果不合格：{req.error}</p>}
      <PromptBlock label="系统提示词（system）" text={req.system} />
      <PromptBlock label="用户提示词（user）" text={req.user} />
      <label className="field">
        <span>模型输出</span>
        <textarea rows={12} value={text} onChange={(e) => setText(e.target.value)} placeholder='{"...": ...}' />
      </label>
      <div className="row">
        <button type="button" className="primary" disabled={!text.trim() || input.isPending} onClick={submit}>
          提交结果
        </button>
      </div>
      {input.error && <p className="error">{errorText(input.error)}</p>}
    </section>
  );
}
