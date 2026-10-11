// /api/ws 推来的事件，以及它们对界面的两种效果：reducer 里的进度/连接状态、要失效的查询。
import type { QueryKey } from '@tanstack/react-query';
import type { Job, JobStatus, QueueState } from '../api/types';

export type WsEvent =
  // event 只在队列状态变化带了具体原因时才有，目前只用在空闲自动释放显存上
  | ({ type: 'queue'; event?: string } & QueueState)
  | { type: 'job'; job: Job }
  | { type: 'log'; job_id: number; line: string }
  | { type: 'progress'; job_id: number; value: number; max: number };

export interface Progress {
  value: number;
  max: number;
}

/**
 * ComfyUI 的进度事件只覆盖采样步数，value 到 max 后 VAE 解码/保存还要跑（内存紧张时能到几分钟）。
 * 这段时间前端收不到新的进度事件，value/max 会一直停在满格——用这个判断是不是进了这个"看着卡住"的阶段。
 */
export function isDecoding(p: Progress | undefined): boolean {
  return !!p && p.max > 0 && p.value >= p.max;
}

export interface EventsState {
  connected: boolean;
  /** 连上过至少一次就一直是 true（哪怕后来断开）：区分"还没连上过"和"断线了"。 */
  everConnected: boolean;
  progress: Record<number, Progress>;
  /** 队列空闲自动释放显存那次事件的时间戳（Date.now()），没发生过是 undefined。
   * 只用来触发"已自动释放显存"提示：每次变化都当作一次新通知，值本身不重要。 */
  gpuFreedAt?: number;
}

export type EventsAction = WsEvent | { type: 'connected' } | { type: 'disconnected' };

export const initialEvents: EventsState = { connected: false, everConnected: false, progress: {} };

export function eventsReducer(state: EventsState, action: EventsAction): EventsState {
  switch (action.type) {
    case 'connected':
      return state.connected ? state : { ...state, connected: true, everConnected: true };
    case 'disconnected':
      return state.connected ? { ...state, connected: false } : state;
    case 'progress':
      return { ...state, progress: { ...state.progress, [action.job_id]: { value: action.value, max: action.max } } };
    case 'job': {
      const id = action.job.id;
      if (action.job.status === 'running' || !(id in state.progress)) return state;
      const progress = { ...state.progress };
      delete progress[id];
      return { ...state, progress };
    }
    case 'queue':
      return action.event === 'gpu_idle_freed' ? { ...state, gpuFreedAt: Date.now() } : state;
    default:
      return state;
  }
}

/** 任务停下来（完成、失败、取消、等人工输入）时，它可能改了剧集、镜头、候选、成片。 */
const SETTLED: JobStatus[] = ['done', 'failed', 'canceled', 'waiting_input'];
const LLM_TYPES = ['script', 'storyboard'];
const CHARACTER_TYPES = [
  'image.portrait',
  'character.suggest',
  'image.turnaround',
  'character.turnaround_look',
  'character.from_photo',
];
// 这些任务结束后会改一集的状态（分镜数、exported 等），项目页的剧集列表要跟着刷新
const EPISODES_TYPES = [...LLM_TYPES, 'export.episode'];

export function invalidationsFor(ev: WsEvent): QueryKey[] {
  switch (ev.type) {
    case 'job': {
      const out: QueryKey[] = [['jobs'], ['queue'], ['job', ev.job.id]];
      // 角色卡显示进行中的抽卡/推荐造型/三视图：排队、开跑、结束都要刷新角色
      if (CHARACTER_TYPES.includes(ev.job.type)) out.push(['characters']);
      if (SETTLED.includes(ev.job.status)) {
        out.push(['episode'], ['exports']);
        if (EPISODES_TYPES.includes(ev.job.type)) out.push(['episodes']);
        if (LLM_TYPES.includes(ev.job.type)) out.push(['characters'], ['locations'], ['style']);
      }
      return out;
    }
    case 'log':
      return [['job', ev.job_id]];
    default:
      return [];
  }
}

// ------------------------------------------------------------ 日志节流

/**
 * 跑视频时日志一秒能来几十行，每行都让任务详情重新拉取太浪费：同一个任务 ms 毫秒内只刷新一次。
 * 第一行立刻刷新；窗口里又来了日志，就在窗口结束时再补刷一次，保证最后几行能显示出来。
 */
export function createLogThrottle(refresh: (jobId: number) => void, ms = 500) {
  const timers = new Map<number, ReturnType<typeof setTimeout>>();
  const pending = new Set<number>();
  const open = (jobId: number) => {
    timers.set(
      jobId,
      setTimeout(() => {
        timers.delete(jobId);
        if (pending.delete(jobId)) {
          refresh(jobId);
          open(jobId);
        }
      }, ms),
    );
  };
  return {
    log(jobId: number) {
      if (timers.has(jobId)) {
        pending.add(jobId);
        return;
      }
      refresh(jobId);
      open(jobId);
    },
    dispose() {
      for (const t of timers.values()) clearTimeout(t);
      timers.clear();
      pending.clear();
    },
  };
}

// ------------------------------------------------------------ 生成任务结束的广播（右下角提示用）

/** 结束时要在右下角提示一下的任务：出图、出视频、出定妆照/三视图、合成导出。 */
export const GENERATION_TYPES = ['image.keyframe', 'video.shot', 'image.portrait', 'image.turnaround', 'export.episode'];

export function isGenerationSettled(job: Job): boolean {
  return GENERATION_TYPES.includes(job.type) && (job.status === 'done' || job.status === 'failed');
}

type JobListener = (job: Job) => void;
const jobListeners = new Set<JobListener>();

/** WS 连接在路由外面（EventsProvider），提示组件在路由里面：用一个小广播把"哪个任务结束了"递过去。 */
export function subscribeJobSettled(fn: JobListener): () => void {
  jobListeners.add(fn);
  return () => {
    jobListeners.delete(fn);
  };
}

export function emitJobSettled(job: Job): void {
  for (const fn of [...jobListeners]) fn(job);
}
