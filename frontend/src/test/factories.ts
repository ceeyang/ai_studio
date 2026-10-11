import type { Candidate, EpisodeDetail, Job, Project, ShotDetail, Slot } from '../api/types';

export function makeProject(p: Partial<Project> = {}): Project {
  return {
    id: 1, slug: 'demo', title: 'demo', aspect: '9:16', image_engine: 'zimage',
    video_engine: 'h3_video', seed: 12345, negative: '', style_preset: 'live_action', style_bible_id: null, archived: false,
    created_at: '2026-10-08T10:00:00', ...p,
  };
}

export function makeCandidate(p: Partial<Candidate> = {}): Candidate {
  return {
    id: 1, slot_id: 1, file_path: 'demo/media/first_frame/a.png',
    url: '/media/demo/media/first_frame/a.png', engine: 'zimage',
    params: { seed: 7, workflow: 'demo/runs/1/workflow.api.json' }, input_hash: 'h', job_id: 1,
    note: '', rating: null, created_at: '2026-10-08T10:00:00', ...p,
  };
}

export function makeSlot(p: Partial<Slot> = {}): Slot {
  return { id: 1, kind: 'first_frame', selected_candidate_id: null, stale: false, candidates: [], ...p };
}

export function makeShot(p: Partial<ShotDetail> = {}): ShotDetail {
  return {
    id: 1, episode_id: 1, scene_block_id: null, order: 0, code: 'S01', shot_size: '近景',
    camera_move: 'static', action: '林夏念稿', dialogue: [], cast: [], props: [],
    image_prompt: 'prompt', motion_prompt: 'motion', audio_prompt: 'audio', image_engine: null,
    video_engine: null, duration: 5, use_end_frame: false, continue_from_prev: false,
    transition_out: 'hard_cut', origin: 'manual', slots: [], ...p,
  };
}

export function makeEpisode(p: Partial<EpisodeDetail> = {}): EpisodeDetail {
  return {
    id: 1, project_id: 1, number: 1, title: '提词器', source_text: '梗概', target_seconds: 10,
    status: 'draft', scene_blocks: [], shots: [], ...p,
  };
}

export function makeJob(p: Partial<Job> = {}): Job {
  return {
    id: 1, type: 'script', status: 'queued', project_id: 1, owner_type: 'episode', owner_id: 1,
    payload: {}, gpu: false, model_family: null, priority: 0, depends_on: [], log: '',
    error: null, result: null, created_at: '2026-10-08T10:00:00Z', started_at: null,
    finished_at: null, ...p,
  };
}
