# 网页出图 / 出视频扩展：Muse、Gemini 视频、会话复用

日期：2026-10-11　状态：已实施（会话复用最终用上游 ade14f8「网页会话续接」，方案 A 弃用，见 §3）

## 1. 目标

1. 接入 Meta Muse 网页版（muse.ai，Meta / Facebook 账号登录）：出图 `muse_image`、出视频 `muse_video`。
2. 接入 Gemini 网页版出视频（Veo，「Create video」工具）：`gemini_video`。
3. 网页任务复用对话：用上游的「网页会话续接」（按角色 / 镜头 / 场景 / 道具、按集按步骤），出视频接进去。
4. 每个视频引擎精确记录：能不能指定时长、怎么指定、默认时长、实测出过的时长。

全部走现有的浏览器扩展（AI Studio 网页出图桥），用户平时用的、已登录的浏览器；不用 API key、不占显卡。
调试阶段允许用 Playwright 连用户的 Chrome（chrome://inspect 远程调试）看页面，正式功能不依赖它。

## 2. 实测记录（2026-10-11，用户的 Chrome 154，已登录）

| 引擎 | 结果 | 用时 | 输出 |
|---|---|---|---|
| Muse 出图 | 能 | ~24 s | 1152×2048 webp，画幅按提示词（9:16 正好） |
| Muse 文生视频 | 能 | ~60 s | 704×1248 h264 + aac，24 fps |
| Muse 首帧生视频 | 能 | ~60 s | 同上；首帧和上传图 SSIM 0.90 |
| Gemini 首帧生视频 | 能 | ~170 s | 720×1280 h264 + aac，24 fps，10.0 s；首帧 SSIM 0.88 |

时长控制见 §6。

### 页面要点

**Muse（muse.ai）**：常驻智能体，一个主对话 + side chat。`https://muse.ai/thread/new` 开新 side chat，
发出第一条后地址变成 `https://muse.ai/thread/<uuid>`（可以直接打开继续）。

- 输入框 `textarea[aria-label="Message"]`；发送 `button[aria-label="Send"]`（有附件或文字时出现），回车也能发；
  生成中 `button[aria-label="Stop"]`。
- 上传：隐藏的 `input[type=file]`（multiple，无 accept），DataTransfer 塞进去 + input/change 事件即可；
  缩略图旁有 `button[aria-label="Remove attachment"]`。
- 消息：`[data-message-role="assistant"|"user"]`，带 `data-message-id`。
- 出图结果：`[data-message-role="assistant"] [data-testid="hatch-chat-attachment-presentation-image"] img`，
  地址 `blob:https://muse.ai/…`，页面里 fetch 得到原图（webp）。
- 视频结果：`[data-message-role="assistant"] [data-testid="hatch-chat-attachment-presentation-video"] video`，
  地址 `blob:https://muse.ai/…`，页面里 fetch 得到 mp4；旁边有 `Download video` 按钮。
- 页面上常驻一个 480×480 的头像动画 `<video>`（blob），不在 assistant 消息里，选择器要限定在消息里。
- 登录：未登录时 muse.ai 跳 `auth.muse.ai` → `facebook.com`。

**Gemini 视频**：输入框左边「Upload & tools」（`button[aria-label="Upload & tools"]`）→ 菜单项「Create video」。
第一次会弹介绍框「Create videos / With Gemini Omni」，按钮「Try it」。选中后输入框提示变成
「Describe your video」，出现 `button[aria-label="Deselect Videos"]` 和画幅按钮
`button[aria-label^="Aspect ratio"]` → 菜单 `[role=menuitemradio]` 「Landscape (16:9)」/「Portrait (9:16)」。
首帧图：往输入框粘贴（和出图一样）。结果：回复先说「I'm generating your video…」，几分钟后
`model-response generated-video video`，地址 `https://contribution.usercontent.google.com/download?…`，
页面里 `fetch(credentials: include)` 拿到 mp4；有 `Download video` 按钮。

## 3. 会话复用：用上游的「网页会话续接」，方案 A 弃用

最初定的方案 A（同一网站、同一集的文案 + 出图 + 出视频共用一条对话）实现完后，发现另一边已经合并了
ade14f8「网页会话续接」（backend/engines/browser/conversations.py：出图按 角色 / 镜头 / 场景 / 道具 各一条，
网页文本模型按 集 + 步骤 / 分镜段落 各一条，20 轮换新，界面有「新开对话」开关、会话链接和全局开关）。
两套只能留一套，留上游的：

- 对话短：一集几十个镜头的出图、视频、重试加上剧本、分镜都进一条对话，一集就超过轮数上限，页面越来越慢、
  前文干扰越来越大；按镜头 / 角色分，每条只装同一个东西的几轮。
- 重新生成同一个镜头 / 角色才是续接最有用的地方，上游正好按它分；方案 A 把不同镜头混在一起。
- 不把剧本、分镜的 JSON 请求和出图、出视频混在一条对话里（也省得在长对话里切 Gemini 的视频模式）。
- 上游更完整（「新开对话」开关、会话链接、续接前缀、测试），已经在 main 上。
- 方案 A 唯一多出来的「出图时模型看得到分镜」意义不大：出图提示词本来就完整。

出视频接进上游：任务类型 video.shot 按 shot_id 归到 shot:<id>，和这个镜头的首帧（image.keyframe）在同一网站上
共用一条对话（比如首帧和视频都用 Gemini）；续接时提示词前加 CONTINUE_VIDEO_PREFIX（这是一段新视频，
用这条消息附的图做首帧）。Muse 的对话地址 /thread/<uuid>（conversation_re），/thread/new 和主对话 muse.ai/ 不算。

## 4. 网页出视频

### 引擎

| 引擎 | family | 首帧 | 画幅 | 有声 | fps |
|---|---|---|---|---|---|
| `muse_video` | `muse_web` | 上传首帧，提示词写明「用附图做第一帧」 | 写进提示词：9:16 / 16:9 | 有 | 24 |
| `gemini_video` | `gemini_web` | 粘贴首帧 | 画幅菜单选：9:16 / 16:9 | 有 | 24 |

和同网站的出图引擎共用 family（同一条排队通道、同一个标签页）。`VideoCaps.max_refs = 0`（只传首帧）。

### 扩展

- job 消息加 `kind: "image" | "video"`（缺省 image，老后端兼容）。
- 站点配置加视频字段（两份 SiteSpec 都加，一致性测试照比）：
  - `video_steps`：选中视频工具要点的按钮序列（Gemini：Upload & tools → Create video；Muse 空）；
  - `video_dismiss`：第一次的介绍框按钮（Gemini「Try it」）；
  - `video_aspect_button` / `video_aspect_options`（{"9:16": 选择器, "16:9": 选择器}）：Gemini 用，Muse 空（写进提示词）；
  - `video_active`：视频工具已选中的标志（Gemini `button[aria-label="Deselect Videos"]`），复用对话时先看它，已选中就不再点；
  - `result_videos`、`video_src_hint`、`video_expected_seconds`、`video_timeout`（默认值见 §6）。
- `content/dom.js` 加 `videos(sel)`：返回 `{src, w, h, duration, ready}`（`readyState >= 1`）。
- `flow.js`：`generate()` 按 kind 分支；视频走 `selectVideoTool → upload → submit → waitVideos → download`。
  `waitVideos` 和 `waitImages` 同样的判断（配额、拒绝、登录、稳定 settle 秒、诊断日志），只是看 `<video>`。
  Gemini 先回一句「正在生成」——有「生成中」文字不算「回复了文字但没出视频」：`no_image_grace` 对视频改用
  `video_pending_patterns`（「generating your video」「正在生成」）命中时不计时。
- `sniffMime` 认 mp4（偏移 4 是 `ftyp`）→ `video/mp4`；视频任务只接受视频，图片任务只接受图片。
- 传回：沿用分块的 `image` 消息（字段不变，mime 是 video/mp4），result 里照上游带 conversation {url, continued, note}。
- manifest：host 加 `https://muse.ai/*`、`https://*.muse.ai/*`、`https://contribution.usercontent.google.com/*`；版本 0.4.0
  （上游的会话续接是 0.3.0）；`hello.features` 加 `video`。后端看到没有 video 能力的老扩展，视频任务直接报「扩展要重新加载」。

### 后端

- `BrowserRunner.run`：`call.output_kind == "video"` 时要求站点有 `result_videos`；cdp 方式直接报
  「网页出视频只支持浏览器扩展」。
- 扩展传回后：视频不过 PIL，用 ffprobe 读宽高、时长、有没有音轨（读不出来报 page 错误）；存
  `<site>_00.mp4`，不裁切（画幅已经在页面上选了 / 写进提示词）；日志写实际时长。
- `run_video` 不用改（已经按 `VIDEO_EXTS` 取文件）。

## 5. 其他改动

- 环境检查：`check_browser` 按网站合并引擎，行名改成「Gemini 登录（gemini_image、gemini_video）」；
  前端 `unavailableReason` 的匹配放宽到「、」分隔。Muse 的登录 cookie 实施时看实际 cookie 名再定。
- 设置页：`EngineTable.capsText` 加网页视频的说明（时长控制见 §6）；`SettingsPage.hasBrowserEngine` 算上视频引擎；
  `format.ts` 的 `ENGINE_LABEL` 加三个引擎；`BrowserPanel` / `errors.py` 的文案把 Muse 加进去。
- `config.example.yaml`：三个新引擎。

## 6. 视频时长

每个视频引擎在 `VideoCaps` 里加 `duration_control`：

- `"param"`：引擎参数精确控制（ComfyUI、API Veo 现有引擎都是）；
- `"prompt"`：只能写进提示词，模型可能不听；
- `"fixed"`：不能指定，总是固定长度。

再加 `allowed_seconds`（离散可选值，没有就空）。设置页能力说明照实写。

实测（2026-10-11）：

| 引擎 | 要求 | 实际 |
|---|---|---|
| `muse_video` | 不指定 | 10 s |
| `muse_video` | 3 s | 3 s |
| `muse_video` | 5 s（首帧生视频） | 5 s |
| `muse_video` | 5 s（文生视频） | 10 s |
| `muse_video` | 8 s | 10 s（回复还说「8-second version」） |
| `gemini_video` | 不指定 | 10.0 s |
| `gemini_video` | 4 s | 10.0 s（界面上没有时长选项） |

结论：

| 引擎 | duration_control | allowed_seconds | default / max | 发给网站的时长 |
|---|---|---|---|---|
| `muse_video` | `prompt` | 3, 5, 10 | 10 / 10 | 镜头时长向上取到最近一档（4 s → 5 s），写进提示词「exactly N seconds」；模型可能给 10 s |
| `gemini_video` | `fixed` | 10 | 10 / 10 | 不写；总是 10 s |

视频比镜头长时照现在的流程在剪辑时按镜头时长截（候选的 `meta` 里记要求时长和实际时长）。

## 7. 测试

- 后端 pytest：video.shot 归到 shot 作用域、Muse 对话地址识别、出视频续接同一镜头的对话；
  extension hub 视频结果（ffprobe 校验、mime 分辨）；runner 视频落盘；doctor 合并行；引擎注册和 caps；
  JS / Python SiteSpec 一致性（新字段）。
- 前端 vitest：`flow.test.js` 加视频流程（选工具、介绍框、画幅、正在生成、取 mp4）；`protocol.test.js` 加 mp4 嗅探；
  `EngineTable.test.tsx` 加网页视频说明和合并后的登录行。
- 实跑：重新加载扩展，后端起服务，三个新引擎各通过 API 跑一次（同一镜头连跑两次确认续接了同一个对话）。
