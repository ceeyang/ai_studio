# 单集工作台 v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 剧本/分镜可以明确地「重新生成」（整集、按段、带修改意见、先看影响），并把一集的剧本/角色/导演台/导出四页合成一个单集工作台（阶段条 + 左栏 + 主区 + 抽屉 + 首帧确认门）。

**Architecture:** 后端只扩 `RunIn`（`scene_block_ids`、`note`）、提示词模板和影响预估参数；前端新建 `EpisodeWorkspace` 页面，把现有页面的主体拆成可嵌入的组件（剧本体、角色抽屉、镜头卡、导出体），旧路由重定向。

**Tech Stack:** FastAPI + pytest（`uv run pytest`）；React 18 + react-router 数据路由 + TanStack Query + Vitest（`cd frontend; npm test`）+ Playwright（`npm run e2e`）。

**Spec:** `docs/specs/2026-10-10-episode-workspace-v2.md`

## Global Constraints

- UI 文案全中文；不出现引擎 id、任务类型 id 作为主要文案。
- 全页只有一个 `.primary` 主按钮（阶段条的「下一步」）；卡片、工具栏用普通/次要样式。
- 不改 `config.yaml`；不碰 `projects/`（真实数据）；不重启/停止 8700 上的服务；手动验证用临时实例（参照 `frontend/e2e/server.mjs`，端口 8712+），用完清理。
- 新数据库字段需 Alembic 迁移（本计划预计不需要）。
- 每个任务结束：`uv run pytest -q` 全绿（0 失败）、`cd frontend; npm test` 全绿、`npm run build`。
- git commit message 中文，不带任何 Claude 署名。
- 沿用现有共用件：useConfirm、Dialog（guard/busy）、EditableFields（onDirtyChange/useUnsavedGuard）、useJobNotice、Toasts、Lightbox、Menu、`pages/director/estimate.ts`、`format.ts`。

## Review Focus

- 带 `note` 的重新生成即使输入没变也必须真正重跑（不能被指纹跳过）。（Task 1 测试）
- 按段重写分镜只动所选段落的 llm 镜头；其他段落、手工镜头、已选定候选不动。（Task 1 测试）
- 旧深链 `/projects/x/episodes/5/director?shot=12` 重定向后仍打开第 12 镜抽屉。（Task 3 测试）
- 首帧确认门：有过期/缺失首帧时「下一步」不能是「生成全部视频」。（Task 4 测试）
- 主区切换阶段时未保存的编辑不丢（离开拦截或保持挂载）。（Task 3 测试）

---

### Task 1: 后端——重新生成参数（scene_block_ids、note）与提示词

**Files:**
- Modify: `backend/app/schemas.py`（`RunIn` 加 `scene_block_ids: list[int] | None = None`、`note: str | None = Field(None, max_length=500)`）
- Modify: `backend/app/routes/runs.py`（透传到 `RunOptions`；`rerun-impact` 接受 `scene_block_ids`（逗号分隔 int）与 `note`）
- Modify: `backend/pipeline/runs.py`（`RunOptions` 加两个字段；script/storyboard payload 带上 `note`、storyboard 带 `scene_block_ids`；`note` 非空时 `force=True`；`scene_block_ids` 只允许 stage=storyboard，否则 JobError「只有分镜能按段重写」；所给 id 不属于本集 → JobError）
- Modify: `backend/pipeline/script.py`、`backend/pipeline/storyboard.py`、`backend/pipeline/impact.py`（`*_prompt_fp` 接受 `note`（及 storyboard 的 `previous_shots`）并传给模板；impact 按 `scene_block_ids` 只计这些段、有 note 视为 force）
- Modify: `backend/prompts/script_structure.md`、`script_visual.md`、`storyboard.md`（user 段末尾：`{% if note %}\n修改意见：{{ note }}\n在满足以上全部要求的前提下按修改意见调整。{% endif %}`；storyboard 另加 `{% if previous_shots %}上一版本段镜头：…{% endif %}`，每镜一行「动作｜台词」，只在有 note 时提供）
- Test: `backend/tests/test_script.py`、`test_storyboard.py`、`test_rerun_impact.py`、`test_api_runs_jobs.py`

**Interfaces — Produces:** POST `/api/episodes/{id}/run` body 新字段 `scene_block_ids?: number[]`、`note?: string`；GET `/api/episodes/{id}/rerun-impact?stage=storyboard&force=0&scene_block_ids=3,5&note=...`。

- [ ] Step 1: 写失败测试——(a) 输入没变、force=false、note="更紧凑" → script 与 storyboard 都真正调用 LLM（假 LLM 记录到的 user 提示词含「修改意见：更紧凑」）；(b) storyboard `scene_block_ids=[第2段]` → 只第 2 段的 llm 镜头被替换，第 1 段镜头 id 不变、手工镜头保留；(c) 有 note 时 storyboard 提示词含上一版镜头的动作；(d) `scene_block_ids` 配 stage=script → 400；别集的段落 id → 400；(e) rerun-impact 按段只计该段、带 note 时 will_run=True。
- [ ] Step 2: 跑测试确认失败。
- [ ] Step 3: 实现。
- [ ] Step 4: 全量 `uv run pytest -q` 通过。
- [ ] Step 5: commit「feat(api): 剧本/分镜重新生成——按段重写分镜、修改意见写进提示词、影响预估按段」

### Task 2: 前端——重新生成对话框，去掉「强制重跑」

**Files:**
- Create: `frontend/src/components/RegenerateDialog.tsx`：props `{open: boolean, onClose: () => void, stage: 'script'|'storyboard', episodeId: number, blockIds?: number[], scopeLabel: string, beforeRun?: () => Promise<boolean>, onSubmitted: (jobIds: number[]) => void}`。内含「修改意见（可选）」textarea（max 500，占位见 spec 3.2）、影响预估（查询键含 episodeId、stage、blockIds、是否有意见，不随每个字重新请求）、确认按钮「重新生成」（会删除有选定视频的镜头时 danger）。确认时先 `beforeRun`（保存未保存的原文/段落，返回 false 则停），再 POST run `{stage, force: true, note, scene_block_ids}`。
- Modify: `frontend/src/api/types.ts`（RunIn 加 `scene_block_ids?`、`note?`）、`api/queries.ts`（rerun-impact 查询带新参数）
- Modify: `frontend/src/pages/ScriptPage.tsx`：删掉「强制重跑」勾选框；无段落「生成剧本」、有段落「重新生成剧本…」（打开对话框）；无镜头「生成分镜」、有镜头「重新生成分镜…」；每个段落行加「重写本段分镜…」（该段有镜头时显示）。首次生成路径保持现有影响确认逻辑。
- Modify: `frontend/src/pages/DirectorPage.tsx` / `director/ShotTable.tsx` / `director/ShotDrawer.tsx`：删掉「强制重跑」勾选框；已有结果的格子按钮和抽屉按钮改名「重拍首帧」「重拍视频」并以 force=true 提交；未出过的仍「生成…」（force=false）。「都是最新的」预检随之删除。
- Test: `RegenerateDialog.test.tsx`（无意见/有意见的提交体、影响文案、beforeRun=false 不提交、按段时 body 含 scene_block_ids）、`ScriptPage.test.tsx`、`DirectorPage.test.tsx`、`ShotTable.test.tsx`、`ShotDrawer.test.tsx` 更新。

- [ ] Step 1–5 同上，commit「feat(ui): 剧本/分镜「重新生成…」对话框（整集/本段、修改意见、影响预估），去掉强制重跑，重拍即强制」

### Task 3: 单集工作台骨架（路由、阶段条、左栏、剧本与成片阶段）

**Files:**
- Create: `frontend/src/pages/EpisodeWorkspace.tsx`（路由 `/projects/:slug/episodes/:episodeId`）：页头（复用 `EpisodeHeader` 的面包屑与上一集/下一集）、`StageBar`（五段，点击切换 `?stage=`）、左栏 `WorkspaceRail`（可折叠；剧本大纲：原文一行摘要 + 段落列表；角色列表：头像/名字/状态，点 → `?char=<id>`）、主区按 stage 渲染。默认 stage = stages 计算出的下一步所在阶段。
- Create: `frontend/src/pages/workspace/StageBar.tsx`、`WorkspaceRail.tsx`
- Modify: `frontend/src/pages/stages.ts`：五段 `script | cast | keyframes | videos | export`（剧本=有段落且有镜头）；`next` 规则见 spec 4.4，返回 `{label, stage, action: 'goto'|'run', count}` 供阶段条生成文案与动作；保持纯函数并更新测试。
- Refactor: `ScriptPage.tsx` 主体抽成 `pages/workspace/ScriptStage.tsx`（不含页头）；`ExportPage.tsx` 主体抽成 `pages/workspace/ExportStage.tsx`。
- Modify: `frontend/src/App.tsx`：新路由；`/script` `/cast` `/director` `/export` 重定向到 `/projects/:slug/episodes/:id?stage=...`（script→script，cast→cast，director→keyframes，export→export；保留原有 search 参数）。
- Modify: `frontend/src/components/Layout.tsx`：「制作流程」只留 项目中心 + 「当前集」（指向工作台）；上下文行保留。
- Modify: 项目中心/项目详情、任务 context.url 消费处（Toasts、JobsPage「打开所属对象」）的链接：前端统一把旧形式 url 交给路由重定向即可，无需改后端。
- 本任务里 cast/keyframes/videos 三个 stage 暂时渲染现有组件主体（CastPage、DirectorPage 去掉页头），保证功能不丢；Task 4、5 替换。
- 切换 stage 时用 `hidden` 保持已访问过的主区挂载，或依赖全局离开拦截（同一路由内切 search 参数不触发 useBlocker——需在切 stage 时自行检查脏状态并 useConfirm）。
- Test: `EpisodeWorkspace.test.tsx`（stage 切换、旧路由重定向保留 ?shot=、左栏点角色设置 ?char=、有未保存修改时切 stage 先确认）、`stages.test.ts` 更新、Layout/Projects 测试更新。

- [ ] Step 1–5，commit「feat(ui): 单集工作台骨架——阶段条、左栏大纲与角色、剧本/成片阶段，旧页面重定向」

### Task 4: 镜头卡片网格与首帧确认门

**Files:**
- Create: `frontend/src/pages/workspace/ShotGrid.tsx`、`ShotCard.tsx`：按段落分组（组头含「重写本段分镜…」→ RegenerateDialog），「手工添加」组；卡片内容与状态见 spec 4.3（先把 ShotTable MediaCell 的状态逻辑抽成 `pages/director/shotStatus.ts` 纯函数并单测）；一个主按钮（普通样式）+ `Menu`「⋯」；卡片左上角勾选多选。
- `EpisodeWorkspace` 的 keyframes/videos stage 渲染 ShotGrid（首帧阶段显示首帧，视频阶段显示视频）；点卡片打开现有 `ShotDrawer`（`?shot=`）。
- 阶段条「下一步」实现首帧确认门（spec 4.4），批量提交前沿用 `estimate.ts` + `useConfirm`（>3 个任务时）；多选时变「对选中的 N 镜生成…」。
- 删除旧 `DirectorPage.tsx` 页面壳与 `ShotTable.tsx`（逻辑已迁到 ShotGrid/ShotCard/shotStatus）；相关测试迁移。
- Test: `shotStatus.test.ts`、`ShotCard.test.tsx`（各状态主按钮文案、失败重试、新候选角标、⋯菜单）、`ShotGrid.test.tsx`（分组、按段重写打开对话框带 blockIds、多选）、`EpisodeWorkspace.test.tsx`（首帧门：缺/过期→生成缺少首帧；全就绪→确认首帧生成全部视频并切到视频阶段）。

- [ ] Step 1–5，commit「feat(ui): 镜头卡片网格按段落分组、卡片单主按钮、首帧确认门」

### Task 5: 角色阶段与角色抽屉

**Files:**
- Create: `frontend/src/pages/workspace/CastStage.tsx`（角色卡网格 + 下方「设定」折叠区：场景、风格编辑器，复用 CastPage 里的编辑器）、`CharacterDrawer.tsx`（`?char=<id>` 打开；页签「造型」「三视图」「资料」；把 `CharacterCard.tsx` 按这三块拆开复用，行为不变：大图预览、设为当前造型确认、抽卡、失败卡、保存并生成、离开拦截）。
- 「按剧本推荐角色」放在角色阶段顶部（次要按钮）。
- 删除 `CastPage.tsx` 页面壳（逻辑迁入）；测试迁移。
- Test: `CastStage.test.tsx`、`CharacterDrawer.test.tsx`（三个页签、单击只预览、设为当前造型需确认、抽卡失败卡、关闭抽屉时有未保存修改先确认）。

- [ ] Step 1–5，commit「feat(ui): 角色阶段卡片网格 + 角色抽屉（造型/三视图/资料），场景与风格收进设定」

### Task 6: 收尾——e2e、截图、清理

- 更新 `frontend/e2e/smoke.spec.ts`、`layout.spec.ts` 走新工作台（生成剧本→生成分镜→首帧阶段→ComfyUI 不通被拒）；加「重新生成分镜（带修改意见）」用例（manual LLM 下验证等待输入的提示词含修改意见）。
- 删除不再使用的组件/样式（grep 确认无引用）。
- 临时实例造数据截图 1600×950 与 1280×800：工作台五个阶段 + 角色抽屉 + 镜头抽屉 + 重新生成对话框，存 `docs/notes/ui/2026-10-10-v2-*.png`，逐张检查并修明显问题。
- 全量：`uv run pytest -q`、`npm test`、`npm run e2e`、`npm run build`。
- commit「test(e2e): 单集工作台 v2 流程与重新生成；更新截图」

## 执行说明

顺序 1 → 2 → 3 → 4 → 5 → 6。每个任务开始前读 spec 与本任务全文，读相关现有代码与测试再动手。
