// 角色抽屉（?char=<id> 打开）：页签「造型」（定妆照条、抽卡、上传、AI 推荐造型）、「三视图」、「资料」。
// 页签用 hidden 切换、都保持挂载，切页签不丢正在编辑的内容；关抽屉时资料或全身描述有没保存的修改先问一句。
import { useEffect, useRef, useState } from 'react';
import type { Character } from '../../api/types';
import { useConfirm } from '../../components/ConfirmDialog';
import { useUnsavedGuard } from '../../components/EditableFields';
import { CharacterLooks } from '../script/CharacterLooks';
import { CharacterProfile } from '../script/CharacterProfile';
import { TurnaroundPanel } from '../script/TurnaroundPanel';

type Tab = 'looks' | 'turnaround' | 'profile';

const TABS: { key: Tab; label: string }[] = [
  { key: 'looks', label: '造型' },
  { key: 'turnaround', label: '三视图' },
  { key: 'profile', label: '资料' },
];

export interface CharacterDrawerProps {
  c: Character;
  slug: string;
  episodeId: number;
  onClose(): void;
}

export function CharacterDrawer({ c, slug, episodeId, onClose }: CharacterDrawerProps) {
  const confirm = useConfirm();
  const [tab, setTab] = useState<Tab>('looks');
  const [profileDirty, setProfileDirty] = useState(false);
  const [lookDirty, setLookDirty] = useState(false);
  // 换到别的页面由全站的离开拦截问（三视图的全身描述自己登记了）；关抽屉另有确认
  useUnsavedGuard(profileDirty);
  const box = useRef<HTMLElement>(null);
  const latest = useRef({ dirty: false, onClose });
  latest.current = { dirty: profileDirty || lookDirty, onClose };

  /** 想关抽屉：有没保存的修改先问一句。 */
  const requestClose = useRef(async () => {
    if (latest.current.dirty) {
      const ok = await confirm({
        title: '放弃没保存的修改？',
        body: '这个角色有改过但没保存的内容，关掉抽屉就没了。',
        confirmText: '放弃修改',
        cancelText: '继续编辑',
        danger: true,
      });
      if (!ok) return;
    }
    latest.current.onClose();
  }).current;

  // 打开时把焦点放进抽屉，关掉后还给原来的元素；Esc 关闭（上面叠着确认框或大图预览时归它们管）
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    box.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || document.querySelector('.modal-scrim')) return;
      e.preventDefault();
      void requestClose();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      prev?.focus?.();
    };
  }, [requestClose]);

  const tabId = (t: Tab) => `char-${c.id}-tab-${t}`;
  const panelId = (t: Tab) => `char-${c.id}-panel-${t}`;

  return (
    <>
      <div className="drawer-scrim" data-testid="drawer-scrim" aria-hidden="true" onMouseDown={() => void requestClose()} />
      <aside ref={box} className="drawer char-drawer" role="dialog" aria-modal="true" aria-label={`角色 ${c.name}`} tabIndex={-1}>
        <header className="drawer-head">
          <h3>{c.name}</h3>
          <button type="button" className="ghost" onClick={() => void requestClose()}>
            关闭
          </button>
        </header>
        {c.description && <p className="char-desc">{c.description}</p>}
        <div className="tabs drawer-tabs" role="tablist" aria-label={`${c.name} 的设定`}>
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              id={tabId(t.key)}
              aria-selected={tab === t.key}
              aria-controls={panelId(t.key)}
              className={tab === t.key ? 'is-active' : undefined}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div role="tabpanel" id={panelId('looks')} aria-labelledby={tabId('looks')} className="tab-body" hidden={tab !== 'looks'}>
          <CharacterLooks c={c} slug={slug} episodeId={episodeId} profileDirty={profileDirty} />
        </div>
        <div
          role="tabpanel"
          id={panelId('turnaround')}
          aria-labelledby={tabId('turnaround')}
          className="tab-body"
          hidden={tab !== 'turnaround'}
        >
          <TurnaroundPanel c={c} slug={slug} onDirtyChange={setLookDirty} />
        </div>
        <div role="tabpanel" id={panelId('profile')} aria-labelledby={tabId('profile')} className="tab-body" hidden={tab !== 'profile'}>
          <CharacterProfile c={c} slug={slug} onDirtyChange={setProfileDirty} />
        </div>
      </aside>
    </>
  );
}
