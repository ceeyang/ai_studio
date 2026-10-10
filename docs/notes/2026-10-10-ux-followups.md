# 流程改造遗留小问题（2026-10-10）

来自逐任务审查和总审查，判定为可以以后再修。计划：docs/superpowers/plans/2026-10-10-ux-flow-overhaul.md

- [任务 1] split of llm shot origin untested; visual fp estimate uses pre-structure chars (doc note); migration backfills old manual shots as llm (accepted)
- [任务 1] _last_action duplicated storyboard.py vs impact.py
- [任务 2] videos+export on subset of shots doesn't precheck unpicked shots' videos
- [任务 2] uploaded portrait (job_id None) after failure doesn't clear last_failure
- [任务 2] _refs_reason keys by label (dup labels collapse)
- [任务 2] owner_jobs per character in list_characters (not batched)
- [任务 2] ComfyUI health message repeats url + English transport text
- [任务 2] schemas.JobContext name clashes with queue.context.JobContext
- [任务 3] export_label accepts bool duration
- [任务 4] EditableFields one-frame dirty flash on server change of untouched field
- [任务 4] JobNotice track([]) shows 已跳过
- [任务 4] JobNotice stuck 进行中 when job query errors (404)
- [任务 4] ConfirmDialog pending promise not settled on unmount; replaced confirm reuses Dialog (no key)
- [任务 4] Toasts timers not cleared on manual dismiss; seen set unbounded
- [任务 4] .toasts z-index 30 vs modal scrim 40 — check intent
- [任务 5] useOnStageDone treats waiting_input as ended → next-step hint lost in manual LLM mode
- [任务 5] impactText says 删除 0 个镜头 when blocks non-empty
- [任务 5] RecommendPanel comment overclaims persistence (?recommend= not on in-app links)
- [任务 5] cast strip shows all project characters
- [任务 6] ShotDrawer aria-modal without focus trap
- [任务 7] player jumps to new final after 设为定稿 when nothing clicked
- [任务 7] no test for bad JSON in localStorage options
- [任务 8] header loading flash (下一步/还没有角色/已经是第一集 before queries load)
- [任务 8] JobsPage onRetried doesn't reset limit; retry-all bypasses shared mutation
- [任务 8] Projects empty state shows two primary 新建项目 buttons
- [任务 8] conditional primaries on cast page (添加角色 / 用这个提示词抽卡 / Lightbox 设为当前造型) can coexist with next-action primary
- [任务 9] settings doctor rows show engine ids (ChatGPT 登录（gpt_image）, manual) — backend doctor.py copy
- [任务 9] project-card secondary 导演台 link for 0-shot episodes
- [任务 9] no skip-to-content link
- [总审] 重跑某段后手工镜头在段内的位置不保留，剧本重跑后手工镜头排到集末
- [总审] 「保存并运行分镜」先保存再弹确认，取消确认时段落已保存

## 单集工作台 v2（2026-10-11）遗留

- [v2 任务 1] run_storyboard doesn't re-validate scene_block_ids at execution (deleted block → silent 0 done)
- [v2 任务 2] confirm title 批量生成 vs 重拍 verb; impactText shows only video OR frame count; char counter code points vs maxLength; beforeRun failure shown twice
- [v2 任务 3] untitled episode h2「第 N 集 」; legacy redirect NaN id; re-click same rail character doesn't rescroll
- [v2 任务 4] 先补齐首帧 count includes in-progress; switch-before-submit failure untested
- [v2 任务 4] ShotDrawer video button not gated by stale first frame (pre-existing)
- [v2 任务 5] 三视图 N/7 ignores stale cells; toolbar portrait count vs stage label scope
- [v2 任务 5] pending discard confirm uses click-time params; Lightbox dirty hint lacks 去「资料」 button; 覆盖外貌描述 confirm wording
- [v2 任务 6] .tab-body > .turnaround nesting override
- [v2 终审] 段落里镜头被手工删光时，下一步一直指向「重新生成分镜（剧本改过）」；只有孤儿镜头时计数为 0
- [v2 终审] 镜头抽屉「先出首帧」按钮没受 generating/saving 禁用；「保存并生成视频」路径的测试被改成首帧路径
- [v2 终审] ComfyUI 4xx 健康原因显示为笼统的「连接出错」；左栏在加载中先展开再收起（闪一下）
- [v2 终审] 改之前存的含修改意见的指纹，会让下一次不强制运行多跑一次（无迁移）
- [v2 终审] 引擎中文名里仍有「H3 图像 / H3 视频」；一段 ≤5 镜时 1600 宽右侧留空
- [v2 后续] @ 引用写进提示词、单镜 LLM 重写、动态分镜预览、导出剪映草稿/FCPXML、夜间批跑
