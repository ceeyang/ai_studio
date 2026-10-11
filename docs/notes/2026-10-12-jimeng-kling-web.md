# 即梦 / 可灵网页版视频生成：页面探查记录

日期：2026-10-12　方式：Playwright 连用户自己的 Chrome（chrome://inspect 开的远程调试，`DevToolsActivePort` 里的 ws 地址）

状态：**探查未完成**。即梦的生成框（模式、模型、参考方式、画幅、时长、积分）已摸清；上传控件、结果卡片、视频地址
还没摸到（探查脚本被自动权限检查拦下，等用户回来确认后继续）。可灵国内 / 国际都已登录，页面还没摸完。

## 即梦（jimeng.jianying.com）

- 生成页：`https://jimeng.jianying.com/ai-tool/generate?type=video`（新版是「创作」对话式页面，底部一个生成框）。
- 登录：cookie `sessionid` / `sid_tt` / `uid_tt`（域 jianying.com）；作者账号已登录。
- 积分：右上角 `[class*=credit-amount-text]`（作者账号当前 **0**）；生成框右下 `[class*=actual-credits]` 是这次要花的积分
  （Seedance 2.0 mini 5 秒 720P：30，原价 45）。积分不够时不能实跑。
- 输入框：tiptap `div[role=textbox].ProseMirror`（contenteditable）。占位文字「上传最多12个参考素材、输入文字或 @ 参考内容…」。
- 工具栏三个 `[role=combobox].lv-select`（Arco Design），点开后选项是 `.lv-select-option`（选中的带
  `lv-select-option-wrapper-selected`，不可用的带 `lv-select-option-wrapper-disabled`）：
  1. 创作类型：Agent 模式 / 图片生成 / **视频生成** / 音乐生成 / 音频生成 / 数字人 / 动作模仿
  2. 模型：即梦 Seedance 2.5（样片模式）/ Seedance 2.5（「最强模型，支持 50 个参考」）/ **Seedance 2.0 mini**（默认，性价比）/
     Seedance 2.0 Fast VIP / Seedance 2.0 VIP / Seedance 2.0 Fast / Seedance 2.0 / Seedance 1.0（禁用）/ Seedance 1.0 Fast（禁用）/
     HappyHorse 1.1 / Wan 3.0。选项文字 = 名字 + 一行说明，按名字开头匹配。注意 2.0 系列写着「暂不支持真人人脸」（参考图里有真人脸会被拒）。
  3. 参考方式：**全能参考**（默认，图 / 视频 / 音频多参考，D3 用）/ **首尾帧**（D1 用）/ 智能多帧 / 智能编辑 / 超长视频
- 画幅 / 分辨率 / 数量：一个 `button.lv-btn`（文字形如「16:9 720P 1」），点开 `.lv-popover`：
  选择比例 21:9 / 16:9 / 4:3 / 1:1 / 3:4 / 9:16；选择分辨率（mini 只有 720P）；选择生成数量 1–4。
- 时长：一个 `button.lv-btn`（文字形如「5s」），点开 `[class*=duration-trigger]` 弹层：滑块 `[role=slider]`
  （aria-valuemin 0、aria-valuemax 15，刻度 0 / 5 / 10 / 15）+ 一个数字输入框（`.lv-input-number` 里的 input），
  看起来可以填 4–15 秒的整数（滑块前段有一段禁用区，约 0–4 秒）。
- 待摸：首尾帧模式下的上传位（`input[type=file]` 还是只能拖拽 / 粘贴）、发送按钮（生成框右下的箭头按钮）、
  结果怎么出现（对话里的卡片？）、视频地址、失败 / 审核不通过 / 积分不足的文字。

## 可灵

- 国内站：`https://klingai.com/app/video/new`（`app.klingai.com/cn/...` 会跳过来）；国际站：`https://kling.ai/app/omni/new`。
- 国际站生成页（已登录）：tiptap `div[role=textbox].ProseMirror`；`.model-type-select`（「VIDEO 3.0 Omni」）；
  `.setting-select`（「1080p · 5s」）；`.selector__skills`（Elements / Image to Video / Transformation / …）；
  隐藏的 `input.el-upload__input[type=file]`（accept `.jpg,.jpeg,.png,.mp4,.mov`）；生成按钮 `button.button-pay`（「Generate」）。
- 待摸：国内站登录后的页面、图生视频（首帧 / 尾帧）入口、积分、结果区、视频地址。
