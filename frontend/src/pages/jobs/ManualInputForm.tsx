import { useState } from 'react';
import { errorText } from '../../api/client';
import { useProvideInput } from '../../api/queries';
import type { Job } from '../../api/types';
import { type MediaItem, useMediaGroup } from '../../components/MediaViewer';

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
  const pics = (req?.images ?? [])
    .map((path, i) => ({ path, i, url: req?.image_urls?.[i] }))
    .filter((x): x is { path: string; i: number; url: string } => !!x.url);
  const items: MediaItem[] = pics.map((x) => ({ url: x.url, alt: `要发给模型的图片 ${x.i + 1}`, title: `图片 ${x.i + 1} · ${x.path}` }));
  const view = useMediaGroup(items, { title: '要发给模型的图片' });
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
      {req.images && req.images.length > 0 && (
        <div className="fields" aria-label="要一起发给模型的图片">
          <p className="muted">这一步要模型看图：把下面的图片和提示词一起发给能看图的模型（网页版 Claude、ChatGPT 等都行）。</p>
          {req.images.map((path, i) => {
            const url = req.image_urls?.[i];
            return (
              <div key={path} className="photo-look-row">
                {url && (
                  <button
                    type="button"
                    className="media-thumb"
                    aria-label={`看大图：要发给模型的图片 ${i + 1}`}
                    onClick={() => view(pics.findIndex((x) => x.i === i))}
                  >
                    <img className="photo-look-thumb" src={url} alt={`要发给模型的图片 ${i + 1}`} />
                  </button>
                )}
                <code>{path}</code>
              </div>
            );
          })}
        </div>
      )}
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
