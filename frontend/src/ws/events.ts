// /api/ws 推来的事件，以及它们对界面的两种效果：reducer 里的进度/连接状态、要失效的查询。
import type { QueryKey } from '@tanstack/react-query';
import type { Job, JobStatus, QueueState } from '../api/types';

export type WsEvent =
  | ({ type: 'queue' } & QueueState)
  | { type: 'job'; job: Job }
  | { type: 'log'; job_id: number; line: string }
  | { type: 'progress'; job_id: number; value: number; max: number };

export interface Progress {
  value: number;
  max: number;
}

export interface EventsState {
  connected: boolean;
  /** 连上过至少一次就一直是 true（哪怕后来断开）：区分"还没连上过"和"断线了"。 */
  everConnected: boolean;
  progress: Record<number, Progress>;
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
    default:
      return state;
  }
}

/** 任务停下来（完成、失败、取消、等人工输入）时，它可能改了剧集、镜头、候选、成片。 */
const SETTLED: JobStatus[] = ['done', 'failed', 'canceled', 'waiting_input'];
const LLM_TYPES = ['script', 'storyboard'];
const CHARACTER_TYPES = ['image.portrait', 'character.suggest', 'image.turnaround', 'character.turnaround_plan'];
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
