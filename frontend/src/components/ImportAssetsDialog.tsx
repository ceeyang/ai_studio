// 「从其他项目导入」（续季 / 跨项目复用资产）：选源项目 → 按角色 / 场景 / 道具勾选（带缩略图）→ 重名怎么办 →
// 导入，显示报告（导入了哪些、跳过了哪些和原因）。图片文件会拷进本项目，两边以后互不影响。
import { useEffect, useMemo, useState } from 'react';
import { errorText } from '../api/client';
import { useAssetSummary, useImportAssets, useProjects } from '../api/queries';
import type { AssetImportOut, AssetKindKey, AssetSummary, AssetSummaryItem } from '../api/types';
import { Dialog } from './Dialog';

const GROUPS: { kind: AssetKindKey; key: keyof Omit<AssetSummary, 'has_style'>; label: string }[] = [
  { kind: 'character', key: 'characters', label: '角色' },
  { kind: 'location', key: 'locations', label: '场景' },
  { kind: 'prop', key: 'props', label: '道具' },
];

const KIND_LABEL: Record<AssetKindKey, string> = { character: '角色', location: '场景', prop: '道具' };

type Picked = Record<AssetKindKey, Set<number>>;

const emptyPicked = (): Picked => ({ character: new Set(), location: new Set(), prop: new Set() });

/** 道具按英文说法判重名（不分大小写、合并空格），角色、场景按名字 */
function nameKey(kind: AssetKindKey, name: string): string {
  return kind === 'prop' ? name.split(/\s+/).filter(Boolean).join(' ').toLowerCase() : name.trim();
}

function Report({ report }: { report: AssetImportOut }) {
  const props = report.imported.filter((x) => x.kind === 'prop').length;
  return (
    <div className="import-report" data-testid="import-report" role="status">
      <p>
        导入了 <b>{report.imported.length}</b> 项（拷贝 {report.files_copied} 张图）
        {report.style_copied ? '，沿用了源项目的风格设定' : ''}。
      </p>
      {report.imported.length > 0 && (
        <ul className="import-report-list">
          {report.imported.map((x) => (
            <li key={`${x.kind}-${x.source_id}`}>
              {KIND_LABEL[x.kind]}「{x.name}」
              {x.renamed_from && <span className="muted">（重名，原名「{x.renamed_from}」）</span>}
              <span className="muted"> · {x.files} 张图</span>
            </li>
          ))}
        </ul>
      )}
      {report.skipped.length > 0 && (
        <>
          <p className="warn">跳过 {report.skipped.length} 项：</p>
          <ul className="import-report-list">
            {report.skipped.map((x) => (
              <li key={`${x.kind}-${x.source_id}`}>
                {KIND_LABEL[x.kind]}「{x.name}」：{x.reason}
              </li>
            ))}
          </ul>
        </>
      )}
      {report.warnings.map((w) => (
        <p key={w} className="warn small">
          {w}
        </p>
      ))}
      {props > 0 && <p className="muted small">道具要等分镜里写到同一个英文说法时才出现在道具列表里，到时直接用导入的道具图。</p>}
    </div>
  );
}

function AssetRow({
  item,
  checked,
  conflict,
  onToggle,
}: {
  item: AssetSummaryItem;
  checked: boolean;
  conflict: boolean;
  onToggle: () => void;
}) {
  return (
    <li>
      <label className="import-asset">
        <input type="checkbox" checked={checked} onChange={onToggle} />
        {item.thumb_url ? (
          <img src={item.thumb_url} alt="" loading="lazy" className="import-thumb" />
        ) : (
          <span className="import-thumb import-thumb-empty" aria-hidden="true">
            {Array.from(item.name)[0] ?? '?'}
          </span>
        )}
        <span className="import-asset-text">
          <b>{item.name}</b>
          {conflict && <span className="chip warn">本项目已有同名</span>}
          {item.detail && <span className="muted small">{item.detail}</span>}
        </span>
      </label>
    </li>
  );
}

export function ImportAssetsDialog({ slug, onClose }: { slug: string; onClose: () => void }) {
  // 往季可能已经归档，连归档的一起列
  const projects = useProjects(true);
  const others = (projects.data ?? []).filter((p) => p.slug !== slug);
  const [source, setSource] = useState('');
  const summary = useAssetSummary(source);
  const mine = useAssetSummary(slug);
  const [picked, setPicked] = useState<Picked>(emptyPicked);
  const [onConflict, setOnConflict] = useState<'skip' | 'rename'>('skip');
  const [suffix, setSuffix] = useState('（续）');
  const [allCandidates, setAllCandidates] = useState(false);
  const [withStyle, setWithStyle] = useState(false);
  const imp = useImportAssets(slug);
  const report = imp.data ?? null;

  // 换了源项目：默认全部勾上
  const data = summary.data;
  useEffect(() => {
    if (!data) {
      setPicked(emptyPicked());
      return;
    }
    setPicked({
      character: new Set(data.characters.map((x) => x.id)),
      location: new Set(data.locations.map((x) => x.id)),
      prop: new Set(data.props.map((x) => x.id)),
    });
  }, [data]);

  const taken = useMemo(() => {
    const m = mine.data;
    const set = (kind: AssetKindKey, rows: AssetSummaryItem[] | undefined) => new Set((rows ?? []).map((x) => nameKey(kind, x.name)));
    return { character: set('character', m?.characters), location: set('location', m?.locations), prop: set('prop', m?.props) };
  }, [mine.data]);

  const total = picked.character.size + picked.location.size + picked.prop.size;
  const toggle = (kind: AssetKindKey, id: number) =>
    setPicked((prev) => {
      const next = new Set(prev[kind]);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { ...prev, [kind]: next };
    });
  const setGroup = (kind: AssetKindKey, ids: number[]) => setPicked((prev) => ({ ...prev, [kind]: new Set(ids) }));

  const submit = () =>
    imp.mutate({
      source,
      character_ids: [...picked.character],
      location_ids: [...picked.location],
      prop_ids: [...picked.prop],
      on_conflict: onConflict,
      rename_suffix: suffix.trim() || '（续）',
      candidates: allCandidates ? 'all' : 'selected',
      with_style: withStyle,
    });

  return (
    <Dialog
      title="从其他项目导入"
      subtitle="续季、番外：把以前项目的角色（定妆照、三视图、声音）、场景图、道具图复制到这个项目。复制后两边互不影响。"
      onClose={onClose}
      busy={imp.isPending}
      wide
    >
      {report ? (
        <>
          <Report report={report} />
          <div className="modal-foot">
            <button type="button" className="ghost" onClick={() => imp.reset()}>
              继续导入
            </button>
            <button type="button" className="primary" onClick={onClose}>
              完成
            </button>
          </div>
        </>
      ) : (
        <div className="fields import-assets">
          <label className="field">
            <span>源项目</span>
            <select value={source} onChange={(e) => setSource(e.target.value)} data-autofocus>
              <option value="">选一个项目…</option>
              {others.map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.title}
                  {p.archived ? '（已归档）' : ''}
                </option>
              ))}
            </select>
          </label>
          {projects.data && others.length === 0 && <p className="muted">还没有别的项目可以导入。</p>}
          {summary.isLoading && <p className="loading">加载中…</p>}
          {summary.error && <p className="error">{errorText(summary.error)}</p>}
          {data && (
            <>
              {GROUPS.map(({ kind, key, label }) => {
                const rows = data[key];
                if (rows.length === 0) return null;
                const all = rows.every((x) => picked[kind].has(x.id));
                return (
                  <fieldset key={kind} className="import-group" aria-label={label}>
                    <legend>
                      {label}（已选 {picked[kind].size}/{rows.length}）
                      <button
                        type="button"
                        className="ghost sm"
                        onClick={() => setGroup(kind, all ? [] : rows.map((x) => x.id))}
                      >
                        {all ? '全不选' : '全选'}
                      </button>
                    </legend>
                    <ul className="import-list">
                      {rows.map((x) => (
                        <AssetRow
                          key={x.id}
                          item={x}
                          checked={picked[kind].has(x.id)}
                          conflict={taken[kind].has(nameKey(kind, x.name))}
                          onToggle={() => toggle(kind, x.id)}
                        />
                      ))}
                    </ul>
                  </fieldset>
                );
              })}
              {data.characters.length + data.locations.length + data.props.length === 0 && (
                <p className="muted">这个项目还没有角色、场景或道具。</p>
              )}
              <fieldset className="import-group">
                <legend>重名时</legend>
                <label className="check">
                  <input type="radio" name="on-conflict" checked={onConflict === 'skip'} onChange={() => setOnConflict('skip')} />
                  跳过（保留本项目原有的）
                </label>
                <label className="check">
                  <input type="radio" name="on-conflict" checked={onConflict === 'rename'} onChange={() => setOnConflict('rename')} />
                  改名导入，名字后面加
                  <input
                    className="import-suffix"
                    aria-label="改名后缀"
                    value={suffix}
                    maxLength={20}
                    disabled={onConflict !== 'rename'}
                    onChange={(e) => setSuffix(e.target.value)}
                  />
                </label>
              </fieldset>
              <label className="check">
                <input type="checkbox" checked={allCandidates} onChange={(e) => setAllCandidates(e.target.checked)} />
                复制全部候选图（默认每个位置只复制选定的那张）
              </label>
              {data.has_style && (
                <label className="check">
                  <input type="checkbox" checked={withStyle} onChange={(e) => setWithStyle(e.target.checked)} />
                  沿用源项目的风格设定（覆盖本项目的风格锚和负面提示词）
                </label>
              )}
            </>
          )}
          {imp.error && <p className="error">{errorText(imp.error)}</p>}
          <div className="modal-foot">
            <button type="button" className="ghost" disabled={imp.isPending} onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className="primary"
              disabled={!source || (total === 0 && !withStyle) || imp.isPending}
              onClick={submit}
            >
              {imp.isPending ? '导入中…' : total > 0 ? `导入 ${total} 项` : '导入'}
            </button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

/** 「从其他项目导入」按钮 + 对话框 */
export function ImportAssetsButton({ slug, className = 'sm' }: { slug: string; className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className={className}
        title="续季、番外：把以前项目的角色、场景、道具（连同定妆照、场景图、道具图）复制过来"
        onClick={() => setOpen(true)}
      >
        从其他项目导入
      </button>
      {open && <ImportAssetsDialog slug={slug} onClose={() => setOpen(false)} />}
    </>
  );
}
