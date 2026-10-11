// 网页出图（ChatGPT / Gemini 网页）的会话续接：同一个角色 / 镜头 / 场景 / 道具在同一个网站上重新出图时，
// 后端接着上次的网页对话发。这里放「新开对话」开关（勾上 = 这次不续接）和「在网页里打开这条会话」链接。
// 只有这次要用的图像引擎是网页引擎时才显示。
import { useBrowserConversations, useEngines } from '../api/queries';
import type { ConversationScopeKind, EngineRow } from '../api/types';

/** 走浏览器扩展的网页出图引擎（引擎列表里拿不到 runtime 时按名字认） */
const WEB_IMAGE_ENGINES = ['gpt_image', 'gemini_image'];

/** 这个图像引擎是不是网页引擎（ChatGPT / Gemini 网页出图）：引擎列表里有 runtime 就按它，没有就按名字 */
export function isWebImageEngine(name: string | null | undefined, rows?: EngineRow[]): boolean {
  if (!name) return false;
  const row = rows?.find((r) => r.name === name);
  if (row?.runtime) return row.runtime === 'browser';
  return WEB_IMAGE_ENGINES.includes(name);
}

export interface WebConversationProps {
  scopeKind: ConversationScopeKind;
  scopeId: number | string;
  /** 这次实际要用的图像引擎（下拉框留空时传默认引擎）；不是网页引擎时整块不显示 */
  engine: string | null | undefined;
  /** 「新开对话」勾没勾（由调用方保管，提交时带进请求体，提交后复位） */
  newConversation: boolean;
  onNewConversationChange(v: boolean): void;
  /** 勾选框的无障碍名字前缀，同一页上有好几处时区分（如「修车行 场景图」） */
  label?: string;
}

export function WebConversation({ scopeKind, scopeId, engine, newConversation, onNewConversationChange, label }: WebConversationProps) {
  const engines = useEngines();
  const web = isWebImageEngine(engine, engines.data?.image);
  const q = useBrowserConversations(scopeKind, scopeId, web);
  if (!web) return null;
  const data = q.data;
  const reuseOff = data?.reuse_enabled === false;
  const items = data?.items ?? [];
  const many = items.length > 1;
  return (
    <div className="web-conv" data-testid="web-conversation">
      {reuseOff ? (
        <p className="muted small" data-testid="web-conv-reuse-off">
          设置里已关闭续接，每次新开对话。
        </p>
      ) : (
        <label className="check small" title="勾上：这次不接着上次的网页对话，在网站上新开一个对话出图">
          <input
            type="checkbox"
            aria-label={label ? `${label} 新开对话` : '新开对话'}
            checked={newConversation}
            onChange={(e) => onNewConversationChange(e.target.checked)}
          />{' '}
          新开对话
        </label>
      )}
      {data &&
        (items.length === 0 ? (
          !reuseOff && <span className="muted small">还没有网页会话，第一次出图会新开。</span>
        ) : (
          items.map((it) => (
            <span key={it.site} className="small web-conv-item">
              {many && <b>{it.label || it.site}：</b>}
              <a href={it.url} target="_blank" rel="noreferrer">
                在网页里打开这条会话
              </a>
              {it.turns > 0 && (
                <span className="muted">
                  {' '}
                  · 已 {it.turns} 轮{!reuseOff && data.max_turns > 0 ? `（超过 ${data.max_turns} 轮自动新开）` : ''}
                </span>
              )}
            </span>
          ))
        ))}
      {data && !data.supported && data.hint && <span className="muted small">{data.hint}</span>}
    </div>
  );
}
