// 查询键与数据 hooks。页面只通过这里读写后端；WebSocket 事件按 keys 的前缀让查询失效。
import { type QueryClient, type QueryKey, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, enc, request } from './client';
import type {
  ApiService,
  ApiServiceIn,
  ApiTestOut,
  AppSettings,
  AppSettingsIn,
  ArchivedCandidate,
  Bgm,
  BrowserLoginOut,
  BrowserStatus,
  Candidate,
  Character,
  CharacterIn,
  CharacterPatch,
  DoctorCheck,
  EndCard,
  Engines,
  Episode,
  EpisodeDetail,
  EpisodeIn,
  EpisodePatch,
  ExportRow,
  FreeGpuResult,
  Job,
  JobTraces,
  Location,
  LocationPatch,
  Project,
  ProjectIn,
  ProjectOverview,
  ProjectPatch,
  QueueState,
  RefreshLooksOut,
  RerunImpact,
  RunIn,
  RunOut,
  SceneBlock,
  SceneBlockPatch,
  Shot,
  ShotDetail,
  ShotIn,
  ShotPatch,
  Style,
  StylePreset,
  SystemHealth,
  FitKhmerIn,
  TranslateIn,
  TurnaroundSheetKey,
} from './types';

export const keys = {
  projects: ['projects'] as const,
  // 挂在 ['projects'] 前缀下：让项目列表失效时连带归档的一起刷新
  projectsAll: ['projects', 'all'] as const,
  project: (slug: string) => ['project', slug] as const,
  engines: ['engines'] as const,
  stylePresets: ['style-presets'] as const,
  episodes: (slug: string) => ['episodes', slug] as const,
  episode: (id: number) => ['episode', id] as const,
  // 挂在 ['episode'] 前缀下：任何任务结束（首帧、成片落地）都会让封面和统计刷新
  projectOverview: (slug: string) => ['episode', 'overview', slug] as const,
  characters: (slug: string) => ['characters', slug] as const,
  locations: (slug: string) => ['locations', slug] as const,
  style: (slug: string) => ['style', slug] as const,
  endCard: (slug: string) => ['endCard', slug] as const,
  exports: (episodeId: number) => ['exports', episodeId] as const,
  bgm: (episodeId: number) => ['bgm', episodeId] as const,
  // 挂在 ['episode', id] 下：任务结束（分镜重出删了镜头）时跟着刷新
  archivedCandidates: (episodeId: number) => ['episode', episodeId, 'archived'] as const,
  rerunImpact: (episodeId: number, stage: string, blockIds: number[] | null) => ['rerunImpact', episodeId, stage, blockIds] as const,
  jobs: (status: string, limit: number) => ['jobs', status, limit] as const,
  job: (id: number) => ['job', id] as const,
  queue: ['queue'] as const,
  doctor: ['doctor'] as const,
  browserStatus: ['browserStatus'] as const,
  config: ['config'] as const,
  health: ['health'] as const,
  // 设置页「默认模型」；带项目时图片/视频步骤的生效值按项目算
  settings: (project?: string) => ['settings', project ?? ''] as const,
  // 设置页「API 服务」：OpenAI / Anthropic / Gemini 的 Key（只有掩码）、Base URL、模型名
  apiServices: ['apiServices'] as const,
};

function invalidate(qc: QueryClient, ...list: QueryKey[]): Promise<void[]> {
  return Promise.all(list.map((queryKey) => qc.invalidateQueries({ queryKey })));
}

// ------------------------------------------------------------ 查询

/** includeArchived：连归档了的一起拿（项目中心要数「已归档（N）」）；默认后端不返回归档的 */
export function useProjects(includeArchived = false) {
  return useQuery({
    queryKey: includeArchived ? keys.projectsAll : keys.projects,
    queryFn: () => api.get<Project[]>(includeArchived ? '/api/projects?archived=1' : '/api/projects'),
  });
}

export function useProject(slug: string) {
  return useQuery({
    queryKey: keys.project(slug),
    queryFn: () => api.get<Project>(`/api/projects/${enc(slug)}`),
    enabled: slug !== '',
  });
}

export function useEngines() {
  return useQuery({ queryKey: keys.engines, queryFn: () => api.get<Engines>('/api/engines'), staleTime: Infinity });
}

/** 风格预设和它推荐的引擎（新建项目对话框预选用） */
export function useStylePresets() {
  return useQuery({
    queryKey: keys.stylePresets,
    queryFn: () => api.get<StylePreset[]>('/api/style-presets'),
    staleTime: Infinity,
  });
}

export function useEpisodes(slug: string) {
  return useQuery({
    queryKey: keys.episodes(slug),
    queryFn: () => api.get<Episode[]>(`/api/projects/${enc(slug)}/episodes`),
    enabled: slug !== '',
  });
}

export function useProjectOverview(slug: string) {
  return useQuery({
    queryKey: keys.projectOverview(slug),
    queryFn: () => api.get<ProjectOverview>(`/api/projects/${enc(slug)}/overview`),
    enabled: slug !== '',
  });
}

export function useEpisode(id: number) {
  return useQuery({
    queryKey: keys.episode(id),
    queryFn: () => api.get<EpisodeDetail>(`/api/episodes/${id}`),
    enabled: id > 0,
  });
}

export function useCharacters(slug: string) {
  return useQuery({
    queryKey: keys.characters(slug),
    queryFn: () => api.get<Character[]>(`/api/projects/${enc(slug)}/characters`),
    enabled: slug !== '',
  });
}

export function useLocations(slug: string) {
  return useQuery({
    queryKey: keys.locations(slug),
    queryFn: () => api.get<Location[]>(`/api/projects/${enc(slug)}/locations`),
    enabled: slug !== '',
  });
}

export function useStyle(slug: string) {
  return useQuery({
    queryKey: keys.style(slug),
    queryFn: () => api.get<Style | null>(`/api/projects/${enc(slug)}/style`),
    enabled: slug !== '',
  });
}

export function useEndCard(slug: string) {
  return useQuery({
    queryKey: keys.endCard(slug),
    queryFn: () => api.get<EndCard | null>(`/api/projects/${enc(slug)}/end-card`),
    enabled: slug !== '',
  });
}

export function useExports(episodeId: number) {
  return useQuery({
    queryKey: keys.exports(episodeId),
    queryFn: () => api.get<ExportRow[]>(`/api/episodes/${episodeId}/exports`),
    enabled: episodeId > 0,
  });
}

/**
 * status 为 '' 表示全部；WebSocket 断开时靠 5 秒轮询兜底。
 * keepPrevious：换条数（加载更多）时先留着上一批，别让列表闪空。
 */
export function useJobs(status: string, limit = 100, keepPrevious = false) {
  const qs = new URLSearchParams();
  if (status) qs.set('status', status);
  qs.set('limit', String(limit));
  return useQuery({
    queryKey: keys.jobs(status, limit),
    queryFn: () => api.get<Job[]>(`/api/jobs?${qs.toString()}`),
    refetchInterval: 5000,
    placeholderData: keepPrevious ? (prev, prevQuery) => (prevQuery?.queryKey[1] === status ? prev : undefined) : undefined,
  });
}

/** 最近完成的任务（批量生成前估耗时用，和任务页共用缓存）；30 秒内不重拉，不轮询 */
export function useRecentDoneJobs(enabled = true) {
  return useQuery({
    queryKey: keys.jobs('done', 100),
    queryFn: () => api.get<Job[]>('/api/jobs?status=done&limit=100'),
    staleTime: 30_000,
    enabled,
  });
}

export function useJob(id: number | null) {
  return useQuery({
    queryKey: keys.job(id ?? 0),
    queryFn: () => api.get<Job>(`/api/jobs/${id}`),
    enabled: id !== null && id > 0,
  });
}

/** 任务的调用明细。查询键挂在 ['job', id] 下面：任务和日志的事件让任务详情失效时一起刷新 */
export function useJobTraces(id: number | null) {
  return useQuery({
    queryKey: [...keys.job(id ?? 0), 'traces'] as const,
    queryFn: () => api.get<JobTraces>(`/api/jobs/${id}/traces`),
    enabled: id !== null && id > 0,
  });
}

export function useQueue() {
  return useQuery({ queryKey: keys.queue, queryFn: () => api.get<QueueState>('/api/queue'), refetchInterval: 10000 });
}

/** 网页出图的浏览器：扩展连没连上、各网站是否登录。很便宜（不开页面），设置页上每 5 秒刷新。 */
export function useBrowserStatus(enabled = true) {
  return useQuery({
    queryKey: keys.browserStatus,
    queryFn: () => api.get<BrowserStatus>('/api/browser/status'),
    refetchInterval: 5000,
    retry: false,
    enabled,
  });
}

/** doctor 会连 ComfyUI、读 object_info，最长十几秒：只在打开设置页和点"重新检查"时跑。 */
export function useDoctor() {
  return useQuery({
    queryKey: keys.doctor,
    queryFn: () => api.get<DoctorCheck[]>('/api/doctor'),
    staleTime: Infinity,
    retry: false,
  });
}

/** ComfyUI、浏览器扩展能不能用：30 秒问一次；接口出错时不重试（横幅只在拿到明确的"不可用"时出现）。 */
export function useHealth() {
  return useQuery({
    queryKey: keys.health,
    queryFn: () => api.get<SystemHealth>('/api/system/health'),
    refetchInterval: 30000,
    retry: false,
  });
}

/** 默认模型设置。project 给了就按这个项目算图片/视频步骤的生效值（工作台引擎下拉框的「默认（…）」） */
export function useAppSettings(project?: string) {
  return useQuery({
    queryKey: keys.settings(project),
    queryFn: () => api.get<AppSettings>(project ? `/api/settings?project=${enc(project)}` : '/api/settings'),
    staleTime: 30000,
    retry: false,
  });
}

/** 定妆照 / 三视图这一步现在生效的引擎（拿不到设置时 undefined，调用方用原来的写法兜底） */
export function useStepDefault(project: string | undefined, step: string): string | undefined {
  // 没有项目就不问：图片/视频步骤的生效值要按项目算
  const st = useQuery({
    queryKey: keys.settings(project),
    queryFn: () => api.get<AppSettings>(`/api/settings?project=${enc(project ?? '')}`),
    staleTime: 30000,
    retry: false,
    enabled: !!project,
  });
  return st.data?.steps.find((x) => x.key === step)?.effective ?? undefined;
}

export function useSaveSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AppSettingsIn) => api.put<AppSettings>('/api/settings', body),
    onSuccess: (data) => {
      // 先用 PUT 的返回当新的基准（下拉框马上回到「没改动」），再让带项目的那些重新算
      qc.setQueryData(keys.settings(), data);
      return invalidate(qc, ['settings']);
    },
  });
}

export function useApiServices() {
  return useQuery({
    queryKey: keys.apiServices,
    queryFn: () => api.get<{ services: ApiService[] }>('/api/api-services'),
    staleTime: 30000,
    retry: false,
  });
}

function putService(qc: QueryClient, svc: ApiService) {
  qc.setQueryData<{ services: ApiService[] }>(keys.apiServices, (old) =>
    old ? { services: old.services.map((s) => (s.id === svc.id ? svc : s)) } : old,
  );
}

/** 保存一家 API 服务；Key 变了，文本模型下拉、引擎列表（能不能选）也跟着刷新 */
export function useSaveApiService() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ApiServiceIn }) => api.put<ApiService>(`/api/api-services/${enc(id)}`, body),
    onSuccess: (svc) => {
      putService(qc, svc);
      return invalidate(qc, ['settings'], keys.engines);
    },
  });
}

/** 测试连接：调这家最便宜的「列模型」接口 */
export function useTestApiService() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<ApiTestOut>(`/api/api-services/${enc(id)}/test`),
    onSuccess: (out) => putService(qc, out.service),
  });
}

export function useConfig() {
  return useQuery({
    queryKey: keys.config,
    queryFn: () => api.get<Record<string, unknown>>('/api/config'),
    staleTime: Infinity,
  });
}

// ------------------------------------------------------------ 变更

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProjectIn) => api.post<Project>('/api/projects', body),
    onSuccess: () => invalidate(qc, keys.projects),
  });
}

/** 项目改名、归档 / 取消归档 */
export function usePatchProject(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProjectPatch) => api.patch<Project>(`/api/projects/${enc(slug)}`, body),
    // 项目换了引擎：工作台里「默认（…）」要跟着变
    onSuccess: () => invalidate(qc, keys.projects, keys.project(slug), keys.settings(slug)),
  });
}

/** 删除一集（连镜头、候选、成片记录）。有排队 / 执行中的任务时后端 409。 */
export function useDeleteEpisode(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (episodeId: number) => api.del(`/api/episodes/${episodeId}`),
    onSuccess: () => invalidate(qc, keys.episodes(slug), keys.projectOverview(slug)),
  });
}

export function useCreateEpisode(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: EpisodeIn) => api.post<Episode>(`/api/projects/${enc(slug)}/episodes`, body),
    onSuccess: () => invalidate(qc, keys.episodes(slug), keys.projectOverview(slug)),
  });
}

export function usePatchEpisode(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: EpisodePatch) => api.patch<Episode>(`/api/episodes/${id}`, body),
    onSuccess: () => invalidate(qc, keys.episode(id), ['episodes']),
  });
}

export function usePatchSceneBlock(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: SceneBlockPatch & { id: number }) =>
      api.patch<SceneBlock>(`/api/scene-blocks/${id}`, body),
    onSuccess: () => invalidate(qc, keys.episode(episodeId)),
  });
}

export function usePatchCharacter(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: CharacterPatch & { id: number }) =>
      api.patch<Character>(`/api/characters/${id}`, body),
    onSuccess: () => invalidate(qc, keys.characters(slug)),
  });
}

export function useUploadPortrait(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, file }: { id: number; file: File }) => {
      const form = new FormData();
      form.append('file', file);
      return api.post<Character>(`/api/characters/${id}/portrait`, form);
    },
    onSuccess: () => invalidate(qc, keys.characters(slug)),
  });
}

/** 上传「从参考图写外观」的参考照片（不是定妆照，不当出图参考图） */
export function useUploadAppearanceRef(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, file }: { id: number; file: File }) => {
      const form = new FormData();
      form.append('file', file);
      return api.post<Character>(`/api/characters/${id}/appearance-ref`, form);
    },
    onSuccess: () => invalidate(qc, keys.characters(slug)),
  });
}

/** 让 LLM 看参考照片写外观（结果记在参考照片上，等用户确认） */
export function useAppearanceFromPhoto(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<RunOut>(`/api/characters/${id}/appearance-from-photo`, {}),
    onSuccess: () => invalidate(qc, keys.characters(slug), ['jobs'], keys.queue),
  });
}

/** 不用 LLM 按参考照片写的外观了（照片留着） */
export function useDiscardPhotoLook(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.del(`/api/characters/${id}/appearance-from-photo`),
    onSuccess: () => invalidate(qc, keys.characters(slug)),
  });
}

export function useCreateCharacter(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CharacterIn) => api.post<Character>(`/api/projects/${enc(slug)}/characters`, body),
    onSuccess: () => invalidate(qc, keys.characters(slug)),
  });
}

/** 抽卡：本地图像引擎出 count 张定妆照候选（每张一个 GPU 任务）。 */
export function useDrawPortraits(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, count, visual_prompt, engine }: { id: number; count: number; visual_prompt?: string; engine?: string }) =>
      api.post<RunOut>(`/api/characters/${id}/portraits/draw`, {
        count,
        ...(visual_prompt ? { visual_prompt } : {}),
        ...(engine ? { engine } : {}),
      }),
    onSuccess: () => invalidate(qc, keys.characters(slug), ['jobs'], keys.queue),
  });
}

/** 切换角色造型：选定这张定妆照（带提示词时角色提示词一起换）。出场镜头的首帧/视频会变过期。 */
export function useUsePortrait(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, candidateId }: { id: number; candidateId: number }) =>
      api.post<Character>(`/api/characters/${id}/portraits/${candidateId}/use`),
    onSuccess: () => invalidate(qc, keys.characters(slug), ['episode']),
  });
}

/** 把镜头里的外貌更新为当前造型：镜头里一字不差的旧外貌换成外貌占位符（出图时按景别填当前外貌）。 */
export function useRefreshShotLooks() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, old }: { id: number; old?: string[] }) =>
      api.post<RefreshLooksOut>(`/api/characters/${id}/shots/refresh-look`, { old: old ?? [] }),
    onSuccess: () => invalidate(qc, ['episode']),
  });
}

export function useSuggestDesigns(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, count = 3, episode_id }: { id: number; count?: number; episode_id?: number }) =>
      api.post<RunOut>(`/api/characters/${id}/suggest`, episode_id ? { count, episode_id } : { count }),
    onSuccess: () => invalidate(qc, keys.characters(slug), ['jobs'], keys.queue),
  });
}

/** 三视图：LLM 把外观提示词整理成从头到脚的全身描述。 */
export function useTurnaroundLook(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<RunOut>(`/api/characters/${id}/turnaround/look`, {}),
    onSuccess: () => invalidate(qc, keys.characters(slug), ['jobs'], keys.queue),
  });
}

/** 改全身描述（已出的设定图会变过期）。 */
export function usePatchTurnaroundLook(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, look }: { id: number; look: string }) =>
      api.patch<Character>(`/api/characters/${id}/turnaround/look`, { look }),
    onSuccess: () => invalidate(qc, keys.characters(slug)),
  });
}

/** 出设定图：不给 sheets 就全身 + 头像各一张，每张一个 GPU 任务。 */
export function useGenerateTurnaround(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, sheets, engine }: { id: number; sheets?: TurnaroundSheetKey[]; engine?: string }) =>
      api.post<RunOut>(`/api/characters/${id}/turnaround/generate`, {
        ...(sheets ? { sheets } : {}),
        ...(engine ? { engine } : {}),
      }),
    onSuccess: () => invalidate(qc, keys.characters(slug), ['jobs'], keys.queue),
  });
}

/** 换选定的设定图（后端立即重新切图）。用到这些格子的镜头会变过期。 */
export function useSelectTurnaroundSheet(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, sheet, candidateId }: { id: number; sheet: TurnaroundSheetKey; candidateId: number }) =>
      api.post<Character>(`/api/characters/${id}/turnaround/sheets/${sheet}/select`, { candidate_id: candidateId }),
    onSuccess: () => invalidate(qc, keys.characters(slug), ['episode']),
  });
}

/** 重新切图：auto 按人物切（找不准自动退回等宽），equal 直接等宽切。 */
export function useCropTurnaround(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, sheet, method = 'auto' }: { id: number; sheet: TurnaroundSheetKey; method?: 'auto' | 'equal' }) =>
      api.post<Character>(`/api/characters/${id}/turnaround/sheets/${sheet}/crop`, { method }),
    onSuccess: () => invalidate(qc, keys.characters(slug), ['episode']),
  });
}

export function useRecommendCharacters(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (count: number = 3) => api.post<RunOut>(`/api/episodes/${episodeId}/characters/recommend`, { count }),
    onSuccess: () => invalidate(qc, ['jobs'], keys.queue),
  });
}

export function usePatchLocation(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: LocationPatch & { id: number }) =>
      api.patch<Location>(`/api/locations/${id}`, body),
    onSuccess: () => invalidate(qc, keys.locations(slug)),
  });
}

/** 出场景图：每张一个任务（不出人物）。engine 不给：设置页场景图这一步 → 项目的图像引擎。 */
export function useDrawLocationImages(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, count, engine }: { id: number; count: number; engine?: string }) =>
      api.post<RunOut>(`/api/locations/${id}/images/draw`, { count, ...(engine ? { engine } : {}) }),
    onSuccess: () => invalidate(qc, keys.locations(slug), ['jobs'], keys.queue),
  });
}

/** 上传场景图：新图成为选定的场景图（这个场景的镜头首帧会过期）。 */
export function useUploadLocationImage(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, file }: { id: number; file: File }) => {
      const form = new FormData();
      form.append('file', file);
      return api.post<Location>(`/api/locations/${id}/image`, form);
    },
    onSuccess: () => invalidate(qc, keys.locations(slug), ['episode']),
  });
}

/** 选定一张场景图。 */
export function useUseLocationImage(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, candidateId }: { id: number; candidateId: number }) =>
      api.post<Location>(`/api/locations/${id}/images/${candidateId}/use`),
    onSuccess: () => invalidate(qc, keys.locations(slug), ['episode']),
  });
}

export function usePutStyle(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Style) => api.put<Style>(`/api/projects/${enc(slug)}/style`, body),
    // 风格锚进了每个首帧的输入哈希：改完要重新拉剧集，过期标记才会更新
    onSuccess: () => invalidate(qc, keys.style(slug), ['episode']),
  });
}

export function usePatchShot(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: ShotPatch & { id: number }) => api.patch<ShotDetail>(`/api/shots/${id}`, body),
    onSuccess: () => invalidate(qc, keys.episode(episodeId)),
  });
}

/** 镜头构图参考图：上传一张（每镜最多 2 张）。 */
export function useUploadShotRef(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, file }: { id: number; file: File }) => {
      const form = new FormData();
      form.append('file', file);
      return api.post<ShotDetail>(`/api/shots/${id}/refs`, form);
    },
    onSuccess: () => invalidate(qc, keys.episode(episodeId)),
  });
}

export function useDeleteShotRef(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, candidateId }: { id: number; candidateId: number }) =>
      request<ShotDetail>('DELETE', `/api/shots/${id}/refs/${candidateId}`),
    onSuccess: () => invalidate(qc, keys.episode(episodeId)),
  });
}

export function useCreateShot(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ShotIn) => api.post<ShotDetail>(`/api/episodes/${episodeId}/shots`, body),
    onSuccess: () => invalidate(qc, keys.episode(episodeId)),
  });
}

export function useDeleteShot(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (shotId: number) => api.del(`/api/shots/${shotId}`),
    onSuccess: () => invalidate(qc, keys.episode(episodeId)),
  });
}

export function useSplitShot(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (shotId: number) => api.post<Shot[]>(`/api/shots/${shotId}/split`),
    onSuccess: () => invalidate(qc, keys.episode(episodeId)),
  });
}

export function useReorderShots(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (shotIds: number[]) =>
      api.post<Shot[]>(`/api/episodes/${episodeId}/shots/reorder`, { shot_ids: shotIds }),
    onSuccess: () => invalidate(qc, keys.episode(episodeId)),
  });
}

/** 同一集的历史候选（新归档的在前）；enabled=false 时不请求（抽屉里收着的时候） */
export function useArchivedCandidates(episodeId: number, enabled = true) {
  return useQuery({
    queryKey: keys.archivedCandidates(episodeId),
    queryFn: () => api.get<ArchivedCandidate[]>(`/api/episodes/${episodeId}/archived-candidates`),
    enabled,
  });
}

/** 把历史候选恢复到镜头同类槽位上（槽位还没选定时就选它），并从历史里去掉 */
export function useRestoreArchived(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, shotId }: { id: number; shotId: number }) =>
      api.post<Candidate>(`/api/archived-candidates/${id}/restore`, { shot_id: shotId }),
    onSuccess: () => invalidate(qc, keys.episode(episodeId)),
  });
}

export function useSelectCandidate(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (candidateId: number) => api.post<Candidate>(`/api/candidates/${candidateId}/select`),
    onSuccess: () => invalidate(qc, keys.episode(episodeId)),
  });
}

export function usePutEndCard(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: EndCard) => api.put<EndCard>(`/api/projects/${enc(slug)}/end-card`, body),
    onSuccess: () => invalidate(qc, keys.endCard(slug)),
  });
}

/** 设为定稿 / 取消定稿（同一集只留一个定稿，后端会自动取消别的）。 */
export function usePatchExport(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, is_final }: { id: number; is_final: boolean }) =>
      api.patch<ExportRow>(`/api/exports/${id}`, { is_final }),
    onSuccess: () => invalidate(qc, keys.exports(episodeId), ['episode']),
  });
}

/** 这一集的配乐（没有时 null） */
export function useBgm(episodeId: number) {
  return useQuery({
    queryKey: keys.bgm(episodeId),
    queryFn: () => api.get<Bgm | null>(`/api/episodes/${episodeId}/bgm`),
    enabled: episodeId > 0,
  });
}

/** 上传 / 替换配乐 */
export function useUploadBgm(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append('file', file);
      return api.post<Bgm>(`/api/episodes/${episodeId}/bgm`, form);
    },
    onSuccess: (data) => {
      qc.setQueryData(keys.bgm(episodeId), data);
      return invalidate(qc, keys.bgm(episodeId));
    },
  });
}

export function useDeleteBgm(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.del(`/api/episodes/${episodeId}/bgm`),
    onSuccess: () => {
      qc.setQueryData(keys.bgm(episodeId), null);
      return invalidate(qc, keys.bgm(episodeId));
    },
  });
}

export function useFitKhmer(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: FitKhmerIn) => api.post<RunOut>(`/api/episodes/${episodeId}/dialogue/fit-khmer`, body),
    onSuccess: () => invalidate(qc, ['jobs'], keys.queue),
  });
}

export function useTranslateSubtitles(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: TranslateIn) => api.post<RunOut>(`/api/episodes/${episodeId}/subtitles/translate`, body),
    onSuccess: () => invalidate(qc, ['jobs'], keys.queue),
  });
}

/** 跑之前问一句后端：现在重跑这个阶段会不会动、会删什么（不调用 LLM）。 */
/** 现在重跑剧本 / 分镜会删什么、覆盖什么。sceneBlockIds：只算这几段（分镜）；note 有内容时后端按强制算。 */
export function fetchRerunImpact(
  episodeId: number,
  stage: 'script' | 'storyboard',
  force: boolean,
  opts: { sceneBlockIds?: number[]; note?: string } = {},
): Promise<RerunImpact> {
  let url = `/api/episodes/${episodeId}/rerun-impact?stage=${stage}&force=${force ? 1 : 0}`;
  if (opts.sceneBlockIds && opts.sceneBlockIds.length > 0) url += `&scene_block_ids=${opts.sceneBlockIds.join(',')}`;
  if (opts.note) url += `&note=${encodeURIComponent(opts.note)}`;
  return api.get<RerunImpact>(url);
}

/**
 * 重新生成对话框里的影响预估：总是按强制算（对话框提交时一定强制），所以修改意见不影响预估，
 * 查询键里也不带它（不随意见里每个字重新请求）。
 */
export function useRerunImpact(
  episodeId: number,
  stage: 'script' | 'storyboard',
  blockIds: number[] | undefined,
  enabled = true,
) {
  return useQuery({
    queryKey: keys.rerunImpact(episodeId, stage, blockIds ?? null),
    queryFn: () => fetchRerunImpact(episodeId, stage, true, { sceneBlockIds: blockIds }),
    enabled,
    staleTime: 0,
  });
}

export function useRunStage(episodeId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RunIn) => api.post<RunOut>(`/api/episodes/${episodeId}/run`, body),
    onSuccess: () => invalidate(qc, ['jobs'], keys.queue),
  });
}

export function useCancelJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: number) => api.post<{ ok: boolean }>(`/api/jobs/${jobId}/cancel`),
    onSuccess: (_r, jobId) => invalidate(qc, ['jobs'], keys.job(jobId), keys.queue),
  });
}

export function useRetryJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: number) => api.post<{ job_id: number }>(`/api/jobs/${jobId}/retry`, {}),
    onSuccess: () => invalidate(qc, ['jobs'], keys.queue),
  });
}

export function useProvideInput() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, text }: { id: number; text: string }) => api.post<Job>(`/api/jobs/${id}/input`, { text }),
    onSuccess: (job) => {
      // 先用响应立刻更新，再重拉一次：任务可能在响应回来之前就跑完了，那条推送触发的刷新会被这份旧响应盖掉
      qc.setQueryData(keys.job(job.id), job);
      return invalidate(qc, keys.job(job.id), ['jobs'], keys.queue);
    },
  });
}

export function usePauseQueue() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<QueueState>('/api/queue/pause'),
    onSuccess: (q) => qc.setQueryData(keys.queue, q),
  });
}

export function useResumeQueue() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<QueueState>('/api/queue/resume'),
    onSuccess: (q) => qc.setQueryData(keys.queue, q),
  });
}

/** 登录 ChatGPT / Gemini：扩展方式在用户自己的浏览器里打开两个网站；cdp 方式打开 studio 管的浏览器。 */
export function useOpenBrowserLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<BrowserLoginOut>('/api/browser/login'),
    onSuccess: () => invalidate(qc, keys.browserStatus),
  });
}

/** 手动释放 ComfyUI 显存；有 GPU 任务在跑时后端会拒绝（409）。 */

export function useFreeGpu() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<FreeGpuResult>('/api/queue/free-gpu'),
    onSuccess: () => invalidate(qc, keys.queue),
  });
}
