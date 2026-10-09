import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { enc, errorText } from '../api/client';
import {
  useCharacters,
  useEpisode,
  useLocations,
  usePatchEpisode,
  usePatchLocation,
  usePatchSceneBlock,
  usePutStyle,
  useRunStage,
  useStyle,
} from '../api/queries';
import type { EpisodePatch, LocationPatch, SceneBlock, Style } from '../api/types';
import { EditableFields, type FieldSpec, type FieldValues } from '../components/EditableFields';
import { EPISODE_STATUS_LABEL } from '../format';
import { CharacterCard } from './script/CharacterCard';
import { RecommendPanel } from './script/RecommendPanel';

export { toCharacterPatch } from './script/CharacterCard';

const EPISODE_FIELDS: FieldSpec[] = [
  { name: 'title', label: '本集标题' },
  { name: 'target_seconds', label: '目标时长（秒）', kind: 'number', step: '1' },
  { name: 'source_text', label: '原文 / 梗概', kind: 'textarea', rows: 14 },
];

const LOCATION_FIELDS: FieldSpec[] = [
  { name: 'name', label: '名字' },
  { name: 'time_of_day', label: '时间' },
  { name: 'atmosphere', label: '氛围', kind: 'textarea', rows: 2 },
  { name: 'visual_prompt', label: '画面提示词（英文，画面中不出人）', kind: 'textarea', rows: 4 },
];

const STYLE_FIELDS: FieldSpec[] = [
  { name: 'anchor_text', label: '风格锚（80–120 词英文，注入所有图像/视频提示词）', kind: 'textarea', rows: 5 },
  { name: 'palette', label: '色板' },
  { name: 'lighting', label: '光线' },
  { name: 'texture', label: '质感' },
  { name: 'character_rules', label: '角色规则', kind: 'textarea', rows: 2 },
  { name: 'mood_keywords', label: '情绪关键词（逗号分隔）' },
  { name: 'negative', label: '负面提示词', kind: 'textarea', rows: 2 },
];

const EMPTY_STYLE: Style = {
  anchor_text: '', palette: '', lighting: '', texture: '', character_rules: '', mood_keywords: [], negative: '',
};

function toEpisodePatch(c: FieldValues): EpisodePatch {
  const out: EpisodePatch = {};
  if (c.title !== undefined) out.title = c.title;
  if (c.source_text !== undefined) out.source_text = c.source_text;
  if (c.target_seconds !== undefined) out.target_seconds = Number(c.target_seconds);
  return out;
}

function toStyle(v: FieldValues): Style {
  return {
    anchor_text: v.anchor_text,
    palette: v.palette,
    lighting: v.lighting,
    texture: v.texture,
    character_rules: v.character_rules,
    negative: v.negative,
    mood_keywords: v.mood_keywords
      .split(/[,，]/)
      .map((x) => x.trim())
      .filter(Boolean),
  };
}

type Tab = 'chars' | 'locs' | 'style';

/** 一个段落：收起时一行摘要（编号 · 场景 · 正文开头），点开编辑。 */
function BlockRow({
  b,
  locationName,
  locationOptions,
  open,
  onToggle,
  onSave,
}: {
  b: SceneBlock;
  locationName: string;
  locationOptions: { value: string; label: string }[];
  open: boolean;
  onToggle: () => void;
  onSave: (v: FieldValues) => void;
}) {
  return (
    <div className={`block${open ? ' is-open' : ''}`} data-testid={`block-${b.id}`}>
      <button type="button" className="block-row" aria-expanded={open} onClick={onToggle}>
        <span className="block-no">段落 {b.order + 1}</span>
        <span className="block-loc">{locationName}</span>
        <span className="block-sum">{b.text}</span>
        <span className="chev" aria-hidden="true" />
      </button>
      {open && (
        <div className="block-edit">
          <EditableFields
            fields={[
              { name: 'location_id', label: '场景', kind: 'select', options: locationOptions },
              { name: 'text', label: '段落内容', kind: 'textarea', rows: 8 },
            ]}
            initial={{ location_id: b.location_id == null ? '' : String(b.location_id), text: b.text }}
            onSave={onSave}
          />
        </div>
      )}
    </div>
  );
}

export function ScriptPage() {
  const { slug = '', episodeId = '' } = useParams();
  const id = Number(episodeId);
  const ep = useEpisode(id);
  const locations = useLocations(slug);
  const characters = useCharacters(slug);
  const style = useStyle(slug);
  const patchEp = usePatchEpisode(id);
  const patchBlock = usePatchSceneBlock(id);
  const patchLoc = usePatchLocation(slug);
  const putStyle = usePutStyle(slug);
  const run = useRunStage(id);
  const [force, setForce] = useState(false);
  const [notice, setNotice] = useState<{ text: string; jobId?: number } | null>(null);
  const [tab, setTab] = useState<Tab>('chars');
  const [openBlocks, setOpenBlocks] = useState<Set<number>>(new Set());
  const [srcOpen, setSrcOpen] = useState<boolean | null>(null);

  if (ep.isLoading) return <p className="loading">加载中…</p>;
  if (ep.error || !ep.data) return <p className="error">{ep.error ? errorText(ep.error) : '找不到这一集'}</p>;
  const e = ep.data;
  const locationOptions = [
    { value: '', label: '（未指定）' },
    ...(locations.data ?? []).map((l) => ({ value: String(l.id), label: l.name })),
  ];
  const chars = characters.data ?? [];
  const locs = locations.data ?? [];
  const locName = new Map(locs.map((l) => [l.id, l.name]));
  const st = style.data ?? EMPTY_STYLE;
  // 还没有段落或原文时默认展开原文：要先写原文才能运行剧本
  const sourceOpen = srcOpen ?? (e.scene_blocks.length === 0 || !e.source_text.trim());
  const allOpen = e.scene_blocks.length > 0 && e.scene_blocks.every((b) => openBlocks.has(b.id));
  const toggleBlock = (bid: number) =>
    setOpenBlocks((prev) => {
      const next = new Set(prev);
      if (next.has(bid)) next.delete(bid);
      else next.add(bid);
      return next;
    });

  const runStage = (stage: 'script' | 'storyboard') =>
    run.mutate(
      { stage, force },
      {
        onSuccess: (r) => setNotice({ text: `已提交任务 #${r.job_ids.join(', #')}`, jobId: r.job_ids[0] }),
        onError: (err) => setNotice({ text: errorText(err) }),
      },
    );

  const tabs: { key: Tab; label: string }[] = [
    { key: 'chars', label: `角色（${chars.length}）` },
    { key: 'locs', label: `场景（${locs.length}）` },
    { key: 'style', label: '风格' },
  ];

  return (
    <div className="page script-page">
      <header className="page-head">
        <span className="eyebrow">02 · 剧本</span>
        <h2>
          剧本 · 第 {e.number} 集 {e.title}
        </h2>
        <span className="chip">
          {EPISODE_STATUS_LABEL[e.status] ?? e.status} · 已有 {e.shots.length} 个镜头
        </span>
        <Link className="btn" to={`/projects/${enc(slug)}/episodes/${id}/director`}>
          去导演台
        </Link>
      </header>

      <div className="toolbar">
        <button type="button" className="primary" disabled={run.isPending} onClick={() => runStage('script')}>
          运行剧本
        </button>
        <button
          type="button"
          disabled={run.isPending || e.scene_blocks.length === 0}
          title={e.scene_blocks.length === 0 ? '先运行剧本生成段落' : undefined}
          onClick={() => runStage('storyboard')}
        >
          运行分镜
        </button>
        <span className="sep" aria-hidden="true" />
        <label>
          <input type="checkbox" checked={force} onChange={(ev) => setForce(ev.target.checked)} /> 强制重跑（输入没变也重新生成）
        </label>
      </div>
      {notice && (
        <p className="notice" role="status">
          {notice.text}
          {notice.jobId !== undefined && (
            <>
              {' · '}
              <Link to={`/jobs?job=${notice.jobId}`}>查看任务</Link>
            </>
          )}
        </p>
      )}

      <div className="script-workspace">
        <section className="panel script-pane" aria-label="剧本">
          <div className={`block src-box${sourceOpen ? ' is-open' : ''}`}>
            <button type="button" className="block-row" aria-expanded={sourceOpen} onClick={() => setSrcOpen(!sourceOpen)}>
              <span className="block-no">原文 / 梗概</span>
              <span className="block-loc">目标 {e.target_seconds} 秒</span>
              <span className="block-sum">{e.source_text.trim() ? `${e.source_text.length} 字 · ${e.source_text}` : '还没有原文'}</span>
              <span className="chev" aria-hidden="true" />
            </button>
            {sourceOpen && (
              <div className="block-edit">
                <EditableFields
                  fields={EPISODE_FIELDS}
                  initial={{ title: e.title, target_seconds: String(e.target_seconds), source_text: e.source_text }}
                  saving={patchEp.isPending}
                  error={patchEp.error ? errorText(patchEp.error) : null}
                  onSave={(_v, c) => patchEp.mutate(toEpisodePatch(c))}
                />
              </div>
            )}
          </div>

          <div className="pane-head">
            <h3>剧本段落（{e.scene_blocks.length}）</h3>
            {e.scene_blocks.length > 0 && (
              <button
                type="button"
                className="ghost"
                onClick={() => setOpenBlocks(allOpen ? new Set() : new Set(e.scene_blocks.map((b) => b.id)))}
              >
                {allOpen ? '全部收起' : '全部展开'}
              </button>
            )}
          </div>
          {e.scene_blocks.length === 0 && <p className="muted">还没有段落。点"运行剧本"由模型拆分。</p>}
          <div className="blocks">
            {e.scene_blocks.map((b) => (
              <BlockRow
                key={b.id}
                b={b}
                locationName={(b.location_id != null && locName.get(b.location_id)) || '未指定场景'}
                locationOptions={locationOptions}
                open={openBlocks.has(b.id)}
                onToggle={() => toggleBlock(b.id)}
                onSave={(v) =>
                  patchBlock.mutate({
                    id: b.id,
                    text: v.text,
                    location_id: v.location_id === '' ? null : Number(v.location_id),
                  })
                }
              />
            ))}
          </div>
          {patchBlock.error && <p className="error">{errorText(patchBlock.error)}</p>}
        </section>

        <section className="panel assets-pane" aria-label="角色、场景与风格">
          <div className="tabs" role="tablist" aria-label="设定">
            {tabs.map((t) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                id={`tab-${t.key}`}
                aria-selected={tab === t.key}
                aria-controls={`tabpanel-${t.key}`}
                className={tab === t.key ? 'is-active' : undefined}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>

          {tab === 'chars' && (
            <div role="tabpanel" id="tabpanel-chars" aria-labelledby="tab-chars" className="tab-body">
              <RecommendPanel slug={slug} episodeId={id} existing={chars} />
              {chars.length === 0 && <p className="muted">运行剧本后自动抽取，也可以按剧本推荐角色。</p>}
              <div className="char-list">
                {chars.map((c) => (
                  <CharacterCard key={c.id} c={c} slug={slug} episodeId={id} />
                ))}
              </div>
            </div>
          )}

          {tab === 'locs' && (
            <div role="tabpanel" id="tabpanel-locs" aria-labelledby="tab-locs" className="tab-body">
              {locs.length === 0 && <p className="muted">运行剧本后自动抽取。</p>}
              <div className="cards">
                {locs.map((l) => (
                  <div key={l.id} className="card" data-testid={`location-${l.id}`}>
                    <EditableFields
                      fields={LOCATION_FIELDS}
                      initial={{ name: l.name, time_of_day: l.time_of_day, atmosphere: l.atmosphere, visual_prompt: l.visual_prompt }}
                      onSave={(_v, ch) => patchLoc.mutate({ id: l.id, ...(ch as LocationPatch) })}
                    />
                  </div>
                ))}
              </div>
              {patchLoc.error && <p className="error">{errorText(patchLoc.error)}</p>}
            </div>
          )}

          {tab === 'style' && (
            <div role="tabpanel" id="tabpanel-style" aria-labelledby="tab-style" className="tab-body style-body">
              {!style.data && <p className="muted">还没有风格设定，运行剧本后自动生成，也可以直接填写保存。</p>}
              <EditableFields
                fields={STYLE_FIELDS}
                initial={{
                  anchor_text: st.anchor_text,
                  palette: st.palette,
                  lighting: st.lighting,
                  texture: st.texture,
                  character_rules: st.character_rules,
                  mood_keywords: st.mood_keywords.join(', '),
                  negative: st.negative,
                }}
                saving={putStyle.isPending}
                error={putStyle.error ? errorText(putStyle.error) : null}
                onSave={(v) => putStyle.mutate(toStyle(v))}
              />
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
