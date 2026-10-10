import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api, enc, errorText } from '../api/client';
import {
  keys,
  useCreateShot,
  useDeleteShot,
  useEpisode,
  useReorderShots,
  useRunStage,
  useSplitShot,
} from '../api/queries';
import type { Job, RunIn, ShotDetail } from '../api/types';
import { useConfirm } from '../components/ConfirmDialog';
import { EpisodeHeader, EpisodeLoadError, nextActionProps, useNextAction } from '../components/EpisodeHeader';
import { useJobNotice } from '../components/JobNotice';
import { type EstimatePart, estimateText } from './director/estimate';
import { ShotDrawer } from './director/ShotDrawer';
import { CELL, type GenStage, ShotTable, hasSelected } from './director/ShotTable';
import { moveId, selectedOf, slotMark, slotOf } from './director/slots';

/** 超过这么多个任务才先弹预估确认 */
const CONFIRM_OVER = 3;

const cellOf = (stage: GenStage) => (stage === 'keyframes' ? CELL.first_frame : CELL.video);
const kindOf = (stage: GenStage) => (stage === 'keyframes' ? 'first_frame' : 'video');

/** 这些镜头这一格过期了（选定的那张是按旧输入出的） */
function staleIds(shots: ShotDetail[], stage: GenStage): number[] {
  return shots.filter((s) => slotMark(slotOf(s, kindOf(stage))) === 'stale').map((s) => s.id);
}

interface Batch {
  stage: GenStage;
  ids: number[];
  force: boolean;
  /** 接着跑的阶段：重出首帧后接着按新首帧重出视频 */
  then?: GenStage[];
}

/** 一批会产生多少个任务：首帧每镜 × 候选数，视频每镜一个（后端视频不按候选数出） */
function batchParts(b: Batch, count: number): EstimatePart[] {
  const parts: EstimatePart[] = [];
  for (const st of [b.stage, ...(b.then ?? [])]) {
    parts.push({ type: cellOf(st).type, n: st === 'keyframes' ? b.ids.length * count : b.ids.length });
  }
  return parts;
}

/** 把几批的任务数按类型合起来，给预估用 */
function mergeParts(list: EstimatePart[]): EstimatePart[] {
  const out: EstimatePart[] = [];
  for (const p of list) {
    const hit = out.find((x) => x.type === p.type);
    if (hit) hit.n += p.n;
    else out.push({ ...p });
  }
  return out;
}

export function DirectorPage() {
  const { slug = '', episodeId = '' } = useParams();
  const id = Number(episodeId);
  const [params, setParams] = useSearchParams();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const notice = useJobNotice();
  const ep = useEpisode(id);
  const run = useRunStage(id);
  const reorder = useReorderShots(id);
  const split = useSplitShot(id);
  const del = useDeleteShot(id);
  const create = useCreateShot(id);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [count, setCount] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  // 下一步在导演台时：缺首帧 / 缺视频 → 对应的生成按钮，有过期 → 只重做过期的，是本页唯一的主按钮
  const nextAct = useNextAction(slug, ep.data, 'director');

  if (ep.isLoading) return <p className="loading">加载中…</p>;
  if (ep.error || !ep.data) return <EpisodeLoadError slug={slug} error={ep.error} />;
  const e = ep.data;
  const shots = e.shots;
  const openId = Number(params.get('shot')) || null;
  const openShot = shots.find((s) => s.id === openId) ?? null;
  const busy = reorder.isPending || split.isPending || del.isPending || create.isPending;
  const generating = run.isPending || submitting;
  const selIds = shots.filter((s) => selected.has(s.id)).map((s) => s.id);
  const scope = selIds.length > 0 ? `选中 ${selIds.length} 镜` : '全部';
  const total = shots.reduce((a, s) => a + s.duration, 0);
  const withVideo = shots.filter((s) => selectedOf(slotOf(s, 'video'))).length;
  const staleKf = staleIds(shots, 'keyframes');
  const staleVid = staleIds(shots, 'videos');
  const staleCount = staleKf.length + staleVid.length;

  const report = (p: Promise<unknown>, ok: string) => {
    p.then(
      () => notice.info(ok),
      (err: unknown) => notice.error(errorText(err)),
    );
  };

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

  /** 任务数超过 CONFIRM_OVER 时先弹预估；返回要不要继续。 */
  const confirmBatch = async (title: string, parts: EstimatePart[]): Promise<boolean> => {
    const n = parts.reduce((a, p) => a + p.n, 0);
    if (n <= CONFIRM_OVER) return true;
    setSubmitting(true);
    const jobs = await doneJobs();
    setSubmitting(false);
    return confirm({ title, body: estimateText(parts, jobs), confirmText: '继续提交' });
  };

  /** 依次提交几批（镜头用 id 而不是 S01 这类编号：编号在排序、拆分、删除后会变），合起来跟踪。 */
  const submit = async (batches: Batch[], text: string) => {
    const ids: number[] = [];
    setSubmitting(true);
    try {
      for (const b of batches) {
        const body: RunIn = { stage: b.stage, candidates: count, force: b.force };
        if (b.ids.length > 0) body.shots = b.ids.map(String);
        if (b.then && b.then.length > 0) body.then = b.then;
        const r = await run.mutateAsync(body);
        ids.push(...r.job_ids);
      }
      notice.track(ids, text);
    } catch (err) {
      // 前面几批已经提交了的也跟踪上，错误一起说
      notice.error(ids.length > 0 ? `${errorText(err)}（已提交的 ${ids.length} 个任务照常进行）` : errorText(err));
    } finally {
      setSubmitting(false);
    }
  };

  /**
   * 提交生成。verb：「生成」不强制（后端跳过已是最新的格子）；「重拍」「重试」强制重出
   * （重拍 = 这一格已有选定结果还要再出；重试 = 失败后再来，不强制的话后端看选定的结果还是最新的会跳过）。
   */
  const generate = async (stage: GenStage, ids: number[], what: string, verb: '生成' | '重拍' | '重试' = '生成') => {
    notice.clear();
    const cell = cellOf(stage);
    const targets = ids.length > 0 ? shots.filter((s) => ids.includes(s.id)) : shots;
    const batch: Batch = { stage, ids, force: verb !== '生成' };
    if (!(await confirmBatch(`批量生成${cell.label}？`, batchParts({ ...batch, ids: targets.map((s) => s.id) }, count)))) return;
    await submit([batch], `${verb}${cell.label}${what}`);
  };

  /** 单个镜头的格子 / 抽屉按钮：已有选定结果就是重拍（强制），否则是生成 */
  const generateOne = (shotId: number, stage: GenStage) => {
    const s = shots.find((x) => x.id === shotId);
    if (!s) return;
    void generate(stage, [shotId], ` ${s.code}`, hasSelected(s, stage) ? '重拍' : '生成');
  };

  const redoStale = async () => {
    notice.clear();
    // 首帧过期的镜头：首帧接着视频一起重出（视频依赖新首帧）；只有视频过期的单独重出视频
    const videoOnly = staleVid.filter((sid) => !staleKf.includes(sid));
    const batches: Batch[] = [
      { stage: 'keyframes' as const, ids: staleKf, force: true, then: ['videos' as const] },
      { stage: 'videos' as const, ids: videoOnly, force: true },
    ].filter((b) => b.ids.length > 0);
    if (batches.length === 0) return;
    const parts = mergeParts(batches.flatMap((b) => batchParts(b, count)));
    if (!(await confirmBatch('重做过期的格子？', parts))) return;
    await submit(batches, `只重做过期的（${staleCount}）`);
  };

  const toggle = (shotId: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(shotId)) next.delete(shotId);
      else next.add(shotId);
      return next;
    });
  const toggleAll = () =>
    setSelected((prev) =>
      shots.every((s) => prev.has(s.id)) ? new Set<number>() : new Set<number>(shots.map((s) => s.id)),
    );

  const move = (shotId: number, delta: -1 | 1) => {
    const next = moveId(shots.map((s) => s.id), shotId, delta);
    if (next) report(reorder.mutateAsync(next), '已调整顺序');
  };

  const open = (shotId: number) => setParams({ shot: String(shotId) }, { replace: true });
  const close = () => setParams({}, { replace: true });

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
    setSelected((prev) => {
      const next = new Set(prev);
      next.delete(shotId);
      return next;
    });
    if (openId === shotId) close();
    report(del.mutateAsync(shotId), `已删除 ${s.code}`);
  };

  return (
    <div className="page">
      <EpisodeHeader slug={slug} ep={e} page="director" title="导演台">
        <span className="chip">
          {shots.length} 个镜头，共 {total.toFixed(1)} 秒
        </span>
      </EpisodeHeader>

      <div className="toolbar">
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
        <span className="sep" aria-hidden="true" />
        <button
          type="button"
          {...nextActionProps(nextAct?.key === 'keyframes' && !nextAct.stale)}
          disabled={shots.length === 0 || generating}
          onClick={() => void generate('keyframes', selIds, `（${scope}）`)}
        >
          生成首帧（{scope}）
        </button>
        <button
          type="button"
          {...nextActionProps(nextAct?.key === 'videos' && !nextAct.stale)}
          disabled={shots.length === 0 || generating}
          onClick={() => void generate('videos', selIds, `（${scope}）`)}
        >
          生成视频（{scope}）
        </button>
        <button
          type="button"
          {...nextActionProps(!!nextAct?.stale)}
          disabled={staleCount === 0 || generating}
          title={staleCount === 0 ? '没有过期的首帧或视频' : '把过期的首帧、视频按当前输入强制重出'}
          onClick={() => void redoStale()}
        >
          只重做过期的（{staleCount}）
        </button>
        <span className="grow" aria-hidden="true" />
        <button type="button" className="ghost" disabled={busy} onClick={() => report(create.mutateAsync({}), '已在末尾新增镜头')}>
          新增镜头
        </button>
        <Link className="btn" to={`/projects/${enc(slug)}/episodes/${id}/export`}>
          去导出（{withVideo}/{shots.length} 镜有视频）
        </Link>
      </div>
      {!openShot && notice.view}

      <div className="director-body">
        {shots.length === 0 ? (
          <p className="panel empty">
            这一集还没有分镜。<Link to={`/projects/${enc(slug)}/episodes/${id}/script`}>去剧本页生成分镜</Link>
          </p>
        ) : (
          <ShotTable
            shots={shots}
            selected={selected}
            busy={busy}
            generating={generating}
            onToggle={toggle}
            onToggleAll={toggleAll}
            onOpen={open}
            onMove={move}
            onSplit={(shotId) => report(split.mutateAsync(shotId), '已拆分')}
            onInsertAfter={(shotId) => report(create.mutateAsync({ after_shot_id: shotId }), '已插入新镜头')}
            onDelete={(shotId) => void remove(shotId)}
            onGenerate={generateOne}
            onRetry={(shotId, stage) => {
              const code = shots.find((s) => s.id === shotId)?.code ?? '';
              void generate(stage, [shotId], ` ${code}`, '重试');
            }}
          />
        )}
      </div>

      {openShot && (
        <ShotDrawer
          key={openShot.id}
          shot={openShot}
          episodeId={id}
          generating={generating}
          count={count}
          onCountChange={setCount}
          notice={notice.view}
          onClose={close}
          onGenerate={(stage) => generateOne(openShot.id, stage)}
        />
      )}
    </div>
  );
}
