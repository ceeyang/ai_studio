// 单集工作台的角色阶段：顶部「按剧本推荐角色」，中间角色卡网格（深度编辑在角色抽屉，?char=<id> 打开），
// 下方可折叠的「设定」放场景（含场景图）和风格编辑器（折叠用 hidden、保持挂载，收起不丢正在编辑的内容）。
import { useEffect, useRef, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { errorText } from '../../api/client';
import {
  useCharacters,
  useDrawLocationImages,
  useEpisode,
  useLocations,
  usePatchLocation,
  useProps,
  usePutStyle,
  useStyle,
} from '../../api/queries';
import type { LocationPatch, Style } from '../../api/types';
import { useConfirm } from '../../components/ConfirmDialog';
import { type LeaveCheck, confirmDiscard } from '../../components/confirmDiscard';
import { EpisodeLoadError, nextActionProps, useNextAction } from '../../components/EpisodeHeader';
import { castTarget, hasPortrait, scenesToDraw } from '../stages';
import {
  type DrawerLoss,
  EditableFields,
  type FieldSpec,
  type FieldValues,
  useDrawerDirty,
  useUnsavedGuard,
} from '../../components/EditableFields';
import { RecommendPanel } from '../script/RecommendPanel';
import { CastCard } from './CastCard';
import { CharacterDrawer } from './CharacterDrawer';
import { LocationImages } from './LocationImages';
import { PropCard } from './PropImages';

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

/** 角色抽屉会不会因这次跳转卸载：换了 ?char=，或离开角色阶段 */
export const charDrawerLoss: DrawerLoss = (from, to) =>
  from.get('char') !== to.get('char') || (from.get('stage') === 'cast' && to.get('stage') !== 'cast');

export function CastStage({
  active = true,
  setLeaveCheck,
}: {
  active?: boolean;
  /** 工作台切走本阶段前调用：角色抽屉有没保存的修改时先问 */
  setLeaveCheck?: (check: LeaveCheck | null) => void;
}) {
  const { slug = '', episodeId = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const charId = Number(params.get('char')) || null;
  const id = Number(episodeId);
  const ep = useEpisode(id);
  const characters = useCharacters(slug);
  const locations = useLocations(slug);
  const props = useProps(slug);
  const style = useStyle(slug);
  const patchLoc = usePatchLocation(slug);
  const putStyle = usePutStyle(slug);
  const drawScenes = useDrawLocationImages(slug);
  // ?settings=1（首帧阶段「去生成场景图」的链接）：一进来就展开设定
  const [settingsOpen, setSettingsOpen] = useState(() => params.get('settings') === '1');
  const wantSettings = params.get('settings') === '1';
  useEffect(() => {
    if (wantSettings) setSettingsOpen(true); // 阶段一直挂着时，从链接再进来也要展开
  }, [wantSettings]);
  const confirm = useConfirm();
  // 抽屉里显示的角色：跟着地址的 ?char= 走；抽屉有没保存的修改时（左栏 / 提示 / 后退换了 ?char=）先问一句
  const [shownId, setShownId] = useState<number | null>(charId);
  // 登记在全站拦截器里：浏览器后退 / 前进换了 ?char= 或离开角色阶段（抽屉会卸载）时也问
  const drawerDirty = useDrawerDirty(charDrawerLoss);
  // 从卡片的「补外观提示词」打开：抽屉直接到资料页签、聚焦提示词
  const [promptFor, setPromptFor] = useState<number | null>(null);
  // 下一步是抽定妆照 / 按剧本推荐角色时：对应的按钮打上 data-next-action，阶段条的「下一步」会触发它
  const nextAct = useNextAction(slug, ep.data, ['cast']);
  // 改过没保存的场景（按 id）和风格：换页面前问一句（角色抽屉自己挂了）
  const [dirtyLocs, setDirtyLocs] = useState<Set<number>>(() => new Set());
  const [dirtyProps, setDirtyProps] = useState<Set<number>>(() => new Set());
  const [styleDirty, setStyleDirty] = useState(false);
  useUnsavedGuard(styleDirty || dirtyLocs.size > 0 || dirtyProps.size > 0);
  const setLocDirty = (locId: number, d: boolean) =>
    setDirtyLocs((prev) => {
      if (d === prev.has(locId)) return prev;
      const next = new Set(prev);
      if (d) next.add(locId);
      else next.delete(locId);
      return next;
    });

  const setPropDirty = (propId: number, d: boolean) =>
    setDirtyProps((prev) => {
      if (d === prev.has(propId)) return prev;
      const next = new Set(prev);
      if (d) next.add(propId);
      else next.delete(propId);
      return next;
    });

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
  const setCharRef = useRef(setChar);
  setCharRef.current = setChar;

  // 切阶段前：抽屉有没保存的修改先问（抽屉卸载时会把脏标记清掉）
  useEffect(() => {
    if (!setLeaveCheck) return;
    setLeaveCheck(async () => {
      if (!drawerDirty.dirty) return true;
      if (!(await confirmDiscard(confirm, '这个角色', '切换阶段'))) return false;
      drawerDirty.set(false);
      return true;
    });
    return () => setLeaveCheck(null);
  }, [setLeaveCheck, confirm, drawerDirty]);

  useEffect(() => {
    if (charId === shownId) return;
    // 阶段藏起来时抽屉本来就不在（离开阶段由阶段条处理），没修改时直接换
    if (!active || shownId == null || !drawerDirty.dirty) {
      setShownId(charId);
      return;
    }
    let stale = false;
    void confirmDiscard(confirm, '这个角色', '换走').then((ok) => {
      if (stale) return;
      if (ok) {
        drawerDirty.set(false);
        setShownId(charId);
      } else setCharRef.current(shownId);
    });
    return () => {
      stale = true;
    };
  }, [charId, shownId, active, confirm, drawerDirty]);

  if (ep.isLoading) return <p className="loading">加载中…</p>;
  if (ep.error || !ep.data) return <EpisodeLoadError slug={slug} error={ep.error} />;
  const e = ep.data;
  const chars = characters.data ?? [];
  const locs = locations.data ?? [];
  const propList = props.data ?? [];
  const st = style.data ?? EMPTY_STYLE;
  // 只出声、不出镜的角色不用定妆照，不算进计数
  const onScreen = chars.filter((c) => !c.voice_only);
  const withPortrait = onScreen.filter(hasPortrait).length;
  // 轮到的角色：和阶段条下一步的算法一致（缺定妆照、没在抽卡；有提示词的优先）
  const target = nextAct ? castTarget(e.shots, chars) : null;
  const nextCharId = target?.id ?? undefined;
  // 场景图：首帧引擎吃参考图时，本集用到、缺场景图的场景可以一键各出一张（阶段条下一步「生成场景图」触发它）
  const canUseScenes = e.shots.some((s) => (s.image_max_refs ?? 0) > 0);
  const toDraw = scenesToDraw(e, locs);
  const drawMissingScenes = async () => {
    setSettingsOpen(true);
    try {
      for (const l of toDraw) await drawScenes.mutateAsync({ id: l.id, count: 1 });
    } catch {
      // 出错写在按钮旁（drawScenes.error），剩下的场景不再提交
    }
  };
  const openChar = chars.find((c) => c.id === shownId) ?? null;
  const open = (cid: number, prompt = false) => {
    setPromptFor(prompt ? cid : null);
    setChar(cid);
  };
  /** 抽屉自己确认过（或没修改）后关闭：先把显示的角色清掉，免得地址变化再问一次 */
  const closeDrawer = () => {
    drawerDirty.set(false);
    setShownId(null);
    setPromptFor(null);
    setChar(null);
  };

  return (
    <div className="stage-body cast-stage">
      <div className="toolbar">
        <RecommendPanel slug={slug} episodeId={id} existing={chars} isNext={!!nextAct && chars.length === 0} />
        {chars.length > 0 && (
          <span className="chip">
            {withPortrait}/{onScreen.length} 个角色有定妆照
          </span>
        )}
        {canUseScenes && toDraw.length > 0 && (
          <button
            type="button"
            className="sm"
            {...nextActionProps(nextAct?.kind === 'scenes')}
            disabled={drawScenes.isPending}
            title="给本集用到、还没有场景图的场景各出一张空场景图（出首帧时排在参考图第一张，同一场景各镜的布景更一致）"
            onClick={() => void drawMissingScenes()}
          >
            生成场景图（{toDraw.length} 个）
          </button>
        )}
        {drawScenes.error && <span className="error small">{errorText(drawScenes.error)}</span>}
      </div>

      {chars.length === 0 ? (
        <p className="panel empty muted">生成剧本后自动抽取，也可以按剧本推荐角色。</p>
      ) : (
        <div className="cast-grid" role="list" aria-label="角色">
          {chars.map((c) => (
            <div key={c.id} role="listitem" className="cast-cell">
              <CastCard c={c} slug={slug} isNext={c.id === nextCharId} onOpen={(prompt) => open(c.id, prompt)} />
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
            场景 {locs.length} 个{locs.length > 0 ? `（${locs.filter((l) => l.image_url).length} 个有场景图）` : ''}
            {locs.some((l) => l.image_stale) ? ` · ${locs.filter((l) => l.image_stale).length} 个场景图已过期` : ''}
            {propList.length > 0 ? ` · 道具 ${propList.length} 个（${propList.filter((p) => p.image_url).length} 个有道具图）` : ''} · 风格
            {style.data ? '' : '（未设）'}
            {styleDirty || dirtyLocs.size > 0 || dirtyProps.size > 0 ? ' · 有未保存的修改' : ''}
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
                <LocationImages loc={l} slug={slug} />
              </div>
            ))}
          </div>
          <h4>道具（{propList.length}）</h4>
          <p className="muted small">
            分镜里出现过的道具。给道具出一张只拍道具、白底的图（或上传），镜头里带了这个道具时，出首帧会把道具图排在角色后面一起给引擎。
          </p>
          {props.error && <p className="error small">{errorText(props.error)}</p>}
          {props.data && propList.length === 0 && <p className="muted">生成分镜后，镜头里写到的道具会列在这里。</p>}
          <div className="cards">
            {propList.map((p) => (
              <PropCard key={p.id} prop={p} slug={slug} onDirtyChange={(d) => setPropDirty(p.id, d)} />
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
        <CharacterDrawer
          key={openChar.id}
          c={openChar}
          slug={slug}
          episodeId={id}
          focusPrompt={promptFor === openChar.id}
          onDirtyChange={drawerDirty.set}
          onClose={closeDrawer}
        />
      )}
    </div>
  );
}
