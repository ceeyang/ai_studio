// 任务详情的「调用明细」：每次 LLM 调用、出图 / 视频引擎提交、ffmpeg 命令一行摘要，点开看提示词、回复、参数。
import { Fragment, useState } from 'react';
import { errorText } from '../../api/client';
import { useJobTraces } from '../../api/queries';
import type { JobTrace, TraceConversation } from '../../api/types';
import { Lightbox, type LightboxItem } from '../../components/Lightbox';

type Data = Record<string, unknown>;

const STATUS_LABEL: Record<string, string> = {
  running: '进行中',
  ok: '成功',
  invalid: '输出不合格',
  error: '出错',
  canceled: '已取消',
  waiting_input: '等人工粘贴',
};
const STATUS_CLASS: Record<string, string> = {
  running: 'job-running',
  ok: 'job-done',
  invalid: 'job-waiting_input',
  error: 'job-failed',
  canceled: 'job-canceled',
  waiting_input: 'job-waiting_input',
};
const OUTPUT_KIND: Record<string, string> = { image: '出图', video: '视频', audio: '配音' };
const IMAGE_EXT = /\.(png|jpe?g|webp|gif)$/i;
const VIDEO_EXT = /\.(mp4|webm|mov)$/i;
const PUBLIC_DIRS = ['media', 'exports'];

const str = (v: unknown): string => (typeof v === 'string' ? v : v == null ? '' : String(v));
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** 相对数据目录的路径 → 能在页面上打开的地址（只有 <项目>/media、<项目>/exports 下的文件可以） */
export function mediaUrl(path: string): string | null {
  const parts = path.split('/');
  if (parts.length < 3 || !PUBLIC_DIRS.includes(parts[1]) || path.includes('..') || /^[a-z]:|^\//i.test(path)) return null;
  return `/media/${parts.map(encodeURIComponent).join('/')}`;
}

function secs(v: unknown): string {
  const n = num(v);
  return n === null ? '' : `${n.toFixed(1)}s`;
}

function joinParts(parts: (string | null | undefined | false)[]): string {
  return parts.filter((x): x is string => !!x).join(' · ');
}

/** 一行摘要：「LLM · storyboard · claude · 第 1 次 · 18.6s」「出图 · h3_image · 首帧 S03 · 9:16 · seed 12345」 */
export function traceSummary(t: JobTrace): string {
  const d = t.data;
  if (t.kind === 'llm') {
    const model = str(d.model);
    const provider = str(d.provider);
    return joinParts([
      'LLM',
      t.title,
      model && model !== provider ? `${provider}（${model}）` : provider,
      num(d.attempt) !== null && `第 ${num(d.attempt)} 次`,
      secs(d.seconds),
    ]);
  }
  if (t.kind === 'engine') {
    const p = (d.params ?? {}) as Data;
    const seed = num(p.seed);
    return joinParts([
      OUTPUT_KIND[str(d.output_kind)] ?? '引擎',
      str(d.engine),
      str(d.what),
      str(p.aspect),
      num(p.seconds) !== null && str(d.output_kind) === 'video' && `${num(p.seconds)}s`,
      seed !== null ? `seed ${seed}` : p.nondeterministic ? '无种子' : '',
      d.output_kind === 'audio' && str(p.voice),
      secs(d.seconds) && `耗时 ${secs(d.seconds)}`,
    ]);
  }
  if (t.kind === 'ffmpeg') return joinParts([t.title || 'ffmpeg', secs(d.seconds)]);
  return t.title;
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false); // 剪贴板不可用时手动全选复制
    }
  };
  return (
    <button type="button" className="ghost trace-copy" aria-label={`复制${label}`} onClick={() => void copy()}>
      {copied ? '已复制' : '复制'}
    </button>
  );
}

/** 长文本块：等宽、可滚动、带「复制」；落了盘的字段给「看全文」链接 */
function TextBlock({ label, text, file }: { label: string; text: string; file?: string | null }) {
  if (!text) return null;
  return (
    <div className="trace-block">
      <div className="trace-block-head">
        <h5>{label}</h5>
        {file && (
          <a href={file} target="_blank" rel="noreferrer">
            看全文
          </a>
        )}
        <CopyButton text={text} label={label} />
      </div>
      <pre className="trace-pre">{text}</pre>
    </div>
  );
}

function asText(v: unknown): string {
  if (v == null || v === '') return '';
  if (typeof v === 'string') return v;
  return JSON.stringify(v, null, 2);
}

function ParamTable({ rows }: { rows: [string, unknown][] }) {
  const shown = rows.filter(([, v]) => v !== undefined && v !== null && v !== '');
  if (shown.length === 0) return null;
  return (
    <dl className="debug-list trace-params">
      {shown.map(([k, v]) => (
        <Fragment key={k}>
          <dt>{k}</dt>
          <dd>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

interface Pic {
  path: string;
  label: string;
  note?: string;
}

/** 参考图 / 产物：能在页面上打开的显示缩略图（点开看大图），其他的只写路径 */
function Pictures({ title, pics }: { title: string; pics: Pic[] }) {
  const [open, setOpen] = useState<number | null>(null);
  if (pics.length === 0) return null;
  const viewable = pics
    .map((p) => ({ p, url: mediaUrl(p.path) }))
    .filter((x): x is { p: Pic; url: string } => !!x.url && IMAGE_EXT.test(x.p.path));
  const items: LightboxItem[] = viewable.map(({ p, url }) => ({ url, alt: p.label, caption: `${p.label} · ${p.path}` }));
  return (
    <div className="trace-block">
      <h5>{title}</h5>
      <ol className="trace-pics">
        {pics.map((p, i) => {
          const url = mediaUrl(p.path);
          const vi = viewable.findIndex((x) => x.p === p);
          return (
            <li key={`${p.path}:${i}`} className="trace-pic">
              {url && IMAGE_EXT.test(p.path) ? (
                <button type="button" className="ghost trace-thumb" aria-label={`看大图：${p.label}`} onClick={() => setOpen(vi)}>
                  <img src={url} alt={p.label} loading="lazy" />
                </button>
              ) : url && VIDEO_EXT.test(p.path) ? (
                <a className="trace-thumb trace-thumb-video" href={url} target="_blank" rel="noreferrer">
                  视频
                </a>
              ) : (
                <span className="trace-thumb trace-thumb-none">无预览</span>
              )}
              <span className="trace-pic-text">
                <b>{p.label}</b>
                {p.note && <span className="muted"> · {p.note}</span>}
                <code>{p.path}</code>
              </span>
            </li>
          );
        })}
      </ol>
      {open !== null && items.length > 0 && (
        <Lightbox title={title} items={items} index={open} onIndex={setOpen} onClose={() => setOpen(null)} />
      )}
    </div>
  );
}

function fileUrl(jobId: number, d: Data, key: string): string | null {
  const files = (d._files ?? {}) as Record<string, { file?: string }>;
  const f = files[key]?.file;
  return f ? `/api/jobs/${jobId}/trace-files/${encodeURIComponent(f)}` : null;
}

function LlmDetail({ jobId, d }: { jobId: number; d: Data }) {
  const followups = list(d.followups) as { role?: string; content?: string }[];
  return (
    <>
      <ParamTable
        rows={[
          ['provider', d.provider],
          ['模型', d.model],
          ['步骤', d.step],
          ['key', d.key !== d.step ? d.key : undefined],
          ['第几次', d.attempt != null ? `${str(d.attempt)} / ${str(d.max_attempts)}` : undefined],
          ['输出格式', d.schema],
          ['耗时', secs(d.seconds)],
          ['token 用量', d.usage],
        ]}
      />
      {d.note != null && <p className="muted">{str(d.note)}</p>}
      <TextBlock label="System 提示词" text={str(d.system)} file={fileUrl(jobId, d, 'system')} />
      <TextBlock label="User 提示词" text={str(d.user)} file={fileUrl(jobId, d, 'user')} />
      {followups.length > 0 && (
        <TextBlock
          label="重试时追加的对话"
          text={followups.map((m) => `【${m.role === 'assistant' ? '上一次的回复' : '修正要求'}】\n${str(m.content)}`).join('\n\n')}
        />
      )}
      <Pictures title="带的图片" pics={list(d.images).map((p) => ({ path: str(p), label: str(p).split('/').pop() ?? '' }))} />
      <TextBlock label="原始回复" text={str(d.reply)} file={fileUrl(jobId, d, 'reply')} />
      {d.error != null && <TextBlock label={d.status === 'invalid' ? '校验错误' : '错误'} text={str(d.error)} />}
      <TextBlock label="解析结果" text={asText(d.parsed)} file={fileUrl(jobId, d, 'parsed')} />
    </>
  );
}

interface RefItem {
  path?: string;
  label?: string;
  role?: string;
  sent?: boolean;
}

function EngineDetail({ jobId, d }: { jobId: number; d: Data }) {
  const p = (d.params ?? {}) as Data;
  const refs = list(d.refs) as RefItem[];
  const outs = [...list(d.candidate_files).map(str), ...list(d.outputs).map(str)];
  const ids = list(d.candidate_ids).map(str);
  const neg = str(d.negative);
  return (
    <>
      <ParamTable
        rows={[
          ['引擎', d.engine],
          ['执行', d.runner],
          ['模型族', d.family],
          ...Object.entries(p).map(([k, v]) => [k, v] as [string, unknown]),
          ['耗时', secs(d.seconds)],
          ['候选', ids.length ? ids.map((x) => `#${x}`).join('、') : undefined],
        ]}
      />
      <TextBlock label="最终提示词（发给引擎的版本）" text={str(d.prompt)} file={fileUrl(jobId, d, 'prompt')} />
      {d.source_prompt != null && <TextBlock label="拼接前的提示词" text={str(d.source_prompt)} />}
      {neg && (
        <TextBlock label={d.negative_sent ? '负面词' : '负面词（这个引擎不吃负面词，没有发送）'} text={neg} />
      )}
      <Pictures
        title={`参考图（按发送顺序，${refs.length} 张）`}
        pics={refs.map((r, i) => ({
          path: str(r.path),
          label: `${i + 1}. ${r.role ? `${r.role} · ` : ''}${str(r.label)}`,
          note: r.sent === false ? '超出上限 / 引擎不吃参考图，没有发送' : undefined,
        }))}
      />
      <Pictures title="产物" pics={outs.map((x) => ({ path: x, label: x.split('/').pop() ?? x }))} />
      {d.error != null && <TextBlock label="错误" text={str(d.error)} />}
      {d.workflow != null && (
        <details className="trace-workflow">
          <summary>{d.runner === 'comfyui' ? 'workflow JSON' : '请求 JSON'}</summary>
          {d.workflow_file != null && <p className="muted">已存到 <code>{str(d.workflow_file)}</code></p>}
          <TextBlock label={d.runner === 'comfyui' ? 'workflow JSON' : '请求 JSON'} text={asText(d.workflow)} file={fileUrl(jobId, d, 'workflow')} />
        </details>
      )}
    </>
  );
}

function shellQuote(a: string): string {
  return /^[\w@%+=:,./\\-]+$/.test(a) ? a : `"${a.replace(/"/g, '\\"')}"`;
}

function FfmpegDetail({ jobId, d }: { jobId: number; d: Data }) {
  return (
    <>
      <ParamTable
        rows={[
          ['输出', d.output],
          ['工作目录', d.cwd],
          ['退出码', d.returncode],
          ['耗时', secs(d.seconds)],
        ]}
      />
      <TextBlock label="filter_complex" text={str(d.filter_complex)} />
      <TextBlock label="-vf" text={str(d.vf)} />
      <TextBlock label="-af" text={str(d.af)} />
      <TextBlock label="命令行" text={list(d.argv).map((a) => shellQuote(str(a))).join(' ')} file={fileUrl(jobId, d, 'argv')} />
      {d.error != null && <TextBlock label="错误" text={str(d.error)} />}
    </>
  );
}

/** 网页引擎 / 网页文本模型这次用的会话：「续接会话 <链接>」「新开会话 <链接>（原因）」 */
export function ConversationLine({ conv }: { conv: unknown }) {
  if (!conv || typeof conv !== 'object') return null;
  const c = conv as Partial<TraceConversation>;
  if (c.mode !== 'continue' && c.mode !== 'new') return null;
  const text = c.mode === 'continue' ? '续接会话' : '新开会话';
  const url = str(c.url);
  const reason = str(c.reason);
  return (
    <p className="trace-conv small" data-testid="trace-conversation">
      {text}
      {url && (
        <>
          {' '}
          <a href={url} target="_blank" rel="noreferrer">
            {url}
          </a>
        </>
      )}
      {reason && <span className="muted">（{reason}）</span>}
    </p>
  );
}

function TraceRow({ jobId, t }: { jobId: number; t: JobTrace }) {
  const d = t.data;
  const status = str(d.status);
  return (
    <li className={`trace-row trace-${t.kind}`}>
      <details>
        <summary>
          <span className="trace-seq">#{t.seq}</span>
          <span className="trace-summary">{traceSummary(t)}</span>
          {status && status !== 'ok' && (
            <span className={`pill ${STATUS_CLASS[status] ?? ''}`}>{STATUS_LABEL[status] ?? status}</span>
          )}
        </summary>
        <div className="trace-body">
          <ConversationLine conv={d.conversation} />
          {t.kind === 'llm' && <LlmDetail jobId={jobId} d={d} />}
          {t.kind === 'engine' && <EngineDetail jobId={jobId} d={d} />}
          {t.kind === 'ffmpeg' && <FfmpegDetail jobId={jobId} d={d} />}
          {t.kind !== 'llm' && t.kind !== 'engine' && t.kind !== 'ffmpeg' && (
            <TextBlock label="说明" text={str(d.text) || asText(d)} />
          )}
        </div>
      </details>
    </li>
  );
}

export function JobTraces({ jobId, active }: { jobId: number; active: boolean }) {
  const q = useJobTraces(jobId);
  let body;
  if (q.isLoading) body = <p className="muted">加载中…</p>;
  else if (q.error) body = <p className="muted">调用明细加载失败：{errorText(q.error)}</p>;
  else {
    const traces = q.data?.traces ?? [];
    if (traces.length === 0) {
      body = (
        <p className="muted" data-testid="trace-empty">
          {active ? '还没有调用记录：任务开始调用模型、引擎后会出现在这里。' : '这个任务是旧版本跑的，没有调用明细。'}
        </p>
      );
    } else {
      body = (
        <ol className="trace-list" aria-label="调用明细">
          {traces.map((t) => (
            <TraceRow key={t.seq} jobId={jobId} t={t} />
          ))}
        </ol>
      );
    }
  }
  return (
    <section className="job-traces">
      <h4>调用明细</h4>
      {body}
    </section>
  );
}
