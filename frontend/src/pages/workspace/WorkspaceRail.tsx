// 单集工作台的左栏：剧本大纲（原文一行摘要 + 段落列表）和本集角色（头像 / 名字 / 定妆状态）。可收起，记在本机浏览器里。
import { useState } from 'react';
import { useCharacters, useLocations } from '../../api/queries';
import type { EpisodeDetail } from '../../api/types';
import { castOf, hasPortrait } from '../stages';

const KEY = 'studio.workspaceRail';

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(KEY) === 'collapsed';
  } catch {
    return false;
  }
}

function saveCollapsed(collapsed: boolean) {
  try {
    if (collapsed) localStorage.setItem(KEY, 'collapsed');
    else localStorage.removeItem(KEY);
  } catch {
    // 存不了就只在这次打开里有效
  }
}

/** 原文第一行非空的文字 */
function firstLine(text: string): string {
  return text.split('\n').map((l) => l.trim()).find(Boolean) ?? '';
}

interface WorkspaceRailProps {
  slug: string;
  ep: EpisodeDetail;
  /** 当前打开的角色（?char=） */
  activeChar: number | null;
  onBlock: (blockId: number) => void;
  onChar: (charId: number) => void;
}

export function WorkspaceRail({ slug, ep, activeChar, onBlock, onChar }: WorkspaceRailProps) {
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const characters = useCharacters(slug);
  const locations = useLocations(slug);
  const toggle = () => {
    setCollapsed(!collapsed);
    saveCollapsed(!collapsed);
  };

  const toggleBtn = (
    <button
      type="button"
      className="ghost sm rail-toggle"
      aria-expanded={!collapsed}
      aria-label={collapsed ? '展开左栏' : '收起左栏'}
      title={collapsed ? '展开剧本大纲和角色' : '收起左栏，主区更宽'}
      onClick={toggle}
    >
      <span aria-hidden="true">{collapsed ? '»' : '«'}</span>
    </button>
  );
  if (collapsed) {
    return (
      <aside className="ws-rail is-collapsed" aria-label="剧本大纲与角色">
        {toggleBtn}
      </aside>
    );
  }

  const locName = new Map((locations.data ?? []).map((l) => [l.id, l.name]));
  const cast = castOf(ep.shots, characters.data ?? []);
  const summary = firstLine(ep.source_text);

  return (
    <aside className="ws-rail" aria-label="剧本大纲与角色">
      <div className="rail-head">
        <h3>剧本大纲</h3>
        {toggleBtn}
      </div>
      <p className="rail-source" data-testid="rail-source" title={summary || undefined}>
        {summary ? `${ep.source_text.length} 字 · ${summary}` : '还没有原文'}
      </p>
      {ep.scene_blocks.length === 0 ? (
        <p className="muted small">还没有段落。</p>
      ) : (
        <ol className="rail-list">
          {ep.scene_blocks.map((b) => (
            <li key={b.id}>
              <button type="button" className="rail-block" onClick={() => onBlock(b.id)} title={b.text}>
                <span className="rail-block-head">
                  段落 {b.order + 1} · {(b.location_id != null && locName.get(b.location_id)) || '未指定场景'}
                </span>
                <span className="rail-block-text">{b.text}</span>
              </button>
            </li>
          ))}
        </ol>
      )}

      <div className="rail-head">
        <h3>角色</h3>
      </div>
      {cast.length === 0 ? (
        <p className="muted small">{characters.data ? '还没有角色。' : '正在读取角色…'}</p>
      ) : (
        <ul className="rail-list">
          {cast.map((c) => {
            const ok = hasPortrait(c);
            return (
              <li key={c.id}>
                <button
                  type="button"
                  className={`rail-char${c.id === activeChar ? ' is-active' : ''}`}
                  aria-current={c.id === activeChar ? 'true' : undefined}
                  onClick={() => onChar(c.id)}
                >
                  {c.portrait_url ? (
                    <img className="rail-avatar" src={c.portrait_url} alt="" />
                  ) : (
                    <span className="rail-avatar" aria-hidden="true">
                      {Array.from(c.name)[0] ?? '?'}
                    </span>
                  )}
                  <span className="rail-char-name">{c.name}</span>
                  <span className={`badge ${ok ? 'ok' : 'stale'}`}>{ok ? '已定妆' : '缺定妆照'}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
