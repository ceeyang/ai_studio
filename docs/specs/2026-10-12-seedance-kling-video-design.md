# 视频引擎：Seedance（即梦 / 火山方舟）与可灵，网页 + API 两种调用，首帧与参考图直出两种模式

日期：2026-10-12　状态：设计已确认，待写实施计划

## 1. 目标

让 studio 能用国内漫剧最主流的两家视频模型出镜头视频，每家两种调用方式：

| | 网页（会员 / 免费额度，走用户已登录的浏览器） | API（按量付费，稳定） |
|---|---|---|
| Seedance（字节） | 即梦 jimeng.jianying.com / 海外 Dreamina | 火山方舟 Ark / 海外 BytePlus ModelArk |
| 可灵（快手） | 可灵国内站 / 可灵国际站 | 可灵开放平台（北京 / 新加坡） |

每个镜头可以选两种生成方式：

- **首帧模式**（现有流程）：先出首帧（参考图锁脸）→ 首帧（+ 可选尾帧）图生视频。
- **参考图直出**：不出首帧，用场景图、构图参考、角色定妆照 / 三视图、道具图直接出视频（Seedance 2.0 最多 9 张）。

国内、海外两个区域都支持：默认国内，设置页切换（开源用户两边都有）。

非目标：文生视频（不带任何图）、参考视频 / 参考音频输入、多镜头一次生成（multi-shot）、按镜头难度自动推荐引擎（另一个子项目）。

## 2. 分步

| 步 | 内容 | 用户能用的东西 |
|---|---|---|
| D1 | 即梦 / Dreamina + 可灵网页版，首帧模式（站点支持时带尾帧） | 用网页额度出视频 |
| D2 | 火山方舟 + 可灵开放平台 API，首帧模式（带尾帧） | 用 API 出视频 |
| D3 | 参考图直出模式，网页和 API 引擎都覆盖 | 镜头可以跳过首帧 |

每步单独写实施计划、单独验收。顺序由用户定：网页先做（省钱、作者自己先用起来）。

## 3. D1：网页版

### 3.1 先摸页面（探查，有用户配合）

现有扩展只能在已登记的站点上执行固定的 DOM 操作，没法探索新网站。探查用 Playwright：

- 首选：连用户自己的 Chrome（chrome://inspect 远程调试，和 2026-10-11 网页出视频那次一样），已登录的状态直接可用；
- 退路：Playwright 开一个独立配置目录的 Chrome（放在 scratchpad），用户在里面登录一次。

两个站点、两个区域各记录：

1. 视频生成页的入口地址；切到「视频生成」的入口（如果和出图同页）；
2. 模型选择（Seedance 2.0 / 可灵 v3 等）、画幅、时长、清晰度、音频开关的控件和可选值；
3. 首帧、尾帧上传控件（`input[type=file]` 还是只能拖拽 / 粘贴），上传完成的标志；
4. 提示词输入框、生成按钮（可点 / 不可点的状态）；
5. 结果区：新任务卡片怎么出现、生成中 / 完成 / 失败的标志、原视频地址（blob 还是 CDN 签名地址）、下载按钮；
6. 积分 / 额度显示，积分不足、内容不合规、排队过长的提示文字；
7. 登录态：cookie 名、未登录时跳转的域名或登录墙选择器；
8. 有没有风控（滑块验证、频繁操作提示）；
9. 参考图模式（即梦「全能参考 / 多图参考」、可灵「多图参考」）的入口和上传上限，给 D3 用。

产出：`docs/notes/2026-10-12-jimeng-kling-web.md`（选择器、实测时长、输出规格、截图）和裁剪过的页面片段（单测 fixture）。

### 3.2 扩展：生成器页流程

现有 `lib/flow.js` 是聊天页流程（输入框 → 发送 → 等助手回复里的图 / 视频）。即梦、可灵是生成器页，流程不同，
新加 `runGenerator(spec, job, driver)`，和聊天流程并列，只在扩展里有（同现在的出视频）：

1. 打开 `spec.generate_url`（按区域取地址），检查登录（`login_hosts` / `login_wall` / `session_cookies`）；
2. 按 `spec.video_tab` 切到视频生成（没有就跳过）；
3. 依次选模型、画幅、时长（`spec.options`：每项一个打开菜单的选择器 + 按文字匹配的选项；找不到要的值时报 `page` 错并列出页面上的可选值）；
4. 上传首帧（和尾帧，站点支持且请求带尾帧时），等缩略图 / 上传完成标志；
5. 输入提示词（一行，同现有 `web_prompt` 规则）；
6. 记下结果区已有的卡片（id 或视频地址），点生成；
7. 轮询新出现的卡片：完成 → 取视频；失败 → 读失败原因；超时 → `timeout`；
8. 页面里 fetch 原视频（blob 或 CDN 地址；CDN 地址在页面上下文里取），分块传回（复用现有分块协议）。

错误分类沿用现有 `FlowError` kind：`not_logged_in` / `quota`（积分不足）/ `refused`（内容不合规）/
`timeout` / `no_image`（没出视频）/ `page`（页面结构变了，说清楚哪一步）/ `browser`。中文原因写清楚怎么办。

站点配置：`browser-extension/sites/jimeng.js`、`kling.js`，每个文件里一个区域一份地址（国内 / 海外），
选择器尽量共用；页面结构不同的地方单独写。和 `muse.js` 一样在文件头写清楚「改版时最容易坏的几项」。

### 3.3 后端引擎

`backend/engines/browser/engines.py` 加两个视频引擎，沿用 `_BrowserVideoEngine`：

| 引擎 | 站点 | 说明 |
|---|---|---|
| `jimeng_video` | jimeng / dreamina | Seedance，有声（以实测为准） |
| `kling_web_video` | kling_cn / kling_global | 可灵，尾帧以实测为准 |

- `caps` 按实测填：`duration_control="param"`（页面上选），`allowed_seconds` 填页面可选档位，`has_audio`、
  `supports_end_frame`、输出尺寸、fps 以实测为准；
- 请求里带 `generator: true`、要选的模型 / 画幅 / 时长，首帧（和尾帧）走现有参考图传输；
- 区域：设置页网页出图那一栏加「即梦区域」「可灵区域」（国内 / 海外），存 app_settings，默认国内；
  引擎 `build()` 按区域选站点 key；
- 每个引擎族一条串行通道（现有 `BROWSER_FAMILIES` 自动生效）；
- 扩展版本号升一档，后端 `supports("generator")` 判断扩展够不够新，不够时引擎下拉里提示「扩展需要重新加载」（复用引擎状态那套）。

### 3.4 测试

- JS 单测：`runGenerator` 用假 driver 跑通成功、未登录、积分不足、内容不合规、超时、选项找不到；
- 选择器 fixture：探查时保存的页面片段，检查选择器在 fixture 上能命中；
- 端到端冒烟：`browser-extension/dev/smoke.py` 加一个假的生成器页面（同现有假 ChatGPT 页面）；
- 后端：引擎 `build()` 的请求结构、区域切换、caps；
- 实跑：两个站点各用作者账号出一条 5 秒视频，记进站点笔记。

## 4. D2：API 版

### 4.1 服务与区域

`backend/domain/api_keys.py` 的 `SERVICES` 加两项，设置页「API 服务」自动出现：

| 服务 | 国内 base URL | 海外 base URL | 认证 |
|---|---|---|---|
| `ark` | `https://ark.cn-beijing.volces.com/api/v3` | `https://ark.ap-southeast.bytepluses.com/api/v3` | `Authorization: Bearer <API Key>` |
| `kling` | `https://api-beijing.klingai.com` | `https://api-singapore.klingai.com` | API Key（Bearer），或 AccessKey + SecretKey 每次签一个短期 JWT（HS256，iss=AK，exp=now+1800，nbf=now-5） |

区域是服务上的一个选项（国内 / 海外），决定默认 base URL；用户自己填了 base URL 时以填的为准。
可灵两种 Key 都收：填了 API Key 用 API Key，否则用 AK/SK 签名。签名只在 runner 发请求时现算，不进 workflow 文件。

### 4.2 引擎

`backend/engines/api/engines.py` 加 `SeedanceEngine`（`seedance_video`，service `ark`）、`KlingEngine`
（`kling_video`，service `kling`）。默认模型 Seedance 2.0、可灵 v3（设置页可改）。
时长档位、画幅、尾帧、是否有声以官方文档为准（实施时逐项核对，写进引擎 docstring）。

顺带修 `_ApiVideoEngine.build`：现在一律丢掉参考图和尾帧；改成 `caps.supports_end_frame` 时带尾帧、
`caps.max_refs > 0` 时带参考图（D3 用），否则照旧记进 `dropped_refs`。

### 4.3 执行

`backend/engines/api/runner.py` 加 `_seedance`、`_kling`：

- Seedance：`POST /contents/generations/tasks`，`content` = 文本 + `image_url`（首帧 `role=first_frame`、
  尾帧 `role=last_frame`，图片用 base64 data URI），参数 `ratio`、`duration`、`resolution`、`generate_audio`；
  轮询 `GET /contents/generations/tasks/{id}` 到 `succeeded`，下载 `content.video_url`（24 小时有效，立刻下载）；
- 可灵：`POST /v1/videos/image2video`（`model_name`、`image`、`image_tail`、`prompt`、`negative_prompt`、
  `mode`、`duration`、`aspect_ratio`），轮询 `GET /v1/videos/image2video/{task_id}` 到 `succeed`，
  下载 `task_result.videos[0].url`；
- 都复用现有 `_poll`（取消、进度）和 `_download`（CDN 地址不带 Key）。

### 4.4 测试

假 HTTP transport 测请求结构、轮询、失败（内容审核、额度不足、401）、超时、Key / 签名不进文件和日志；
JWT 签名单测；最后用作者的 Key 各实跑一条。

## 5. D3：参考图直出

### 5.1 数据

`shots` 表加 `video_mode`（`first_frame` | `refs`，null = `first_frame`），Alembic 迁移。
`VideoCaps` 加 `ref_mode: bool`（能不能不用首帧、只用参考图出视频），参考图上限沿用 `max_refs`。
`VideoRequest.first_frame` 改成可选（`Path | None`）；首帧模式下仍然必填，由流水线保证。

### 5.2 流水线

- 关键帧阶段：`video_mode=refs` 的镜头跳过（日志写一句「这一镜参考图直出，不出首帧」）；
- 视频阶段：参考图直出时不要求选定首帧；参考图按出首帧的同一套打包（`shot_refs`：场景图 → 构图参考 →
  角色图 → 道具图），超过 `caps.max_refs` 时先保住每个角色一张（`split_refs`）；
- 输入哈希按参考图算（换了定妆照、场景图 → 视频过期）；
- 自检：跳过首帧相似度（没有首帧），保留中段叠入参考图检测；
- 引擎不支持 `ref_mode` 时提交前就拒绝，说清楚换哪个引擎；
- 「接上一镜」（用上一镜尾帧当首帧）和参考图直出互斥：选了接上一镜就按首帧模式。

### 5.3 界面

- 镜头抽屉加「生成方式」下拉（首帧 / 参考图直出）；当前视频引擎不支持参考图直出时这一项禁用并说明；
- 参考图直出时首帧区折叠成「用到的参考图」列表（复用资产选择器）；
- 任务详情的「调用明细」里参考图照现有格式列出。

### 5.4 网页版的参考图直出

D1 探查时记录的参考图模式，D3 时给生成器流程加「参考图模式」（切到参考模式 → 上传多张图），站点支持就打开 `ref_mode`。

### 5.5 测试

流水线单测：参考图直出跳过首帧、打包和截断、哈希过期、和接上一镜互斥、引擎不支持时拒绝；前端单测：下拉禁用和切换。

## 6. 风险

- 网页改版会让选择器失效：站点文件头写清楚最容易坏的几项，fixture 测试能尽早发现；
- 网页额度有限，可灵国内站每日免费积分少；积分不足要明确报 `quota`，不要重试；
- 站点有风控时（滑块、频繁操作），流程停下来报错，提示用户去页面上手动处理后重试；
- API 文档细节（时长档位、尾帧是否要 pro 模式、参考图上限）以实施时的官方文档为准，不对的地方回写本文档；
- 两种 API Key 和 JWT 签名不能写进任务目录的 `workflow.api.json` / `call.json`（现有约束）。
