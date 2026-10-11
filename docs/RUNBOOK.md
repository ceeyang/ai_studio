# 运维手册

本机跑 AI Studio 时的日常操作：启动、检查、备份、升级，以及常见故障怎么处理。安装和配置见 [README](../README.md)。

标了 `AUTO-GENERATED` 的段落是照代码整理的（命令、接口、路径都核对过源码），代码改了要跟着重新生成；其余是手写说明。

## 启动 / 重启

<!-- AUTO-GENERATED:runbook-start START -->
<!-- 来源：pyproject.toml [project.scripts]、backend/app/main.py（create_app / install_frontend / run）、backend/config.py（load_config）、config.example.yaml -->
1. **配置**：第一次先 `cp config.example.yaml config.yaml`（Windows：`Copy-Item config.example.yaml config.yaml`）再按本机改。
   读配置的顺序是：环境变量 `STUDIO_CONFIG` 指的文件 → 仓库根目录 `config.yaml` → `config.example.yaml`；同时读仓库根目录的 `.env`。
2. **前端构建**：`cd frontend && npm ci && npm run build`，产物在 `frontend/dist`。
   后端按 `config.yaml` 的 `frontend_dist`（默认 `frontend/dist`）托管前端；目录里没有 `index.html` 时只提供 API。
3. **启动**：仓库根目录 `uv run studio-server`，监听 `server.host:server.port`（默认 `127.0.0.1:8700`），打开 http://127.0.0.1:8700。
   启动时会自动：写日志到 `logs/`、把数据库 `<server.data_dir>/studio.db`（默认 `projects/studio.db`）用 Alembic 升到最新、启动任务队列。
4. **重启**：在跑服务的终端按 Ctrl+C，再 `uv run studio-server`。
   重启前还在「运行中」的任务会被标成失败（错误信息 `interrupted：服务重启时任务仍在运行，可一键重试`），排队中的任务不受影响，重启后接着跑。
   所以重启前最好先看一眼 `uv run studio queue status`，等运行中的任务结束，或者重启后去重试。
5. **开发态**：后端照样 `uv run studio-server`，另开终端 `cd frontend && npm run dev`（http://127.0.0.1:5173）；
   Windows 可以用 `powershell -ExecutionPolicy Bypass -File start.ps1` 一次启动两个。
<!-- AUTO-GENERATED:runbook-start END -->

服务只接受本机访问（Host 不是 `127.0.0.1` / `localhost` 的请求一律 400），不要把 `server.host` 改成 `0.0.0.0` 指望局域网访问。

## 健康检查

<!-- AUTO-GENERATED:runbook-health START -->
<!-- 来源：backend/app/routes/system.py、backend/app/routes/jobs.py、cli/studio.py（doctor / queue） -->
| 检查 | 命令 | 正常时 |
|---|---|---|
| 服务活着 | `curl -s http://127.0.0.1:8700/api/health` | `{"ok": true, "version": "0.1.0"}` |
| ComfyUI 和浏览器扩展 | `curl -s http://127.0.0.1:8700/api/system/health` | `comfyui.ok` 和 `browser_ext.ok` 都是 `true`（顶栏状态灯用的就是这个；ComfyUI 探测 1 秒超时，连上的结果缓存 15 秒） |
| 任务队列 | `curl -s http://127.0.0.1:8700/api/queue` 或 `uv run studio queue status` | `paused: false`；`counts` 里 queued / running / waiting_input 的数量 |
| 完整环境检查 | `uv run studio doctor`（接口 `GET /api/doctor`，设置页「环境检查」同一份） | 没有 `×`；有失败项时命令退出码为 1。检查 ComfyUI、各引擎的节点和模型、浏览器扩展和各网站登录、文本模型、API Key、ffmpeg / libass、磁盘、内存 |
| 最近的失败任务 | `uv run studio jobs --status failed` | — |
<!-- AUTO-GENERATED:runbook-health END -->

## 数据库迁移与备份

<!-- AUTO-GENERATED:runbook-db START -->
<!-- 来源：backend/domain/db.py（Database.upgrade、WAL）、backend/app/main.py（lifespan）、backend/migrations/versions/、backend/config.py（data_dir） -->
- **迁移自动跑**：每次 `studio-server` 启动都会执行 Alembic `upgrade head`（`Database.upgrade()`），不需要手动迁移。迁移脚本在 `backend/migrations/versions/`。
- **迁移的性质**：绝大多数是加列、加表；有两个迁移（`ed80f4d77578_shots_autoincrement`、`c8a4f1e2d6b9_shots_autoincrement_again`）会重建 `shots` 表。
  升级到含新迁移的版本之前都应该先备份。
- **数据都在 `projects/`**（`config.yaml` 的 `server.data_dir`）：数据库 `studio.db`（SQLite，WAL 模式，旁边有 `studio.db-wal` / `studio.db-shm`），
  各项目的图片、视频、导出成片，以及任务调用明细的落盘文件（`jobs/<任务 id>/`）。API Key 存在数据库里。
<!-- AUTO-GENERATED:runbook-db END -->

**备份**（服务开着也可以，`.backup` 会带上 WAL 里还没写回的内容；直接拷 `studio.db` 文件可能拷到不完整的数据）：

```bash
sqlite3 projects/studio.db ".backup 'projects/studio.db.bak-$(date +%Y%m%d-%H%M%S)'"
```

图片、视频等媒体文件不在数据库里，要整份留底就把 `projects/` 整个目录另外拷一份（拷之前最好停掉服务）。

**回滚**（升级后出问题）：

1. Ctrl+C 停掉服务；
2. 代码退回升级前的提交：`git checkout <升级前的提交>`（`git log` 里找）；
3. 用升级前的备份覆盖数据库：先删掉 `projects/studio.db-wal`、`projects/studio.db-shm`，再把备份拷回 `projects/studio.db`；
4. `uv sync`、`cd frontend && npm ci && npm run build`，再 `uv run studio-server`。

新版本迁移过的数据库不要直接拿旧代码打开：旧代码的 Alembic 不认识新的迁移版本号，会启动失败。一定要配套恢复备份。

## 常见问题与处理

<!-- AUTO-GENERATED:runbook-faq START -->
<!-- 来源：backend/engines/comfyui/health.py、backend/app/routes/system.py、backend/engines/browser/extension.py（reload_text）、browser-extension/sites/*.js（quota_patterns、Gemini「Start a new chat?」）、backend/doctor.py 和 backend/pipeline/export.py（libass）、backend/domain/api_keys.py、backend/queue/manager.py（pause / retry）、backend/app/routes/jobs.py -->
| 现象 | 原因 | 处理 |
|---|---|---|
| 顶栏 ComfyUI 灯红，提示「连不上 127.0.0.1:8188（…）」；本机引擎在下拉里标用不了 | ComfyUI 没开，或 `config.yaml` 的 `comfyui.url` 不对 | 启动 ComfyUI，核对 `comfyui.url`；`curl -s http://127.0.0.1:8700/api/system/health` 看 `comfyui.ok`。显存没还回去可以调 `POST /api/queue/free-gpu`（有 GPU 任务在跑时返回 409） |
| 「浏览器扩展没有连上」，网页出图 / 出视频 / 网页文本模型失败 | 扩展没装、浏览器没开，或扩展连的不是这个 studio | 在常用的 Chrome / Edge 里按 README 加载 `browser-extension`，浏览器保持打开；扩展通过 `ws://127.0.0.1:8700/api/browser/ext` 连接 |
| 「浏览器扩展需要重新加载：现在连着的是 vX…」 | 代码更新了扩展，浏览器里还是旧版本 | 在 `chrome://extensions`（Edge 是 `edge://extensions`）找到「AI Studio 网页出图桥」点「重新加载」，再重试任务 |
| Gemini 出视频时页面弹「Start a new chat?」 | 在已有对话里点做视频，Gemini 只能在新对话里做 | 已自动处理（扩展会点 New chat），不用管 |
| 导出时 AI 角标 / 字幕烧不上，doctor 里 ffmpeg 一项提示「没有 libass」 | ffmpeg 编译时没带 libass（macOS 上 Homebrew 的 ffmpeg 8 默认就没有） | 换带 libass 的 ffmpeg，macOS：`brew install ffmpeg-full`，再把 `config.yaml` 的 `ffmpeg.ffmpeg` / `ffmpeg.ffprobe` 指过去（不在 PATH 里时写完整路径），重启服务 |
| 即梦 / 可灵 / ChatGPT / Gemini / Muse 任务失败，提示「出图次数到上限或被限流」（错误类型 `quota`） | 网站账号的积分 / 额度用完，或被限流（扩展识别「积分不足」「额度不足」「今日上限」等文案） | 去网站上充值或等额度恢复后重试；或者给这个镜头换一个引擎 |
| API 引擎在下拉里不可选，或任务报没配 Key | 对应服务没配 API Key | 网页设置页「API 服务」里填，或设环境变量（见 README「引擎配置说明」）后重启 |
| 任务一直排队不动 | 队列被暂停了 | `uv run studio queue status` 看是否「已暂停」，`uv run studio queue resume`（或 `POST /api/queue/resume`）恢复 |
| 任务失败 | 看任务面板里的日志和「调用明细」，或 `uv run studio job show <id>` | 修好原因后重试：`uv run studio job retry <id>`，或 `curl -s -X POST http://127.0.0.1:8700/api/jobs/<id>/retry -H 'Content-Type: application/json' -d '{}'`。只有失败或已取消的任务能重试，重试会提交成一个新任务 |
<!-- AUTO-GENERATED:runbook-faq END -->

网页出图类问题（网站改版、找不到按钮）的排查和报告方式见 [CONTRIBUTING.md](../CONTRIBUTING.md) 的「网站改版 / 选择器失效」。

## 日志位置

<!-- AUTO-GENERATED:runbook-logs START -->
<!-- 来源：backend/logging_setup.py、backend/config.py（log_dir）、backend/app/routes/jobs.py（traces） -->
- 后端日志：`logs/studio.log`，每天零点轮转（旧文件名带日期后缀，如 `studio.log.2026-10-11`），保留 14 天。
- 单个任务的日志：任务面板，或 `uv run studio job show <id>`（显示最后 20 行）。
- 任务调用明细（每次 LLM 调用的提示词和回复、引擎提交的提示词 / 参考图 / 参数、ffmpeg 命令，Key 已掩码）：任务面板「调用明细」，
  接口 `GET /api/jobs/<id>/traces`；过长的字段落盘在 `projects/jobs/<id>/`。
<!-- AUTO-GENERATED:runbook-logs END -->

## 升级流程

<!-- AUTO-GENERATED:runbook-upgrade START -->
<!-- 来源：pyproject.toml、frontend/package.json、backend/domain/db.py -->
1. 看一眼 `uv run studio queue status`，等运行中的任务结束（或接受它们重启后要重试）；
2. 备份数据库（见上面「备份」）；
3. Ctrl+C 停掉服务；
4. `git pull`
5. `uv sync`
6. `cd frontend && npm ci && npm run build && cd ..`
7. `uv run studio-server`（启动时自动跑数据库迁移）；
8. 如果这次更新改了 `browser-extension/`，到 `chrome://extensions` 里把扩展「重新加载」；
9. `uv run studio doctor` 确认环境正常。
<!-- AUTO-GENERATED:runbook-upgrade END -->
