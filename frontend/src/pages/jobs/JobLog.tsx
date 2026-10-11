// 任务详情的「日志」页签：等宽、行号、运行中自动滚到底（往上翻就暂停跟随）、搜索、复制。
import { Fragment, type ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { CopyButton } from './CopyButton';
import { splitTraceback } from './log';

function highlight(line: string, q: string) {
  if (!q) return line;
  const lower = line.toLowerCase();
  const parts: ReactNode[] = [];
  let i = 0;
  let k = 0;
  for (;;) {
    const at = lower.indexOf(q, i);
    if (at < 0) break;
    parts.push(<Fragment key={k++}>{line.slice(i, at)}</Fragment>);
    parts.push(<mark key={k++}>{line.slice(at, at + q.length)}</mark>);
    i = at + q.length;
  }
  parts.push(<Fragment key={k++}>{line.slice(i)}</Fragment>);
  return parts;
}

export function JobLog({ log, active }: { log: string; active: boolean }) {
  const { brief, hasTrace } = splitTraceback(log);
  const [showTrace, setShowTrace] = useState(false);
  const [follow, setFollow] = useState(true);
  const [query, setQuery] = useState('');
  const ref = useRef<HTMLPreElement>(null);
  const text = showTrace ? log : brief;
  const q = query.trim().toLowerCase();

  const lines = useMemo(() => {
    const all = text.replace(/\n$/, '').split('\n').map((t, i) => ({ n: i + 1, t }));
    return q ? all.filter((l) => l.t.toLowerCase().includes(q)) : all;
  }, [text, q]);

  // 跟随：日志变长时滚到底。用户自己往上翻就暂停，翻回底部再恢复。
  useEffect(() => {
    const el = ref.current;
    if (el && follow && !q) el.scrollTop = el.scrollHeight;
  }, [text, follow, q]);

  const onScroll = () => {
    const el = ref.current;
    if (!el || !active) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    if (atBottom !== follow) setFollow(atBottom);
  };

  const empty = !log.trim();
  return (
    <div className="job-log">
      <div className="job-log-bar">
        <input
          type="search"
          className="job-log-search"
          aria-label="搜索日志"
          placeholder="搜索日志"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={empty}
        />
        {q && (
          <span className="muted small" role="status">
            {lines.length} 行匹配
          </span>
        )}
        <span className="grow" />
        {hasTrace && (
          <label className="check">
            <input type="checkbox" checked={showTrace} onChange={(e) => setShowTrace(e.target.checked)} />
            显示调用栈
          </label>
        )}
        {active && (
          <button
            type="button"
            className={follow ? 'ghost sm is-on' : 'ghost sm'}
            aria-pressed={follow}
            onClick={() => setFollow((f) => !f)}
          >
            {follow ? '跟随最新' : '已暂停跟随'}
          </button>
        )}
        <CopyButton text={log} what="日志" />
      </div>
      <pre className="log" ref={ref} onScroll={onScroll} data-testid="job-log" tabIndex={0} aria-label="任务日志">
        {empty ? (
          <span className="log-empty">{active ? '等待日志输出…' : '（没有日志）'}</span>
        ) : lines.length === 0 ? (
          <span className="log-empty">没有匹配「{query.trim()}」的行</span>
        ) : (
          lines.map((l) => (
            <span key={l.n} className="log-line" data-n={l.n}>
              {highlight(l.t, q)}
              {'\n'}
            </span>
          ))
        )}
      </pre>
    </div>
  );
}
