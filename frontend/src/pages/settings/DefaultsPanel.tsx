import { useEffect, useMemo, useRef, useState } from 'react';
import { FoldPanel } from './FoldPanel';
import { useSearchParams } from 'react-router-dom';
import { errorText } from '../../api/client';
import { useAppSettings, useSaveSettings } from '../../api/queries';
import type { AppSettings, AppSettingsIn, EngineChoice, LlmChoice, SettingKind, SettingValue } from '../../api/types';
import { engineLabel, engineOptionDisabled, engineOptionText } from '../../format';

/** 设置页「默认模型」：默认文本模型 / 新项目的图片来源 / 新项目的视频来源，按步骤单独设置。
 *  null（空串）= 跟随默认。规则：设置页只管默认，项目和镜头上选的总是优先。 */

interface Draft {
  text_model: string;
  image_engine: string;
  video_engine: string;
  steps: Record<string, string>;
}

function draftOf(d: AppSettings): Draft {
  return {
    text_model: d.text_model.value ?? '',
    image_engine: d.image_engine.value ?? '',
    video_engine: d.video_engine.value ?? '',
    steps: Object.fromEntries(d.steps.map((s) => [s.key, s.value ?? ''])),
  };
}

/**
 * 不带项目时跟项目走的步骤写什么：定妆照先看项目的风格预设推荐（真人写实 / 商业广告推荐 krea2），
 * 没推荐才是项目图片引擎；场景图等直接跟项目图片引擎。inOption = 下拉框「跟随默认（…）」里的写法。
 */
function followsLabel(key: string, inOption: boolean): string {
  if (key !== 'portrait') return '项目图片引擎';
  return inOption ? '项目风格预设推荐 / 图片引擎' : '项目（风格预设推荐 / 图片引擎）';
}

/** 只把改过的字段变成 PUT 体；空串 → null（跟随默认） */
export function settingsPatch(saved: Draft, draft: Draft): AppSettingsIn {
  const out: AppSettingsIn = {};
  for (const k of ['text_model', 'image_engine', 'video_engine'] as const) {
    if (draft[k] !== saved[k]) out[k] = draft[k] || null;
  }
  const steps: Record<string, string | null> = {};
  for (const [k, v] of Object.entries(draft.steps)) {
    if (v !== (saved.steps[k] ?? '')) steps[k] = v || null;
  }
  if (Object.keys(steps).length) out.steps = steps;
  return out;
}

function llmName(choices: LlmChoice[], id: string | null | undefined): string {
  if (!id) return '未配置';
  return choices.find((c) => c.id === id)?.label ?? id;
}

function engineName(choices: EngineChoice[], name: string | null | undefined): string {
  if (!name) return '—';
  const row = choices.find((c) => c.name === name);
  return row ? engineLabel(row) : engineLabel(name);
}

/** 存的设置值已经不在可选项里（config 删了 provider、引擎停用了）：下拉框里留一项，旁边说清楚现在用的是什么 */
function InvalidOption({ v }: { v: SettingValue }) {
  return v.invalid && v.value ? <option value={v.value}>{`已失效：${v.value}`}</option> : null;
}

function InvalidNote({ v, now, id }: { v: SettingValue; now: string; id: string }) {
  if (!v.invalid || !v.value) return null;
  return (
    <small className="error" data-testid={`invalid-${id}`}>
      已失效（配置里没有 {v.value}），现在用 {now}
    </small>
  );
}

function Options({ kind, data }: { kind: SettingKind; data: AppSettings }) {
  if (kind === 'llm') {
    return (
      <>
        {data.choices.llm.map((c) => (
          <option key={c.id} value={c.id} disabled={c.ready === false} title={c.note ?? undefined}>
            {c.ready === false ? `${c.label}（${c.hint ?? '先在 API 服务里填 Key'}）` : c.label}
          </option>
        ))}
      </>
    );
  }
  const rows = kind === 'image' ? data.choices.image : data.choices.video;
  return (
    <>
      {rows.map((e) => (
        <option key={e.name} value={e.name} disabled={engineOptionDisabled(e)} title={e.note ?? undefined}>
          {engineOptionText(e)}
        </option>
      ))}
    </>
  );
}

export function DefaultsPanel() {
  const settings = useAppSettings();
  const save = useSaveSettings();
  const data = settings.data;
  const saved = useMemo(() => (data ? draftOf(data) : null), [data]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [justSaved, setJustSaved] = useState(false);
  useEffect(() => {
    if (saved) setDraft(saved);
  }, [saved]);
  // ?step=<key>（从「看不了图片」之类的失败提示跳过来）：展开按步骤设置，焦点放到这一步的下拉框
  const [params] = useSearchParams();
  const focusStep = params.get('step');
  const overridesRef = useRef<HTMLDetailsElement>(null);
  const ready = !!draft;
  useEffect(() => {
    if (!focusStep || !ready || !overridesRef.current) return;
    const row = Array.from(overridesRef.current.querySelectorAll<HTMLElement>('tr[data-step]')).find((r) => r.dataset.step === focusStep);
    if (!row) return;
    overridesRef.current.open = true;
    row.scrollIntoView?.({ block: 'center' });
    row.querySelector('select')?.focus();
  }, [focusStep, ready]);

  if (settings.error) {
    return (
      <FoldPanel id="defaults" className="area-defaults" ariaLabel="默认模型" title="默认模型">
        <p className="error" role="alert">
          {errorText(settings.error)}
        </p>
      </FoldPanel>
    );
  }
  if (!data || !draft || !saved) {
    return (
      <FoldPanel id="defaults" className="area-defaults" ariaLabel="默认模型" title="默认模型">
        <p className="muted" role="status">
          加载中…
        </p>
      </FoldPanel>
    );
  }

  const patch = settingsPatch(saved, draft);
  const dirty = Object.keys(patch).length > 0;
  const set = (k: 'text_model' | 'image_engine' | 'video_engine', v: string) => {
    setJustSaved(false);
    setDraft({ ...draft, [k]: v });
  };
  const setStep = (key: string, v: string) => {
    setJustSaved(false);
    setDraft({ ...draft, steps: { ...draft.steps, [key]: v } });
  };
  const onSave = () => {
    save.mutate(patch, { onSuccess: () => setJustSaved(true) });
  };
  const overridden = data.steps.filter((s) => s.value).length;
  // 网页文本模型（ChatGPT / Gemini / Claude 网页）：用不了的写明原因（扩展没连上 / 要重新加载 / 没登录）
  const web = data.choices.llm.filter((c) => c.kind === 'web');
  // 几家原因一样（扩展没连上、要重新加载）合成一条：「ChatGPT / Gemini / Claude 网页用不了：…」
  const webNotes = new Map<string, string[]>();
  for (const c of web) {
    if (c.ready !== false || !c.note) continue;
    webNotes.set(c.note, [...(webNotes.get(c.note) ?? []), c.label.split('（')[0]]);
  }
  // API 引擎没配 Key 不在这里一条条列（下拉里禁用并写明，「API 服务」那块说清楚）
  const bad = [...data.choices.image, ...data.choices.video].filter((e) => !e.ready && e.note && e.runtime !== 'api');
  const stateText = save.isPending ? '保存中…' : dirty ? '有改动，还没保存' : justSaved ? '已保存，马上生效' : '';
  const nameOf = (kind: SettingKind, id: string | null | undefined) =>
    kind === 'llm' ? llmName(data.choices.llm, id) : engineName(kind === 'image' ? data.choices.image : data.choices.video, id);

  return (
    <FoldPanel
      id="defaults"
      className="area-defaults"
      ariaLabel="默认模型"
      title="默认模型"
      forceOpen={!!focusStep}
      actions={
        <>
          {/* 状态一直在 DOM 里，读屏软件才会念出变化 */}
          <span
            role="status"
            aria-live="polite"
            className={`small ${justSaved && !dirty && !save.isPending ? 'saved-text' : 'muted'}`}
            data-testid="defaults-state"
          >
            {stateText}
          </span>
          <button type="button" className="primary" disabled={!dirty || save.isPending} onClick={onSave}>
            保存
          </button>
        </>
      }
    >
      <p className="muted small">设置页只管默认，项目和镜头上选的总是优先。改了马上生效，不用重启。</p>
      {save.error && (
        <p className="error" role="alert">
          {errorText(save.error)}
        </p>
      )}
      <div className="defaults-grid">
        <label className="field">
          <span>文本模型</span>
          <select aria-label="文本模型" value={draft.text_model} onChange={(e) => set('text_model', e.target.value)}>
            <option value="">{`按配置文件（${llmName(data.choices.llm, data.text_model.fallback)}）`}</option>
            <InvalidOption v={data.text_model} />
            <Options kind="llm" data={data} />
          </select>
          <InvalidNote v={data.text_model} now={nameOf('llm', data.text_model.effective)} id="text_model" />
          <small className="muted">剧本、分镜、角色推荐、台词适配、字幕翻译都用它（下面可以单独改）</small>
          {web.length > 0 && (
            <small className="muted" data-testid="web-llm-advice">
              ChatGPT / Gemini / Claude 网页走浏览器扩展、用网页的免费额度，较慢：适合写梗概、角色推荐这类短文本；剧本、分镜输出长，网页版可能截断，优先用 API 或本机 Claude。
            </small>
          )}
          {Array.from(webNotes, ([note, names]) => (
            <small key={note} className="muted" data-testid="web-llm-note">
              {names.join(' / ')}现在用不了：{note}
            </small>
          ))}
        </label>
        <label className="field">
          <span>新项目的图片来源</span>
          <select aria-label="新项目的图片来源" value={draft.image_engine} onChange={(e) => set('image_engine', e.target.value)}>
            <option value="">{`内置默认（${engineName(data.choices.image, data.image_engine.fallback)}）`}</option>
            <InvalidOption v={data.image_engine} />
            <Options kind="image" data={data} />
          </select>
          <InvalidNote v={data.image_engine} now={nameOf('image', data.image_engine.effective)} id="image_engine" />
          <small className="muted">只影响之后新建的项目；已有项目在项目里改图像/视频引擎</small>
        </label>
        <label className="field">
          <span>新项目的视频来源</span>
          <select aria-label="新项目的视频来源" value={draft.video_engine} onChange={(e) => set('video_engine', e.target.value)}>
            <option value="">{`内置默认（${engineName(data.choices.video, data.video_engine.fallback)}）`}</option>
            <InvalidOption v={data.video_engine} />
            <Options kind="video" data={data} />
          </select>
          <InvalidNote v={data.video_engine} now={nameOf('video', data.video_engine.effective)} id="video_engine" />
          <small className="muted">只影响之后新建的项目；已有项目在项目里改图像/视频引擎</small>
        </label>
      </div>
      {bad.length > 0 && (
        <ul className="defaults-notes small">
          {bad.map((e) => (
            <li key={e.name} className="error">
              {engineLabel(e)}：{e.note}
            </li>
          ))}
        </ul>
      )}
      <details className="step-overrides" ref={overridesRef}>
        <summary>
          按步骤单独设置{overridden ? `（已单独设置 ${overridden} 步）` : ''}
        </summary>
        <table className="table step-table">
          <thead>
            <tr>
              <th>步骤</th>
              <th>用什么</th>
              <th>现在生效</th>
            </tr>
          </thead>
          <tbody>
            {data.steps.map((s) => (
              <tr key={s.key} data-step={s.key}>
                <td>
                  {s.label}
                  <span className="pill">{s.kind === 'llm' ? '文本' : s.kind === 'image' ? '图片' : '视频'}</span>
                </td>
                <td>
                  <select aria-label={`${s.label}用什么`} value={draft.steps[s.key] ?? ''} onChange={(e) => setStep(s.key, e.target.value)}>
                    <option value="">{`跟随默认（${s.follows_project ? followsLabel(s.key, true) : nameOf(s.kind, s.fallback)}）`}</option>
                    <InvalidOption v={s} />
                    <Options kind={s.kind} data={data} />
                  </select>
                  <InvalidNote v={s} now={nameOf(s.kind, s.effective)} id={s.key} />
                </td>
                <td className="muted" data-testid={`effective-${s.key}`}>
                  {/* 不带项目时这一步实际跟各项目的图片引擎走，effective 只是新项目默认引擎的估计 */}
                  {s.follows_project && !draft.steps[s.key] ? `跟随${followsLabel(s.key, false)}` : nameOf(s.kind, s.effective)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">
          文本步骤：这里的设置 → 上面的默认文本模型 → 配置文件。三视图、场景图：抽卡时手选的 → 这里的设置 → 项目的图像引擎（三视图没设置时有 H3 先用 H3）。
          定妆照：抽卡时手选的 → 这里的设置（只在它和项目的风格预设合得来时用，即在预设推荐的引擎里；这里设成 krea2，动漫、国风项目不会也画成写实照片）→ 预设推荐的定妆照引擎（真人写实、商业广告推荐 krea2）→ 项目的图像引擎。
          首帧、视频没有单独设置：跟项目和镜头上选的引擎走。
        </p>
      </details>
    </FoldPanel>
  );
}
