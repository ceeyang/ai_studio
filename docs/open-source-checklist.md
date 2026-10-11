# 开源前检查清单

审计日期：2026-10-12（分支 `feat/video-seedance-kling`，提交 79e6c42，283 个提交，534 个跟踪文件）。

本文只列发现和建议。**没有删除任何文件、没有改写 git 历史**，每一项都由仓库所有者决定怎么处理。
勾选框留给所有者：处理完（或决定保留）就勾上，并在「决定」一栏写一句。

风险等级：**高** = 公开后难以收回、涉及他人隐私或商业信息；**中** = 个人信息或会让别人用不起来；**低** = 观感或维护问题。

---

## 1. 密钥与凭据

扫描方法：在当前跟踪的文件树里用 `git grep` 找常见 Key 格式（`sk-…`、`AIza…`、`ghp_…`、`AKIA…`、`-----BEGIN`、JWT 头），
以及 `api_key / secret / token / password = <长字符串>` 形式的赋值；在全部历史里用 `git log --all -p -G` 再找一遍 Key 格式。

- [ ] **未发现明文密钥**
  - 位置：当前文件树与全部历史均未命中。`config.yaml`、`.env`、`browser-profile/`（cdp 模式的浏览器登录目录）都在 `.gitignore` 里，历史中也没有被提交过。
  - 风险：低。
  - 建议：公开前再用专门工具（如 gitleaks / trufflehog）对全部历史跑一遍做二次确认。
  - 决定：

## 2. 个人信息

- [ ] **个人绝对路径 `C:\Users\cee\…`**
  - 位置：`config.example.yaml`（已在本次提交里改成 `null` + 注释示例）、`docs/specs/2026-10-07-ai-studio-design.md:35`、`docs/superpowers/plans/2026-10-07-m1-backend.md:406`。
  - 风险：中（暴露本机用户名和目录结构）。
  - 建议：文档里的两处改成 `<你的用户名>` 或删掉这一行。
  - 决定：

- [ ] **本机其他路径（`D:\works\ai_videos\…`、`ai_videos\tools\comfy-8189.ps1`）和硬件描述**
  - 位置：Windows 绝对路径出现次数——`docs/specs/2026-10-07-ai-studio-design.md`（9）、`docs/notes/m1-verification.md`（3）、`docs/notes/no1-car-repair-brief.md`（4）、`docs/superpowers/plans/2026-10-07-m1-frontend.md`（3）、`docs/superpowers/plans/2026-10-07-m1-backend.md`（2）。设计文档里还有 GPU / CPU / 内存 / 磁盘余量。
  - 风险：低到中。
  - 建议：设计文档保留但把本机路径换成占位；`m1-verification.md` 是个人实测记录，可整篇移出公开仓库。
  - 决定：

- [ ] **提交作者信息**
  - 位置：git 历史。作者有两种：`cee <ceeyang2024@gmail.com>`（255 个提交）和 `ceeyang <ceeyang@ceeyangs-Mac-Mini.local>`（28 个提交，暴露了机器名）。
  - 风险：中（邮箱公开后会被收录、可能收到垃圾邮件）。
  - 建议：可以接受就不动；不想公开邮箱，有两个办法——(a) 不带历史，用一个新的初始提交公开；(b) 用 `git filter-repo --mailmap` 改写历史（会改变所有提交哈希，要在公开**之前**做）。以后的提交可以用 GitHub 的 `noreply` 邮箱。
  - 决定：

- [ ] **UI 截图里的本机路径**
  - 位置：`docs/notes/ui/*.png`（69 张，约 15 MB）。例如 `docs/notes/ui/2026-10-10-settings-1600.png` 的环境检查表里有 `C:\Users\cee\AppData\Local\Temp\claude\…` 和 `D:\works\ai_studio\browser-extension`。
  - 风险：中。
  - 建议：逐张看一遍；README 需要截图时，用干净的示例项目重新截几张放到 `docs/images/`，其余截图不公开。
  - 决定：

## 3. 客户 / 商业信息（最需要所有者决定）

- [ ] **`docs/notes/no1-car-repair-brief.md`——真实客户的广告需求摘要**
  - 内容：客户姓氏和身份（「鲁总」及其所在集团）、店铺中 / 高棉 / 英文名、门牌地址、电话 / Telegram 号码、Facebook 主页链接、报价、内部交接文档的路径。
  - 风险：**高**（第三方的商业和联系信息，公开前需要客户同意；git 历史里删不干净）。
  - 建议：不公开。要公开仓库的话，在**不带历史**的新仓库里不放这个文件，或者用 `git filter-repo --path docs/notes/no1-car-repair-brief.md --invert-paths` 从历史中去掉。
  - 决定：

- [ ] **客户店名出现在代码注释和测试数据里**
  - 位置：`backend/post/timeline.py:6`（注释），`backend/tests/test_api_post.py`、`test_cli.py`、`test_post_ffmpeg.py`、`test_post_pipeline.py`、`test_post_units.py`，`frontend/src/pages/director/EndCardPanel.test.tsx`（「柬埔寨1号铭车」及其高棉语名，当作术语表 / 片尾卡测试数据）。
  - 风险：中（只有店名，没有联系方式，但能对上客户）。
  - 建议：测试数据换成虚构店名（如「示例汽修」），注释里换成「某条汽修广告」。改测试要同步改断言。
  - 决定：

- [ ] **客户项目的角色定妆照 / 三视图**
  - 位置：`docs/notes/ui/turnaround-sheet-*.png`、`turnaround-*.png`（真人风格的「陈师傅」三视图，属于上面那个客户项目）。
  - 风险：中到高（如果参考图来自真人照片，涉及肖像权；就算是纯 AI 生成，也是客户项目素材）。
  - 建议：不公开。
  - 决定：

- [ ] **对私有旧仓库 `ai_videos` / `vglib` 的引用**
  - 位置：`backend/engines/comfyui/client.py:1`、`export_ui.py:1`、`backend/post/__init__.py:1`（「移植自 ai_videos/vglib…」），`backend/tests/test_engines.py:159`，`workflows/*.map.yaml` 头部注释，以及多篇设计文档。
  - 风险：低（只是名字）。需要确认：移植过来的代码是不是全部是你自己写的（`export_ui.py` 写了「自有代码」）——如果 vglib 里有别人的代码，开源前要确认许可证。
  - 建议：确认版权归属后，可以把「移植自 ai_videos/…」改成「最初来自作者早期的私有脚本」。
  - 决定：

- [ ] **工作区外未跟踪的笔记**
  - 位置：主工作区里有一个未跟踪的 `docs/notes/2026-10-12-jimeng-kling-web.md`（不在本分支中，本次未审阅）。`browser-extension/sites/jimeng.js` 的文件头注释引用了它（「页面探查记录见…」），现在这个链接在仓库里是断的。
  - 风险：未知（实测记录里可能有账号、积分余额、页面截图之类的信息）。
  - 建议：看一遍内容，去掉个人信息后提交，或者把 `jimeng.js` 里的引用删掉。
  - 决定：

## 4. 第三方网站与模型

- [ ] **浏览器扩展会自动操作第三方网页（ChatGPT、Gemini、Claude、Muse、即梦 / Dreamina、可灵）**
  - 位置：`browser-extension/`（`manifest.json` 的 host_permissions 列了这些域名）、`backend/engines/browser/`。
  - 风险：中。很多网站的服务条款不允许自动化访问，用户的账号可能因此被限制。这是用户自己的风险，但项目公开后应当写清楚。
  - 建议：README 的「合规提示」里已经写了一段提醒（本次提交）；要不要再在扩展首次连接时弹一次提示，由你决定。
  - 决定：

- [ ] **ComfyUI 工作流里点名的模型**
  - 位置：`workflows/*.map.yaml`（如 `moodyCutieMixKrea2_v20.safetensors`、`MiniMaxH3\minimax_h3_*.safetensors`），对应的 `workflows/*.json`（模型名字段是空的）。
  - 风险：低。仓库里只有模型文件名，不带权重；各模型有自己的许可证（有的社区模型禁止商用），用户要自己下载并遵守。
  - 建议：README 写明「模型需自行下载，遵守各自许可证」（本次已写）；可以考虑在每个 map 文件头部加上模型来源链接和许可证。
  - 决定：

- [ ] **`claude-agent-sdk` 依赖**
  - 位置：`pyproject.toml`。
  - 风险：低。需要确认这个包的许可证和你选的开源许可证兼容（作为依赖安装，不随仓库分发）。
  - 决定：

## 5. 平台可移植性（会让 macOS / Linux 用户用不起来的地方）

- [ ] **`start.ps1`（开发态一键启动）只有 Windows 版**
  - 风险：低。
  - 建议：README 里写了 macOS / Linux 开两个终端分别启动的方法（本次）；要不要加一个 `start.sh`，由你决定。
  - 决定：

- [ ] **字幕字体默认值偏 Windows**
  - 位置：`backend/config.py` 里 `_windows_fonts()`（不设 `fonts_dir` 时默认 `%WINDIR%\Fonts`，非 Windows 上是 `C:\Windows\Fonts`，找不到就跳过、libass 回退到系统字体）；`config.example.yaml` 的 `zh_font: Microsoft YaHei`、`km_font: Khmer UI`。
  - 风险：低（不会报错，但 macOS / Linux 上字体会被替换）。
  - 建议：README 已提示在 `config.yaml` 里换成本机有的字体；以后可以按平台给默认值。
  - 决定：

- [ ] **macOS 的 Homebrew ffmpeg 8 不带 libass**
  - 位置：`backend/post/render.py` 的 `ffmpeg_has_filter` 注释；`backend/doctor.py` 已经会提示「没有 libass」。本机跑测试时有 2 个和 libass 相关的用例失败，属于已知环境问题。
  - 风险：低。
  - 建议：README 已写；CI 用 Ubuntu 的 ffmpeg（带 libass）。
  - 决定：

## 6. 大文件与 git 历史

- [ ] **截图占了仓库体积的大头**
  - 当前文件树：`docs/notes/ui/` 下 69 张 PNG，最大的 1.4 MB（`turnaround-sheet-face.png`），合计约 15 MB；其次是 `uv.lock`（约 0.5 MB）和两份 M1 计划文档（0.35 MB、0.26 MB）。没有模型权重、视频、数据库之类的大二进制文件。
  - 历史中最大的对象：同上这些 PNG；历史里删过的文件只有 8 张 `docs/notes/ui/2026-10-10-*.png` 截图和几个旧源码文件，没有大文件。
  - 风险：低。
  - 建议：如果第 3 节决定不公开截图 / 客户文件，最省事的做法是**不带历史**公开（新建一个干净的初始提交）；想保留提交历史，就用 `git filter-repo` 在公开前把这些路径从历史里去掉（会改变所有提交哈希，旧的克隆要重新克隆）。
  - 决定：

- [ ] **`docs/superpowers/plans/`、`docs/notes/` 是开发过程记录**
  - 内容：给 AI 代理的逐步实现计划、实测记录、遗留问题清单。
  - 风险：低。对贡献者有参考价值，但很长，有的写的是早期 Windows 本机环境。
  - 建议：保留或移到 `docs/archive/`，由你决定。
  - 决定：

## 7. 许可证（由所有者选择）

本仓库现在**没有** `LICENSE` 文件。没有许可证时，按默认的著作权规则，别人只能看，不能合法地复制、修改或分发。
下面是三种常见选择对这个项目意味着什么（只做对比，不做推荐）：

| | MIT | Apache-2.0 | AGPL-3.0 |
|---|---|---|---|
| 别人能不能商用、改了闭源 | 能 | 能 | 能商用，但分发或**通过网络提供服务**时必须公开修改后的源码 |
| 必须保留的东西 | 版权声明和许可证全文 | 版权声明、许可证、`NOTICE` 文件；改过的文件要标明 | 版权声明、许可证；衍生作品整体用 AGPL-3.0 |
| 专利 | 没有明确条款 | 贡献者明确授予专利许可；起诉专利侵权的人许可自动终止 | 有专利许可条款（GPLv3 体系） |
| 对这个项目的影响 | 最宽松、最容易被采用；有人把它包装成收费的在线漫剧平台、不回馈代码，也合规 | 和 MIT 一样宽松，多了专利保护和更正式的贡献条款，对公司用户更友好 | 有人拿去做在线服务就必须开源他们的改动；会让一部分公司不敢用 |
| 和依赖的兼容性 | 和 FastAPI、React 等常见依赖都兼容 | 同左 | 可以使用 MIT / Apache 依赖；反过来，别的宽松许可证项目不能直接并入 AGPL 代码 |

另外要考虑：
- 浏览器扩展（`browser-extension/`）可以和主体用同一个许可证，也可以单独选；
- 如果以后想同时提供商业版本，AGPL + 另售商业授权是常见模式，但需要所有贡献者签贡献者协议（CLA），否则别人贡献的代码你不能另行授权；
- 选定后：在根目录加 `LICENSE`，把 README「许可证」一节改成对应名字，`pyproject.toml` 和 `frontend/package.json` 可以加上 `license` 字段。

- [ ] 选定许可证并添加 `LICENSE`
  - 决定：

## 8. 本次已经做了的（不涉及所有者决定）

- `config.example.yaml`：`comfyui.models_dir` 从个人路径改成 `null`，加了各平台示例；补充了各 API 服务的环境变量名（`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GEMINI_API_KEY` / `GOOGLE_API_KEY`、`ARK_API_KEY`、`KLING_API_KEY` 或 `KLING_ACCESS_KEY` + `KLING_SECRET_KEY`，以及可选的 `*_BASE_URL`，名字取自 `backend/domain/api_keys.py`）。
- README 重写（去掉「私有」，补全中文说明和英文摘要）、新增 `CONTRIBUTING.md`、`.github/` 下的 issue / PR 模板和 CI 工作流。
- 没有添加 `LICENSE`，没有删除文件，没有改写历史。
