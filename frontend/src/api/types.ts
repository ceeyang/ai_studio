// 与后端 JSON 一一对应：backend/app/schemas.py、backend/queue/manager.py（job_to_dict / queue_state）、
// backend/doctor.py（Check）、GET /api/engines。改后端字段时同步改这里。

export type Aspect = '9:16' | '16:9';

export interface Project {
  id: number;
  slug: string;
  title: string;
  aspect: Aspect;
  image_engine: string;
  video_engine: string;
  seed: number;
  negative: string;
  style_bible_id: number | null;
  created_at: string;
}

export interface ProjectIn {
  title: string;
  slug?: string;
  aspect: Aspect;
  image_engine: string;
  video_engine: string;
}

export interface EngineRow {
  name: string;
  family: string;
  caps: Record<string, unknown>;
}

export interface Engines {
  image: EngineRow[];
  video: EngineRow[];
}

export interface Episode {
  id: number;
  project_id: number;
  number: number;
  title: string;
  source_text: string;
  target_seconds: number;
  status: string;
}

/** GET /api/projects/{slug}/overview：项目中心卡片用的封面和统计。 */
export interface EpisodeStats {
  id: number;
  number: number;
  title: string;
  status: string;
  shot_count: number;
  export_count: number;
  cover_url: string | null;
}

export interface ProjectOverview {
  episode_count: number;
  cover_url: string | null;
  episodes: EpisodeStats[];
}

export interface EpisodeIn {
  title: string;
  source_text: string;
  target_seconds: number;
}

export interface EpisodePatch {
  title?: string;
  source_text?: string;
  target_seconds?: number;
}

export interface SceneBlock {
  id: number;
  order: number;
  location_id: number | null;
  text: string;
}

export interface SceneBlockPatch {
  text?: string;
  location_id?: number | null;
}

export interface Candidate {
  id: number;
  slot_id: number;
  file_path: string;
  url: string;
  engine: string;
  params: Record<string, unknown>;
  input_hash: string;
  job_id: number | null;
  note: string;
  rating: number | null;
  created_at: string;
}

export interface Slot {
  id: number;
  kind: string;
  selected_candidate_id: number | null;
  stale: boolean;
  candidates: Candidate[];
}

/** km_review：ai_draft = AI 译稿，发布前要母语者校对；checked = 已校对。 */
export type KmReview = 'ai_draft' | 'checked';

export interface DialogueLine {
  speaker: string;
  zh: string;
  start: number;
  end: number;
  /** 高棉语字幕 / 配音稿 */
  km?: string | null;
  km_review?: KmReview | null;
  /** 按高棉语时长改写前的原中文（dialogue.fit_khmer 写入） */
  zh_original?: string | null;
  /** 高棉语 / 现在的中文用 Edge TTS 念出来多少秒，以及两者之比（中文 / 高棉语） */
  km_seconds?: number | null;
  zh_seconds?: number | null;
  fit_ratio?: number | null;
}

export interface CastEntry {
  character_id: number;
  variation_id: number | null;
}

export const TRANSITIONS = [
  'hard_cut',
  'dissolve_0.5',
  'white_flash_0.2',
  'black_dip_0.3',
  'fade_out_1.0',
] as const;
export type Transition = (typeof TRANSITIONS)[number];

export interface Shot {
  id: number;
  episode_id: number;
  scene_block_id: number | null;
  order: number;
  code: string;
  shot_size: string;
  camera_move: string;
  action: string;
  dialogue: DialogueLine[];
  cast: CastEntry[];
  props: unknown[];
  image_prompt: string;
  motion_prompt: string;
  audio_prompt: string;
  image_engine: string | null;
  video_engine: string | null;
  duration: number;
  use_end_frame: boolean;
  continue_from_prev: boolean;
  transition_out: string;
}

export interface ShotDetail extends Shot {
  slots: Slot[];
}

export interface EpisodeDetail extends Episode {
  scene_blocks: SceneBlock[];
  shots: ShotDetail[];
}

export interface ShotPatch {
  shot_size?: string;
  camera_move?: string;
  action?: string;
  image_prompt?: string;
  motion_prompt?: string;
  audio_prompt?: string;
  image_engine?: string | null;
  video_engine?: string | null;
  duration?: number;
  transition_out?: Transition;
  dialogue?: DialogueLine[];
}

export interface ShotIn extends ShotPatch {
  after_shot_id?: number | null;
}

export type Stage = 'script' | 'storyboard' | 'keyframes' | 'videos' | 'export' | 'all' | 'fit_khmer';
export type NextStage = 'keyframes' | 'videos' | 'export';

export type SubtitleMode = 'none' | 'zh' | 'zh+km';
export type DubMode = 'off' | 'km';
export type AlignMode = 'timeline' | 'detected';

/** 导出选项（backend/post/options.py::ExportOptions）。 */
export interface ExportOptions {
  subtitles: SubtitleMode;
  dub: DubMode;
  end_card: boolean;
  trim?: boolean;
  /** 保留原声底（压低的 H3 原音轨）；false = 原音轨整条静音 */
  bed?: boolean;
  /** detected：配音放到 H3 音轨里实际开口的位置 */
  align?: AlignMode;
}

export interface RunIn {
  stage: Stage;
  shots?: string[];
  candidates?: number;
  force?: boolean;
  priority?: number;
  export?: ExportOptions;
}

/** 按高棉语时长改写中文（POST /api/episodes/{id}/dialogue/fit-khmer）。 */
export interface FitKhmerIn {
  shot_ids?: number[];
  force?: boolean;
  then?: NextStage[];
  export?: ExportOptions;
}

export interface TranslateIn {
  force?: boolean;
  glossary?: Record<string, string>;
}

export interface EndCardLine {
  text: string;
  font: string | null;
  size: number;
  bold: boolean;
}

/** 项目片尾卡（GET/PUT /api/projects/{slug}/end-card）。字号是 1080×1920 画面上的像素。 */
export interface EndCard {
  seconds: number;
  background: string;
  lines: EndCardLine[];
}

export interface RunOut {
  job_ids: number[];
}

export interface Character {
  id: number;
  project_id: number;
  name: string;
  description: string;
  visual_prompt: string;
  core_features: string;
  voice: string | null;
  /** 选定的定妆照（首帧/视频按在场角色当参考图），没有时为 null */
  portrait_candidate_id?: number | null;
  portrait_url?: string | null;
  /** 全部定妆照候选（上传的 + 本地 AI 抽的），params.visual_prompt 记着抽卡用的提示词 */
  portraits?: Candidate[];
  /** AI 推荐造型 */
  design_options?: DesignOption[];
  /** 进行中的抽卡（image.portrait）/ 推荐造型（character.suggest）/ 三视图任务 */
  active_jobs?: JobBrief[];
  /** 三视图设定图和切出来的格子 */
  turnaround?: Turnaround | null;
}

export type TurnaroundSheetKey = 'body' | 'face';

export type TurnaroundView =
  | 'front_full'
  | 'three_quarter'
  | 'profile'
  | 'back'
  | 'face_front'
  | 'face_three_quarter'
  | 'face_profile';

/** 一种设定图：全身 4 视角（body）或头像 3 视角（face），一张图一起出，候选可切换 */
export interface TurnaroundSheet {
  sheet: TurnaroundSheetKey;
  label: string;
  /** 从左到右切出来的视角 */
  views: TurnaroundView[];
  selected_candidate_id: number | null;
  url: string | null;
  /** 选定的这张是按旧定妆照 / 旧全身描述出的 */
  stale: boolean;
  /** segment 按人物切 / equal 等宽切 / null 还没切 */
  crop_method: 'segment' | 'equal' | null;
  candidates: Candidate[];
}

/** 从选定的设定图自动切出来的单个视角，首帧 / 视频按镜头挑一格当参考图 */
export interface TurnaroundCell {
  view: TurnaroundView;
  label: string;
  sheet: TurnaroundSheetKey;
  selected_candidate_id: number | null;
  url: string | null;
  /** 切自的设定图过期了 */
  stale: boolean;
}

export interface Turnaround {
  /** 出设定图用的全身描述（英文），空表示用外貌提示词 */
  look: string;
  default_engine: string;
  sheets: TurnaroundSheet[];
  cells: TurnaroundCell[];
}

export interface DesignOption {
  label: string;
  visual_prompt: string;
  core_features: string;
}

export interface JobBrief {
  id: number;
  type: string;
  status: JobStatus;
  /** image.turnaround 任务出的是哪种设定图 */
  sheet?: TurnaroundSheetKey | null;
}

export interface CharacterIn {
  name: string;
  description?: string;
  visual_prompt?: string;
  core_features?: string;
}

/** character.recommend 任务 result.characters 的一项 */
export interface RecommendedCharacter {
  name: string;
  description: string;
  visual_prompt: string;
  core_features: string;
}

export type CharacterPatch = Partial<Pick<Character, 'name' | 'description' | 'visual_prompt' | 'core_features' | 'voice'>>;

export interface Location {
  id: number;
  project_id: number;
  name: string;
  time_of_day: string;
  atmosphere: string;
  visual_prompt: string;
}

export type LocationPatch = Partial<Pick<Location, 'name' | 'time_of_day' | 'atmosphere' | 'visual_prompt'>>;

export interface Style {
  anchor_text: string;
  palette: string;
  lighting: string;
  texture: string;
  character_rules: string;
  mood_keywords: string[];
  negative: string;
}

export type JobStatus = 'queued' | 'running' | 'waiting_input' | 'done' | 'failed' | 'canceled';

/** manual provider 抛出 NeedsInput 时写进 job.result.input_request 的内容（backend/llm/gateway.py::_manual）。 */
export interface InputRequest {
  key: string;
  task: string;
  system: string;
  user: string;
  schema?: unknown;
  error?: string;
}

export interface JobResult {
  input_request?: InputRequest;
  [key: string]: unknown;
}

export interface Job {
  id: number;
  type: string;
  status: JobStatus;
  project_id: number | null;
  owner_type: string | null;
  owner_id: number | null;
  payload: Record<string, unknown>;
  gpu: boolean;
  model_family: string | null;
  priority: number;
  depends_on: number[];
  log: string;
  error: string | null;
  result: JobResult | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface QueueCounts {
  queued: number;
  running: number;
  waiting_input: number;
}

export interface QueueState {
  paused: boolean;
  counts: QueueCounts;
  current_family: string | null;
}

export interface GpuMemSnapshot {
  vram_free_gb: number | null;
  ram_free_gb: number | null;
  process_rss_gb: number | null;
}

export interface FreeGpuResult {
  before: GpuMemSnapshot;
  after: GpuMemSnapshot;
}

export interface DoctorCheck {
  group: string;
  name: string;
  status: 'ok' | 'warn' | 'fail';
  detail: string;
}

export interface ExportRow {
  id: number;
  episode_id: number;
  variant: string;
  file_path: string;
  url: string;
  settings: Record<string, unknown>;
  created_at: string;
}
