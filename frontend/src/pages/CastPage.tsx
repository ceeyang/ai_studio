// 角色页：角色 / 场景 / 风格三个页签，全宽。页签用 hidden 切换、都保持挂载，切页签不丢正在编辑的内容。
import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { errorText } from '../api/client';
import { useCharacters, useEpisode, useLocations, usePatchLocation, usePutStyle, useStyle } from '../api/queries';
import type { LocationPatch, Style } from '../api/types';
import { EpisodeHeader, EpisodeLoadError } from '../components/EpisodeHeader';
import { EditableFields, type FieldSpec, type FieldValues, useUnsavedGuard } from '../components/EditableFields';
import { CharacterCard } from './script/CharacterCard';
import { RecommendPanel } from './script/RecommendPanel';

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

export function CastPage() {
  const { slug = '', episodeId = '' } = useParams();
  const id = Number(episodeId);
  const ep = useEpisode(id);
  const characters = useCharacters(slug);
  const locations = useLocations(slug);
  const style = useStyle(slug);
  const patchLoc = usePatchLocation(slug);
  const putStyle = usePutStyle(slug);
  const [tab, setTab] = useState<Tab>('chars');
  // 改过没保存的场景（按 id）和风格：换页面前问一句（角色卡自己挂了）
  const [dirtyLocs, setDirtyLocs] = useState<Set<number>>(() => new Set());
  const [styleDirty, setStyleDirty] = useState(false);
  useUnsavedGuard(styleDirty || dirtyLocs.size > 0);
  const setLocDirty = (locId: number, d: boolean) =>
    setDirtyLocs((prev) => {
      if (d === prev.has(locId)) return prev;
      const next = new Set(prev);
      if (d) next.add(locId);
      else next.delete(locId);
      return next;
    });

  if (ep.isLoading) return <p className="loading">加载中…</p>;
  if (ep.error || !ep.data) return <EpisodeLoadError slug={slug} error={ep.error} />;
  const e = ep.data;
  const chars = characters.data ?? [];
  const locs = locations.data ?? [];
  const st = style.data ?? EMPTY_STYLE;
  const withPortrait = chars.filter((c) => c.portrait_url).length;

  const tabs: { key: Tab; label: string }[] = [
    { key: 'chars', label: `角色（${chars.length}）` },
    { key: 'locs', label: `场景（${locs.length}）` },
    { key: 'style', label: '风格' },
  ];

  return (
    <div className="page cast-page">
      <EpisodeHeader slug={slug} ep={e} page="cast" title="角色">
        {chars.length > 0 && (
          <span className="chip">
            {withPortrait}/{chars.length} 个角色有定妆照
          </span>
        )}
      </EpisodeHeader>

      <section className="panel cast-pane" aria-label="角色、场景与风格">
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

        <div role="tabpanel" id="tabpanel-chars" aria-labelledby="tab-chars" className="tab-body" hidden={tab !== 'chars'}>
          <RecommendPanel slug={slug} episodeId={id} existing={chars} />
          {chars.length === 0 && <p className="muted">运行剧本后自动抽取，也可以按剧本推荐角色。</p>}
          <div className="char-list">
            {chars.map((c) => (
              <CharacterCard key={c.id} c={c} slug={slug} episodeId={id} />
            ))}
          </div>
        </div>

        <div role="tabpanel" id="tabpanel-locs" aria-labelledby="tab-locs" className="tab-body" hidden={tab !== 'locs'}>
          {locs.length === 0 && <p className="muted">运行剧本后自动抽取。</p>}
          <div className="cards">
            {locs.map((l) => (
              <div key={l.id} className="card" data-testid={`location-${l.id}`}>
                <EditableFields
                  fields={LOCATION_FIELDS}
                  initial={{ name: l.name, time_of_day: l.time_of_day, atmosphere: l.atmosphere, visual_prompt: l.visual_prompt }}
                  saving={patchLoc.isPending && patchLoc.variables?.id === l.id}
                  error={patchLoc.error && patchLoc.variables?.id === l.id ? errorText(patchLoc.error) : null}
                  onSave={(_v, ch) => patchLoc.mutate({ id: l.id, ...(ch as LocationPatch) })}
                  onDirtyChange={(d) => setLocDirty(l.id, d)}
                />
              </div>
            ))}
          </div>
        </div>

        <div
          role="tabpanel"
          id="tabpanel-style"
          aria-labelledby="tab-style"
          className="tab-body style-body"
          hidden={tab !== 'style'}
        >
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
            onDirtyChange={setStyleDirty}
          />
        </div>
      </section>
    </div>
  );
}
