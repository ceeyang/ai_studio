# M1 实测记录（2026-10-08）

环境：Windows 11 Pro，RTX 5080 16GB，64GB 内存，ComfyUI 0.38.1（隔离实例 `127.0.0.1:8189`，由 `ai_videos\tools\comfy-8189.ps1` 启动，模型路径复用 Desktop 的 `shared_model_paths.yaml`），Claude Code CLI 2.1.241，ffmpeg 8.1.2。

## Claude provider
- Agent SDK：可用。`claude_agent_sdk` 用自带的 `_bundled\claude.exe`，没有出现"退回 claude -p"的告警；selftest 5.8s，输出 `通过：ok=True word='你好'`。
- claude -p：可用；--system-prompt-file 参数：有（`claude --help` 不单独列出，但 `--bare` 的说明里写了 `--system-prompt[-file]`，`--no-sdk` 实测通过，7.9s）。
- 注意：`claude -p` 每次调用 cache_creation_input_tokens ≈ 63k（加载了用户级设置/插件上下文），SDK 路径不返回 usage。剧本、分镜两次调用各约 60s。
- 结论：config 里 use_sdk = true（保持默认，不用改）。

## LM Studio
- 模型：磁盘上有 `Gemma4-26B-A4B-Uncensored-HauhauCS-Balanced`、`Qwen3.5-9B-Uncensored-HauhauCS-Aggressive`（`~/.lmstudio/models/HauhauCS/`）；selftest 耗时：未测。
- 本次没能起来：`lms server start` / `lms ls` 都报 `Timed out waiting for LM Studio daemon to start`；直接启动 `LM Studio.exe` 进程立即以退出码 0 结束，`main.log` 没有新记录（最后一次是 10-05）。可能需要在桌面会话里手动打开一次。
- lms unload --all：未能实测（服务没起来）；替代方案：未评估。留到 LM Studio 能在本机正常起来时再测，测法见 M1 计划 Task 21 Step 3。
- 处理：`llm.routes` 的 script / storyboard 保持走 claude；doctor 对 lmstudio 给的是警告（!），不影响流水线。

## doctor
- ComfyUI √（8189，显存空闲 14.6/15.9 GB）；zimage / h3_image / h3_video / wan22_i2v 的节点和模型全部 √；claude √；ffmpeg √；磁盘 898 GB；内存可用 38.1/64 GB。
- 失败或警告项及处理：仅 `! [LLM] lmstudio：连不上 http://127.0.0.1:1234/v1`，见上节，未处理。
- 本地 `config.yaml` 只改了 `comfyui.url` → `http://127.0.0.1:8189`（Desktop 的 8188 当时没在跑）。

## 冒烟（zimage + wan22_i2v，2 镜 10 秒）
- 一次跑通，无失败任务。
- 各阶段耗时：剧本 59s，分镜 61s，首帧 26s（首张，含加载模型）/ 10s（第二张），视频 88s / 94s 每镜（5s 镜头），合成 3.6s。
- 成片：`projects\smoke\exports\ep01_9x16_20261008_113609.mp4`，1080×1920，10.0s，只有视频流（Wan 没有声音，符合预期）。
- Wan 单镜原始输出是 480×832 @16fps，合成时放大到 1080×1920。
- 问题与修复：无。

## 验收（zimage + h3_video，15 秒）
- 一次跑通，无失败任务。耗时：剧本 62s，分镜 61s，首帧 27s / 10s / 18s，视频 227s / 171s / 246s 每镜，合成 6s。
- 成片：`projects\h3check\exports\ep01_9x16_20261008_115012.mp4`，h264 1080×1920 + aac 48kHz 双声道，时长 17.46s，有声。
- 音频检查：`volumedetect` mean −16.6 dB / max −0.7 dB；`silencedetect`(−40dB) 只有几段 0.5–0.9s 的停顿，其余为持续声音，说明不是静音轨。尚未人工试听确认是对白还是环境声，留给人工确认。
- 观感问题（留给 M2）：
  - 时长超出目标：分镜给了 5.5+5.0+6.0=16.5s，H3 实际出片 5.875 / 5.167 / 6.583s（768×1344 @24fps），合起来 17.46s，比目标 15s 多 16%。分镜时长控制和"按分镜时长裁剪"值得看一下。
  - 画面里的文字是乱码：S02 提词器上本应是一句台词，画出来是"麦嘴青素"。需要文字的镜头最好后期叠字幕，或在分镜提示里避免要求画面出字。
  - 人物三镜之间长相、服装基本一致（同一首帧引擎、同一描述），尚可。
  - Wan 冒烟片原始分辨率只有 480×832，放大后偏软。
