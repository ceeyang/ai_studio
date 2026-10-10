// 首帧 / 视频阶段的镜头卡片网格：按剧本段落分组（组头「第 N 段 · 场景名 · M 镜」+「重写本段分镜…」），
// 上一版剧本留下的（分镜生成、段落已经没了）一组，没有段落的手工镜头放最后一组「手工添加」。上移 / 下移按全集顺序。
import { useEffect, useRef, useState } from 'react';
import type { Character, Location, SceneBlock, ShotDetail } from '../../api/types';
import { RegenerateDialog } from '../../components/RegenerateDialog';
import type { CardAction, GenStage } from '../director/shotStatus';
import { isOrphanShot } from '../stages';
import { ShotCard } from './ShotCard';

export interface ShotGridProps {
  episodeId: number;
  shots: ShotDetail[];
  blocks: SceneBlock[];
  locations: Location[];
  characters: Character[];
  stage: GenStage;
  selected: ReadonlySet<number>;
  generating?: boolean;
  busy?: boolean;
  onToggle(id: number): void;
  /** 组头勾选：整段选上 / 去掉 */
  onSelectMany(ids: number[], checked: boolean): void;
  onOpen(id: number): void;
  onAction(shot: ShotDetail, action: CardAction): void;
  onMove(id: number, delta: -1 | 1): void;
  onSplit(id: number): void;
  onInsertAfter(id: number): void;
  onDelete(id: number): void;
  /** 「重写本段分镜…」提交了：任务 id 和提示条里的说法 */
  onRegenerated(jobIds: number[], text: string): void;
}

const ORPHAN_SCOPE = '上一版剧本留下的镜头';

interface Group {
  key: string;
  title: string;
  block: SceneBlock | null;
  shots: ShotDetail[];
}

/**
 * 段落按顺序各一组（没镜头的段落也列出来，好按段重写）；段落对不上的镜头：分镜生成的（重新生成剧本后旧段落没了）
 * 单独一组「上一版剧本留下的镜头」，手工添加的一组「手工添加」。
 */
export function groupShots(shots: ShotDetail[], blocks: SceneBlock[], locations: Location[]): Group[] {
  const locName = new Map(locations.map((l) => [l.id, l.name]));
  const ordered = [...blocks].sort((a, b) => a.order - b.order);
  const known = new Set(ordered.map((b) => b.id));
  const groups: Group[] = ordered.map((b) => {
    const list = shots.filter((s) => s.scene_block_id === b.id);
    const place = (b.location_id != null && locName.get(b.location_id)) || '未指定场景';
    return { key: `b${b.id}`, title: `第 ${b.order + 1} 段 · ${place} · ${list.length} 镜`, block: b, shots: list };
  });
  const orphans = shots.filter((s) => isOrphanShot(s, known));
  if (orphans.length > 0) {
    groups.push({ key: 'orphan', title: `${ORPHAN_SCOPE} · ${orphans.length} 镜`, block: null, shots: orphans });
  }
  const manual = shots.filter((s) => !isOrphanShot(s, known) && (s.scene_block_id == null || !known.has(s.scene_block_id)));
  if (manual.length > 0) groups.push({ key: 'manual', title: `手工添加 · ${manual.length} 镜`, block: null, shots: manual });
  return groups;
}

/** 组头的「全选本段」：部分选中时是半选 */
function GroupCheck({ label, ids, selected, onChange }: { label: string; ids: number[]; selected: ReadonlySet<number>; onChange(checked: boolean): void }) {
  const ref = useRef<HTMLInputElement>(null);
  const n = ids.filter((id) => selected.has(id)).length;
  const all = ids.length > 0 && n === ids.length;
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = n > 0 && !all;
  }, [n, all]);
  return (
    <input ref={ref} type="checkbox" aria-label={label} checked={all} disabled={ids.length === 0} onChange={() => onChange(!all)} />
  );
}

export function ShotGrid(props: ShotGridProps) {
  const { shots, stage, selected } = props;
  const [regen, setRegen] = useState<SceneBlock | null>(null);
  const charOf = new Map(props.characters.map((c) => [c.id, c]));
  const groups = groupShots(shots, props.blocks, props.locations);
  const lastId = shots[shots.length - 1]?.id;

  return (
    <div className="shot-grid-wrap">
      {groups.map((g) => {
        const ids = g.shots.map((s) => s.id);
        const scope = g.block ? `第 ${g.block.order + 1} 段` : g.key === 'orphan' ? ORPHAN_SCOPE : '手工添加';
        return (
          <section key={g.key} className="shot-group" aria-label={g.title}>
            <header className="shot-group-head">
              <GroupCheck
                label={g.block ? `选择${scope}的全部镜头` : g.key === 'orphan' ? '选择上一版剧本留下的全部镜头' : '选择手工添加的全部镜头'}
                ids={ids}
                selected={selected}
                onChange={(checked) => props.onSelectMany(ids, checked)}
              />
              <h3>{g.title}</h3>
              <span className="grow" aria-hidden="true" />
              {g.block && (
                <button type="button" className="sm ghost" onClick={() => setRegen(g.block)}>
                  重写本段分镜…
                </button>
              )}
            </header>
            {g.shots.length === 0 ? (
              <p className="muted small shot-group-empty">这一段还没有镜头</p>
            ) : (
              <div className="shot-grid">
                {g.shots.map((s) => (
                  <ShotCard
                    key={s.id}
                    shot={s}
                    stage={stage}
                    selected={selected.has(s.id)}
                    first={s.id === shots[0]?.id}
                    last={s.id === lastId}
                    cast={s.cast.map((c) => charOf.get(c.character_id)).filter((c): c is Character => !!c)}
                    generating={props.generating}
                    busy={props.busy}
                    onToggle={() => props.onToggle(s.id)}
                    onOpen={() => props.onOpen(s.id)}
                    onAction={(a) => props.onAction(s, a)}
                    onMove={(d) => props.onMove(s.id, d)}
                    onSplit={() => props.onSplit(s.id)}
                    onInsertAfter={() => props.onInsertAfter(s.id)}
                    onDelete={() => props.onDelete(s.id)}
                  />
                ))}
              </div>
            )}
          </section>
        );
      })}
      <RegenerateDialog
        open={!!regen}
        onClose={() => setRegen(null)}
        stage="storyboard"
        episodeId={props.episodeId}
        blockIds={regen ? [regen.id] : undefined}
        scopeLabel={regen ? `第 ${regen.order + 1} 段` : ''}
        onSubmitted={(ids) => {
          if (regen) props.onRegenerated(ids, `重写第 ${regen.order + 1} 段分镜`);
        }}
      />
    </div>
  );
}
