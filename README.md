# AI Studio · 本机 AI 漫剧工作台

> **English summary.** AI Studio is a local-first workbench for producing AI micro-dramas (漫剧 / short vertical series).
> It runs on your own machine (FastAPI backend + React UI) and walks one episode from idea or script → storyboard →
> character portraits → first frames → video clips → subtitles / dubbing → final export. Images and videos can come from a
> local ComfyUI, from web apps you are already logged into (ChatGPT, Gemini, Muse, Jimeng/Dreamina, Kling — driven by the
> bundled browser extension, no API key needed), or from paid APIs (OpenAI, Gemini/Veo, Sora, Volcengine Ark Seedance,
> Kling). The UI and docs are in Chinese. License: not decided yet (see `docs/open-source-checklist.md`).

一句话：**在自己电脑上，把一个点子或一份剧本做成一集能发布的 AI 短剧。**

<!-- 截图占位：项目中心 / 剧集工作台 / 导出页各一张，放在 docs/images/ 下，再把下面一行换成图片链接 -->
> 截图待补。

## 能做什么

一集短剧按这几步走，每一步都能单独重跑、换引擎、挑候选：

1. **剧本**：从一句话点子出几个故事方向、扩成梗概，或导入自己的梗概 / 小说，让文本模型写剧本。
2. **分镜**：拆成镜头（时长、景别、台词、出镜角色），可以手改。
3. **剧本体检**：花出图、出视频额度之前，先按规则检查开头 15 秒有没有钩子、结尾有没有卡点、镜头是否过长，也可以让模型给建议。
4. **角色定妆照 / 三视图**、场景图、道具图：作为后面出图的参考，保证同一角色前后一致。
5. **首帧**：每个镜头出首帧图（可多张候选），再**图生视频**；也可以选「**参考图直出**」，不出首帧，用定妆照、场景图等直接出视频。
6. **视频**：每个镜头出视频片段，失败或不满意就再抽一次。**抽卡统计**会记每个镜头抽了几次、选了第几条。
7. **配音 / 字幕 / 配乐**：中文字幕、高棉语译稿和字幕、高棉语外挂配音（Edge TTS），片尾卡，背景音乐。
8. **导出**：拼成整集成片。默认带 **AI 生成内容标识**（画面角标 + 文件元数据）。

另外：
- **续季导入**：把一个项目的角色、场景、道具（连同选定的图）复制到新项目，接着做第二季。
- **一键出完剩下的**：缺的定妆照 → 首帧 → 视频 → 导出一次排进任务队列，出好的镜头跳过。
- **命令行**：网页上能做的主要操作都有 `studio` 命令，方便脚本和 AI 助手调用。

### 支持的出图 / 出视频来源

| 方式 | 图片 | 视频 | 需要什么 |
|---|---|---|---|
| 本机 ComfyUI | Z-Image、Krea2、MiniMax H3 | MiniMax H3（有声）、Wan2.2 图生视频 | 本机 ComfyUI 和对应模型（自行下载，遵守各模型许可证），有显卡 |
| 网页版（浏览器扩展） | ChatGPT、Gemini、Muse | Gemini（Veo）、Muse、即梦 / Dreamina（Seedance）、可灵（国内 / 国际） | 在自己的 Chrome / Edge 里装 `browser-extension` 并登录这些网站；用的是你账号的会员或免费额度 |
| API | OpenAI（gpt-image）、Gemini | Sora、Veo、火山方舟 Seedance（国内 / BytePlus 海外）、可灵开放平台（国内 / 国际） | 对应服务的 API Key，按量付费 |
| 云端 ComfyUI（可选） | RunningHub 工作流 | RunningHub 工作流 | RunningHub API Key，见 `config.example.yaml` 的 `runninghub` 段 |

写剧本、分镜用的**文本模型**可以选：本机 Claude Code（Agent SDK / `claude -p`）、本机 LM Studio、OpenAI / Anthropic / Gemini API、
网页版 ChatGPT / Gemini / Claude / Muse（走浏览器扩展），或者「手动」（自己把提示词贴给任意模型，再把结果贴回来）。

## 快速开始

需要：Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)、Node.js 22.22+ 或 24+ 和 npm、ffmpeg（要带 libass，否则烧字幕和 AI 标识角标会失败）。
用本机出图、出视频还需要 [ComfyUI](https://github.com/comfyanonymous/ComfyUI)；只用网页版或 API 可以不装。

### 1. 安装和配置

macOS / Linux：

```bash
git clone <本仓库地址> ai_studio && cd ai_studio
uv sync
cp config.example.yaml config.yaml        # 按本机情况改，config.yaml 不进 git
cd frontend && npm install && npm run build && cd ..
```

Windows（PowerShell）：

```powershell
git clone <本仓库地址> ai_studio; cd ai_studio
uv sync
Copy-Item config.example.yaml config.yaml
cd frontend; npm install; npm run build; cd ..
```

`config.yaml` 里常改的几项：
- `comfyui.url`、`comfyui.models_dir`：本机 ComfyUI 的地址和模型目录（不用 ComfyUI 可以不管，引擎会标「未配置」）；
- `llm.default` / `llm.routes`：剧本、分镜默认用哪个文本模型；
- `subtitles.zh_font` 等：默认是 Windows 字体（Microsoft YaHei），macOS / Linux 上换成本机有的字体，比如 `PingFang SC`、`Noto Sans CJK SC`；
- `ffmpeg.ffmpeg` / `ffmpeg.ffprobe`：不在 PATH 里时写完整路径。macOS 上 Homebrew 的 ffmpeg 8 默认不带 libass，需要另装带 libass 的版本。

### 2. 启动

```bash
uv run studio-server          # 打开 http://127.0.0.1:8700
```

改前端代码时用开发态（热更新）：后端照样 `uv run studio-server`，另开一个终端 `cd frontend && npm run dev`，打开 http://127.0.0.1:5173
（`/api` 和 `/media` 代理到后端）。Windows 上也可以用 `powershell -ExecutionPolicy Bypass -File start.ps1` 一次启动两个。

### 3. 加载浏览器扩展（用网页版出图 / 出视频 / 写文本时需要）

1. 在你平时用的 Chrome 或 Edge 打开 `chrome://extensions`（Edge 是 `edge://extensions`），打开「开发者模式」；
2. 「加载已解压的扩展程序」，选仓库里的 `browser-extension` 目录；
3. 在这个浏览器里登录要用的网站（ChatGPT、Gemini、Muse、即梦、可灵……）。

扩展通过 `ws://127.0.0.1:8700/api/browser/ext` 连本机 studio，在已登录的标签页里操作并把原图 / 原视频传回来；
studio 不启动浏览器、不读也不拷 cookie。设置页能看到扩展连没连上、各网站登没登录。

### 4. 环境检查

服务启动后：

```bash
uv run studio doctor          # 检查 ComfyUI、模型文件、浏览器扩展和各网站登录、文本模型、ffmpeg / libass、磁盘、内存
```

网页设置页的「环境检查」是同一份结果。

### 5. 命令行示例

```bash
uv run studio project create 雨夜来电 --slug rainy-call
uv run studio episode import rainy-call --file story.md --seconds 90
uv run studio run all rainy-call -e 1
uv run studio jobs --watch
uv run studio episode show rainy-call 1
```

所有命令加 `--json` 输出 JSON；完整命令见 `uv run studio --help`。命令行调用的是本机服务的 HTTP API，
所以要先启动 `studio-server`（地址不是默认的 `http://127.0.0.1:8700` 时设环境变量 `STUDIO_URL`）。

## 引擎配置说明

- **API Key 不写进 `config.yaml`**。在网页设置页「API 服务」里填（存在本机数据库里，界面和日志只显示掩码），
  或者用环境变量（也可以写进仓库根目录的 `.env`，已在 `.gitignore` 里）：

  | 服务 | Key 环境变量 | 可选 Base URL |
  |---|---|---|
  | OpenAI（GPT / gpt-image / Sora） | `OPENAI_API_KEY` | `OPENAI_BASE_URL` |
  | Anthropic（Claude） | `ANTHROPIC_API_KEY` | `ANTHROPIC_BASE_URL` |
  | Google（Gemini / Veo） | `GEMINI_API_KEY` 或 `GOOGLE_API_KEY` | — |
  | 火山方舟（Seedance） | `ARK_API_KEY` | `ARK_BASE_URL` |
  | 可灵（Kling） | `KLING_API_KEY`，或 `KLING_ACCESS_KEY` + `KLING_SECRET_KEY` | `KLING_BASE_URL` |
  | RunningHub | `RUNNINGHUB_API_KEY`（名字可在 `config.yaml` 的 `runninghub.api_key_env` 改） | — |

  设置页填的优先，没填才读环境变量。火山方舟和可灵在设置页选国内 / 海外区域。
- **网页版引擎**不需要 Key，用的是你自己账号的额度；即梦 / 可灵的区域（国内 / 海外）在设置页「网页出图」里选。
- **本机 ComfyUI 引擎**的工作流在 `workflows/`（`*.json` 是 ComfyUI API 格式，`*.map.yaml` 写参数映射和模型文件名）。
- 每个项目可以分别选默认的图片、视频来源，每个镜头也能单独换。

## 合规提示

使用本工具产出和发布内容，**责任在使用者**。请注意：

- **AI 生成内容标识**：《人工智能生成合成内容标识办法》（2025-09-01 施行）和国家标准 GB 45438-2025 要求 AI 生成内容带显式标识和文件元数据里的隐式标识；
  广电总局对 AI 参与制作的微短剧要求在明显位置标注「AI 辅助制作」等字样（实现依据见 `backend/post/aigc.py` 的说明）。
  本工具导出时默认烧角标并写元数据，**不要关掉后再去发布**。具体要求以主管部门和平台的最新规定为准。
- **平台规则**：抖音、快手、视频号、B 站、YouTube 等平台对 AI 内容的标注、备案、上架都有各自的规则，发布前请自行确认。
- **版权和肖像权**：不要用未经授权的真人照片、明星脸、他人作品当参考图或剧本素材；配乐、字体也要有使用授权。
- **第三方服务条款**：浏览器扩展会在你登录的网页（ChatGPT、Gemini、即梦、可灵等）上自动操作，部分网站的服务条款限制自动化使用，
  账号可能因此受限，请自行评估。API 和网页额度的费用由你自己的账号承担。
- **模型许可证**：本机 ComfyUI 用到的模型需要自行下载，并遵守各自的许可证（有的不允许商用）。

## 项目结构

```
backend/            FastAPI 后端
  app/              HTTP / WebSocket 路由、页面数据
  domain/           数据模型和业务规则（项目、剧集、镜头、角色、设置、API Key……）
  pipeline/         每一步的流程（剧本、分镜、首帧、视频、导出、剧本体检、点子向导……）
  engines/          出图 / 出视频引擎：comfyui/、browser/（网页版，经浏览器扩展）、api/、runninghub/
  llm/              文本模型网关和各家提供方
  post/             后期：字幕、配音、片尾卡、AI 标识、ffmpeg 渲染
  queue/            任务队列
  prompts/          提示词模板
  migrations/       数据库迁移（Alembic）
  tests/            后端测试
frontend/           React + Vite 前端（src/pages 是各页面，e2e/ 是 Playwright 冒烟测试）
browser-extension/  Chrome / Edge 扩展（sites/ 下每个网站一份页面脚本）
cli/studio.py       命令行
workflows/          ComfyUI 工作流和参数映射
docs/specs/         设计文档；docs/superpowers/plans/ 实现计划；docs/notes/ 调研和实测记录
config.example.yaml 配置模板
```

## 开发与测试

```bash
uv run pytest backend/tests                 # 后端
cd frontend && npx vitest run               # 前端组件测试（也可以 npm test）
cd frontend && npm run typecheck            # TypeScript 类型检查
cd frontend && npm run build                # 构建（含类型检查）
cd frontend && npm run e2e                  # Playwright 冒烟：临时数据目录 + 手动文本模型，不需要显卡
```

没有 ffmpeg 时相关测试会自动跳过；ffmpeg 不带 libass 时，烧字幕的两个导出测试会失败，属于环境问题。
贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 路线图

设计和后续计划都在 [`docs/specs/`](docs/specs/)：总体设计 `2026-10-07-ai-studio-design.md`，最近的有从点子出片
`2026-10-11-idea-to-video-design.md`、Seedance / 可灵视频引擎 `2026-10-12-seedance-kling-video-design.md`。

## 许可证

许可证：待定（见 [docs/open-source-checklist.md](docs/open-source-checklist.md)）。
