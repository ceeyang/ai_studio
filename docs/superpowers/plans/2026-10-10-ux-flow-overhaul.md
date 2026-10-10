# 工作台流程改造（UX 评审落地）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「剧本 → 角色 → 分镜 → 首帧 → 视频 → 导出」这条流水线好用：不丢数据、提交后结果和失败回到原处、每一步告诉用户下一步、阶段各归其位。

**Architecture:** 后端补"状态与校验"（镜头进行中/失败任务、过期原因、提交前置校验、重跑影响预估、手工镜头保护、任务上下文）；前端先建一组共用件（确认框、未保存保护、任务跟踪提示、中文名映射），再按页面接入；最后拆出「角色」「导出」两页并在剧集页头加阶段进度条。

**Tech Stack:** FastAPI + SQLAlchemy 2 + Alembic + pytest（`uv run pytest`）；React 18 + TanStack Query + react-router + Vitest（`cd frontend; npm test`）+ Playwright（`npm run e2e`）。

**Spec:** `docs/specs/2026-10-07-ai-studio-design.md`（第 6、11 节）；评审依据见本文末「评审来源」。

## Global Constraints

- UI 文案全部中文；不得出现引擎 id、任务类型 id、槽位 kind 等英文代码作为主要文案（可以放进「调试信息」折叠区）。
- 只监听 127.0.0.1、单用户；不加登录。
- 新增数据库字段必须写 Alembic 迁移（`backend/migrations/versions/`），旧数据可用。
- 每个任务结束：`uv run pytest -q` 与 `cd frontend; npm test` 全绿；改了前端要 `npm run build`（生产态由 8700 托管 `frontend/dist`）。
- 不改 `config.yaml`；不碰用户真实项目数据（`projects/`）；手动验证用临时实例（参照 `frontend/e2e/server.mjs`，端口 8712+）。
- git commit message 中文、不带任何 Claude 署名。
- 沿用现有代码风格：中文注释、`styles.css` 设计令牌、`components/Dialog.tsx` 弹窗。

## Review Focus

- 一次提交里同时有 keyframes+videos(+export) 的链式任务：前置校验不能误拒（首帧会在同一链里生成）。（Task 2 测试覆盖）
- 用户正在编辑时 WebSocket 触发 refetch：只同步用户没动过的字段，动过的保留。（Task 4、6 测试覆盖）
- 旧候选没有记录 `inputs`：过期原因要退化为「输入已变化（旧记录无法细分）」而不是报错。（Task 2 测试覆盖）
- 剧集没有镜头 / 没有角色 / 没有导出时，各页面的空状态和阶段进度条都能正常显示。（Task 8 stages 单测覆盖）
- 运行剧本时原文没变、也没勾强制：影响预估应为「不会删除任何镜头」，不要吓人。（Task 1 测试覆盖）

---

## Task 1: 后端——手工镜头保护 + 重跑影响预估

**Files:**
- Modify: `backend/domain/models.py`（Shot 加 `origin: str = "llm"`，取值 `llm` / `manual`）
- Create: 迁移 `backend/migrations/versions/<rev>_shot_origin.py`（加列，默认 `llm`；down_revision 用 `uv run alembic heads` 查当前 head）
- Modify: `backend/app/routes/shots.py`（`create_shot` 建出的镜头 `origin="manual"`；拆分复制 origin）
- Modify: `backend/pipeline/storyboard.py:107-109,138-141`（按段落删除、孤儿删除都只删 `origin == "llm"`）
- Modify: `backend/pipeline/storyboard.py` / `backend/pipeline/script.py`：把"哪些段落会重跑"的指纹判断抽成纯函数，供预估复用
- Create: `backend/pipeline/impact.py`：`rerun_impact(s, sv, episode_id, stage, force) -> RerunImpact`
- Modify: `backend/app/routes/runs.py`：`GET /api/episodes/{id}/rerun-impact?stage=script|storyboard&force=0|1`
- Modify: `backend/app/schemas.py`：`RerunImpact`
- Test: `backend/tests/test_storyboard.py`、新建 `backend/tests/test_rerun_impact.py`

**Interfaces — Produces:**
```python
class RerunImpact(BaseModel):
    stage: str                 # script / storyboard
    will_run: bool             # False = 输入没变、没勾强制，会整体跳过
    blocks: list[int]          # 会重跑的段落序号（从 1 开始）
    shots_deleted: int         # 会被删掉的镜头数（只算 origin=llm）
    selected_first_frames: int # 其中有选定首帧的
    selected_videos: int       # 其中有选定视频的
    manual_kept: int           # 手工镜头数（保留）
    overwrites: list[str]      # script 阶段会覆盖的东西，如 ["角色描述", "场景氛围"]
```
script 阶段：原文变了或强制导致段落清空时，`shots_deleted` = 全部 llm 镜头（随后分镜会删孤儿）。

- [ ] **Step 1: 写失败测试**——手工镜头在完整重跑分镜后仍在：
```python
def test_manual_shot_survives_storyboard_rerun(client, storyboarded_episode):
    ep = storyboarded_episode
    sid = client.post(f"/api/episodes/{ep}/shots", json={"action": "手工加的"}).json()["id"]
    run_storyboard(client, ep, force=True)          # 用 tests/helpers 里现成的跑法
    ids = [s["id"] for s in client.get(f"/api/episodes/{ep}").json()["shots"]]
    assert sid in ids
```
以及 `test_rerun_impact_counts`（2 个 llm 镜头，其一有选定首帧+视频；1 个手工）：force=1 时 `shots_deleted==2, selected_videos==1, manual_kept==1`；force=0 且输入没变时 `will_run is False and shots_deleted == 0`。fixture 名按 `backend/tests/conftest.py`、`helpers.py` 实际已有的来。
- [ ] **Step 2:** `uv run pytest backend/tests/test_rerun_impact.py backend/tests/test_storyboard.py -q`，确认失败。
- [ ] **Step 3:** 实现迁移、origin、删除过滤、`impact.py`（复用抽出来的段落指纹函数，不调 LLM）、路由。
- [ ] **Step 4:** 测试通过；全量 `uv run pytest -q` 通过。
- [ ] **Step 5:** commit「fix(storyboard): 手工新增的镜头重跑分镜时保留；新增重跑影响预估接口」

## Task 2: 后端——提交前置校验 + 进行中/失败状态 + 过期原因 + 任务上下文 + 健康检查

**Files:**
- Modify: `backend/pipeline/runs.py`（提交时同步拒绝）
- Modify: `backend/app/views.py`（`shot_detail` 带 `active_jobs`、`last_failure`、每个 slot 的 `stale_reason`、`new_candidates`）
- Modify: `backend/app/routes/projects.py:character_out`（加 `last_failure`）
- Modify: `backend/pipeline/keyframes.py`、`backend/pipeline/videos.py`（候选 params 里记 `inputs` 摘要）
- Modify: `backend/app/routes/jobs.py` + `schemas.py`（JobOut 加 `context`）
- Modify: `backend/engines/browser/` 下仍写「在设置页点'打开浏览器登录'」的报错：transport=extension 时改为「在装了扩展的浏览器里登录 gemini.google.com / chatgpt.com」
- Modify: `backend/app/routes/system.py`：`GET /api/system/health`
- Test: `backend/tests/test_api_runs_jobs.py`、`test_api_shots.py`、`test_character_portraits.py`、新建 `test_health.py`

**Interfaces — Produces:**
```python
class JobBrief(BaseModel):            # 已有，加字段
    id: int; type: str; status: str; sheet: str | None = None
    progress: float | None = None     # 0-1，运行中才有（取 queue manager 里的进度）
    error: str | None = None          # last_failure 里才有，取 error 第一行，截 200 字
    finished_at: datetime | None = None

class SlotOut(BaseModel):             # 已有，加字段
    stale_reason: str | None = None   # 「陈师傅 定妆照换了」「提示词改了」「引擎换了」「输入已变化（旧记录无法细分）」
    new_candidates: int = 0           # id 大于选定候选且未选定的候选数

class ShotDetail(ShotOut):            # 已有，加字段
    active_jobs: list[JobBrief] = []  # owner=shot、状态 queued/running/waiting_input
    failures: list[JobBrief] = []     # 首帧(image.keyframe)、视频(video.shot)各取最近一个 failed；
                                      # 仅当其 id 大于该 slot 选定候选的 job_id（或该 slot 没有选定）才返回

class CharacterOut:                   # 已有，加字段
    last_failure: JobBrief | None = None  # 最近 failed 的 image.portrait，且之后没有成功的 image.portrait

class JobContext(BaseModel):
    label: str                        # 「束埔寨1号铭车 · 第1集 · S05」/「束埔寨1号铭车 · 陈师傅」
    url: str | None                   # 前端路由：/projects/<slug>/episodes/<id>/director?shot=<id>；角色→/projects/<slug>/episodes/<最近一集>/cast；剧集→.../script；导出→.../export
    project_slug: str | None = None
    episode_id: int | None = None
    shot_id: int | None = None
    character_id: int | None = None

class JobOut: context: JobContext | None   # 列表和详情都带

GET /api/system/health -> {"comfyui": {"ok": bool, "message": str}, "browser_ext": {"ok": bool, "message": str}}
```
**说明：ShotDetail 用 `failures: list[JobBrief]`（不是单个 last_failure），前端按 type 放到首帧/视频格。**

提交前置校验（`submit_chain` 抛 `JobError`，路由已映射为 422 中文）：
- stages 含 `videos` 而不含 `keyframes`：所选镜头里没有选定首帧的 →「S01、S03 还没有选定首帧，先生成首帧」。
- stages 含 `export` 而不含 `videos` 和 `keyframes`：没有选定视频的镜头 →「以下镜头还没有选定视频：S01, S02」。
- 本次要用的图像/视频引擎 runner 是 ComfyUI 且健康检查连不上 →「ComfyUI 连不上（<url>），先启动 ComfyUI 或到设置页检查」。健康检查函数：ComfyUI `GET /system_stats` 1 秒超时，结果缓存 15 秒；测试里可 monkeypatch。
- 候选 `params["inputs"]`：首帧 `{"prompt", "negative", "engine", "aspect", "refs": [{"id": cid, "label": "陈师傅 定妆照"}]}`；视频再加 `"duration", "first_frame"`。`stale_reason` 按「参考图（写出变化的 label）→ 提示词 → 引擎 → 画幅 → 时长 → 首帧」取第一条；无 inputs 的旧候选给「输入已变化（旧记录无法细分）」。

- [ ] **Step 1: 写失败测试**：
  - `test_videos_without_first_frame_rejected_at_submit`：POST run stages=["videos"] → 422 含「还没有选定首帧」；stages=["keyframes","videos"] 不拒绝。
  - `test_export_without_videos_rejected_at_submit`。
  - `test_comfy_down_rejected_at_submit`（monkeypatch 健康函数为 down）。
  - `test_shot_detail_active_and_failure`：提交 keyframe → `active_jobs` 有它；失败（假 comfy 报错）→ `failures` 有 image.keyframe 且 error 非空、`active_jobs` 空；再成功出图 → `failures` 不含 image.keyframe。
  - `test_stale_reason_portrait_changed`：出首帧后换角色定妆照 → first_frame `stale` 且 `stale_reason` 含角色名。
  - `test_stale_reason_legacy_candidate`：手工造一个无 inputs 的候选 + 改提示词 → 「输入已变化（旧记录无法细分）」。
  - `test_new_candidates_count`：candidates=2 → `new_candidates == 1`。
  - `test_job_context_for_shot_job`：`context.label` 含 `S01`、`context.url` 含 `?shot=`。
  - `test_character_last_failure`。
  - `test_health_endpoint_shape`。
- [ ] **Step 2:** 跑这些测试，确认失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 测试与全量通过。
- [ ] **Step 5:** commit「feat(api): 提交前置校验、镜头/角色带进行中与失败任务、过期原因、任务上下文、健康检查」

## Task 3: 后端——导出可读化、定稿、剧集删除、项目改名与归档

**Files:**
- Modify: `backend/domain/models.py`（Export 加 `is_final: bool = False`；Project 加 `archived: bool = False`）+ 一个迁移
- Modify: 导出所在路由（`grep -rn "exports" backend/app/routes`）：`PATCH /api/exports/{id}` `{is_final: bool}`（同集只允许一个 final）；`ExportOut` 加 `label: str` 和 `is_final`。label 规则照抄前端现有拼法（`frontend/src/pages/DirectorPage.tsx:37-67` 附近），形如「9:16 · 中高字幕 · 高棉配音 · 无原声 · 片尾卡 · 37.1 秒」；列表 final 排最前，其余按 id 倒序。
- Modify: `backend/app/routes/episodes.py`：`DELETE /api/episodes/{id}`（连带镜头与槽位记录；媒体文件不删，日志记一句）
- Modify: `backend/app/routes/projects.py`：`PATCH` 支持 `title`、`archived`；`GET /api/projects` 默认不含 archived，`?archived=1` 全部返回
- Test: `backend/tests/test_api_exports.py`、`test_api_projects.py`

- [ ] Step 1 写失败测试（final 互斥与排序、label 内容、删除剧集后 404 且镜头没了、归档项目不在默认列表）→ Step 2 确认失败 → Step 3 实现 → Step 4 通过 → Step 5 commit「feat(api): 导出可读标签与定稿、删除剧集、项目改名与归档」

## Task 4: 前端共用件

**Files:**
- Create: `frontend/src/components/ConfirmDialog.tsx`：`ConfirmProvider` + `useConfirm()` → `confirm(opts: {title: string; body: ReactNode; confirmText?: string; danger?: boolean}) => Promise<boolean>`，基于现有 `Dialog`；Provider 挂在 `App.tsx`（测试用的 `frontend/src/test/render.tsx` 也要包上）。
- Modify: `frontend/src/components/Dialog.tsx`：焦点陷阱、打开聚焦第一个可聚焦元素、关闭还焦点；新 prop `guard?: () => boolean`（true=有未保存内容，Esc/点遮罩时先在弹窗底部显示「放弃修改？[放弃] [继续编辑]」）；`busy?: boolean` 时 Esc/遮罩/关闭按钮都不关。
- Modify: `frontend/src/components/EditableFields.tsx`：外部 initial 变化时按字段合并——只覆盖用户没改过的字段（`values[k] === base[k]`），改过的保留；新增 `onDirtyChange?: (dirty: boolean) => void`；导出 `useUnsavedGuard(dirty: boolean)`（`beforeunload` + react-router `useBlocker`，离开前 `window.confirm('有未保存的修改，确定离开？')`）。
- Create: `frontend/src/components/JobNotice.tsx`：`useJobNotice()` → `{view: ReactNode, track(jobIds: number[], text: string): void, error(msg: string): void, info(msg: string, opts?: {action?: {label: string; to: string}}): void, clear(): void}`。track 后订阅这些任务（`useJob`/`useJobs`），全部结束时改写为：全部 done 且有 `result.skipped` →「已跳过：输入没变，勾选强制重跑可重出」；有 failed →「失败：<第一条错误> · 查看任务」（链接 `/jobs?job=<id>`）；有 waiting_input →「在等人工输入 · 去填写」；全部 done →「完成：<text>」。成功 6 秒自动消失；错误用 `.error` 样式带关闭按钮。
- Modify: `frontend/src/format.ts`：`JOB_TYPE_LABEL` 补全所有后端任务类型（`grep -rn "HANDLERS.register" backend` 取全集），导出 `jobTypeLabel(type)`，未知回退「其他任务」；`engineLabel(name)` 统一中文名（zimage「Z-Image 快速」、h3_image「H3 图像」、krea2「Krea2」、gpt_image「ChatGPT 网页」、gemini_image「Gemini 网页」、h3_video「H3 视频（有声）」、wan22_i2v「Wan2.2 图生视频（无声）」，未知回退 id）；`VOICES` 常用音色表（zh-CN-XiaoxiaoNeural 等 4 个、km-KH-PisethNeural、km-KH-SreymomNeural）。
- Create: `frontend/src/components/HealthBanner.tsx`：轮询 `/api/system/health`（30 秒，`api/queries.ts` 加 `useHealth`），ComfyUI 或扩展不可用时任务条下方显示可关闭黄色横幅 +「去设置」。
- Modify: `frontend/src/components/TaskBar.tsx`：只有等待输入时写「N 个任务在等你输入」；「排队 0」隐藏；取消运行中任务走 `useConfirm`，正文写已运行时长；挂 `HealthBanner`。
- Modify: `frontend/src/ws/events.ts`：`log` 事件节流（同一 job 500ms 内只 invalidate 一次）；生成类任务 done/failed 时右下角 toast（新建 `components/Toasts.tsx`，最多 3 条，8 秒消失），文案「<context.label> <任务中文名>完成/失败 · 查看」，链接 `context.url`；当前 URL 已是该 url 时不弹。
- Test: `ConfirmDialog.test.tsx`、`EditableFields.test.tsx`（合并用例：改了 A 未保存，initial 的 B 变化 → A 保留、B 更新）、`JobNotice.test.tsx`（failed、skipped、done 三种）、`TaskBar.test.tsx`（只有等待输入的文案）、`format.test.ts`（后端所有任务类型都有中文名——把类型清单写成常量断言）、`Dialog` guard 用例。

**Interfaces — Produces:** `useConfirm`、`useJobNotice`、`useUnsavedGuard`、`EditableFields.onDirtyChange`、`HealthBanner`、`useHealth`、`engineLabel`、`jobTypeLabel`、`VOICES`、`Toasts`。

- [ ] Step 1 写失败测试 → Step 2 确认失败 → Step 3 实现 → Step 4 `npm test` 通过 → Step 5 commit「feat(ui): 共用确认框、未保存保护、任务跟踪提示、健康横幅、完成提示、任务与引擎中文名」

## Task 5: 剧本页瘦身 + 新「角色」页

**Files:**
- Create: `frontend/src/pages/CastPage.tsx`（路由 `/projects/:slug/episodes/:episodeId/cast`）：把 `ScriptPage.tsx` 右栏「角色 / 场景 / 风格」整块搬来，全宽；页签用 `hidden` 保持挂载（切页签不丢编辑）。
- Modify: `frontend/src/pages/ScriptPage.tsx`：只留原文 + 段落；右上一个轻量「本集角色」条（头像、名字、有无定妆照，点了去角色页）。
  - 「运行剧本」「运行分镜」：剧集已有镜头时先 GET `/api/episodes/{id}/rerun-impact`，`useConfirm` 展示「将重建段落 3、5 的镜头：删除 12 个镜头（其中 8 个有选定视频），保留 2 个手工镜头；会覆盖角色描述、场景氛围」；`will_run=false` 时不提交，直接 info「输入没变，会跳过；要重做请勾强制重跑」。
  - 原文有未保存修改时「运行剧本」变「保存并运行剧本」（先 patch 再 run）。
  - 段落展开/收起与「全部收起」用 `hidden` 保持挂载；段落 EditableFields 传 `saving`/`error`，错误显示在该段内。
  - 用 `useJobNotice`；剧本任务完成 → info「剧本好了。下一步：去角色页抽定妆照」带按钮；分镜完成 → 「下一步：去导演台生成首帧」。
  - 「目标时长」min=5 max=3600，空值不能保存。
- Modify: `frontend/src/pages/script/CharacterCard.tsx`：
  - 定妆照卡单击 = 打开 `components/Lightbox.tsx`（新建：Esc 关闭、左右键切换、显示「当前」标记）；预览里和卡片悬停时有「设为当前造型」，点了 `useConfirm`：「用这张的提示词覆盖外貌描述，出场的 N 个镜头首帧会过期」（N = 本集镜头 cast 含该角色的数量，从 episode 查询算；没有镜头时不写这句）。
  - 「设为角色提示词」同样确认。
  - `c.last_failure` 时在定妆照条末尾显示失败卡：「抽卡失败：原因」+「重试」（再提交同参数抽卡）+「查看任务」。
  - 音色改下拉（`VOICES` + 「其他（手填）」切成输入框）。
  - 卡片 EditableFields 接 `onDirtyChange`，dirty 时「设为当前造型」先提示「先保存或放弃修改」。
- Modify: `frontend/src/pages/script/TurnaroundPanel.tsx`：描述框 dirty 时「全部生成」「生成」变「保存描述并生成」；「全部生成」只提交没有进行中任务的设定图，全在跑时禁用；`hasLook` 用已保存的 `t.look || c.visual_prompt`；禁用原因写在按钮旁的可见小字。
- Modify: `frontend/src/pages/script/RecommendPanel.tsx`：jobId 存 URL 参数 `?recommend=<id>`。
- Modify: `frontend/src/styles.css`：三视图空格子去掉 `#f4f4f4` 浅底与条纹，用与 `.media-none` 一致的深色虚线占位（`.turn-sheet-media`、`.turn-cell-media`、`.turn-cand` 的空态）。
- Modify: `frontend/src/pages/ProjectsPage.tsx`：新建剧集默认 30 秒；新建剧集弹窗 `guard`（原文非空）与 `busy`。
- Modify: `frontend/src/App.tsx`：cast 路由。
- Test: `ScriptPage.test.tsx`（重跑确认内容、will_run=false 不提交、保存并运行、收起不丢编辑）、`CastPage.test.tsx`（三页签、切换不丢编辑）、`CharacterCard.test.tsx`（单击只预览、确认后才换造型、失败卡与重试）、`TurnaroundPanel.test.tsx`（保存描述并生成、进行中不重复提交）。旧测试里依赖右栏在剧本页的用例移到 CastPage 测试。

- [ ] Step 1–5，commit「feat(ui): 剧本页只管剧本、新增角色页；重跑前影响确认；定妆照预览与显式换造型；抽卡失败卡；保存并生成」

## Task 6: 导演台只管出图出视频

**Files:**
- Modify: `frontend/src/pages/DirectorPage.tsx`：
  - 工具栏：候选数、强制重跑、「生成首帧（全部/选中）」「生成视频（全部/选中）」「只重做过期的（N）」、新增镜头、「去导出（x/y 镜有视频）」。导出选项、高棉语两个按钮、成片列表、片尾卡移到导出页（Task 7 接手；本任务先把它们从导演台拿掉并保证 Task 7 前导出功能仍可从导出页到达——**本任务与 Task 7 连着做，Task 6 不提交删除导出区的改动直到 Task 7 完成**，见下方执行说明）。
  - 批量生成任务数 > 3 时 `useConfirm` 预估：「将提交 12 个首帧任务，按最近同类任务平均 58 秒，约 12 分钟，会占用显卡」——平均耗时从 `useJobs('done')` 同类型最近 20 条（started_at→finished_at）算，没有样本时写「耗时未知」。
  - 「只重做过期的」：按 stage 收集 slot `stale` 的镜头，分别提交 `shots`，force=true；数量为 0 时按钮禁用。
  - 单候选、不强制、所选镜头该格都是已出且未过期 → 不提交，info「都是最新的，勾强制重跑才会重出」。
  - `useJobNotice` 跟踪；删除镜头用 `useConfirm`（danger）。
- Modify: `frontend/src/pages/director/ShotTable.tsx`：
  - MediaCell 状态：排队中 / 生成中 xx%（`shot.active_jobs` 按 type：image.keyframe→首帧格、video.shot→视频格）/ 失败（`shot.failures` 同 type，红色角标「失败」，格子下方原因一行 +「重试」）/「N 张新候选」角标（`slot.new_candidates`，点了打开抽屉）；「已过期」角标 title 用 `stale_reason`。
  - 该格有进行中任务时对应按钮禁用并显示「生成中」。
  - 每行只显示「生成首帧」「生成视频」+「⋯」菜单（上移、下移、拆分、在后面插入、删除）；菜单为新建 `components/Menu.tsx`（aria-haspopup、方向键、Esc、点外面关闭）。
  - 视频/首帧缩略图点击打开抽屉；「最新」小圆点加 visually-hidden 文本「已选定」。
- Modify: `frontend/src/pages/director/ShotDrawer.tsx`：`aria-modal`、打开聚焦、Esc 关闭、点遮罩关闭（`.drawer-scrim` 去掉 `pointer-events:none`；有未保存修改先确认）；`key={shot.id}`；`setParams(..., {replace: true})`；EditableFields dirty 时生成按钮变「保存并生成」；候选数/强制重跑作为 props 从导演台传入并可在抽屉改；删掉「候选数和『强制重跑』用导演台工具栏上的设置」；显示 `stale_reason` 与失败原因。
- Modify: `frontend/src/pages/director/DialogueEditor.tsx`：refetch 时按句按字段合并；dirty 时不覆盖，显示「对白已被任务更新 · 载入最新」。
- Modify: `frontend/src/pages/director/CandidateGrid.tsx`：默认显示时间与 `engineLabel`；`#id · seed · 参数 JSON` 收进「高级」折叠。
- Test: `ShotTable.test.tsx`、`ShotDrawer.test.tsx`、`DirectorPage.test.tsx`、`DialogueEditor.test.tsx`、新建 `Menu.test.tsx`。

- [ ] Step 1–5，commit「feat(ui): 导演台只管生成——镜头行显示生成中/失败/新候选、只重做过期、批量预估、抽屉保存并生成」

## Task 7: 新「导出」页

**Files:**
- Create: `frontend/src/pages/ExportPage.tsx`（路由 `/projects/:slug/episodes/:episodeId/export`）：
  - 就绪检查：「6/6 镜有视频 · 2 镜视频已过期（S05、S06）」；缺视频时「合成导出」禁用并列出镜号 +「去导演台」。
  - 选项分组：字幕（中文 / 高棉语）、配音（高棉语 Edge TTS、保留原声底、配音对齐实际说话）、片尾卡（勾选 + 内联 `EndCardPanel`）；选项按剧集 id 存 localStorage（try/catch）。
  - 高棉语工具：「翻译高棉语字幕」「按高棉语时长改写中文」+ 一句说明。
  - 成片列表：后端 `label` + 时间；点列表切换主播放器；「设为定稿」（PATCH is_final），定稿置顶带标记；「高棉语待母语校对」在列表头显示一次；文件名作为下载链接小字。
  - `useJobNotice` 跟踪导出。
- Modify: `frontend/src/pages/DirectorPage.tsx`：删除 ExportsPanel、导出选项、片尾卡、高棉语按钮，相关测试移到 `ExportPage.test.tsx`。
- Modify: `frontend/src/App.tsx`：export 路由。
- Test: `ExportPage.test.tsx`（缺视频禁用并列镜号、选项持久化、设为定稿、播放切换、高棉语按钮提交）。

- [ ] Step 1–5，commit「feat(ui): 新增导出页——就绪检查、选项分组并记住、成片可读标签与定稿」

## Task 8: 信息架构——剧集页头、阶段进度、导航、任务页、项目管理、设置页

**Files:**
- Create: `frontend/src/pages/stages.ts`：纯函数 `episodeStages(ep: EpisodeDetail, chars: Character[], exports: ExportOut[]) => {stages: Stage[]; next: NextStep | null}`。
```ts
export type StageKey = 'script' | 'cast' | 'storyboard' | 'keyframes' | 'videos' | 'export';
export interface Stage { key: StageKey; label: string; state: 'done' | 'active' | 'todo' | 'stale'; detail: string; to: string }
export interface NextStep { label: string; to: string } // 如 {label: '下一步：生成视频（2 镜缺视频）', to: '.../director'}
```
  规则：script=有段落；cast=所有出场角色都有定妆照（detail「2/2 有定妆照」）；storyboard=有镜头；keyframes=全部镜头有选定首帧（detail「6/6」，有过期则 state=stale、detail 加「· 2 过期」）；videos 同理；export=有导出（detail「8 版」，有定稿写「已定稿」）。`next` 取第一个非 done 的段。
- Create: `frontend/src/components/StageStepper.tsx`、`frontend/src/components/EpisodeHeader.tsx`：面包屑「项目名 › 第 N 集 标题」（项目名链接到项目详情）、上一集/下一集（保持当前阶段页）、`StageStepper`、右侧唯一主按钮 = `next`。
- Modify: `ScriptPage`、`CastPage`、`DirectorPage`、`ExportPage` 用 `EpisodeHeader`；加载失败显示「回到项目」。
- Modify: `frontend/src/components/Layout.tsx`：流程导航 项目中心 / 剧本 / 角色 / 导演台 / 导出；导航上方显示当前上下文「项目 · 第 N 集」；在 `/projects/:slug` 时四项指向该项目最近一集；禁用项可聚焦并有可见说明。
- Modify: `frontend/src/pages/ProjectsPage.tsx`：项目卡与项目详情「⋯」菜单（改名、归档，`useConfirm`）；剧集卡删除（danger，写明镜头数）；默认不显示归档，底部「显示已归档（N）」；引擎芯片用 `engineLabel`；「目录 slug」收进「高级」；空状态里放「新建项目」按钮；剧集卡显示迷你阶段进度（复用 `episodeStages` 的 detail，若概览接口数据不足则只显示已有统计）。
- Modify: `frontend/src/pages/JobsPage.tsx`：列「# / 任务（jobTypeLabel + context.label）/ 状态 / 耗时 / 开始时间」；# 列为可聚焦按钮；详情「打开所属对象」(context.url)；错误含 ComfyUI/扩展/登录时给「去设置检查」；payload/result/模型族收进「调试信息」，日志里的 traceback 收进「完整日志」折叠；「加载更多」（limit 每次 +100）；失败筛选时「重试全部失败」（逐个 retry，`useConfirm` 写数量）；页头「04 · 任务」样式与前三页一致。
- Modify: `frontend/src/pages/SettingsPage.tsx`：页头「05 · 设置」；「环境检查」去掉 doctor；引擎表主文案 `engineLabel`，id 小字；扩展说明地址用 `window.location.host`。
- Test: `stages.test.ts`（空剧集、只有剧本、有镜头无首帧、部分过期、全部完成+定稿）、`Layout.test.tsx`、`JobsPage.test.tsx`、`ProjectsPage.test.tsx`、新建 `EpisodeHeader.test.tsx`（上一集/下一集、next 按钮）。

- [ ] Step 1–5，commit「feat(ui): 剧集页头带面包屑、切集、阶段进度与下一步；五段流程导航；任务页带上下文；项目改名归档与剧集删除」

## Task 9: 收尾——e2e、可访问性、截图验收

- Modify: `frontend/e2e/smoke.spec.ts`、`frontend/e2e/layout.spec.ts`：适配新路由与文案；加用例「ComfyUI 不通时点生成首帧 → 提交被拒、页面显示原因与去设置链接」（e2e 服务器本就把 ComfyUI 指到死地址）。
- 检查：Tab 能走完各页主要操作；图标按钮都有 aria-label；只靠颜色的状态都有文字。
- 运行：`uv run pytest -q`、`cd frontend; npm test`、`npm run e2e`、`npm run build`。
- 临时实例 + Playwright 截图（1600×950、1280×800）：项目中心、项目详情、剧本、角色、导演台（含生成中/失败/过期角标）、导出、任务、设置，存 `docs/notes/ui/2026-10-10-*.png`，逐张检查。
- commit「test(e2e): 适配新流程并覆盖提交前置校验；更新界面截图」

## 执行说明

- 顺序：1 → 2 → 3 → 4 → 5 → 6+7（同一个实现者连续做，导出页先建好再从导演台删除导出区，保证任何提交点导出功能都可达）→ 8 → 9。
- 每个任务开始前读本计划 Global Constraints 与该任务全文；读相关现有代码与测试再动手；fixture/helper 名称以仓库实际为准。

## 评审来源

1. 首轮页面评审（项目中心/剧本/导演台/任务/设置截图 + 前端代码通读）12 条。
2. 新用户旅程走查（临时实例，ComfyUI 断开）：失败不回原处、根因不在路上、前置条件不拦、下一步不提示、文案矛盾。
3. 点击路径审计 CLICK-PATH-001～016：跳过静默、新候选不进表格、失败不可见、双提交、refetch 冲掉编辑、保存前生成、页签卸载丢状态、抽屉残留、log 事件过度失效。

## 不在本计划内

拖拽排序、提示词版本历史、场景图/道具图/造型变体生成、连播与转场预览、超分补帧设置、设置页在线改配置。
