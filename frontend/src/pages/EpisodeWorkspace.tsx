// 单集工作台：一集一个主工作面。页头（面包屑、切集）+ 阶段条（五段 + 唯一的主按钮「下一步」）+ 左栏（剧本大纲、角色）+ 主区。
// 地址：/projects/:slug/episodes/:episodeId?stage=script|cast|keyframes|videos|export&shot=<id>&char=<id>
// 切阶段只改 ?stage=：访问过的阶段内容用 hidden 藏起来、保持挂载，没保存的编辑不丢，也不用问。
import { type ComponentType, useEffect, useState } from 'react';
import { Navigate, type RouteObject, useLocation, useParams, useSearchParams } from 'react-router-dom';
import { useCharacters, useEpisode, useExports, useRecentDoneJobs } from '../api/queries';
import { EpisodeHeader, EpisodeLoadError } from '../components/EpisodeHeader';
import { type NextStep, type StageKey, episodeStages, isStageKey } from './stages';
import { CastStage } from './workspace/CastStage';
import { ExportStage } from './workspace/ExportStage';
import { ScriptStage } from './workspace/ScriptStage';
import { type ShotGate, shotGate } from './workspace/shotGate';
import { SHOTS_PANEL, type ShotWork, ShotStage } from './workspace/ShotStage';
import { StageBar, triggerNextAction } from './workspace/StageBar';
import { LEGACY_STAGE, stageSearch, workspacePath } from './workspace/urls';
import { WorkspaceRail } from './workspace/WorkspaceRail';

type PanelKey = 'script' | 'cast' | typeof SHOTS_PANEL | 'export';

/**
 * 主区的内容块：每个阶段渲染哪一块。首帧和视频共用镜头卡片网格（同一个实例，抽屉和勾选不重复，
 * 卡片按当前阶段显示首帧或视频）。
 */
const PANEL_OF: Record<StageKey, PanelKey> = {
  script: 'script',
  cast: 'cast',
  keyframes: SHOTS_PANEL,
  videos: SHOTS_PANEL,
  export: 'export',
};

/** 阶段内容：active = 当前显示的是它（藏起来时不要留抽屉之类的浮层）；work 是镜头阶段和阶段条共用的状态 */
export interface StagePanelProps {
  active: boolean;
  work: ShotWork;
}

const PANELS: { key: PanelKey; label: string; Component: ComponentType<StagePanelProps> }[] = [
  { key: 'script', label: '剧本', Component: ScriptStage },
  { key: 'cast', label: '角色', Component: CastStage },
  { key: SHOTS_PANEL, label: '首帧与视频', Component: ShotStage },
  { key: 'export', label: '成片', Component: ExportStage },
];

/** 首帧确认门换成阶段条的下一步：run 在当前阶段提交，goto 切过去 */
function gateStep(g: ShotGate, current: StageKey): NextStep {
  if (g.kind === 'goto') return { label: g.label, stage: g.stage, action: 'goto', count: 0, stale: false };
  return { label: g.label, stage: current, action: 'run', count: 0, stale: false };
}

function Workspace({ slug, id }: { slug: string; id: number }) {
  const ep = useEpisode(id);
  const characters = useCharacters(slug);
  const exports = useExports(id);
  const [params, setParams] = useSearchParams();
  const [visited, setVisited] = useState<Set<PanelKey>>(() => new Set());
  // 左栏点了段落：切到剧本阶段后滚到那一段
  const [scrollBlock, setScrollBlock] = useState<number | null>(null);
  // 首帧 / 视频阶段的勾选和候选数：会改阶段条「下一步」的字（对选中的 N 镜…、预计耗时），所以放在这里
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [count, setCount] = useState(1);
  const [submitting, setSubmitting] = useState(false);

  const progress = ep.data ? episodeStages(ep.data, characters.data ?? [], exports.data ?? []) : null;
  // 角色和成片也读到了（或读失败了）才算得准默认阶段
  const ready = !!progress && (!!characters.data || characters.isError) && (!!exports.data || exports.isError);
  const param = params.get('stage');
  const explicit = isStageKey(param) ? param : null;
  // 没给阶段：带 ?shot= 的去首帧、带 ?char= 的去角色，否则去下一步所在的阶段（都完成了就是成片）
  const fallback: StageKey | null = params.get('shot')
    ? 'keyframes'
    : params.get('char')
      ? 'cast'
      : ready
        ? (progress?.next?.stage ?? 'export')
        : null;
  const stage = explicit ?? fallback;
  const panel = stage ? PANEL_OF[stage] : null;
  const shotStage = stage === 'keyframes' || stage === 'videos' ? stage : null;
  const doneJobs = useRecentDoneJobs(!!shotStage);

  // 默认阶段写进地址（replace），之后进度变了也不会自己跳走
  useEffect(() => {
    if (explicit || !fallback) return;
    setParams(
      (prev) => new URLSearchParams(stageSearch(prev.toString(), fallback)),
      { replace: true },
    );
  }, [explicit, fallback, setParams]);

  useEffect(() => {
    if (panel && !visited.has(panel)) setVisited((prev) => new Set(prev).add(panel));
  }, [panel, visited]);

  useEffect(() => {
    if (scrollBlock == null || stage !== 'script') return;
    const t = window.setTimeout(() => {
      document.querySelector(`[data-testid="block-${scrollBlock}"]`)?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
      setScrollBlock(null);
    }, 0);
    return () => window.clearTimeout(t);
  }, [scrollBlock, stage]);

  if (ep.isLoading) return <p className="loading">加载中…</p>;
  if (ep.error || !ep.data || !progress) return <EpisodeLoadError slug={slug} error={ep.error} />;

  // 切阶段（可以顺带设别的参数）；离开首帧 / 视频去掉 shot（抽屉关掉），离开角色去掉 char；地址没变就不再记一条历史
  const select = (s: StageKey, extra?: Record<string, string>) => {
    const next = new URLSearchParams(stageSearch(params.toString(), s));
    if (PANEL_OF[s] !== SHOTS_PANEL) next.delete('shot');
    if (s !== 'cast') next.delete('char');
    for (const [k, v] of Object.entries(extra ?? {})) next.set(k, v);
    if (next.toString() !== params.toString()) setParams(next);
  };
  const mounted = new Set(visited);
  if (panel) mounted.add(panel);
  // 首帧 / 视频阶段的下一步是首帧确认门（没有镜头时还是原来的下一步：去剧本生成分镜）
  const gate = shotStage ? shotGate(ep.data.shots, shotStage, selected, count, doneJobs.data ?? []) : null;
  const work: ShotWork = {
    stage: shotStage ?? 'keyframes',
    gate,
    selected,
    setSelected,
    count,
    setCount,
    submitting,
    setSubmitting,
    goStage: select,
  };

  return (
    <div className="page workspace">
      <EpisodeHeader slug={slug} ep={ep.data} stage={stage} />
      {stage ? (
        <StageBar
          stages={progress.stages}
          current={stage}
          next={gate ? gateStep(gate, stage) : progress.next}
          nextDisabled={gate?.kind === 'busy' || (!!gate && submitting)}
          onSelect={(s) => select(s)}
          onRun={() => triggerNextAction(PANEL_OF[stage])}
        />
      ) : (
        <p className="loading">加载中…</p>
      )}
      <div className="ws-body">
        <WorkspaceRail
          slug={slug}
          ep={ep.data}
          activeChar={Number(params.get('char')) || null}
          onBlock={(blockId) => {
            setScrollBlock(blockId);
            select('script');
          }}
          onChar={(charId) => select('cast', { char: String(charId) })}
        />
        <div className="ws-main">
          {PANELS.filter((p) => mounted.has(p.key)).map(({ key, label, Component }) => (
            <section key={key} className="ws-panel" data-stage-panel={key} aria-label={label} hidden={key !== panel}>
              <Component active={key === panel} work={work} />
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}

/** 换集时整个重建：各阶段的编辑状态、访问过的阶段都按集算 */
export function EpisodeWorkspace() {
  const { slug = '', episodeId = '' } = useParams();
  return <Workspace key={`${slug}/${episodeId}`} slug={slug} id={Number(episodeId)} />;
}

/** 旧的剧本 / 角色 / 导演台 / 导出页面：重定向到工作台对应阶段，其余查询参数（如 ?shot=）保留 */
function LegacyRedirect({ page }: { page: keyof typeof LEGACY_STAGE }) {
  const { slug = '', episodeId = '' } = useParams();
  const { search } = useLocation();
  return <Navigate replace to={`${workspacePath(slug, Number(episodeId))}${stageSearch(search, LEGACY_STAGE[page])}`} />;
}

export const episodeRoutes: RouteObject[] = [
  { path: '/projects/:slug/episodes/:episodeId', element: <EpisodeWorkspace /> },
  ...(Object.keys(LEGACY_STAGE) as (keyof typeof LEGACY_STAGE)[]).map((page) => ({
    path: `/projects/:slug/episodes/:episodeId/${page}`,
    element: <LegacyRedirect page={page} />,
  })),
];
