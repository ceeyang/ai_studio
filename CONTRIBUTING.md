# 参与贡献

> **English.** Contributions are welcome. Set up with `uv sync` and `cd frontend && npm install`, run
> `uv run pytest backend/tests` and `cd frontend && npx vitest run` before sending a PR. Commit messages are written
> in Chinese with a conventional prefix (`feat:` / `fix(scope):` / `docs:`), a short summary, then `——` and the details.
> When a web site (ChatGPT, Gemini, Jimeng, Kling …) changes its page and the browser extension stops working,
> please open a "网站改版 / 选择器失效" issue. Issues and discussions in English are fine too.

欢迎提问题、修 bug、加新引擎。项目的界面、文档和提交信息都用中文，写英文的 issue 也没问题。

## 开发环境

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)、Node.js 22.22+ 或 24+、ffmpeg（带 libass）。

```bash
uv sync                                    # 后端依赖（含 pytest）
cp config.example.yaml config.yaml         # Windows：Copy-Item config.example.yaml config.yaml
cd frontend && npm install && cd ..
```

开发态：一个终端 `uv run studio-server`（http://127.0.0.1:8700），另一个终端 `cd frontend && npm run dev`
（http://127.0.0.1:5173，热更新）。Windows 上可以用 `powershell -ExecutionPolicy Bypass -File start.ps1` 一次启动两个。

## 测试

提 PR 前请跑下表里标「PR 前必跑」的四项（和 CI `.github/workflows/ci.yml` 跑的一致），其余按改动范围选跑：

<!-- AUTO-GENERATED:dev-commands START -->
<!-- 来源：pyproject.toml（[project.scripts]、[tool.pytest.ini_options]）、frontend/package.json 的 scripts、browser-extension/dev/smoke.py、start.ps1 -->
| 命令 | 在哪跑 | 作用 | PR 前必跑 |
|---|---|---|---|
| `uv run pytest backend/tests` | 仓库根目录 | 后端测试（单个测试超时 60 秒） | 是 |
| `npm run typecheck` | `frontend/` | TypeScript 类型检查（`tsc --noEmit`） | 是 |
| `npx vitest run`（或 `npm test`） | `frontend/` | 前端组件测试，也包括浏览器扩展的页面脚本测试（`src/extension/`） | 是 |
| `npm run build` | `frontend/` | 类型检查 + 构建到 `frontend/dist` | 是 |
| `npm run e2e` | `frontend/` | 先构建，再跑 Playwright 冒烟（临时数据目录，不需要显卡）；设 `STUDIO_REAL_URL` 时对着已在跑的服务跑 `real-*.spec.ts` | 改界面流程时 |
| `uv run --no-sync python browser-extension/dev/smoke.py` | 仓库根目录 | 浏览器扩展冒烟（临时后端端口 18799 + Playwright 自带 Chromium） | 改扩展时 |
| `npm run proxy-soak -- [前端地址] [秒数] [后端地址]` | `frontend/` | 开发代理回归检查（502 / ECONNRESET / 超时退出码 1） | 改 Vite 代理时 |
| `uv run studio-server` | 仓库根目录 | 启动后端（http://127.0.0.1:8700） | — |
| `npm run dev` | `frontend/` | Vite 开发服务器（http://127.0.0.1:5173，热更新） | — |
| `uv run studio doctor` | 仓库根目录 | 环境检查（要先启动后端），有失败项时退出码 1 | — |
<!-- AUTO-GENERATED:dev-commands END -->

- 测试不碰真实的网站、显卡和付费 API：ComfyUI、网页、API 都有假实现（`backend/tests/fakes/`、`frontend/src/extension/fakePage.js`）。
- 没有 ffmpeg 时相关测试自动跳过；ffmpeg 不带 libass 时有两个烧字幕的导出测试会失败，属于环境问题（CI 用的 Ubuntu ffmpeg 带 libass）。
- 改了界面流程可以再跑 `cd frontend && npm run e2e`（Playwright，临时数据目录，不需要显卡）。
- 改了浏览器扩展可以跑 `uv run --no-sync python browser-extension/dev/smoke.py`：用 Playwright 自带的 Chromium 加载扩展，对着假的 ChatGPT / 即梦页面走一遍，不碰你的浏览器配置。
- 修 bug 时请先写一个能复现的测试。

## 提交信息

参照 `git log` 里的写法：

```
<类型>(<范围，可省>): <一句话说改了什么>——<为什么、怎么改的、边界情况>
```

- 类型：`feat`（新功能）、`fix`（修 bug）、`docs`、`refactor`、`test` 等；范围是模块名，如 `fix(extension):`、`fix(queue):`、`fix(migrations):`。
- 用中文，说用户能感知到的变化；`——` 后面写原因和细节，可以很具体（实测里遇到了什么、为什么这样改）。
- 例子：`fix(queue): 暂停后立刻提交的任务偶尔还是被领走——…领任务时在锁里再看一次暂停`

## 加一个新引擎

先看 `backend/engines/base.py`（引擎接口：`build()` 只把请求拼成调用，执行交给 runner）和
`backend/engines/registry.py`（按 `config.yaml` 的 `engines.<name>` 加载）。按类型：

- **本机 ComfyUI 工作流**：在 `workflows/` 放 API 格式的 `<name>.json` 和参数映射 `<name>.map.yaml`，
  在 `backend/engines/comfyui/engines.py` 加一个引擎类并在 `registry.py` 的 `ENGINE_CLASSES` 里注册，
  `config.example.yaml` 的 `engines:` 下加一行。参考 `zimage`、`wan22_i2v`。
- **API 引擎**：`backend/engines/api/engines.py`（引擎定义）和 `runner.py`（提交、轮询、下载），
  服务的 Key / Base URL / 区域在 `backend/domain/api_keys.py` 的 `SERVICES` 里登记，HTTP 细节在 `backend/llm/api_http.py`。
  参考 `seedance_video`、`kling_video`，测试参考 `backend/tests/test_api_video_seedance_kling.py`。
- **网页引擎（经浏览器扩展）**：两边各一份——
  - 扩展：`browser-extension/sites/<网站>.js`（选择器、页面流程参数），在 `browser-extension/sites/index.js` 里登记；
    通用流程在 `browser-extension/lib/flow.js`。聊天类网站照 `chatgpt.js` 写；生成器页（即梦 / 可灵这种表单式页面）照 `jimeng.js`、`kling.js` 写。
  - 后端：`backend/engines/browser/sites/`（聊天类一个同名文件；生成器页只在 `generators.py` 记名字、地址、登录 cookie），
    引擎定义在 `backend/engines/browser/engines.py`。`backend/tests/test_browser_extension_js.py` 会比对两边是否一致。
  - 扩展的测试在 `frontend/src/extension/*.test.js`（假页面 `fakePage.js`）。
- 新的文本模型提供方：`backend/llm/`（`api_providers.py`、`web_providers.py`）。

新引擎请写清楚：需要什么账号 / Key、费用、时长和画幅限制，以及在 doctor 里怎么检查。

## 网站改版 / 选择器失效

网页版引擎靠页面上的按钮、输入框定位，网站一改版就可能失效。遇到时：

1. 先在设置页「环境检查」或 `uv run studio doctor` 确认扩展连着、网站已登录；扩展有新版本时在 `chrome://extensions` 里点「重新加载」。
2. 在任务面板打开失败的任务，看日志和「调用明细」里报的是哪一步（找不到输入框、上传失败、没出现结果……）。
3. 用「网站改版 / 选择器失效」模板提 issue，写上网站、区域（国内 / 海外）、界面语言、哪一步失败、任务日志。
   截图请遮掉头像、账号名、积分余额等个人信息。
4. 想自己修：出图 / 出视频网站的文件头部（`browser-extension/sites/*.js`）都有「改版时最容易坏的几项」，对着页面改选择器或文案，跑 `npx vitest run` 和扩展冒烟。

## 行为准则

请友善、就事论事。不要在 issue 和 PR 里贴 API Key、cookie、账号信息或未经授权的他人肖像和作品。
