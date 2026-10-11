// 与后端 JSON 一一对应：backend/app/schemas.py、backend/queue/manager.py（job_to_dict / queue_state）、
// backend/doctor.py（Check）、GET /api/engines。改后端字段时同步改这里。

export type Aspect = '9:16' | '16:9';

/** 风格预设（backend/domain/style_presets.py） */
export type StylePresetKey = 'live_action' | 'cg_realistic' | 'anime' | 'guofeng' | 'western_comic' | 'commercial';

/** GET /api/style-presets 的一项：image_engine / video_engine 是推荐引擎里第一个可用的（只是建议） */
export interface StylePreset {
  key: StylePresetKey;
  label: string;
  description: string;
  negative: string;
  image_engines: string[];
  video_engines: string[];
  /** 推荐的定妆照引擎（设置页「角色定妆照」这一步），如真人写实的 krea2 */
  portrait_engines?: string[];
  has_storyboard_guidance: boolean;
  image_engine: string | null;
  video_engine: string | null;
  /** portrait_engines 里第一个可用的 */
  portrait_engine?: string | null;
}

export interface Project {
  id: number;
  slug: string;
  title: string;
  aspect: Aspect;
  image_engine: string;
  video_engine: string;
  seed: number;
  negative: string;
  /** 风格预设；老项目是 live_action（真人写实） */
  style_preset: StylePresetKey;
  style_bible_id: number | null;
  /** 镜头节奏：每镜几秒（3 / 4 / 5）；null = 跟随视频引擎 */
  shot_seconds?: number | null;
  /** 视频开头没接上首帧时自动换种子重拍一次 */
  auto_retry_video?: boolean;
  /** 归档了的项目默认不在项目中心显示（GET /api/projects?archived=1 才带上） */
  archived: boolean;
  created_at: string;
}

/** PATCH /api/projects/{slug}：改名、归档 / 取消归档 */
export interface ProjectPatch {
  title?: string;
  archived?: boolean;
  /** 改风格预设：负面词还是旧预设的默认值时跟着换；风格锚和外观要重新生成视觉设定才变 */
  style_preset?: StylePresetKey;
  /** 镜头节奏：每镜几秒；0 = 跟随视频引擎 */
  shot_seconds?: 0 | 3 | 4 | 5;
  auto_retry_video?: boolean;
}

export interface ProjectIn {
  title: string;
  slug?: string;
  aspect: Aspect;
  image_engine: string;
  video_engine: string;
  style_preset: StylePresetKey;
}

export interface EngineRow {
  name: string;
  /** 后端给的中文名（format.ts 的 ENGINE_LABEL 没有时用，比如 RunningHub 云端引擎） */
  label?: string;
  family: string;
  /** comfyui：本机显卡；browser：驱动已登录的 ChatGPT / Gemini 网页出图（不占显卡、没有种子）；
   *  runninghub：RunningHub 云端 */
  runtime?: 'comfyui' | 'browser' | 'runninghub';
  caps: Record<string, unknown>;
}

/** GET /api/settings 的一项：value 是设置值（null = 跟随默认），effective 是现在实际生效的 */
export interface SettingValue {
  value: string | null;
  effective: string | null;
  /** 改回「跟随默认」后会用的 */
  fallback: string | null;
  /** 设置值已经不在可选项里（config 删了 provider / 引擎停用），effective 是退回去用的 */
  invalid?: boolean;
  /** 文本模型才有：provider 的友好名字 */
  effective_label?: string | null;
  fallback_label?: string | null;
  /** step：这一步单独设置；settings：设置页全局默认；config：config.yaml；default：内置默认 / 项目 */
  source: string;
}

export type SettingKind = 'llm' | 'image' | 'video';

export interface SettingStep extends SettingValue {
  key: string;
  label: string;
  kind: SettingKind;
  /** 不带项目时：这一步没单独设置，实际跟各项目的图片引擎走（定妆照、场景图） */
  follows_project?: boolean;
}

export interface LlmChoice {
  id: string;
  label: string;
  kind: string;
  model?: string | null;
}

export interface EngineChoice {
  name: string;
  label: string;
  runtime: 'comfyui' | 'browser' | 'runninghub';
  family: string;
  enabled: boolean;
  /** false：现在提交会被拒绝（比如 RunningHub 没有 API key），原因在 note */
  ready: boolean;
  note?: string | null;
}

export interface AppSettings {
  text_model: SettingValue;
  image_engine: SettingValue;
  video_engine: SettingValue;
  steps: SettingStep[];
  /** 带了 ?project= 时：图片/视频步骤的生效值按这个项目算 */
  project: string | null;
  choices: { llm: LlmChoice[]; image: EngineChoice[]; video: EngineChoice[] };
}

/** PUT /api/settings：只传要改的；null = 跟随默认 */
export interface AppSettingsIn {
  text_model?: string | null;
  image_engine?: string | null;
  video_engine?: string | null;
  steps?: Record<string, string | null>;
}

/** POST /api/browser/login。extension：让扩展在用户自己的浏览器里打开两个网站；
 *  cdp：打开 studio 管的浏览器（launched 表示是否新启动），在两个网站的首页各开一个标签页 */
export interface BrowserLoginOut {
  transport?: 'extension' | 'cdp';
  launched: boolean;
  urls: string[];
  endpoint?: string;
  user_data_dir?: string;
}

/** 扩展报的一个网站的状态：开着几个标签页、是否登录（cookie 在不在 + 页面上有没有登录按钮） */
export interface BrowserSiteStatus {
  label: string;
  host: string;
  tabs: number | null;
  logged_in: boolean | null;
  cookie: boolean | null;
  page: 'login_wall' | 'logged_in' | 'unknown' | null;
}

export interface BrowserExtensionInfo {
  connected: boolean;
  version?: string | null;
  browser?: string | null;
  ext_id?: string;
  connected_at?: number;
  last_seen?: number;
  last_disconnect?: number | null;
}

/** GET /api/browser/status（backend/engines/browser/runner.py BrowserRunner.status） */
export interface BrowserStatus {
  transport: 'extension' | 'cdp' | null;
  running: boolean;
  logged_in: Record<string, boolean | null>;
  // extension
  connected?: boolean;
  extension?: BrowserExtensionInfo;
  extension_dir?: string;
  sites?: Record<string, BrowserSiteStatus>;
  // cdp
  executable?: string | null;
  endpoint?: string;
  user_data_dir?: string;
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

/** 历史候选：重跑分镜（或删镜头）删掉的镜头留下的首帧 / 视频候选，可以恢复到同一集的镜头上 */
export interface ArchivedCandidate {
  id: number;
  episode_id: number;
  /** 原来那个镜头的编号 */
  shot_code: string;
  kind: string;
  file_path: string;
  url: string;
  engine: string;
  params: Record<string, unknown>;
  note: string;
  /** 删之前是不是那个镜头的选定 */
  was_selected: boolean;
  /** 删之前镜头的内容（动作、提示词、对白……） */
  shot_snapshot: Record<string, unknown>;
  reason: string;
  created_at: string;
  archived_at: string;
}

export interface Slot {
  id: number;
  kind: string;
  selected_candidate_id: number | null;
  stale: boolean;
  /** 过期原因：「陈师傅 定妆照换了」「提示词改了」……；不过期时为 null */
  stale_reason?: string | null;
  /** 比选定的那张更新、还没选的候选数 */
  new_candidates?: number;
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
  /** 分镜建议这一镜接上一镜（只是建议，没打开 continue_from_prev） */
  suggest_continue?: boolean;
  transition_out: string;
  /** llm = 分镜生成的；manual = 手工添加（含拆分出来的） */
  origin: 'llm' | 'manual' | string;
}

export interface ShotDetail extends Shot {
  slots: Slot[];
  /** 排队 / 运行 / 等输入的首帧（image.keyframe）、视频（video.shot）任务 */
  active_jobs?: JobBrief[];
  /** 首帧、视频各自最近一次失败（之后没出过新的才给），每种最多一条 */
  failures?: JobBrief[];
  /** 首帧引擎完全不吃参考图时，这个镜头用不上的参考图（如「老周 定妆照」）；吃参考图时为空 */
  first_frame_refs_unused?: string[];
  /** 首帧引擎的参考图上限装不下所有出场角色的定妆照时，没带上的那几张（「小李 定妆照」）——这些人的脸只能按文字出 */
  first_frame_portraits_dropped?: string[];
  /** image_prompt 里有角色外貌占位符时，出图实际用的文字（占位符按景别换成角色当前外貌） */
  image_prompt_resolved?: string | null;
  /** image_prompt 里的外貌占位符 → 角色名（「{{char:3}}」→「老周」） */
  placeholder_names?: Record<string, string>;
  /** 构图参考图（最多 2 张，首帧参考图里排在场景图后面） */
  shot_refs?: Candidate[];
  /** 首帧引擎最多吃几张参考图；0 = 不吃参考图；引擎不存在时 null */
  image_max_refs?: number | null;
  /** 开了「接上一镜」：上一镜是哪个、有没有选定视频；第一镜或没开时 null */
  continue_prev?: ContinuePrev | null;
}

export interface ContinuePrev {
  shot_id: number;
  code: string;
  has_video: boolean;
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
  /** 接上一镜：首帧取上一镜选定视频的最后一帧 */
  continue_from_prev?: boolean;
  /** false = 不要「建议接上一镜」这条提示 */
  suggest_continue?: boolean;
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
  /** 配乐：把这一集上传的背景音乐垫在下面 */
  music?: boolean;
  /** 配乐音量（dB，-40 ~ 6） */
  music_db?: number;
  music_fade_in?: number;
  music_fade_out?: number;
  /** 有台词时自动压低配乐 */
  music_duck?: boolean;
}

/** 剧集配乐（GET/POST /api/episodes/{id}/bgm） */
export interface Bgm {
  candidate_id: number;
  url: string;
  filename: string;
  created_at: string;
}

export interface RunIn {
  stage: Stage;
  shots?: string[];
  candidates?: number;
  force?: boolean;
  priority?: number;
  export?: ExportOptions;
  /** 本阶段完成后接着跑（同一批提交，后面的任务依赖前面的），如 keyframes → videos */
  then?: NextStage[];
  /** 只重写这些段落的分镜（只对 storyboard 有效） */
  scene_block_ids?: number[];
  /** 修改意见（≤500 字），写进提示词；有内容时按强制算 */
  note?: string;
  /** 不强制的剧本/分镜运行会删掉有选定首帧/视频或改过的镜头时，用户已确认 */
  confirm?: boolean;
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
  /** 不拦提交的提醒（如出场角色还没有定妆照，锁脸引擎只能按文字出脸） */
  warnings?: string[];
  /** 说明（如只跑首帧时接上一镜的镜头等上一镜视频，这次跳过） */
  notes?: string[];
}

/** GET /api/episodes/{id}/rerun-impact：现在重跑剧本 / 分镜会删什么、覆盖什么 */
export interface RerunImpact {
  stage: 'script' | 'storyboard' | 'all';
  /** false = 输入没变、没勾强制，会整体跳过 */
  will_run: boolean;
  /** 会重跑的段落序号（从 1 开始） */
  blocks: number[];
  shots_deleted: number;
  selected_first_frames: number;
  selected_videos: number;
  /** 手工镜头数（保留） */
  manual_kept: number;
  /** 会覆盖的东西，原样显示 */
  overwrites: string[];
  /** 会删掉的镜头里生成后被改过的（改了提示词、台词、拆分出来的……） */
  edited_shots?: number;
  /** 分镜规则更新过，现有镜头按旧规则出的（不强制不会自动重跑） */
  rules_outdated?: boolean;
}

export interface Character {
  id: number;
  project_id: number;
  name: string;
  description: string;
  visual_prompt: string;
  core_features: string;
  voice: string | null;
  /** 只出声、不出镜（画外音、电话里的声音）：不抽定妆照，分镜里不给外貌占位符 */
  voice_only?: boolean;
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
  /** 最近一次失败的抽卡（之后没有成功的抽卡才有） */
  last_failure?: JobBrief | null;
  /** 「从参考图写外观」上传的参考照片（只给 LLM 看，不当出图参考图）和 LLM 写好、等确认的外观 */
  appearance_ref?: AppearanceRef | null;
}

export interface AppearanceRef {
  candidate_id: number;
  url: string;
  /** LLM 按照片写的英文外观；还没写 / 放弃了是 null */
  visual_prompt: string | null;
  core_features: string | null;
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
  /** 出设定图用的全身描述（英文），空表示用外观提示词 */
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
  /** 0-1，运行中且报过进度才有 */
  progress?: number | null;
  /** 失败任务才有：错误第一行 */
  error?: string | null;
  finished_at?: string | null;
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

/** 「把镜头里的外貌更新为当前造型」的结果；镜头写成「第1集 S03」 */
export interface RefreshLooksOut {
  /** 旧外貌换成了外貌占位符的镜头 */
  updated: string[];
  replacements: number;
  /** 本来就用占位符、自动跟着当前造型的镜头 */
  already: string[];
  /** 出场但找不到一字不差的旧外貌的镜头（分镜改写过）：要手改或重写分镜 */
  unmatched: string[];
  /** 只对上一部分（换了还会留下旧服装等外貌文字）的镜头：没有换，要手改或重写分镜 */
  partial?: string[];
  /** 跟着更新了分镜记录的段落数（换干净以后，整集运行不再要求删掉这些段重出） */
  blocks_rebased?: number;
}

export type CharacterPatch = Partial<Pick<Character, 'name' | 'description' | 'visual_prompt' | 'core_features' | 'voice' | 'voice_only'>>;

export interface Location {
  id: number;
  project_id: number;
  name: string;
  time_of_day: string;
  atmosphere: string;
  visual_prompt: string;
  /** 选定的场景图（不出人物，首帧参考图里排第一），没有时为 null */
  image_candidate_id?: number | null;
  image_url?: string | null;
  /** 选定的场景图是按旧设定（名字 / 时段 / 画面提示词）出的：设定改了，要重出 */
  image_stale?: boolean;
  /** 全部场景图候选（上传的 + AI 出的） */
  images?: Candidate[];
  /** 排队 / 生成中的场景图任务（image.location） */
  active_jobs?: JobBrief[];
  /** 最近一次出场景图失败（之后没出过新的才有） */
  last_failure?: JobBrief | null;
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
  /** 这一步要模型看图（如「从参考图写外观」）：图片的本机路径，和页面上能打开的地址 */
  images?: string[];
  image_urls?: string[];
}

export interface JobResult {
  input_request?: InputRequest;
  [key: string]: unknown;
}

/** 任务在说哪一集/哪个镜头/哪个角色：label 是给人看的名字，url 是对应页面（可能为空）。只有 HTTP 接口带，WS 事件不带。 */
export interface JobContext {
  label: string;
  url: string | null;
  project_slug: string | null;
  episode_id: number | null;
  shot_id: number | null;
  character_id: number | null;
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
  context?: JobContext | null;
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
  /** 后端拼好的可读标签：「9:16 · 中高字幕 · 高棉语配音 · 37.1 秒」 */
  label?: string;
  /** 定稿：同一集最多一个 */
  is_final?: boolean;
  created_at: string;
}

export interface HealthItem {
  ok: boolean;
  /** 一句短中文，如「连不上 127.0.0.1:8188（超时）」 */
  message: string;
  /** 原始错误文本（英文异常等），只放在悬停提示里 */
  detail?: string;
}

/** GET /api/system/health：任务条下面的横幅用，很便宜，30 秒轮询一次 */
export interface SystemHealth {
  comfyui: HealthItem;
  browser_ext: HealthItem;
}
