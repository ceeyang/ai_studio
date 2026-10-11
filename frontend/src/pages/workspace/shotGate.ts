// 首帧 / 视频阶段的「下一步」（首帧确认门，spec 4.4）：缺 / 过期的先补；首帧都就绪才「确认首帧，生成全部视频」；
// 视频都就绪就「去成片」。勾选了镜头时变成「对选中的 N 镜生成… / 重拍…」。纯函数：阶段条算字、镜头阶段照着提交，两边结果一致。
import type { Job, ShotDetail } from '../../api/types';
import { type EstimatePart, etaText } from '../director/estimate';
import { type GenStage, activeJob, cellOf, cellStatus, continues, hasSelected, waitsPrevVideo } from '../director/shotStatus';
import type { StageKey } from '../stages';

/** 超过这么多个任务才先弹预估确认 */
export const CONFIRM_OVER = 3;

export interface GateBatch {
  stage: GenStage;
  /** 镜头 id（不用 S01 这类编号：排序、拆分、删除后会变） */
  ids: number[];
  /** 已有选定结果（重拍）或过期的要强制，否则后端会跳过「已是最新」的格子 */
  force: boolean;
}

export type ShotGate =
  | {
      kind: 'run';
      /** 阶段条上「下一步：」后面的字 */
      label: string;
      /** 确认框标题 */
      title: string;
      /** 确认框里预估之后补的一句（几镜强制重出、几镜跳过） */
      note?: string;
      batches: GateBatch[];
      /** 给预估用的任务数 */
      parts: EstimatePart[];
      /** 要不要先确认：勾选的总是确认（按钮带「…」），否则超过 CONFIRM_OVER 个任务才确认 */
      confirm: boolean;
      /** 提交前先切到这个阶段（确认首帧后去视频阶段） */
      switchTo?: GenStage;
      /** 提交后提示条里的说法 */
      doneText: string;
      /** 是对勾选的镜头做的（提交后清掉勾选） */
      selection: boolean;
    }
  | { kind: 'busy'; label: string }
  | { kind: 'goto'; label: string; stage: StageKey };

const ready = (s: ShotDetail, st: GenStage) => cellStatus(s, st).mark === 'ok';
const busy = (s: ShotDetail, st: GenStage) => !!activeJob(s, st);

function partsOf(batches: GateBatch[], count: number): EstimatePart[] {
  const out: EstimatePart[] = [];
  for (const b of batches) {
    const type = cellOf(b.stage).type;
    const n = b.stage === 'keyframes' ? b.ids.length * count : b.ids.length;
    const hit = out.find((p) => p.type === type);
    if (hit) hit.n += n;
    else out.push({ type, n });
  }
  return out;
}

/**
 * 能出视频的镜头（pool 里、按集内顺序）：首帧就绪的；接上一镜的看上一镜——上一镜有视频，或上一镜也在这一批里能出视频
 * （后端按顺序串起来：上一镜视频 → 截尾帧 → 这一镜视频）。
 */
export function videoReady(pool: ShotDetail[]): Set<number> {
  const ok = new Set<number>();
  for (const s of pool) {
    if (continues(s)) {
      if (s.continue_prev!.has_video || ok.has(s.continue_prev!.shot_id)) ok.add(s.id);
    } else if (ready(s, 'keyframes')) ok.add(s.id);
  }
  return ok;
}

/**
 * 先不强制的、后强制的，空的去掉。
 * 视频里有接上一镜的镜头时合成一批（强制）：分两次提交的话，后端没法把「上一镜视频 → 截尾帧」串起来。
 * 没有视频的镜头强制也一样（没有可跳过的旧结果）。
 */
function twoBatches(stage: GenStage, fresh: ShotDetail[], forced: ShotDetail[]): GateBatch[] {
  if (stage === 'videos' && fresh.length > 0 && forced.length > 0 && [...fresh, ...forced].some(continues)) {
    const ids = new Set([...fresh, ...forced].map((s) => s.id));
    return [{ stage, ids: [...ids], force: true }];
  }
  return [
    { stage, ids: fresh.map((s) => s.id), force: false },
    { stage, ids: forced.map((s) => s.id), force: true },
  ].filter((b) => b.ids.length > 0);
}

function run(
  g: Omit<Extract<ShotGate, { kind: 'run' }>, 'kind' | 'parts' | 'confirm' | 'label'> & { label: (eta: string) => string },
  count: number,
  jobs: Job[],
): ShotGate {
  const parts = partsOf(g.batches, count);
  const tasks = parts.reduce((a, p) => a + p.n, 0);
  return { ...g, kind: 'run', label: g.label(etaText(parts, jobs)), parts, confirm: g.selection || tasks > CONFIRM_OVER };
}

const joinNote = (xs: (string | false)[]) => xs.filter(Boolean).join('；') || undefined;

/** 勾选了镜头：已有结果的重拍（强制）、没有的生成（不强制）；视频阶段首帧缺少或过期的跳过 */
function selectionGate(sel: ShotDetail[], stage: GenStage, count: number, jobs: Job[]): ShotGate {
  const { label } = cellOf(stage);
  const busyN = sel.filter((s) => busy(s, stage)).length;
  const free = sel.filter((s) => !busy(s, stage));
  const vr = videoReady(free);
  // 首帧阶段：接上一镜而上一镜还没有视频的没法取尾帧，先跳过
  const noKf = stage === 'videos' ? free.filter((s) => !vr.has(s.id)) : free.filter(waitsPrevVideo);
  const targets = free.filter((s) => !noKf.includes(s));
  if (targets.length === 0) {
    if (stage === 'keyframes' && noKf.length > 0) return { kind: 'busy', label: `等上一镜视频（选中的 ${noKf.length} 镜接上一镜）` };
    if (noKf.length > 0) return { kind: 'goto', label: `先补齐首帧（选中的 ${noKf.length} 镜首帧缺少或过期）`, stage: 'keyframes' };
    return { kind: 'busy', label: `等选中的镜头生成完（${busyN} 镜生成中）` };
  }
  const has = targets.filter((s) => hasSelected(s, stage));
  const fresh = targets.filter((s) => !hasSelected(s, stage));
  const n = targets.length;
  const verb = has.length === n ? '重拍' : fresh.length === n ? '生成' : '生成 / 重拍';
  return run(
    {
      label: () => `对选中的 ${n} 镜${has.length === n ? '重拍' : '生成'}…`,
      title: `对选中的 ${n} 镜${verb}${label}？`,
      note: joinNote([
        has.length > 0 && fresh.length > 0 && `其中 ${fresh.length} 镜还没有${label}，生成；${has.length} 镜已有${label}，重拍（强制重出新的候选）`,
        has.length === n && `都已有${label}，重拍会强制重出新的候选`,
        noKf.length > 0 &&
          (stage === 'videos' ? `另有 ${noKf.length} 镜首帧缺少或过期，先跳过` : `另有 ${noKf.length} 镜接上一镜、上一镜还没有视频，先跳过`),
        busyN > 0 && `另有 ${busyN} 镜正在生成，先跳过`,
      ]),
      batches: twoBatches(stage, fresh, has),
      doneText: `对选中的 ${n} 镜${verb}${label}`,
      selection: true,
    },
    count,
    jobs,
  );
}

/** 这一格缺 / 过期、又没在生成的镜头：缺的不强制，过期的强制 */
function todo(shots: ShotDetail[], stage: GenStage) {
  // 首帧：接上一镜而上一镜还没有视频的取不了尾帧，不算「缺」（出视频时按顺序串起来）
  const list = shots.filter((s) => !ready(s, stage) && !busy(s, stage) && !(stage === 'keyframes' && waitsPrevVideo(s)));
  return {
    list,
    fresh: list.filter((s) => cellStatus(s, stage).mark === 'none'),
    stale: list.filter((s) => cellStatus(s, stage).mark === 'stale'),
  };
}

/**
 * 首帧 / 视频阶段的下一步。没有镜头时 null（阶段条用原来的下一步）。
 * count 是候选数（首帧每镜出几张，视频每镜一个）；jobs 是最近完成的任务，用来估耗时。
 */
export function shotGate(
  shots: ShotDetail[],
  stage: GenStage,
  selected: ReadonlySet<number>,
  count: number,
  jobs: Job[],
): ShotGate | null {
  if (shots.length === 0) return null;
  const sel = shots.filter((s) => selected.has(s.id));
  if (sel.length > 0) return selectionGate(sel, stage, count, jobs);

  const kf = todo(shots, 'keyframes');
  const kfBusy = shots.filter((s) => busy(s, 'keyframes')).length;
  const vr = videoReady(shots);
  const vidReady = shots.filter((s) => vr.has(s.id));
  const vid = todo(vidReady, 'videos');
  const vidBusy = shots.filter((s) => busy(s, 'videos')).length;

  if (stage === 'keyframes') {
    if (kf.list.length > 0) {
      // 张数 = 镜头数 × 候选数（和确认框里的任务数一致）
      const n = kf.list.length * count;
      return run(
        {
          label: (eta) => `生成缺少/过期的首帧（${n} 张，${eta}）`,
          title: '生成缺少/过期的首帧？',
          note: joinNote([kf.stale.length > 0 && `其中 ${kf.stale.length} 镜已过期，强制重出`]),
          batches: twoBatches('keyframes', kf.fresh, kf.stale),
          doneText: `生成缺少/过期的首帧（${n} 张）`,
          selection: false,
        },
        count,
        jobs,
      );
    }
    if (kfBusy > 0) return { kind: 'busy', label: `等首帧生成完（${kfBusy} 镜生成中）` };
    if (vid.list.length > 0) {
      const n = vid.list.length;
      return run(
        {
          label: (eta) => `确认首帧，生成全部视频（${n} 镜，${eta}）`,
          title: '确认首帧，生成全部视频？',
          note: joinNote([vid.stale.length > 0 && `其中 ${vid.stale.length} 镜的视频已过期，强制重出`]),
          batches: twoBatches('videos', vid.fresh, vid.stale),
          switchTo: 'videos',
          doneText: `生成全部视频（${n} 镜）`,
          selection: false,
        },
        count,
        jobs,
      );
    }
    if (vidBusy > 0) return { kind: 'busy', label: `等视频生成完（${vidBusy} 镜生成中）` };
    return { kind: 'goto', label: '去成片', stage: 'export' };
  }

  const kfMissing = shots.length - vidReady.length;
  if (vid.list.length > 0) {
    const n = vid.list.length;
    return run(
      {
        label: (eta) => `生成缺少/过期的视频（${n} 个，${eta}）`,
        title: '生成缺少/过期的视频？',
        note: joinNote([
          vid.stale.length > 0 && `其中 ${vid.stale.length} 个已过期，强制重出`,
          kfMissing > 0 && `另有 ${kfMissing} 镜还没有可用的首帧，先跳过`,
        ]),
        batches: twoBatches('videos', vid.fresh, vid.stale),
        doneText: `生成缺少/过期的视频（${n} 个）`,
        selection: false,
      },
      count,
      jobs,
    );
  }
  if (vidBusy > 0) return { kind: 'busy', label: `等视频生成完（${vidBusy} 镜生成中）` };
  if (kfMissing > 0) return { kind: 'goto', label: `先补齐首帧（${kfMissing} 镜缺少或过期）`, stage: 'keyframes' };
  return { kind: 'goto', label: '去成片', stage: 'export' };
}
