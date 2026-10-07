# AI Studio 设计文档

- 日期：2026-10-07
- 状态：待审阅
- 位置：`D:\works\ai_studio`（私有，仅本机使用，不开源）

## 1. 背景与目标

现有两份参考：

- **`D:\works\ai_videos`**（自有代码）：命令行流水线 `vg.py` + `vglib/`。本地 ComfyUI（MiniMax H3 / Wan2.2 / Z-Image / Krea2）、LM Studio 写分镜、ffmpeg 合成、字幕、edge-tts 配音都已实测可用。痛点是只有 CLI + yaml、没有可视化审片、跨镜人脸漂移、没有超分/补帧、质量全靠人工，任务和项目状态散落在文件里。
- **BigBanana AI Director**（第三方，CC BY-NC-SA 4.0）：网页工作台，产品流程成熟（剧本 → 一致性资产 → 导演台 → 导出；美术风格锚、角色九宫格三视图、造型变体、首尾帧续接、提示词版本、断点续跑）。但只接云端 API、媒体以 base64 塞浏览器、许可证禁止商用。**本项目只借鉴其流程思路，不复制任何代码与提示词原文。**

**目标**：做一个跑在本机的网页工作台，把 BigBanana 的产品流程和 ai_videos 的本地推理链路合在一起，以本机 RTX 5080 16GB + ComfyUI Desktop + LM Studio 为主力，Agnes 云端为补充，并能被 Claude Code 直接驱动。

**首个验收项目**：原创 IP《提词员》第一集（`D:\works\ai_videos\docs\ai-manju-runbook.md`、`故事构思/提词员.md`），9:16 仿真人 AI 漫剧，20–28 镜，1080×1920 成片。

## 2. 已确定的决策

| 主题 | 决策 |
|---|---|
| 形态 | 本机网页工作台，单用户，只监听 127.0.0.1 |
| 代码来源 | 新建独立仓库；从 vglib 移植已验证的引擎/合成逻辑并重构；`ai_videos` 保持不动 |
| 技术栈 | Python FastAPI + SQLite + 文件存储 + 单 GPU 任务队列；React + Vite + TypeScript 前端 |
| 文本模型 | 可配置：LM Studio（本地）、任意 OpenAI 兼容服务、本机 Claude Code（订阅）三种 provider |
| Claude Code | 双向：工作台可调用 Claude 当文本模型；Claude Code 可通过 CLI / MCP 操作工作台 |
| 图像/视频生成 | 本地 ComfyUI 为主 + Agnes 云端；引擎可插拔 |
| 第一版范围 | 主线 + 角色三视图/换装 + 字幕/配音 + 超分/补帧 + 小说导入拆集（按 M1–M4 分批交付） |
| 开源 | 不开源 |

## 3. 运行环境基线（2026-10-07 实测）

- GPU：RTX 5080 16GB；CPU i9-14900KF；内存 64GB；D 盘剩余约 900GB
- ComfyUI Desktop 0.38.1，`127.0.0.1:8188`，PyTorch 2.10 + cu130，无 sageattention
  - 模型目录：`C:\Users\cee\AppData\Local\Comfy-Desktop\ComfyUI-Shared\models`
  - 已有节点：VideoHelperSuite、KJNodes、WanVideoWrapper、GGUF、LTXVideo、frame-interpolation、seedvr2_videoupscaler 等
- LM Studio（`127.0.0.1:1234`，平时未启动），模型 Gemma4-26B-A4B、Qwen3.5-9B
- Python 3.12（系统）、uv 0.11、Node 24、ffmpeg 8.1 full（libass）、Docker
- 现有 key：`AGNES_API_KEY`、`AGNES_BASE_URL`（在 ai_videos/.env）

以上路径和模型名全部写进本地配置，不进代码。

## 4. 架构

```
浏览器 (React 工作台)            Claude Code
        │ HTTP + WebSocket          │ studio CLI / MCP 工具
        └────────────┬──────────────┘
               FastAPI 服务 (127.0.0.1:8700)
   ┌──────────┬───────────┬────────────┬─────────────┐
 领域服务     任务队列     LLM 网关       媒体存储
 (SQLite)    (单 GPU      (OpenAI 兼容 /   projects/<slug>/media
             worker +     Claude /
             CPU worker)  手动)
                   │
             引擎适配层
   ┌──────────┬─────────┬───────────┬──────────┐
 ComfyUI     Agnes      ffmpeg       TTS
 (H3/Wan2.2/ (图/视频)  (合成/字幕/   (edge-tts，
  Z-Image/              转码)        预留本地)
  Krea2/SeedVR2/RIFE)
```

**原则**

1. **HTTP API 是唯一入口**。网页、CLI、MCP 都是它的客户端，任何操作三端都能做。
2. **生成类操作一律是任务**。API 只负责提交任务和查询状态，不在请求里同步跑生成。
3. **媒体是普通文件**，路径存数据库，可直接用资源管理器、剪映打开。
4. **产物都是候选**。同一槽位可以有多个候选，下游只使用"选定"的那一个，旧候选不删。
5. **上游变更使下游过期**。每个候选记录生成时的输入哈希，输入变了就标记过期（只标记，不自动重跑）。

### 4.1 仓库结构

```
ai_studio/
  backend/
    app/            FastAPI 入口、路由、WebSocket
    domain/         实体、仓储（SQLite）、过期判定
    pipeline/       各阶段编排：script / assets / storyboard / keyframes / videos / post / export
    engines/        引擎接口 + comfyui / agnes / ffmpeg / tts 适配器
    llm/            LLM 网关、provider、JSON 校验重试
    queue/          任务队列与 worker
    prompts/        提示词模板（*.md，自写）
    tests/
  workflows/        ComfyUI API 格式工作流模板（*.json）+ 参数映射（*.yaml）
  frontend/         React + Vite + TS
  cli/              studio 命令（薄客户端）
  mcp/              MCP server（薄客户端）
  config.example.yaml
  config.yaml       本地配置（gitignore）
  .env              密钥（gitignore）
  projects/         数据目录（gitignore）
  docs/
```

Python 依赖用 uv 管理；前端用 npm。启动脚本 `start.ps1` 同时拉起后端和前端（开发态），生产态由 FastAPI 直接托管前端构建产物，只占一个端口。

### 4.2 配置

`config.yaml`（示例见 `config.example.yaml`）包含：

- `server`：端口（默认 8700）、数据目录
- `comfyui`：地址、模型根目录（仅供 doctor 检查用）
- `llm.providers[]`：`{id, kind: openai|claude|manual, base_url, model, api_key_env}`；`llm.routes`：各任务（novel_split / script / storyboard / prompt_enrich / turnaround_plan / style_bible）用哪个 provider
- `engines`：每个引擎的启用状态、模型文件名、默认参数（步数、分辨率、帧率、turbo）
- `agnes`：base_url，key 从环境变量读
- `ffmpeg`：可执行文件路径、字体

密钥只放 `.env` / 环境变量。

## 5. 数据模型

SQLite（`projects/studio.db`），通过 SQLAlchemy 2 + Alembic 迁移。媒体在 `projects/<slug>/media/<kind>/<id>.<ext>`。

| 实体 | 关键字段 |
|---|---|
| **Project** | id, slug, title, aspect (9:16/16:9), default_engines{image,video}, style_bible_id, created_at |
| **StyleBible** | palette, lighting, texture, character_rules, mood_keywords[], anchor_text（80–120 词英文一致性锚，注入所有图像/视频提示词）, negative |
| **Character** | project_id, name, description, visual_prompt, core_features, voice（TTS 音色）, portrait_slot, turnaround_slots[9] |
| **Variation** | character_id, name, visual_prompt（只描述服装/造型）, portrait_slot |
| **Location** | project_id, name, time_of_day, atmosphere, visual_prompt, image_slot（画面中不出人） |
| **Prop** | project_id, name, description, visual_prompt, image_slot |
| **Episode** | project_id, number, title, source_text, target_seconds, status |
| **SceneBlock** | episode_id, order, location_id, text（剧本段落） |
| **Shot** | episode_id, scene_block_id, order, shot_size, camera_move, action, dialogue[{speaker, zh, start, end}], cast[{character_id, variation_id}], props[], image_prompt, motion_prompt, audio_prompt, image_engine, video_engine, duration, use_end_frame, continue_from_prev, transition_out, first_frame_slot, end_frame_slot, video_slot |
| **Slot** | 产物槽位：owner_type, owner_id, kind（portrait / turnaround_n / location_image / first_frame / end_frame / video / post_video / audio / final）, selected_candidate_id |
| **Candidate** | slot_id, file_path, engine, params_json（含完整提示词、seed、工作流 JSON 路径）, input_hash, job_id, note, rating, created_at |
| **PromptVersion** | owner_type, owner_id, field, text, created_at, author（user / llm:<provider>） |
| **Job** | type, owner, payload_json, status（queued / running / done / failed / canceled）, gpu: bool, model_family, priority, log, error, started_at, finished_at, outputs[] |
| **Export** | episode_id, variant（9x16 / 16x9 / bundle）, file_path, settings_json, created_at |

**过期判定**：`Candidate.input_hash` = 哈希（提示词、引擎、关键参数、所用参考图的选定候选 id）。查询时重新计算当前哈希，不一致就标记 `stale`。选定候选变化会沿着依赖链传播：定妆照 → 首帧 → 视频 → 成片。

## 6. 工作流阶段

每个阶段都支持人工编辑和一键自动，自动结果都可以修改。

1. **导入 / 剧本**
   - 输入：粘贴梗概或小说，或导入 story-* skills 的产出目录。
   - LLM 拆集（长文）→ 每集剧本（SceneBlock 列表，挂场景）。
   - 剧本分三步生成（结构 → 视觉 → 镜头），每步记录输入指纹，输入没变就跳过，支持断点续跑。
2. **资产**
   - LLM 抽取角色 / 场景 / 道具，写 visual_prompt；生成 StyleBible。
   - 定妆照：每个角色可出多张候选，挑一张选定。
   - 三视图：LLM 先写 9 格描述（正面全身 / 半身 / 脸部特写、左侧、右侧、3/4、背面、仰视、俯视），用户确认后逐格出图（每格单独一个槽位，可单独重生成）。不拼成一张大图，这样参考图打包时可以按机位取最匹配的那一格。
   - 造型变体：以定妆照为参考，只换服装，不换脸型、发型、体型。
   - 场景图、道具图。
3. **分镜**
   - 镜头数 = 目标时长 ÷ 计划单镜时长（按引擎甜区，H3 默认 5s）。按场景分配镜头数，LLM 输出数量不符时带错误信息重试。
   - 镜头字段见 Shot。image_prompt 沿用 ai_videos 的规则：80–150 个英文词，顺序为景别/焦段 → 主体 → 三个以上具体物件 → 光线 → 色彩/质感 → 风格锚点；角色外貌每镜原样复述，禁止"same as before"；台词每句 ≤16 字。
   - 人工可增删、拆分、排序。
4. **关键帧**
   - 参考图打包顺序固定：场景图 → 出场角色（选定造型的定妆照 + 与机位最匹配的三视图格）→ 道具 → 上一镜尾帧（续接开启时）。超出引擎上限时按此优先级截断，界面提示被丢弃的参考图。
   - 每镜出首帧；`use_end_frame` 时出尾帧。尾帧只对 `caps.supports_end_frame` 为真的视频引擎开放；本地引擎的首尾帧（H3 FL2VA、Wan2.2 FLF2V）在 ai_videos 中尚未实测，M2 先验证再开放，未验证前只有 Agnes 可用。`continue_from_prev` 时首帧直接用上一镜选定视频的最后一帧。
   - 每个槽位可出 N 张候选（默认 1，可设 2–4），人工挑选。
5. **视频**：逐镜调用视频引擎。H3 带原生音频（对白 + 环境声），Wan2.2 无声。
6. **后期**：SeedVR2 超分到目标分辨率；RIFE 补帧到 24/30fps；TTS 配音（外挂，H3 原声压低当底）；字幕（libass，按 dialogue 时间轴）；片尾卡；AI 生成标识（角标 + 片头/片尾文字，参数可配）。
7. **合成导出**：ffmpeg 按 transition_out 做 xfade 拼接；输出 9:16 主版本，可选 16:9；另可导出素材包（选定的关键帧 + 视频 + 字幕 srt + 分镜表 csv）。

## 7. 引擎层

### 7.1 接口

```python
class ImageEngine(Protocol):
    caps: ImageCaps          # max_refs, sizes, supports_negative
    def build(self, req: ImageRequest) -> EngineCall: ...

class VideoEngine(Protocol):
    caps: VideoCaps          # max_refs, supports_end_frame, has_audio, durations, fps, sizes
    def build(self, req: VideoRequest) -> EngineCall: ...

class PostEngine(Protocol):  # upscale / interpolate / tts
    ...
```

`build` 只负责构造调用（ComfyUI 工作流 JSON，或云端请求体），执行交给对应的 runner（ComfyUIRunner / AgnesRunner / LocalRunner）。这样提示词组装和工作流参数注入可以纯单元测试。

### 7.2 ComfyUI 适配

- 移植 vglib 的客户端：`/prompt`、`/history`、`/upload/image`、`/free`、`/interrupt`、`/queue`，以及 WebSocket 进度。
- **参考图通过 `/upload/image` 上传**，不再写文件到 ComfyUI input 目录（修复 vglib 输入目录写死的问题）。
- **错误分类**：连接失败、超时属于可重试；HTTP 4xx、节点校验错误、文件不存在属于不可重试，立即失败并报告出错节点（修复 vglib 把所有 OSError 当网络错误重试 30 次的问题）。
- **工作流模板**：`workflows/<engine>.json`（API 格式）+ `workflows/<engine>.map.yaml`（参数名 → 节点 id / 输入名）。在 ComfyUI 界面调好的图导出 API 格式，写一份 map 就能接入。
- 自动在 8189 起备用实例的逻辑保留为可选项（默认关闭）。

### 7.3 第一版引擎清单

| 引擎 | 类型 | 来源 | 要点 |
|---|---|---|---|
| zimage | 图像 | vglib | Z-Image Turbo，快速出关键帧 |
| krea2 | 图像 | vglib | moodyCutieMixKrea2 + qwen3vl_4b |
| h3_image | 图像 | vglib | H3 生成 5 帧取第 1 帧；支持多参考图（上限 9），定妆照首选 |
| h3_video | 视频 | vglib | H3 ref2va int8 + Qwen3-VL-32B nvfp4；768×1344@24fps；原生立体声；音频 VAE 必须 fp32；可选 turbo 4 步 |
| wan22_i2v | 视频 | vglib | Wan2.2 I2V 14B fp8 + lightx2v 4 步；832×480@16fps；无声 |
| agnes_image / agnes_video | 云端 | vglib | 本地图转 data URI，参考图放 extra_body；视频轮询 `/agnesapi` |
| seedvr2 | 后期 | 新接 | 视频超分 |
| rife | 后期 | 新接 | 补帧（ComfyUI frame-interpolation 节点） |
| edge_tts | 后期 | vglib | 在线 TTS；接口为本地 TTS 预留 |

每次调用都把最终工作流 JSON 和参数写到 `projects/<slug>/runs/<job_id>/`，方便复现，也能直接拖进 ComfyUI 手调。

## 8. 任务队列与显存调度

- 两个 worker：**GPU worker**（串行，执行 ComfyUI 任务）和 **CPU worker**（ffmpeg、TTS、云端轮询，可并发 2）。
- 队列持久化在 SQLite。服务重启时把 `running` 的任务标记为 `failed (interrupted)`，可以一键重跑。
- **调度**：优先级高的先跑；同优先级时优先选和当前已加载模型同族（`model_family`）的任务，减少换模型；换族时先调用 `/free`。
- **控制**：暂停/恢复队列；取消单个任务（排队中直接移除，运行中则 interrupt + 清 ComfyUI 队列 + `/free`）；批量任务拆成子任务，失败的单独重试。
- **LLM 与 GPU 互斥**：调用 LM Studio 前暂停 GPU worker 并等待当前任务结束；调用结束后卸载 LM Studio 模型（`lms unload` 或其 REST 接口，M1 实测确定），再恢复 GPU worker。云端或 Claude provider 不受此限制。
- 进度通过 WebSocket 推送（任务状态 + ComfyUI 步数进度）。

## 9. LLM 网关

- **Provider 类型**
  1. `openai`：OpenAI 兼容 `/v1/chat/completions`（LM Studio、DeepSeek、Agnes 等），要求 JSON 输出。
  2. `claude`：本机 Claude Code，优先使用 Claude Agent SDK（Python），失败时退回 `claude -p --output-format json`；走用户订阅，不需要 API key。
  3. `manual`：生成完整提示词，界面显示"复制提示词 / 粘贴结果"，供人或外部 Claude 会话填写。
- **结构化输出**：每类任务定义 Pydantic schema；解析失败或校验失败时把错误信息附上重试，最多 2 次，仍失败则任务失败并保留原始输出。
- **提示词模板**：`backend/prompts/*.md`，使用 Jinja2；项目可在 `projects/<slug>/prompts/` 覆盖同名模板。全部自行编写。
- 每次调用记录 provider、模型、耗时、token（如果有）到 Job 日志。

## 10. Claude Code 集成

- **CLI `studio`**（Python，typer）：调本机 HTTP API。命令覆盖 project / episode / shot / asset / candidate / job / export / doctor，例如：
  - `studio project create 提词员 --aspect 9:16`
  - `studio episode import 提词员 --file 故事构思/提词员.md`
  - `studio run keyframes 提词员 --episode 1 [--shots S01,S03]`
  - `studio jobs --watch`
  - `studio candidate select <id>`
  - 输出默认人类可读，加 `--json` 便于 Claude 解析。
- **MCP server**（stdio，Python MCP SDK）：提供同等能力的工具，另有 `view_candidate` 返回图片内容，让 Claude 看图评估和挑选。在 Claude Code 中通过 `.mcp.json` 注册。
- **story-* 导入**：识别 story-long-write / story-short-write 的项目目录结构（大纲、正文章节），按章导入为集草稿。
- **ai_videos 迁移**：把旧 `project.yaml` 转换为新项目（shots、refs、字幕），只导入数据，不改原文件。

## 11. 界面

左侧阶段导航，顶部常驻任务条（当前任务、进度、排队数、暂停/取消）。

- **项目中心**：项目列表、新建、导入。
- **剧本**：集列表；原文与剧本段落左右对照编辑。
- **资产**：角色卡（定妆照、9 格三视图、造型变体）、场景卡、道具卡、StyleBible 编辑。
- **导演台**：镜头列表，每行显示首帧 / 尾帧 / 视频 / 对白 / 状态（含过期标记）；拖拽排序、拆分、续接开关、批量生成。镜头详情抽屉显示所有候选、提示词（可编辑，有版本历史）、生成参数、"导出到 ComfyUI"。
- **后期与导出**：按顺序连播预览、转场、字幕/配音/超分/补帧/AI 标识设置、导出与历史。
- **任务**：队列、日志、耗时、取消/重试。
- **设置**：服务连接、LLM 路由、引擎默认参数、doctor 结果。

UI 文案为中文。前端状态用 TanStack Query，任务状态用 WebSocket 推送更新。

## 12. 错误处理与可观测性

- 任务失败保留完整日志、ComfyUI 错误节点和输入参数，界面一键重试（可修改参数后重试）。
- `doctor`：检查 ComfyUI / LM Studio / Agnes / ffmpeg / Claude CLI 连通性；按每个启用的引擎检查所需模型文件和自定义节点是否存在；检查显存和磁盘空间。
- 后端日志写 `logs/studio.log`（按天轮转）。

## 13. 测试策略

- **单元测试（pytest）**：提示词组装、参考图打包与截断、镜头数量预算、schema 校验与重试、过期哈希、工作流参数注入、ffmpeg 命令构造、错误分类。不依赖 GPU。
- **协议测试**：用 mock ComfyUI（本地 FastAPI 假服务）测试提交、轮询、上传、中断、错误分类。
- **前端**：关键组件 Vitest；主要流程用 Playwright 冒烟。
- **端到端冒烟项目**：2 镜、低步数、Z-Image + Wan2.2，在真实 ComfyUI 上跑通全流程（手动触发，不进常规测试）。

## 14. 里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M1 主线** | 仓库骨架、配置、SQLite 模型、任务队列（GPU/CPU worker）、ComfyUI 适配（zimage、h3_image、h3_video、wan22_i2v）、LLM 网关（openai / claude / manual）、剧本 → 分镜 → 首帧 → 视频 → 合成；前端项目中心 / 剧本 / 导演台 / 任务 / 设置；CLI；doctor | 一段梗概在网页里走完全流程，产出带 H3 原生声音的成片 |
| **M2 一致性** | StyleBible、定妆照、9 格三视图、造型变体、场景/道具图、参考图打包、首尾帧与续接、多候选挑选、提示词版本、过期传播；资产页 | 《提词员》主角在 20 镜以上的成片中辨识度稳定 |
| **M3 后期** | seedvr2、rife、字幕、片尾卡、AI 标识、edge-tts 配音、双画幅导出、素材包、agnes 引擎、krea2；后期与导出页 | 输出 1080×1920 带字幕、可直接发布的成片 |
| **M4 导入与集成** | 小说拆集、story-* 导入、MCP server、ai_videos yaml 迁移 | 在 Claude Code 中一句话完成写分镜并跑完关键帧 |

每个里程碑单独写实施计划。

## 15. 不做（第一版）

多用户/登录/远程访问、云端部署、LTX-2.3、局部重绘与换装视频（edit / animate / v2v）、Blender 白膜、剪映草稿导出、AI 自动质量评分、口型同步。

## 16. 风险与待验证

| 风险 | 应对 |
|---|---|
| 16GB 显存下 H3 + 大编码器换模型耗时长 | 同族任务合批；保留 4B ClipProj 编码器选项 |
| 64GB 内存在 offload 时吃紧（实测空闲仅约 7.5GB） | doctor 报告内存；LM Studio 与 ComfyUI 互斥 |
| H3 远景人脸崩、跨镜漂移 | 三视图按机位选参考格；分镜提示词规避极远景人脸；多候选挑选 |
| SeedVR2 在 16GB 上处理 1080p 的速度/显存 | M3 先实测，必要时分块处理或改用图像超分逐帧处理 |
| LM Studio 卸载接口的可用性 | M1 实测 `lms` CLI 与 REST 接口，择一 |
| Claude Agent SDK 在 Windows 上的调用方式 | M1 实测，`claude -p` 作为兜底 |
| 第三方许可 | BigBanana 只借鉴思路；所有提示词与代码自写 |
