// 单集工作台的角色阶段：顶部「按剧本推荐角色」，中间角色卡网格（深度编辑在角色抽屉，?char=<id> 打开），
// 下方可折叠的「设定」放场景和风格编辑器（折叠用 hidden、保持挂载，收起不丢正在编辑的内容）。
import { useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { errorText } from '../../api/client';
import { useCharacters, useEpisode, useLocations, usePatchLocation, usePutStyle, useStyle } from '../../api/queries';
import type { LocationPatch, Style } from '../../api/types';
import { EpisodeLoadError, useNextAction } from '../../components/EpisodeHeader';
import { castOf, hasPortrait } from '../stages';
import { EditableFields, type FieldSpec, type FieldValues, useUnsavedGuard } from '../../components/EditableFields';
import { RecommendPanel } from '../script/RecommendPanel';
import { CastCard } from './CastCard';
import { CharacterDrawer } from './CharacterDrawer';

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

export function CastStage({ active = true }: { active?: boolean }) {
  const { slug = '', episodeId = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const charId = Number(params.get('char')) || null;
  const id = Number(episodeId);
  const ep = useEpisode(id);
  const characters = useCharacters(slug);
  const locations = useLocations(slug);
  const style = useStyle(slug);
  const patchLoc = usePatchLocation(slug);
  const putStyle = usePutStyle(slug);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // 下一步是抽定妆照 / 按剧本推荐角色时：对应的按钮打上 data-next-action，阶段条的「下一步」会触发它
  const nextAct = useNextAction(slug, ep.data, ['cast']);
  // 改过没保存的场景（按 id）和风格：换页面前问一句（角色抽屉自己挂了）
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
  const withPortrait = chars.filter(hasPortrait).length;
  // 轮到的角色：出场角色里第一个还没定妆照的（和进度条的算法一致）
  const nextCharId = nextAct ? castOf(e.shots, chars).find((c) => !hasPortrait(c))?.id : undefined;
  const openChar = chars.find((c) => c.id === charId) ?? null;

  // 只动 char 参数（开关抽屉不记历史），stage 等其他参数保留
  const setChar = (cid: number | null) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (cid == null) next.delete('char');
        else next.set('char', String(cid));
        return next;
      },
      { replace: true },
    );

  return (
    <div className="stage-body cast-stage">
      <div className="toolbar">
        <RecommendPanel slug={slug} episodeId={id} existing={chars} isNext={!!nextAct && chars.length === 0} />
        {chars.length > 0 && (
          <span className="chip">
            {withPortrait}/{chars.length} 个角色有定妆照
          </span>
        )}
      </div>

      {chars.length === 0 ? (
        <p className="panel empty muted">生成剧本后自动抽取，也可以按剧本推荐角色。</p>
      ) : (
        <div className="cast-grid" role="list" aria-label="角色">
          {chars.map((c) => (
            <div key={c.id} role="listitem" className="cast-cell">
              <CastCard c={c} slug={slug} isNext={c.id === nextCharId} onOpen={() => setChar(c.id)} />
            </div>
          ))}
        </div>
      )}

      <section className="panel cast-settings" aria-label="设定">
        <button
          type="button"
          className="fold-toggle"
          aria-expanded={settingsOpen}
          aria-controls="cast-settings-body"
          onClick={() => setSettingsOpen((v) => !v)}
        >
          设定
          <span className="muted">
            场景 {locs.length} 个 · 风格{style.data ? '' : '（未设）'}
            {styleDirty || dirtyLocs.size > 0 ? ' · 有未保存的修改' : ''}
          </span>
        </button>
        <div id="cast-settings-body" className="cast-settings-body" hidden={!settingsOpen}>
          <h4>场景（{locs.length}）</h4>
          {locs.length === 0 && <p className="muted">生成剧本后自动抽取。</p>}
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
          <h4>风格</h4>
          <div className="style-body">
            {!style.data && <p className="muted">还没有风格设定，生成剧本后自动生成，也可以直接填写保存。</p>}
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
        </div>
      </section>

      {openChar && active && (
        <CharacterDrawer key={openChar.id} c={openChar} slug={slug} episodeId={id} onClose={() => setChar(null)} />
      )}
    </div>
  );
}
