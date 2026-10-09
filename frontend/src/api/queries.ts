// 查询键与数据 hooks。页面只通过这里读写后端；WebSocket 事件按 keys 的前缀让查询失效。
import { type QueryClient, type QueryKey, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, enc } from './client';
import type {
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
  Location,
  LocationPatch,
  Project,
  ProjectIn,
  ProjectOverview,
  QueueState,
  RunIn,
  RunOut,
  SceneBlock,
  SceneBlockPatch,
  Shot,
  ShotDetail,
  ShotIn,
  ShotPatch,
  Style,
  FitKhmerIn,
  TranslateIn,
  TurnaroundSheetKey,
} from './types';

export const keys = {
  projects: ['projects'] as const,
  project: (slug: string) => ['project', slug] as const,
  engines: ['engines'] as const,
  episodes: (slug: string) => ['episodes', slug] as const,
  episode: (id: number) => ['episode', id] as const,
  // 挂在 ['episode'] 前缀下：任何任务结束（首帧、成片落地）都会让封面和统计刷新
  projectOverview: (slug: string) => ['episode', 'overview', slug] as const,
  characters: (slug: string) => ['characters', slug] as const,
  locations: (slug: string) => ['locations', slug] as const,
  style: (slug: string) => ['style', slug] as const,
  endCard: (slug: string) => ['endCard', slug] as const,
  exports: (episodeId: number) => ['exports', episodeId] as const,
  jobs: (status: string, limit: number) => ['jobs', status, limit] as const,
  job: (id: number) => ['job', id] as const,
  queue: ['queue'] as const,
  doctor: ['doctor'] as const,
  config: ['config'] as const,
};

function invalidate(qc: QueryClient, ...list: QueryKey[]): Promise<void[]> {
  return Promise.all(list.map((queryKey) => qc.invalidateQueries({ queryKey })));
}

// ------------------------------------------------------------ 查询

export function useProjects() {
  return useQuery({ queryKey: keys.projects, queryFn: () => api.get<Project[]>('/api/projects') });
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

/** status 为 '' 表示全部；WebSocket 断开时靠 5 秒轮询兜底。 */
export function useJobs(status: string, limit = 100) {
  const qs = new URLSearchParams();
  if (status) qs.set('status', status);
  qs.set('limit', String(limit));
  return useQuery({
    queryKey: keys.jobs(status, limit),
    queryFn: () => api.get<Job[]>(`/api/jobs?${qs.toString()}`),
    refetchInterval: 5000,
  });
}

export function useJob(id: number | null) {
  return useQuery({
    queryKey: keys.job(id ?? 0),
    queryFn: () => api.get<Job>(`/api/jobs/${id}`),
    enabled: id !== null && id > 0,
  });
}

export function useQueue() {
  return useQuery({ queryKey: keys.queue, queryFn: () => api.get<QueueState>('/api/queue'), refetchInterval: 10000 });
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
    mutationFn: ({ id, count, visual_prompt }: { id: number; count: number; visual_prompt?: string }) =>
      api.post<RunOut>(`/api/characters/${id}/portraits/draw`, visual_prompt ? { count, visual_prompt } : { count }),
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

export function useSuggestDesigns(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, count = 3, episode_id }: { id: number; count?: number; episode_id?: number }) =>
      api.post<RunOut>(`/api/characters/${id}/suggest`, episode_id ? { count, episode_id } : { count }),
    onSuccess: () => invalidate(qc, keys.characters(slug), ['jobs'], keys.queue),
  });
}

/** 三视图：LLM 把外貌提示词整理成从头到脚的全身描述。 */
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
    mutationFn: ({ id, sheets }: { id: number; sheets?: TurnaroundSheetKey[] }) =>
      api.post<RunOut>(`/api/characters/${id}/turnaround/generate`, sheets ? { sheets } : {}),
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
      qc.setQueryData(keys.job(job.id), job);
      return invalidate(qc, ['jobs'], keys.queue);
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

/** 手动释放 ComfyUI 显存；有 GPU 任务在跑时后端会拒绝（409）。 */
export function useFreeGpu() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<FreeGpuResult>('/api/queue/free-gpu'),
    onSuccess: () => invalidate(qc, keys.queue),
  });
}
