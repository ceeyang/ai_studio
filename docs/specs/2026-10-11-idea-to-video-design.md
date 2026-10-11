# 从一个点子出片（首页向导）

日期：2026-10-11　状态：设计已确认，实施中

## 目标

用户只有一句话的点子，想尽快得到一段视频：AI 先发散几个差异明显的方向让用户挑，再扩写成梗概，确认后自动建项目、
跑到分镜停一下给用户看，用户点「继续出片」后自动出定妆照、首帧、视频、导出。

不做：方案历史版本、多集（只建第 1 集）、自动挑最好的候选（沿用默认选第一个的规则）。

## 向导（前端 /start，首页「从一个点子开始」）

1. **点子**：一句话；时长 10 / 15 / 30 / 60 秒；画幅（默认 9:16）；风格预设（默认设置里的）。
2. **方向**：AI 出 4 张卡片（标题、一句话卖点、开头钩子、反转、结尾卡点、类型）；「再来 4 个」（带上已出的标题让模型避开）；
   补一句要求重出；单选一张，或勾两张合并。
3. **梗概**：扩写选中的方向（篇幅随时长）；可直接改文字；写一句修改要求让 AI 重写。
4. **确认**：项目名（AI 起的，可改）、图像 / 视频引擎（按风格预设推荐）→「开始制作」。

方案、梗概只在页面里，刷新时用 localStorage 保草稿；「开始制作」前不写库。

## 后端接口

| 接口 | 作用 |
|---|---|
| `POST /api/ideas/options` `{idea, target_seconds, style_preset, feedback?, exclude?: [标题], count=4}` → `{options: [{title, logline, hook, twist, ending, genre}]}` | 出方向卡片。同步调用文本模型（step `episode.idea`，任务 script），模板 `idea_options.md` |
| `POST /api/ideas/expand` `{idea, options: [卡片] (1–2 个), target_seconds, style_preset, current?, instruction?}` → `{title, synopsis}` | 扩写 / 合并 / 按要求改写。模板 `idea_expand.md`，规则沿用 `episode_idea.md` |
| `POST /api/ideas/start` `{title, synopsis, target_seconds, aspect, style_preset, image_engine?, video_engine?}` → `{slug, episode_id, job_ids}` | 建项目（slug 由标题生成，重名加 -2、-3…）+ 第 1 集（原文 = 梗概），提交剧本 → 分镜 |
| `POST /api/episodes/{id}/autopilot` → `{job_ids, skipped: [说明]}` | 「继续出片」：出镜、没有定妆照、有外貌提示词的角色各出 1 张定妆照；首帧 → 视频 → 导出用 depends_on 接在定妆照后面 |

文本模型输出不合格、连不上：中文原因，接口 502；手动粘贴模型：400 说明要去设置里换。

## 剧集工作台

URL 带 `?autopilot=1` 时顶上显示「点子出片」条：剧本 / 分镜在跑 → 显示进度；分镜做完 → 「看一下分镜和角色，没问题点『继续出片』」+ 按钮；
已经点过 → 显示「定妆照 → 首帧 → 视频 → 导出」进度，跑完给「看成片」。

## 测试

- 后端：卡片 / 梗概 JSON 校验、exclude 进提示词、slug 重名、start 建的项目和排的任务、autopilot 排的任务和依赖、跳过的角色。
- 前端：向导四步（假接口）、合并两张、修改要求重写、草稿恢复；工作台的「继续出片」条。
