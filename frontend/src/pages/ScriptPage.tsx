import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { enc, errorText } from '../api/client';
import {
  useCharacters,
  useEpisode,
  useLocations,
  usePatchCharacter,
  usePatchEpisode,
  usePatchLocation,
  usePatchSceneBlock,
  usePutStyle,
  useRunStage,
  useStyle,
} from '../api/queries';
import type { CharacterPatch, EpisodePatch, LocationPatch, Style } from '../api/types';
import { EditableFields, type FieldSpec, type FieldValues } from '../components/EditableFields';
import { EPISODE_STATUS_LABEL } from '../format';

const EPISODE_FIELDS: FieldSpec[] = [
  { name: 'title', label: '本集标题' },
  { name: 'target_seconds', label: '目标时长（秒）', kind: 'number', step: '1' },
  { name: 'source_text', label: '原文 / 梗概', kind: 'textarea', rows: 18 },
];

const CHARACTER_FIELDS: FieldSpec[] = [
  { name: 'name', label: '名字' },
  { name: 'description', label: '人物描述', kind: 'textarea', rows: 2 },
  { name: 'visual_prompt', label: '外貌提示词（英文，会原样写进每个出场镜头）', kind: 'textarea', rows: 4 },
  { name: 'core_features', label: '核心特征', kind: 'textarea', rows: 2 },
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

export function ScriptPage() {
  const { slug = '', episodeId = '' } = useParams();
  const id = Number(episodeId);
  const ep = useEpisode(id);
  const locations = useLocations(slug);
  const characters = useCharacters(slug);
  const style = useStyle(slug);
  const patchEp = usePatchEpisode(id);
  const patchBlock = usePatchSceneBlock(id);
  const patchChar = usePatchCharacter(slug);
  const patchLoc = usePatchLocation(slug);
  const putStyle = usePutStyle(slug);
  const run = useRunStage(id);
  const [force, setForce] = useState(false);
  const [notice, setNotice] = useState<{ text: string; jobId?: number } | null>(null);

  if (ep.isLoading) return <p className="muted">加载中…</p>;
  if (ep.error || !ep.data) return <p className="error">{ep.error ? errorText(ep.error) : '找不到这一集'}</p>;
  const e = ep.data;
  const locationOptions = [
    { value: '', label: '（未指定）' },
    ...(locations.data ?? []).map((l) => ({ value: String(l.id), label: l.name })),
  ];
  const chars = characters.data ?? [];
  const locs = locations.data ?? [];
  const st = style.data ?? EMPTY_STYLE;

  const runStage = (stage: 'script' | 'storyboard') =>
    run.mutate(
      { stage, force },
      {
        onSuccess: (r) => setNotice({ text: `已提交任务 #${r.job_ids.join(', #')}`, jobId: r.job_ids[0] }),
        onError: (err) => setNotice({ text: errorText(err) }),
      },
    );

  return (
    <div className="page">
      <header className="page-head">
        <h2>
          剧本 · 第 {e.number} 集 {e.title}
        </h2>
        <span className="muted">
          {EPISODE_STATUS_LABEL[e.status] ?? e.status} · 已有 {e.shots.length} 个镜头
        </span>
        <Link to={`/projects/${enc(slug)}/episodes/${id}/director`}>去导演台</Link>
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

      <div className="cols">
        <section className="panel">
          <h3>原文</h3>
          <EditableFields
            fields={EPISODE_FIELDS}
            initial={{ title: e.title, target_seconds: String(e.target_seconds), source_text: e.source_text }}
            saving={patchEp.isPending}
            error={patchEp.error ? errorText(patchEp.error) : null}
            onSave={(_v, c) => patchEp.mutate(toEpisodePatch(c))}
          />
        </section>
        <section className="panel">
          <h3>剧本段落（{e.scene_blocks.length}）</h3>
          {e.scene_blocks.length === 0 && <p className="muted">还没有段落。点"运行剧本"由模型拆分。</p>}
          {e.scene_blocks.map((b) => (
            <div key={b.id} className="block" data-testid={`block-${b.id}`}>
              <h4>段落 {b.order + 1}</h4>
              <EditableFields
                fields={[
                  { name: 'location_id', label: '场景', kind: 'select', options: locationOptions },
                  { name: 'text', label: '段落内容', kind: 'textarea', rows: 5 },
                ]}
                initial={{ location_id: b.location_id == null ? '' : String(b.location_id), text: b.text }}
                onSave={(v) =>
                  patchBlock.mutate({
                    id: b.id,
                    text: v.text,
                    location_id: v.location_id === '' ? null : Number(v.location_id),
                  })
                }
              />
            </div>
          ))}
          {patchBlock.error && <p className="error">{errorText(patchBlock.error)}</p>}
        </section>
      </div>

      <section className="panel">
        <h3>角色（{chars.length}）</h3>
        {chars.length === 0 && <p className="muted">运行剧本后自动抽取。</p>}
        <div className="cards">
          {chars.map((c) => (
            <div key={c.id} className="card" data-testid={`character-${c.id}`}>
              <EditableFields
                fields={CHARACTER_FIELDS}
                initial={{ name: c.name, description: c.description, visual_prompt: c.visual_prompt, core_features: c.core_features }}
                onSave={(_v, ch) => patchChar.mutate({ id: c.id, ...(ch as CharacterPatch) })}
              />
            </div>
          ))}
        </div>
        {patchChar.error && <p className="error">{errorText(patchChar.error)}</p>}
      </section>

      <section className="panel">
        <h3>场景（{locs.length}）</h3>
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
      </section>

      <section className="panel">
        <h3>风格</h3>
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
      </section>
    </div>
  );
}
