import { useEffect, useState } from 'react';
import { FoldPanel } from './FoldPanel';
import { errorText } from '../../api/client';
import { useApiServices, useSaveApiService, useTestApiService } from '../../api/queries';
import type { ApiModelKind, ApiService, ApiServiceIn } from '../../api/types';

/** 设置页「API 服务」：OpenAI（GPT）、Anthropic（Claude）、Google（Gemini）的 Key、Base URL、模型名。
 *  后端只回掩码（sk-…abcd），这里也从不显示明文；Key 输入框留空 = 不改。 */

const KINDS: ApiModelKind[] = ['text', 'image', 'video'];

const USES: Record<string, string> = {
  openai: '文本模型（GPT，能看图）· 出图（gpt-image-1，文生图 / 参考图）· 视频（Sora，首帧图生视频）',
  anthropic: '文本模型（Claude，能看图）。Claude 没有出图、视频',
  gemini: '文本模型（Gemini，能看图）· 出图（Gemini 图像，文生图 / 参考图）· 视频（Veo，首帧图生视频）',
};

interface Draft {
  key: string;
  base: string;
  models: Partial<Record<ApiModelKind, string>>;
}

function draftOf(s: ApiService): Draft {
  return {
    key: '',
    base: s.base_url ?? '',
    models: Object.fromEntries(KINDS.filter((k) => s.models[k]).map((k) => [k, s.models[k]?.value ?? ''])),
  };
}

/** 只把改过的字段变成 PUT 体：Key 空 = 不改；Base URL / 模型名清空 = 恢复默认（发空串） */
export function servicePatch(s: ApiService, d: Draft): ApiServiceIn {
  const out: ApiServiceIn = {};
  if (d.key.trim()) out.api_key = d.key.trim();
  if (d.base.trim() !== (s.base_url ?? '')) out.base_url = d.base.trim();
  const models: Partial<Record<ApiModelKind, string>> = {};
  for (const k of KINDS) {
    const m = s.models[k];
    if (m && (d.models[k] ?? '').trim() !== (m.value ?? '')) models[k] = (d.models[k] ?? '').trim();
  }
  if (Object.keys(models).length) out.models = models;
  return out;
}

function sourceText(s: ApiService): string {
  if (!s.configured) return '未配置';
  const where = s.source === 'settings' ? '设置页填的' : `环境变量 ${s.env_name ?? ''}`;
  return `已配置 · ${where} · ${s.masked ?? ''}`;
}

function ServiceCard({ s }: { s: ApiService }) {
  const save = useSaveApiService();
  const test = useTestApiService();
  const [draft, setDraft] = useState<Draft>(() => draftOf(s));
  const [show, setShow] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  // 后端数据真的变了（保存、清除、别处改了）才重置草稿；同样内容的重新拉取不打断正在输入的
  const sig = JSON.stringify({ ...s, last_test: null });
  useEffect(() => {
    setDraft(draftOf(s));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig]);
  const patch = servicePatch(s, draft);
  const dirty = Object.keys(patch).length > 0;
  const edit = (d: Partial<Draft>) => {
    setJustSaved(false);
    setDraft({ ...draft, ...d });
  };
  const onSave = () => save.mutate({ id: s.id, body: patch }, { onSuccess: () => setJustSaved(true) });
  const onClear = () => save.mutate({ id: s.id, body: { clear_key: true } }, { onSuccess: () => setJustSaved(true) });
  const result = test.data ?? (s.last_test ? { ...s.last_test } : null);
  const busy = save.isPending || test.isPending;
  const id = `api-${s.id}`;

  return (
    <div className="api-service" data-testid={id}>
      <div className="api-service-head">
        <h3>{s.label}</h3>
        <span className={`pill ${s.configured ? 'pill-ok' : ''}`} data-testid={`${id}-status`}>
          {sourceText(s)}
        </span>
      </div>
      <p className="muted small">{USES[s.id] ?? ''}</p>
      <div className="api-fields">
        <label className="field">
          <span>API Key</span>
          <span className="api-key-row">
            <input
              type={show ? 'text' : 'password'}
              aria-label={`${s.label} API Key`}
              autoComplete="off"
              spellCheck={false}
              placeholder={s.configured ? `已配置（${s.masked ?? ''}），留空不改` : `粘贴 API Key（或设环境变量 ${s.env_keys.join(' / ')}）`}
              value={draft.key}
              onChange={(e) => edit({ key: e.target.value })}
            />
            <button type="button" onClick={() => setShow(!show)} aria-label={show ? '隐藏 Key' : '显示 Key'}>
              {show ? '隐藏' : '显示'}
            </button>
            {s.has_saved_key && (
              <button type="button" onClick={onClear} disabled={busy} title="删掉设置页存的 Key（环境变量里的照样能用）">
                清除
              </button>
            )}
          </span>
        </label>
        <label className="field">
          <span>Base URL（可选）</span>
          <input
            type="text"
            aria-label={`${s.label} Base URL`}
            placeholder={s.default_base_url}
            value={draft.base}
            onChange={(e) => edit({ base: e.target.value })}
          />
          <small className="muted">{s.base_hint}。留空用官方地址</small>
        </label>
        {KINDS.map((k) => {
          const m = s.models[k];
          if (!m) return null;
          const listId = `${id}-${k}-models`;
          return (
            <label className="field" key={k}>
              <span>{`${m.label}名`}</span>
              <input
                type="text"
                aria-label={`${s.label} ${m.label}`}
                list={listId}
                placeholder={`默认 ${m.default}`}
                value={draft.models[k] ?? ''}
                onChange={(e) => edit({ models: { ...draft.models, [k]: e.target.value } })}
              />
              <datalist id={listId}>
                {m.suggestions.map((x) => (
                  <option key={x} value={x} />
                ))}
              </datalist>
            </label>
          );
        })}
      </div>
      <div className="row api-actions">
        <button type="button" disabled={!dirty || busy} onClick={onSave}>
          {save.isPending ? '保存中…' : '保存'}
        </button>
        <button type="button" disabled={busy || !s.configured} onClick={() => test.mutate(s.id)} title={s.configured ? '调这家的「列模型」接口，不生成内容、不扣费' : '先填 Key'}>
          {test.isPending ? '测试中…' : '测试连接'}
        </button>
        <span role="status" aria-live="polite" className="small" data-testid={`${id}-result`}>
          {save.error ? (
            <span className="error">{errorText(save.error)}</span>
          ) : test.error ? (
            <span className="error">{errorText(test.error)}</span>
          ) : dirty ? (
            <span className="muted">有改动，还没保存</span>
          ) : justSaved ? (
            <span className="saved-text">已保存，马上生效</span>
          ) : result ? (
            <span className={result.ok ? 'saved-text' : 'error'}>{`${result.ok ? '测试通过' : '测试失败'}：${result.message}`}</span>
          ) : null}
        </span>
      </div>
    </div>
  );
}

export function ApiServicesPanel() {
  const q = useApiServices();
  return (
    <FoldPanel
      id="api"
      className="area-api"
      ariaLabel="API 服务"
      title="API 服务"
      defaultOpen={false}
      meta={q.data && `已配置 ${q.data.services.filter((s) => s.configured).length} / ${q.data.services.length}`}
    >
      <p className="muted small">
        用 OpenAI、Anthropic、Google 的官方 API（按量计费）：填了 Key，对应的文本模型就能在上面「默认模型」里选（各步骤也能单独选），
        出图 / 视频引擎（标「API」）能在项目和镜头里选。Key 只存在本机数据库里，页面上只显示掩码；没填时用环境变量。
      </p>
      {q.error && (
        <p className="error" role="alert">
          {errorText(q.error)}
        </p>
      )}
      {!q.data && !q.error && (
        <p className="muted" role="status">
          加载中…
        </p>
      )}
      <div className="api-services">
        {(q.data?.services ?? []).map((s) => (
          <ServiceCard key={s.id} s={s} />
        ))}
      </div>
    </FoldPanel>
  );
}
