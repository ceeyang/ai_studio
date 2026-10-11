# 推广素材

发帖、写介绍时可以直接用。内容都按现在代码里实际有的功能写；新接入、还没实测的引擎单独标出来，别写成「已支持」。

## 一段话介绍

**中文**

AI Studio 是一个在自己电脑上跑的 AI 漫剧 / AI 短剧工作台（FastAPI + React，中文界面）。从一句话点子或一份梗概开始，
按剧本 → 分镜 → 角色定妆照 / 三视图 → 首帧 → 视频 → 字幕 / 配音 / 配乐 → 导出的顺序做出一集成片，每一步都能单独重跑、换引擎、挑候选。
出图、出视频可以用本机 ComfyUI，也可以通过自带的浏览器扩展用你已登录的 ChatGPT、Gemini、Muse 等网页额度（不用 Key、不读 cookie），
或者接 OpenAI、Gemini / Veo、Sora、火山方舟 Seedance、可灵的官方 API。导出默认带「AI 辅助制作」角标和文件元数据标识。

**English**

AI Studio is a local-first workbench for AI micro-dramas (short vertical or horizontal series), with a FastAPI backend and a
Chinese React UI. Starting from a one-line idea or a synopsis, it walks an episode through script → storyboard → character
portraits / turnarounds → first frames → video clips → subtitles / dubbing / music → final export, and every step can be re-run,
switched to another engine, or picked from several candidates. Images and videos can come from a local ComfyUI, from web apps
you are already logged into (ChatGPT, Gemini, Muse … via the bundled browser extension, no API key, no cookie access), or from
official APIs (OpenAI, Gemini/Veo, Sora, Volcengine Ark Seedance, Kling). Exports carry a visible "AI-assisted" label and
metadata by default.

## 社交平台短文

### 小红书风格

> 在自己电脑上做 AI 短剧，我写了个开源工作台
>
> 一句话点子 → AI 出 4 个故事方向 → 梗概 → 剧本分镜 → 角色定妆照 / 三视图 → 首帧 → 视频 → 字幕配乐 → 导出成片。
>
> 几个我自己最常用的：
> - 已经有 ChatGPT / Gemini 会员？装个浏览器扩展就能直接用网页额度出图（ChatGPT / Gemini / Muse）、出视频（Gemini / Muse），不用申请 Key
> - 角色定妆照 + 三视图自动带进每个镜头，同一个人前后长得一样
> - 抽卡统计：每个镜头抽了几次、失败几次、哪个引擎可用率高，一目了然
> - 导出默认带「AI 辅助制作」角标和元数据
>
> 本地运行，界面全中文。#AI短剧 #AI漫剧 #AIGC #开源

### 即刻风格

> 开源了一个本机跑的 AI 漫剧工作台：点子 → 剧本 → 分镜 → 定妆照 → 首帧 → 视频 → 成片，一条线做完。
> 引擎可以混着用：本机 ComfyUI、网页版额度（浏览器扩展驱动 ChatGPT / Gemini / Muse，即梦 / 可灵在接）、官方 API（Veo、Sora、Seedance、可灵）。
> 花额度前有「剧本体检」查开头 15 秒有没有钩子、结尾有没有卡点；出视频有抽卡统计帮你算哪家更省。
> 还在早期，欢迎来提 issue。

### X / 推特风格（中文）

> AI Studio：在自己电脑上把一句话点子做成一集 AI 短剧。
> 本机 ComfyUI + 网页额度 + 官方 API 混用，每个镜头单独选引擎；定妆照 / 三视图锁脸；抽卡统计控成本；导出默认带 AI 标识。
> 中文界面，开源。

（话题标签按平台习惯增减；发之前把仓库地址补上。）

## 功能清单

- 点子向导：一句话点子 → 4 个方向 → 梗概 → 确认，建好项目后自动写剧本和分镜
- 导入梗概 / 小说新建剧集，目标时长可设；AI 写梗概、续写
- 剧本、分镜可以手改；单段「重写本段分镜」
- 剧本体检：规则检查（不调模型）+ AI 打分（开场钩子、冲突与爽点密度、结尾卡点、节奏）
- 角色：按剧本推荐角色、定妆照抽卡 / 上传、AI 推荐造型、三视图自动切图、配音音色
- 场景图、道具图、每镜最多 2 张构图参考，出首帧时按顺序带给引擎
- 首帧多候选、图生视频；「接上一镜」用上一镜尾帧当首帧
- 参考图直出：跳过首帧，用场景图 / 定妆照 / 道具图直接出视频（目前 Seedance API 支持，未实测）
- 引擎建议：按镜头难度和硬性要求（台词要有声、尾帧、时长上限）推荐引擎，只建议不自动换
- 抽卡统计：每镜 / 每引擎的抽数、失败数、可用率
- 出片自检：开头没接上首帧、疑似跳切、疑似声音性别不对
- 成片：中文 / 高棉语字幕、高棉语 Edge TTS 配音、背景音乐（自动压低）、片尾卡
- AI 生成内容标识：画面角标 + 文件元数据，默认开
- 一键出完剩下的：缺的定妆照 → 首帧 → 视频 → 导出一次排队
- 续季导入：把角色、场景、道具连同选定的图复制到新项目
- 任务队列：暂停 / 恢复、失败重试、日志、每次调用明细；文本模型可选「手动」粘贴
- 命令行 `studio`：项目、剧集、阶段任务、任务队列、字幕、环境检查
- 引擎：本机 ComfyUI（H3、Krea2、Z-Image、Wan2.2）、网页（ChatGPT、Gemini、Muse；即梦、可灵选择器待实测）、
  API（OpenAI 图像、Gemini 图像、Veo、Sora；Seedance、可灵未用真实 Key 实跑）、RunningHub 云端工作流（可选）

## 建议的 GitHub topics

`ai-video` `ai-drama` `short-drama` `micro-drama` `aigc` `text-to-video` `image-to-video` `storyboard` `comfyui`
`fastapi` `react` `browser-extension` `seedance` `kling` `veo` `local-first` `chinese`

## 图片素材

都在 [`docs/images/`](images/)，1440 宽（hero 1600 宽）JPEG，深色界面，来自示例项目「海风吹散的初恋」。截图里没有本机路径、邮箱和 Key。

| 文件 | 内容 |
|---|---|
| `hero.jpg` | 单集工作台视频阶段 + 镜头抽屉（首帧、视频候选），适合做封面 |
| `01-projects.jpg` | 项目中心 |
| `02-start.jpg` | 从一个点子开始（向导第一步） |
| `03-project.jpg` | 项目页：剧集、角色、场景、风格 |
| `04-script.jpg` | 剧本阶段 + 剧本体检展开 |
| `05-cast.jpg` | 角色阶段 |
| `06-character-looks.jpg` | 角色抽屉：造型（定妆照） |
| `07-turnaround.jpg` | 角色抽屉：三视图 |
| `08-scenes.jpg` | 设定：场景、道具 |
| `09-keyframes.jpg` | 首帧阶段 |
| `10-shot-drawer.jpg` | 镜头抽屉：接上一镜、生成方式、构图参考、提示词 |
| `11-videos.jpg` | 视频阶段 + 抽卡统计 |
| `12-export.jpg` | 成片阶段：字幕、配音、配乐、AI 标识、片尾卡、成片播放 |
| `13-finish-all.jpg` | 一键出完剩下的（确认框） |
| `14-jobs.jpg` | 任务页 + 任务详情 |
| `15-import-assets.jpg` | 从其他项目导入 |
| `16-settings-defaults.jpg` | 设置：默认模型 |
| `17-settings-api.jpg` | 设置：API 服务（未填 Key） |
| `18-settings-browser.jpg` | 设置：网页出图（浏览器扩展） |
| `19-settings-engines.jpg` | 设置：引擎表 |
| `20-settings-doctor.jpg` | 设置：环境检查（本机路径已遮掉） |
| `sample-strip.jpg` | 成片截帧四连 |
| `sample.gif` | 成片动图预览（约 4 秒） |

## 使用须知（发帖时建议带上）

- 用本工具生成和发布的内容，**责任在使用者**：遵守《人工智能生成合成内容标识办法》等规定和各平台的 AI 内容规则，发布时保留 AI 标识。
- 浏览器扩展会在你登录的网站上自动操作，部分网站的服务条款限制自动化使用，账号可能因此受限，请自行评估；用掉的是你自己账号的额度。
- API 按量付费，费用由你自己的账号承担；本机模型请遵守各自的许可证。
- 不要用未经授权的真人照片、明星脸、他人作品做参考图或剧本素材。
- 许可证还没定（见 [open-source-checklist.md](open-source-checklist.md)），对外推广前先定好。
