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

## 网页端验收（2026-10-08，zimage + h3_video，15 秒）

- 方式：浏览器（Playwright MCP，连到本机 Chrome），生产态 `uv run studio-server` 托管 `frontend/dist`，只开 8700 一个端口；ComfyUI 8189。项目 `网页验收`，画幅 9:16，图像 zimage，视频 h3_video，目标 15 秒，梗概同 Task 17 brief。全程只在网页里点：新建项目 → 新建剧集 → 运行剧本 → 运行分镜 → 生成首帧（全部）→ 生成视频（全部）→ 合成导出。
- doctor：ComfyUI √（8189，cuda:0 RTX 5080，显存空闲 14.5/15.9 GB）；zimage / h3_image / h3_video / wan22_i2v 节点和模型全部 √；claude √；ffmpeg √（8.1.2）；磁盘 898 GB；内存可用 35.2/64 GB。唯一警告：`! lmstudio 连不上 http://127.0.0.1:1234/v1`（平时不开，不影响）。
- 一次跑通，任务 #23–#31 全部 done，无失败任务。
- 各阶段耗时：剧本 69s（集名"提词器上的秘密"，1 段落 / 1 角色 / 1 场景 / 风格已填），分镜 88s（3 镜 6.0+5.0+6.0=17.0s），首帧 25s（首张，含加载模型）/ 9s / 9s，视频 246s / 164s / 326s 每镜，合成 7s。
- 成片：`projects\网页验收\exports\ep01_9x16_20261008_144843.mp4`，`ffprobe`：h264 1080×1920 + aac 48kHz 双声道，时长 18.17 秒；volumedetect mean −17.1 dB / max 0.0 dB（不是静音轨）。导演台"成片"区出现下载链接和 `<video>`，浏览器里 readyState=4、1080×1920、18.17s，可播放；尚未人工试听确认对白内容。
- 导出到 ComfyUI：S01 抽屉首帧候选 #13 点"导出到 ComfyUI"，浏览器下载了 `zimage_13.json`（5437 字节），节点 10 个、连线 10 条，界面无报错；能否在 ComfyUI 界面打开：未拖进去实测。
- 过期标记：改 S01 图像提示词（末尾加 ` cinematic`）保存后，S01 首帧单元格和抽屉"首帧候选"旁都出现"已过期"：是；删掉这个词再保存，"已过期"消失。
- 界面问题与修复：无代码修复。观察：成片时长 18.17s 仍比目标 15s 长约 21%（分镜给 17s，H3 出片 6.58/5.17/6.58s），与上一节验收的结论一致，留给 M2。

## 实战：柬埔寨1号铭车 30 秒广告（2026-10-09，h3_image + h3_video，带定妆照）

- 需求：`docs/notes/no1-car-repair-brief.md`。素材来自 ai_videos（信息表 xlsx、交接文档附录 A/B、旧 project.yaml 里用户认可的六拍对白），两张定妆照 `refs/master_chen.png`、`refs/xiao_yu.png`。旧项目导入器已撤销（新框架不兼容旧项目），先前导入的 `no1-car-repair` 已从库里删除（含角色槽位），磁盘文件留在 `projects/no1-car-repair/`。
- 全程走 CLI / HTTP API：项目 `cambodia-no1-car`（9:16，seed 20261003）→ 剧集 target 30s → script → `studio character portrait` 设两张定妆照 → storyboard → 人工改镜 → keyframes（审图、重出）→ videos（审片、重出）→ export。
- 耗时：剧本 90s（视觉设定一次重试）；分镜 291s（6 段各 1 镜，**每段都重试一次**：要求把角色外貌原样抄进 image_prompt，两人同框时加上风格锚就超过 180 词上限）；首帧 H3 20 步带参考图 23–64s/张（首张含加载模型）；视频 H3 turbo 164–236s/镜（5s 镜约 165s，6s 镜约 235s）；合成 11s。
- 人工改镜：S01 改成两人同框（原来小雨只在画外说话，H3 容易让画面里的男人对女声口型）；S04/S06 音频提示去掉 LLM 加的音乐；时长从 35s 调到 31s（4.5/5/4.5/6/5/6）并重排台词时间。
- 首帧重出：S02（没手机、在笑，节拍丢了）、S03（不在车底、拿的不是扭力扳手）、S04（只是半身像，看不出车间规模）各抽 2 张；S04 第二轮两张都把中国国旗画成黄底红圆、横幅是乱码字，去掉提示词里的国旗和横幅后第三轮通过。
- 视频重出：S01 第一版 H3 在 1 秒内推成陈师傅特写，女声台词配在男人脸上（自相关粗测基频约 300Hz）；改为"固定机位、两人都在画面、只有她说话"后通过。
- 成片：`projects\cambodia-no1-car\exports\ep01_9x16_20261009_185347.mp4`，h264 1080×1920@24 + aac 48kHz 双声道，33.42s，volumedetect mean −14.9 dB / max −0.5 dB。六镜两人长相与定妆照一致。尚未人工试听确认对白内容和口型。
- 问题（留给 M2/M3）：
  - 时长仍超：分镜 31s，H3 帧数网格把 4.5/5s 都出成 5.167s、6s 出成 6.583s，成片 33.4s。需要"按分镜时长裁剪"。
  - H3 视频会无视首帧构图：S04 首帧是全景，视频 0.5s 内就推成中景人像（参考图把人"拉近"）。
  - 分镜 prompt 的 80–150 词限制与"外貌原样抄写"冲突，每段都要多一次 LLM 调用。
  - 画面文字：工装胸标是乱码小字（定妆照本身就有），国旗/横幅这类需要准确图案或文字的物件不要写进提示词。
- 还不能做（M3）：中文在上、高棉语在下的双语字幕；高棉语配音版（Edge TTS km-KH-PisethNeural / SreymomNeural，原音轨压低当环境底）；片尾三语信息卡（店名、地址、营业时间、Telegram）。

## 后期（M3 切片）：柬埔寨1号铭车双语字幕、高棉语配音、片尾卡（2026-10-09）

- 新功能（API / CLI / 导演台都能用）：导出选项 `subtitles: none|zh|zh+km`、`dub: off|km`、`end_card`、`trim`；`POST /api/episodes/{id}/run {"stage":"export","export":{...}}`，`studio run export <项目> --subtitles zh+km --dub km --end-card`，导演台"导出选项"勾选框。字幕翻译任务 `subtitles.translate`（走 claude，结果标 `km_review: ai_draft`），`studio subtitles translate|show|set`，镜头抽屉里中文/高棉语可编辑、可勾"母语已校对"。配音任务 `post.dub`（Edge TTS，音色写在角色上：`studio character voice`），片尾卡 `GET/PUT /api/projects/{slug}/end-card`、`studio project end-card --file`、导演台"片尾卡设置"。字体在 config.yaml `subtitles:`（zh_font / km_font / latin_font），配音参数在 `dub:`（bed_db -14、duck_db -30、max_rate 35、loudness -14）。
- H3 帧数改为取最近的 17k+5：4.5s → 107 帧（4.46s），5s → 124 帧（5.17s），6s → 141 帧（5.88s）。
- 裁尾规则（`backend/post/timeline.py`）：裁剪点 = 分镜时长 + 0.15s；只有 max(分镜最后一句 end, silencedetect 实测最后一段声音结束) + 0.3s ≤ 裁剪点 时才裁。实测发现只看分镜时间不够：H3 会把台词摊满整段视频（旧 S01 分镜写 0.5–3.2s，实际说话 2.0–4.8s；S03、S04、S06 也都说到片尾），按分镜时间裁会切掉台词。
- 时长决定：旧片段在新规则下一段都不能裁（台词都说到结尾），只靠裁尾到不了 30 秒，所以按新取整重出了 S01/S03/S04/S06 四镜（H3 turbo，约 15 分钟 GPU），S02/S05 本来就是 124 帧不变。逐镜对比旧片段的抽帧和音轨包络后全部改用新片段：S01 两人同框、她在说话；S03 与旧版几乎一致；S04 多了喷漆房；S06 后半推到陈师傅近景说 Telegram 那句（旧版两人同框更贴"两人一笑"，新版可接受）。台词都落在新片段里（silencedetect：S01 1.7–4.4s，S06 到 5.85s）。成片正片 30.54s（原来 33.42s）。
- 字幕时间：H3 实际说话时间和分镜估计差很多，用每段音轨的能量包络（>−26 dB 的段落）量出每句的起止，通过 `studio subtitles set --start --end` 改到对白上（不影响视频的过期判定，哈希只看中文台词）。
- 高棉语：翻译任务（#61）出了 12 句 AI 译稿；与用户在 ai_videos 旧项目里用过的高棉语对照，中文完全相同的句子（S01、S03、S04.1、S05×2、S06.1）直接用旧稿，旧项目里合成一句、新项目拆成两句的（S02、S04.2/3、S06.2/3）按句拆分旧稿；12 句全部仍标 `ai_draft`——旧稿同样是 AI 译稿，**发布前必须由柬籍母语者校对**（附录 B 的 14 个重点词）。
- 配音：12 句全部塞进台词窗口，只有 S04 第 3 句（"一个地方办完"）提速 +35% 后仍超 0.08s。Edge TTS 每句句尾带约 1.5s 空白，起初几乎每句都判超窗，改为先剪首尾静音再算时长和语速后解决。
- 成片（ffprobe）：
  - (a) 普通话原声 + 中高字幕 + 片尾卡：`projects\cambodia-no1-car\exports\ep01_9x16_zhkm_card_20261009_193657.mp4`，h264 1080×1920@24 + aac 48kHz 双声道，33.58s（正片 30.54 + 片尾卡 3.0），−10.9 LUFS。
  - (b) 高棉语配音 + 中高字幕 + 片尾卡：`projects\cambodia-no1-car\exports\ep01_9x16_zhkm_dubkm_card_20261009_193700.mp4`，同规格，33.58s，−14.0 LUFS（混音后 loudnorm；未归一前 −20.9 LUFS，比普通话版轻 10 LU）。
- 看帧：在 3.0 / 15.15 / 21.65 / 26.0 / 29.2s 和片尾卡 31.0 / 32.5s 抽帧查看。中文在上、高棉文在下，白字黑边在亮背景上可读；高棉文字形正常（下加字、上标元音都排对，不是方框）；两行之间留了间距（第一版高棉文上标元音贴着中文，字号也偏小，已改：KM 字号 0.031H=59px、两行间插 14px 空行）；S06 第 3 句高棉文自动折成两行。片尾卡深蓝底，高棉语店名最大在最上，下面中文店名、英文公司名、地址两行、中文地址、营业时间、Telegram 0318891666，居中、无溢出。
- 导出记录 #7（第一版配音，10 句判超窗）和 #8（字幕间距修正前）留在成片列表里，以 #9 / #10 为准。
- 待人工：试听两个版本（口型对不上外挂人声是预期的）；高棉语母语校对后在镜头抽屉勾"母语已校对"再导出一次，成片列表的"高棉语待母语校对"标记会消失。
