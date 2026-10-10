// 单集工作台的首帧 / 视频阶段：工具栏（镜头数、候选数、勾选）+ 按段落分组的镜头卡片网格 + 镜头抽屉（?shot=<id>）。
// 阶段条的「下一步」（首帧确认门，见 shotGate）由工作台算好传进来；点它时这里照着提交。
import { useQueryClient } from '@tanstack/react-query';
import { type Dispatch, type SetStateAction, useEffect, useRef } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api, errorText } from '../../api/client';
import {
  keys,
  useCharacters,
  useCreateShot,
  useDeleteShot,
  useEpisode,
  useLocations,
  useReorderShots,
  useRunStage,
  useSplitShot,
} from '../../api/queries';
import type { Job, RunIn, ShotDetail } from '../../api/types';
import { useConfirm } from '../../components/ConfirmDialog';
import { type DrawerLoss, useDrawerDirty } from '../../components/EditableFields';
import { type LeaveCheck, confirmDiscard } from '../../components/confirmDiscard';
import { EpisodeLoadError, NEXT_ACTION_EVENT, isNextActionFor } from '../../components/EpisodeHeader';
import { useJobNotice } from '../../components/JobNotice';
import { type EstimatePart, estimateText } from '../director/estimate';
import { ShotDrawer } from '../director/ShotDrawer';
import { type CardAction, type GenStage, cellOf, hasSelected } from '../director/shotStatus';
import { moveId } from '../director/slots';
import type { StageKey } from '../stages';
import { CONFIRM_OVER, type GateBatch, type ShotGate } from './shotGate';
import { type ScriptBlockEdits, ShotGrid } from './ShotGrid';
import { workspacePath } from './urls';

/** 工作台里这块内容的名字（阶段条的下一步事件按它发） */
export const SHOTS_PANEL = 'shots';

/** 镜头抽屉会不会因这次跳转卸载：换了 ?shot=，或离开首帧 / 视频阶段 */
export const shotDrawerLoss: DrawerLoss = (from, to) => {
  const shotStage = (p: URLSearchParams) => p.get('stage') === 'keyframes' || p.get('stage') === 'videos';
  return from.get('shot') !== to.get('shot') || (shotStage(from) && !shotStage(to));
};

/** 工作台和镜头阶段共用的状态：勾选、候选数会影响阶段条上「下一步」的字，所以放在工作台 */
export interface ShotWork {
  stage: GenStage;
  /** 阶段条上的下一步；点「下一步」时照着提交 */
  gate: ShotGate | null;
  selected: ReadonlySet<number>;
  setSelected: Dispatch<SetStateAction<Set<number>>>;
  count: number;
  setCount(n: number): void;
  /** 生成请求提交中（还没拿到响应）：阶段条的「下一步」和卡片按钮都禁用，防止重复提交 */
  submitting: boolean;
  setSubmitting(b: boolean): void;
  /** 切阶段，可以顺带设 ?shot= */
  goStage(stage: StageKey, extra?: Record<string, string>): void;
  /** 剧本阶段登记的段落编辑（「重写本段分镜…」先保存那一段）；剧本阶段没挂载时为 null */
  scriptEdits?: () => ScriptBlockEdits | null;
}

export function ShotStage({
  active = true,
  work,
  setLeaveCheck,
}: {
  active?: boolean;
  work: ShotWork;
  /** 工作台切走本阶段前调用：镜头抽屉有没保存的修改时先问 */
  setLeaveCheck?: (check: LeaveCheck | null) => void;
}) {
  const { slug = '', episodeId = '' } = useParams();
  const id = Number(episodeId);
  const [params, setParams] = useSearchParams();
  const qc = useQueryClient();
  const confirm = useConfirm();
  // 镜头抽屉里有没保存的提示词 / 对白：切阶段前先问（抽屉卸载时清掉）。登记在全站拦截器里，
  // 浏览器后退 / 前进换了 ?shot= 或离开首帧 / 视频阶段（抽屉会卸载）时也问；首帧↔视频抽屉还在，不问
  const drawerDirty = useDrawerDirty(shotDrawerLoss);
  useEffect(() => {
    if (!setLeaveCheck) return;
    setLeaveCheck(async () => {
      if (!drawerDirty.dirty) return true;
      if (!(await confirmDiscard(confirm, '这个镜头', '切换阶段'))) return false;
      drawerDirty.set(false);
      return true;
    });
    return () => setLeaveCheck(null);
  }, [setLeaveCheck, confirm, drawerDirty]);
  const notice = useJobNotice();
  const ep = useEpisode(id);
  const locations = useLocations(slug);
  const characters = useCharacters(slug);
  const run = useRunStage(id);
  const reorder = useReorderShots(id);
  const split = useSplitShot(id);
  const del = useDeleteShot(id);
  const create = useCreateShot(id);
  const { stage, selected, setSelected, count, setCount, submitting, setSubmitting } = work;
  const generating = run.isPending || submitting;

  /** 最近完成的任务（和任务页共用缓存）：拿来估耗时，拿不到就当没有样本 */
  const doneJobs = async (): Promise<Job[]> => {
    try {
      return await qc.fetchQuery({
        queryKey: keys.jobs('done', 100),
        queryFn: () => api.get<Job[]>('/api/jobs?status=done&limit=100'),
        staleTime: 30_000,
      });
    } catch {
      return [];
    }
  };

  /** 先弹预估（和补充说明），返回要不要继续 */
  const confirmBatch = async (title: string, parts: EstimatePart[], note?: string): Promise<boolean> => {
    setSubmitting(true);
    const jobs = await doneJobs();
    setSubmitting(false);
    const body = note ? `${estimateText(parts, jobs)}。${note}。` : estimateText(parts, jobs);
    return confirm({ title, body, confirmText: '继续提交' });
  };

  /** 依次提交几批，合起来跟踪；全部提交成功返回 true */
  const submit = async (batches: GateBatch[], text: string): Promise<boolean> => {
    const ids: number[] = [];
    setSubmitting(true);
    try {
      for (const b of batches) {
        const body: RunIn = { stage: b.stage, candidates: count, force: b.force };
        if (b.ids.length > 0) body.shots = b.ids.map(String);
        const r = await run.mutateAsync(body);
        ids.push(...r.job_ids);
      }
      notice.track(ids, text);
      return true;
    } catch (err) {
      // 前面几批已经提交了的照常进行，错误一起说
      notice.error(ids.length > 0 ? `${errorText(err)}（已提交的 ${ids.length} 个任务照常进行）` : errorText(err));
      return false;
    } finally {
      setSubmitting(false);
    }
  };

  /** 阶段条「下一步」：缺 / 过期的补上，或确认首帧后切到视频阶段生成全部视频，或对勾选的镜头生成 / 重拍 */
  const runGate = async (g: ShotGate | null) => {
    if (!g || g.kind !== 'run' || generating) return;
    notice.clear();
    if (g.confirm && !(await confirmBatch(g.title, g.parts, g.note))) return;
    if (g.switchTo && g.switchTo !== stage) work.goStage(g.switchTo);
    const ok = await submit(g.batches, g.doneText);
    if (ok && g.selection) setSelected(new Set());
  };
  const runGateRef = useRef(runGate);
  runGateRef.current = runGate;
  const gateRef = useRef(work.gate);
  gateRef.current = work.gate;
  const activeRef = useRef(active);
  activeRef.current = active;
  useEffect(() => {
    const onNext = (ev: Event) => {
      if (activeRef.current && isNextActionFor(ev, SHOTS_PANEL)) void runGateRef.current(gateRef.current);
    };
    window.addEventListener(NEXT_ACTION_EVENT, onNext);
    return () => window.removeEventListener(NEXT_ACTION_EVENT, onNext);
  }, []);

  if (ep.isLoading) return <p className="loading">加载中…</p>;
  if (ep.error || !ep.data) return <EpisodeLoadError slug={slug} error={ep.error} />;
  const shots = ep.data.shots;
  const openId = Number(params.get('shot')) || null;
  const openShot = shots.find((s) => s.id === openId) ?? null;
  const busy = reorder.isPending || split.isPending || del.isPending || create.isPending;
  const total = shots.reduce((a, s) => a + s.duration, 0);
  const selCount = shots.filter((s) => selected.has(s.id)).length;

  const report = (p: Promise<unknown>, ok: string) => {
    p.then(
      () => notice.info(ok),
      (err: unknown) => notice.error(errorText(err)),
    );
  };

  /** 单个镜头：「生成」不强制；「重拍」「重试」强制（不强制的话后端看选定的结果还是最新的会跳过） */
  const generateOne = async (shot: ShotDetail, st: GenStage, verb: '生成' | '重拍' | '重试') => {
    notice.clear();
    const cell = cellOf(st);
    const batch: GateBatch = { stage: st, ids: [shot.id], force: verb !== '生成' };
    const parts = [{ type: cell.type, n: st === 'keyframes' ? count : 1 }];
    if (parts[0].n > CONFIRM_OVER && !(await confirmBatch(`${verb}${cell.label}？`, parts))) return;
    await submit([batch], `${verb}${cell.label} ${shot.code}`);
  };

  const onAction = (shot: ShotDetail, a: CardAction) => {
    if (a.kind === 'busy') return;
    // 视频阶段还没有首帧：回首帧阶段并打开这个镜头
    if (a.kind === 'need_first_frame') return work.goStage('keyframes', { shot: String(shot.id) });
    void generateOne(shot, stage, a.kind === 'generate' ? '生成' : a.kind === 'redo' ? '重拍' : '重试');
  };

  const toggle = (shotId: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(shotId)) next.delete(shotId);
      else next.add(shotId);
      return next;
    });
  const selectMany = (ids: number[], on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      for (const x of ids) {
        if (on) next.add(x);
        else next.delete(x);
      }
      return next;
    });

  const move = (shotId: number, delta: -1 | 1) => {
    const next = moveId(shots.map((s) => s.id), shotId, delta);
    if (next) report(reorder.mutateAsync(next), '已调整顺序');
  };

  // 只动 shot 参数，stage 等其他参数保留
  const setShot = (shotId: number | null) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (shotId == null) next.delete('shot');
        else next.set('shot', String(shotId));
        return next;
      },
      { replace: true },
    );

  const remove = async (shotId: number) => {
    const s = shots.find((x) => x.id === shotId);
    if (!s) return;
    const ok = await confirm({
      title: `删除镜头 ${s.code}？`,
      body: '它的首帧和视频候选会一起删掉（媒体文件留在磁盘上）。',
      confirmText: '删除',
      danger: true,
    });
    if (!ok) return;
    selectMany([shotId], false);
    if (openId === shotId) {
      drawerDirty.set(false);
      setShot(null);
    }
    report(del.mutateAsync(shotId), `已删除 ${s.code}`);
  };

  return (
    <div className="stage-body shot-stage">
      <div className="toolbar">
        <span className="chip">
          {shots.length} 个镜头，共 {total.toFixed(1)} 秒
        </span>
        <label>
          候选数{' '}
          <select aria-label="候选数" value={count} onChange={(ev) => setCount(Number(ev.target.value))}>
            {[1, 2, 3, 4].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        {selCount > 0 && (
          <>
            <span className="sep" aria-hidden="true" />
            <span className="chip">
              已选 {selCount} 镜（阶段条的「下一步」只对它们）
            </span>
            <button type="button" className="sm ghost" onClick={() => setSelected(new Set())}>
              清除选择
            </button>
          </>
        )}
        <span className="grow" aria-hidden="true" />
        <button type="button" className="ghost" disabled={busy} onClick={() => report(create.mutateAsync({}), '已在末尾新增镜头')}>
          新增镜头
        </button>
      </div>
      {!(openShot && active) && notice.view}

      {shots.length === 0 ? (
        <p className="panel empty">
          这一集还没有分镜。<Link to={`${workspacePath(slug, id)}?stage=script`}>去剧本阶段生成分镜</Link>
        </p>
      ) : (
        <ShotGrid
          episodeId={id}
          shots={shots}
          blocks={ep.data.scene_blocks}
          locations={locations.data ?? []}
          characters={characters.data ?? []}
          stage={stage}
          selected={selected}
          generating={generating}
          busy={busy}
          onToggle={toggle}
          onSelectMany={selectMany}
          onOpen={setShot}
          onAction={onAction}
          onMove={move}
          onSplit={(shotId) => report(split.mutateAsync(shotId), '已拆分')}
          onInsertAfter={(shotId) => report(create.mutateAsync({ after_shot_id: shotId }), '已插入新镜头')}
          onDelete={(shotId) => void remove(shotId)}
          onRegenerated={(ids, text) => notice.track(ids, text)}
          blockEdits={work.scriptEdits}
        />
      )}

      {openShot && active && (
        <ShotDrawer
          key={openShot.id}
          shot={openShot}
          episodeId={id}
          generating={generating}
          count={count}
          onCountChange={setCount}
          notice={notice.view}
          onDirtyChange={drawerDirty.set}
          onClose={() => {
            // 抽屉自己问过了（或没修改）
            drawerDirty.set(false);
            setShot(null);
          }}
          onGenerate={(st) => void generateOne(openShot, st, hasSelected(openShot, st) ? '重拍' : '生成')}
        />
      )}
    </div>
  );
}
