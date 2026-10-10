# 海外 AI 视频/短剧生产工具的工作流与 UI 调研（2025–2026）

调研日期：2026-10-10　|　目的：为本地单用户「AI 竖屏短剧/广告工作台」（剧本 → 角色 → 分镜 → 首帧 → 图生视频 → 字幕/配音 → 导出）做减法提供参照。

## 0. 说明与可信度标记

- **[官方]**：厂商官网、博客、帮助中心、changelog。
- **[三方]**：媒体报道或独立评测。
- **[厂商SEO]**：厂商自建的营销/GEO 页面（如 `geo.higgsfield.ai`），措辞夸张，只当旁证。
- **[未核实]**：只有搜索摘要或单一低质来源，没能打开原文核对。
- 很多页面靠 JS 渲染或拦截爬虫（Higgsfield 产品页、Freepik/Magnific 博客、ltx.io 部分页面用 WebFetch 打不开，改用 curl 抓到正文），所以**没有任何截图级的 UI 核对**。下文的「屏数」都是按文档描述推出来的。

---

## 1. 各产品逐项分析

每个产品按七个维度写：①核心工作单元 ②从想法到第一条视频要几步 ③角色一致性 ④重生成/版本/候选 ⑤进度/队列/成本 ⑥哪些自动做、哪些问用户 ⑦值得借鉴的做法和反模式

### 1.1 LTX Studio（Lightricks）

1. **核心单元**：项目 → 场景 → 镜头（Shot）。工作区分成三块：Gen Space（单次生成）、Storyboard（分镜板）、Video Editor（时间线）。[三方] https://wavespeed.ai/blog/ai-workflows/ltx-studio-review/
2. **步骤**：粘贴剧本或 brief → 选画幅（9:16 等）和图像模型 → **先看拆解结果**（场景数、每场镜头数和每镜文字，可以合并、拆分、加拍），同时看自动抽出的角色和物体 Elements → 点 Generate 出整板 → 逐镜转视频。大约 3 屏：输入、结构预览、分镜板。[官方] https://ltx.io/blog/how-to-storyboard ；https://ltx.io/blog/ltx-storyboard-generator-update
3. **角色一致性**：Elements 是项目级（也可跨项目）的资产中心，分 Character / Object / Logo / Font / Style / Other 六类。角色可以绑定声音。在镜头描述或提示词里打 `@名字` 就能引用。在 Elements 里点 Replace Image，所有打过这个标签的地方都会更新。剧本里出现的人物会自动抽成 Element，同名提及绑定到同一个 Element。[官方] https://ltx.io/blog/getting-started-with-elements
4. **重生成**：点任意分镜格子就能单独重生成、换模型或改描述，「不碰板上其他格子」。视频层面有 **Retake**：选 2–16 秒片段只重做这一段，前后保持不变。[官方] https://ltx.io/blog/how-to-storyboard ；https://ltx.io/blog/top-ltx-studio-features 。镜头级版本历史 UI 的细节**[未核实]**。
5. **进度/成本**：按「计算秒数」计费，协作者各用各的额度，额度不结转。[三方] wavespeed 同上；dupple 评测 https://dupple.com/reviews/ltx-studio 。队列 UI **[未核实]**。
6. **自动/询问**：自动拆场景拆镜头、抽 Elements、推荐机位；在**生成前**让用户确认结构和角色，官方说这一步是「保护预算的地方」。
7. **借鉴**：「生成前的结构预览闸门」；`@` 引用资产，改一处全局生效；每格只保留四要素：画面、一句镜头描述、运镜、台词（官方原话：「其余通常是杂乱」）；剧本始终挂在分镜上，台词和格子对齐；先出 animatic 再出视频。**反模式**：三个工作区来回切（Gen Space / Storyboard / Editor），一个项目里混着多个模型的产物，交接时容易乱（见 wavespeed 评测）。

### 1.2 Runway（Gen-4/4.5、References、Act-Two、Aleph、Workflows、Agent）

1. **核心单元**：原本是单次生成工具；2025-10 加了节点画布 **Workflows**；2026-05 推出 **Runway Agent**（对话式），2026-07 把时间线编辑器**嵌进 Agent 对话**。[官方] https://runway.com/changelog
2. **步骤**：Agent 路线是：描述想法 → 上传参考图 → 选比例、时长、音频偏好 → Agent 提出概念和故事结构 → 用户在对话里改 → 生成多镜头成片 → 用时间线做最后调整。[官方] https://runway.com/news/introducing-runway-agent （2026-05-13）。第三方评测写的是「2026 年 6 月上线」，和官方日期不一致，以官方为准。https://fast.io/resources/runway-ai-review-2026/
3. **角色一致性**：Gen-4 References 最多 3 张参考图。图可以打标签存进库，提示词里用 `@` 调用。[三方] https://design-offset.com/20250501-runway-gen-4-references 。Act-Two 把头、脸、身体、手的表演迁移到角色上，2025-08 起可以直接换声音。[官方] changelog。2026-08 起 Agent 会话接入 Projects 和 Brand Kits。
4. **重生成/版本**：Workflows 里每个节点都有 **Node Execution History**：左右箭头翻看这个节点的历史输出，可以「Swap 为当前输出」继续往下游接，可以收藏、下载、转成独立图片节点。节点可以 **Freeze（冻结）**，整条流程重跑时不再生成这个节点。可以单独 Run 一个节点。[官方] https://help.runwayml.com/hc/en-us/articles/45769159004691 ；https://help.runwayml.com/hc/en-us/articles/45763528999699 。Aleph 2.0（2026-05）的做法是「改一帧，其余帧跟着改」。
5. **进度/成本**：Workflows 可以并行跑多组节点，有面板查看和取消任务。官方原话：「只能运行不依赖于正在运行或未完成节点的节点」。按积分计费（评测称 Gen-4.5 约 25 积分/秒）；Max 档有 **Relaxed Mode**，闲时生成不额外扣积分。[官方] help 同上；[三方] fast.io
6. **自动/询问**：Agent 自动拆分镜、选模型、写提示词、配乐配音、组装；用户负责「对提案做反应并引导」，最后在时间线里手动调。
7. **借鉴**：节点级的「历史 + 当前选用指针 + 冻结」，用来做镜头级版本管理正合适；Agent 和时间线放在同一个界面；Apps（2025-10）把复杂工作流包装成「按用例的小应用」，2025-12 起工作流可以发布成 App。**反模式**：功能面太广（8 类工具），新手不知道从哪下手；评测指出复杂时序还得回标准编辑器手动操作。

### 1.3 Higgsfield（Popcorn、Soul ID、Cinema Studio）

1. **核心单元**：一组工具，没有统一的项目视图。Popcorn 是「多帧故事板」生成器，Cinema Studio 生成镜头，Soul ID 训练角色，此外还有 Genjutsu、Recast 等修补工具。[官方] https://higgsfield.ai/blog/ai-animated-short-film-2026
2. **步骤**：Popcorn 有两种模式。Auto：一段提示词加最多 4 张参考图，选 4/6/8 帧，一次出一整组连贯帧。Manual：逐帧写描述。帧可以直接送去动画化，也可以作为新参考继续生成。[官方] https://higgsfield.ai/blog/The-AI-Storyboard-Generator-That-Feels-Like-Directing （发布于 2025-10-22）
3. **角色一致性**：Soul ID 用 20 张以上同一角色的肖像训练出可复用的身份；Popcorn 的提示词按「图 1 的人、图 3 的地点、图 4 的服装」这样组合参考图；Cinema Studio 单次生成最多支持 50 张参考图。[官方] https://higgsfield.ai/blog/keep-characters-consistent-ai-animated-series
4. **重生成**：主推「局部修补」：Genjutsu 的 Object Swap 换道具或服装，Motion Transfer 保留动作换角色或场景，Seedance Edit 对 30 秒内的片段按提示词或圈选区域修改，都不用整条重生成。[官方] 同上
5. **进度/成本**：积分制；队列 UI **[未核实]**。
6. **自动/询问**：Auto 模式自动扩写每帧的提示词；Cinema Studio 把类型、镜头、焦段、节奏、情绪、色板、年代做成**下拉控件**，不让用户写长提示词。[官方]
7. **借鉴**：「一段话 + N 帧」整组生成，帧与帧天然一致；用结构化控件（情绪、运镜、节奏）代替自由文本；先批准静帧再动画化。厂商 SEO 页还宣称有「8 步 Script-to-Screen、必须先批准 Anchor 图」的流程 **[厂商SEO，未核实]**（https://geo.higgsfield.ai/blog/best-integrated-workflow-cinematic-storyboarding-video-1）。**反模式**：子工具太多、命名花哨（Popcorn / Genjutsu / Recast / Soul Cinema…），用户得自己在工具之间搬运素材，没有统一的「项目/镜头」骨架。

### 1.4 Krea（Nodes、Node Agent、Krea Agent、Realtime）

1. **核心单元**：Nodes 是无限画布节点图；Krea Agent 是对话式的生产代理，产物放在「上下文库」（会话 / 个人 / 团队三级文件系统）里；Realtime 是实时画布。[官方] https://docs.krea.ai/user-guide/features/nodes ；https://www.krea.ai/blog/what-is-krea-agent
2. **步骤**：Node Agent 的流程是：一句话 → 读取当前画布 → **先给出分阶段计划**（可以换模型、删阶段）→ 逐层摆节点并连线 → 补齐缺失参数 → **显示每个节点的算力成本** → 用户确认后才运行。[官方] https://www.krea.ai/index/ai-workflow-agent （2026-09）
3. **角色一致性**：上下文库存放品牌和参考资料；可以训练风格 LoRA、建 moodboard；有支持角色参考的编辑模型。没有专门的「角色」实体 **[未核实]**。
4. **重生成**：改了上游，**只重跑受影响的下游**，前面缓存的结果保留。[官方] 同上。Agent 会话里有生成面板和媒体编辑器，可以在图上标注区域要求修改。
5. **进度/成本**：执行前显示每个节点的成本；Realtime 官方原话是「没有队列、没有等待、没有渲染按钮」。[官方] https://docs.krea.ai/realtime
6. **自动/询问**：Agent 自动选模型、写提示词、自检；用「推理力度」（Instant…Extra High）控制自检和重试的强度；外部工具有「允许 / 每次询问」两档权限。
7. **借鉴**：**计划 → 成本预估 → 批准 → 执行**的四段式；只重跑下游；可以把一次流程存成「技能」以后复用。**反模式**：Krea 自己说 Nodes「不牵着你走」，面向进阶用户；对普通用户，它的解法是在节点上再套一层 Agent，等于承认裸节点图太难用。

### 1.5 FLORA（含 FAUNA 代理）与 Figma Weave（原 Weavy）

1. **核心单元**：节点画布。FLORA 在 2026-03-31 推出 FAUNA，它在可见的画布上**实时加节点、连模型、执行**，用户看着它做，随时可以插手。[三方] https://ppc.land/floras-fauna-the-ai-creative-agent-that-fights-back-against-generic/ 。Figma Weave 也是节点画布，主打「剧本拆解 → 出帧 → 排镜」放在一条管线里。[官方] https://www.figma.com/solutions/ai-storyboard-generator/
2. **步骤**：画布上从模板开始，或者让 Agent 搭流程。屏数少（只有一张画布），但每步都要理解节点概念。
3. **角色一致性**：Weave 的做法是把角色参考做成节点，管线建一次，「跨场景、跨集、跨项目」复用。[官方] 同上
4. **重生成**：调某个节点、换模型、给镜头重新排序，「分镜在原有基础上演进，不用从头开始」。[官方] 同上
5. **进度/成本**：FLORA 按积分计费，积分可结转，宣称「没有慢队列」，模型单价差距很大（例如 Veo 3.1 一次 4267 积分）。[三方] ppc.land
6. **自动/询问**：FAUNA 会先理解用户过往作品和口味再生成。
7. **借鉴**：Weave 能把复杂管线**打包成「简化工具」**，官方原话是编剧「输入场景描述就拿到画面帧，不用学整个管线」。这就是「专家搭管线、普通用户只看表单」的分层。**反模式**：对单人短剧作者来说，节点图属于「可见的机器」，信息量远超需要。

### 1.6 Freepik Spaces（2026-04-28 起 Freepik 更名 Magnific）

1. **核心单元**：无限画布加节点，每个节点做一件事（生成图、写提示词、配音、放大等），重点是团队实时协作和评论。[三方] https://www.hackceleration.com/freepik-spaces-review ；https://www.krea.ai/blog/freepik-spaces-vs-krea-nodes 。更名：[三方] https://raxxo.shop/blogs/lab/freepik-just-rebranded-as-magnific-what-changes-for-solo-creators
2. **步骤**：从模板或空画布开始搭流程。评测提到搭可复用模板「花了约一小时」，在图、视频、音频模式之间切换「点击次数偏多」。
3. **角色一致性**：靠参考图节点和风格迁移，没有专门的角色实体 **[未核实]**。
4. **重生成**：工作流可以重复运行；评测称没有时间线，视频只能做短片段，多镜头广告还得导到 Premiere 拼。
5. **进度/成本**：**生成前显示积分消耗**。[三方] 同上
6. **自动/询问**：内置 AI 助手帮写提示词；模板化的工作流让「没有 AI 经验的同事零培训就能执行」。
7. **借鉴**：生成前显示成本；「模板 = 可复用流程」。**反模式**：节点画布有学习曲线；没有时间线、没有镜头序列概念，做不了多镜头叙事。

### 1.7 Luma（Dream Machine Boards → Luma Agents）

1. **核心单元**：**Board（板）**，每块板是一张无限画布，按项目归组图片和视频；另有 Ideas（全部生成流）。2026-03-05 推出 Luma Agents，Agent 直接在板上生成和整理。官方原话：工作「在板上空间化地演进，而不是通过文件夹或割裂的时间线」。[官方] https://lumalabs.ai/learning-hub/navigating-boards-ideas ；https://lumalabs.ai/learning-center/articles/welcome-to-luma-agents
2. **步骤**：新建项目 → 写提示词并上传素材（图、PDF、剧本、音视频）→ Agent 分析后**出计划** → 生成「类分镜」并在聊天里追问 → 输出成品。[三方] https://heise.de/-11202316
3. **角色一致性**：**Master Reference Assets（主参考资产）**：先固定角色脸型、比例、服装，产品外形，标语等「不可动元素」，每次迭代都要重新锚定到原始资产，不能只基于上一版输出。[官方] https://lumalabs.ai/learning-hub/master-reference-assets-maintain-consistency-at-scale-in-luma
4. **重生成/候选**：官方建议「几乎不要只生成一个选项」，原话：「只生成一个是在猜，生成几个是在选」。板上可以分组对比各版本；可以只重生成某一帧，不动其余序列。[官方] https://lumalabs.ai/learning-hub/generate-variations-in-luma-why-batching-beats-one-output ；[三方] https://www.mindstudio.ai/blog/what-is-luma-boards-agents-ai-storyboards 。另有 Edit Threads、Reply、Modify 等迭代工具（见官方学习中心目录 https://lumalabs.ai/learning-hub/the-new-luma-app-creative-multimodal-agent）。
5. **进度/成本**：积分制，Dream Machine 和 Agents 两个额度池**互不相通**。[三方] https://www.fast.io/resources/luma-ai-review-2026.md
6. **自动/询问**：Agent 自动路由模型（Ray3.14 / Veo / Sora / Kling 等）、写提示词；高级用户也可以在板上直接操作单个模型。
7. **借鉴**：「板」= 项目唯一工作面；先锚定主参考资产；用批量候选代替反复单抽。**反模式**（官方自己点名）：基于最新输出链式迭代会造成「创作熵」，角色在一版版之间漂移；两套额度池割裂。

### 1.8 Pika

1. **核心单元**：单条片段加一键特效（Pikaffects），偏社交、偏手机端；2.5 版本附带 Pika 2.5 Studio（时间线 + 图层）和 Pikascenes（多镜头、共享角色）。[三方] https://aivideopicks.com/posts/pika-review-2026.html （各功能细节均来自这一篇评测，**部分未核实**）
2. **步骤**：上传图或片段 → 选效果 → 生成，20–45 秒出片。[三方] 同上
3. **角色一致性**：Scene Ingredients（自定义图像元素）、Pikascenes 跨镜头共享角色；评测称一致性弱于专门的多镜头模型。
4. **重生成**：Pikaframes 关键帧插值，最多延长到 25 秒；Pikaswaps、Pikadditions 做局部替换和插入。[三方] https://alternativeto.net/news/2025/3/pika-labs-launches-video-model-2-2-with-longer-1080p-videos-and-new-pikaframes-tool
5. **进度/成本**：积分制，最高档有「最快队列」。
6. **自动/询问**：效果模板把提示词完全藏起来了。
7. **借鉴**：用「一个按钮 = 一个结果」的模板消灭提示词。和叙事短剧关系不大。

### 1.9 OpenArt（Characters、One-Click Story）

1. **核心单元**：One-Click Story 输入一句话、一段剧本或一首歌，生成约 1 分钟带故事弧的视频，提供 Character Vlog / Music Video / Explainer 三个模板；生成后可以进编辑器的 **storyboard mode**，逐片段改提示词。[三方] https://techcrunch.com/?p=3035461
2. **步骤**：选模板 → 上传角色图并写提示词 → 一键出片 → 回到分镜模式微调。2 屏起步。
3. **角色一致性**：Characters 用一张图或一段文字建角色，命名保存后在任意图片或视频提示词里引用；故事生成用 FLUX.1 Kontext，从原先需要 10–20 张参考图降到 1 张。[官方] https://bfl.ai/blog/openart-with-flux1-kontext
4. **重生成**：在分镜模式里按片段改提示词再生成。
5. **进度/成本**：积分制，套餐直接写明「含 N 个 One-Click Story / N 个角色」。[三方] TechCrunch
6. **自动/询问**：剧本、分镜、角色一致性、视频、音频、组装全部自动，只问模板和角色。
7. **借鉴**：「先给完整初稿，再逐格修」（draft-first）；角色作为套餐里的一等资产。**反模式**：全自动出片后，局部问题只能回分镜模式一格格改，用户对过程没有预期。

### 1.10 Google Flow（Veo / Nano Banana / Gemini Omni + Flow Agent）

1. **核心单元**：项目，项目内是资产网格加 Collections；Scenebuilder（简易时间线）把片段串起来；2026-02 合并了 Whisk 和 ImageFX，图像生成成为核心体验。[官方] https://blog.google/innovation-and-ai/models-and-research/google-labs/flow-updates-february-2026/ 。2026-05-19（I/O）推出 Flow Agent、Flow Tools（用自然语言「vibe code」自定义工具）和 Gemini Omni Flash。[官方] https://blog.google/innovation-and-ai/models-and-research/google-labs/flow-updates/
2. **步骤**：Nano Banana 出图 → 直接作为 Ingredients 或首尾帧喂给 Veo →（需要的话）Extend 续写或 Jump to 切到下一镜 → 在 Scenebuilder 里拼接。[官方] https://blog.google/technology/ai/veo-updates-flow/ （2025-10）
3. **角色一致性**：有「Characters」标签页，角色 = 参考图（可再生成第二张不同角度的图）+ 声音 + **「Character info」性格描述**，性格描述会喂给 Flow Agent 写对白和场景；也可以用 Avatar 扫描自己的脸和声音。[三方] https://kartaca.com/en/mastering-google-flow-the-ultimate-guide-to-character-avatar-creation/ 。Omni Flash 宣称跨场景保持「身份和声音」一致。[官方] I/O 博文
4. **重生成/候选**：Agent 能「一次生成多个变体」并排比较，还能**批量编辑**：一处修改反映到所有相关资产上。图像上可以用套索圈选区域，再用自然语言修改；视频支持插入或移除物体、延长。[官方] 同上
5. **进度/成本**：积分制；Android 版支持**后台生成完成后推送通知**，用户不用守着屏幕。[三方] https://www.creativeainews.com/articles/google-flow-android-beta-agent-io-2026/
6. **自动/询问**：Agent 负责头脑风暴、对白、情节建议、批量变体、自动归类和重命名资产，并强调「在你的控制下」。开发期曾泄露提示词栏上有「Agent 开关」，可在普通生成和代理编排之间切换 **[三方，上线前泄露]** https://www.testingcatalog.com/google-prepares-agent-mode-for-flow-to-automate-video-production/
7. **借鉴**：角色同时带「外观 + 声音 + 性格」，下游的 Agent 和配音直接复用；图和视频在同一资产网格里，图一键变成视频输入；套索圈选后局部修改；完成通知。**反模式**：Scenebuilder 被评测称为「非常简陋的时间线」（https://theaifilmmaker.substack.com/p/testing-the-limits-of-ai-video-with）；有第三方文章把 Flow 描述成「统一的分镜 + 时间线」，**[三方低质，未核实]** https://bonega.ai/en/blog/google-flow-march-2026-unified-ai-video-workspace

### 1.11 Invideo AI（Agent One / Agent Two + Workflows）

1. **核心单元**：对话代理加「Workflows（引导式流程）」。2026 年推出的 Agent Two 带 9 个工作流：选角、参考、美术、**分镜**、广告改编、本地化、改画幅等。[官方] https://invideo.io/news/introducing-workflows-invideo-agent-two/
2. **步骤**：选一个工作流 → 回答它的问题 → 上传参考 → **在它给的选项里挑** → 完成。官方原话：「Agent 建议，但每一步由你决定」。
3. **角色一致性**：选角工作流会读剧本里的每个角色，建脸，出角色设定图，再拆出每套服装并锁定，最后为每个角色生成声音存进 **Context**，以后所有运行都能复用。[官方] 同上
4. **重生成**：改画幅工作流里，扩边「先做成图片并批准，再生成视频」，原话是「积分花在已经正确的镜头上」。成片后可以用自然语言指令修改，如「删除第 3 场」「换成英式女声」。[三方] https://creatify.ai/review/invideo-ai
5. **进度/成本**：积分制 **[细节未核实]**。
6. **自动/询问**：流程固定，问题由流程来问；用户也可以把自己的步骤存成 markdown 工作流以后复用。
7. **借鉴**：**引导式向导代替空白聊天框**；先图后视频的批准闸门；角色的外观、服装、声音一次锁定后进入上下文。**反模式**：（推论）纯对话编辑，比如「删除第 3 场」，依赖用户记得编号，缺少可视化的镜头列表。

### 1.12 Katalist.ai

1. **核心单元**：剧本 → 镜头列表 / 分镜板 → 视频，现在偏广告效果团队（换产品、本地化）。[官方] https://katalist.ai/
2. **步骤**：在应用内写剧本或导入（CSV / Word / PPT）→ 自动拆镜并出分镜 → 逐镜调景别和构图、重生成 → 镜头转视频 → 导出。
3. **角色一致性**：从参考图「选角一次」，全片保持同一张脸；想换演员就换参考图再重生成。
4. **重生成**：逐镜「重生成任何不满意的」；可以一键换角色。[官方] 同上
5. **进度/成本**：免费积分起步，之后约电话定价 **[细节未核实]**。
6. **自动/询问**：自动拆镜和保持一致性；导出前逐镜审阅。
7. **借鉴**：选角是「一次设置」，换角色是一个动作而不是一串动作。

### 1.13 Boords（AI 分镜 + 脚本 + 动态分镜 animatic）

1. **核心单元**：项目内有脚本（Script Editor）→ 分镜（frame）→ animatic；脚本支持自由格式和双栏 AV 格式，评论锚定在具体行上。[官方] https://boords.com/changes
2. **步骤**：写脚本 →（审批）→ 点页眉的 **Storyboard**，一键生成，每场一帧，字段预填。
3. **角色一致性**：团队共享图片库，提示词里用 `@名字` 引用角色、地点、产品，最多组合 4 个参考。[官方] 同上
4. **重生成/版本**：逐帧生成，可选模型；有版本控制和帧级评论（https://boords.com/blog/the-6-best-ai-storyboard-generators）。生成后还能改画幅，并会询问如何处理已有图片（留白 / 裁切 / 不变）。
5. **进度/成本**：**[未核实]**
6. **自动/询问**：脚本到分镜一键完成；Agent 开始前可以先选画风。
7. **借鉴**：剧本和分镜在同一个项目里，「一键从剧本到分镜」；用 animatic（静帧 + 字幕 + 声音）检查节奏，再决定是否出视频；改画幅时明确询问处理策略。

### 1.14 Storyboarder（Wonder Unit，开源桌面）

- 免费开源桌面应用，绘制分镜加 Shot Generator（输入描述，自动摆好 3D 人偶和机位）。**最后一次正式发布是 v2.1.0（2020-09），最后一次提交在 2022-06**，基本停更。[官方] https://github.com/wonderunit/storyboarder ；[三方] https://www.cartoonbrew.com/tools/new-storyboarding-software-free-open-source-153927.html
- 借鉴：本地、单人、离线，**格子列表加大预览**的极简布局；导出到 Premiere、FCP 等。没有 AI 生成流程，参考价值有限。

### 1.15 CapCut 与 Descript（怎么把复杂度藏起来）

- **Descript**：编辑器分成 Script（转录文本，像改文档一样改视频）、Canvas（画面）、Timeline（传统波形时间线）和侧栏。**删一句话就删掉对应的音视频**；改字可以用克隆声音重新配音。[官方] https://help.descript.com/hc/en-us/articles/37585546799757-The-editor-interface 。Underlord 代理能写脚本、按反馈改、批量做繁琐编辑，而且「可以让它重做任意多次」。[官方] https://www.descript.com/agent
- **CapCut**：桌面版有「Transcript-based editing」，可以一键去掉口癖词。[官方] https://www.capcut.com/tools/video-transcript-editing 。CapCut Web 推出 **Video Studio**：画布式工作区，把「想法 → 分镜 → 生成 → 剪辑 → 导出」放在一张画布上，内置 AI 代理写脚本、分镜、全能参考生成。[三方] https://www.hunted.space/dashboard/capcut-video-and-image-editor/launches/capcut-video-studio （细节**未核实**）
- **借鉴**：「文本就是时间线」。用户编辑的是剧本或台词，时间线是派生视图，默认可以收起。正好对应我们的字幕 / 配音环节：改台词 = 改字幕 = 触发这一句重新配音。

---

## 2. 横向速查表

| 产品 | 主工作面 | 角色存放与绑定 | 单镜重做 | 版本/候选 | 成本/进度 | 自动化程度 |
|---|---|---|---|---|---|---|
| LTX Studio | 分镜板（项目→场景→镜头） | Elements 库，`@` 标签，改一处全局生效 | 点格子重做 / Retake 片段 | 未核实 | 计算秒数 | 自动拆镜和抽角色，生成前确认 |
| Runway | Agent 对话 + 嵌入时间线；Workflows 画布 | References `@` / Brand Kits | 单节点 Run | **节点历史 + Swap + Freeze** | 积分；任务面板可取消；Relaxed | Agent 全包，用户引导 |
| Higgsfield | 多个独立工具 | Soul ID 训练 + 多参考图 | 局部修补（Swap / Edit） | — | 积分 | Auto 模式整组出帧 |
| Krea | 节点画布 / Agent 会话 | 上下文库 | 只重跑下游 | 缓存 | **执行前按节点估成本** | 计划→批准→执行 |
| FLORA / Weave | 节点画布 | 参考节点 | 调单个节点 | — | 积分 | Agent 当面搭图；打包成简化工具 |
| Freepik Spaces | 节点画布 | 参考节点 | 重跑 | — | 生成前显示积分 | 模板 |
| Luma | **Board 画布 + Agent** | Master Reference Assets | 单帧重生成 | **批量候选、分组对比** | 两个额度池 | Agent 出计划、追问 |
| Google Flow | 项目资产网格 + Scenebuilder + Agent | **Characters（外观 + 声音 + 性格）** | 套索局部修改 / 插入 / 移除 | Agent 多变体并排、批量编辑 | 积分；后台完成通知 | Agent 在用户控制下工作 |
| Invideo | **引导式 Workflow** + 对话 | Context（脸 / 服装 / 声音） | 指令式修改 | 让用户在选项中挑 | 积分 | 流程问问题，用户挑 |
| OpenArt | 一键成片 → 分镜模式 | Characters 命名引用 | 改片段提示词 | — | 积分 | 全自动初稿 |
| Katalist / Boords | 剧本 → 分镜板 | 选角一次 / `@` 引用 | 逐镜重生成 | Boords 有版本 | — | 一键剧本→分镜 |
| Descript / CapCut | **剧本文本 = 时间线** | — | 改文本 | — | — | Agent 批量剪辑 |

---

## 3. 跨产品模式综合（9 条）

### P1. 一个主工作面，不用多页签
- **谁在做**：Luma（一块 Board 承载全部内容，官方明说「不用文件夹或割裂的时间线」）、CapCut Video Studio（想法到导出在一张画布上）、LTX（以分镜板为中心）、Google Flow（项目资产网格 + 内嵌 Scenebuilder）、Runway（Agent 对话里嵌入时间线）。
- **为什么能降复杂度**：用户永远知道「我的片子在哪」；上下游产物（剧本句、角色、首帧、视频、台词）在同一屏能看到，不用在页面间来回搬运和比对。
- **是否适合本地慢 GPU**：★★★ 很适合。不一定要无限画布，可以是「镜头卡片网格 + 侧栏」。

### P2. 剧本 → 自动拆镜和抽角色，生成前确认一次结构
- **谁在做**：LTX（结构预览：场景数、每场镜头数、可合并拆分，同时抽出 Elements）、Boords（页眉一键 Storyboard）、Katalist、Invideo 选角和分镜工作流、OpenArt。
- **为什么能降复杂度**：把「拆镜、建角色、填字段」这些表单工作变成**一次审阅**；用户只改 AI 弄错的地方。
- **是否适合本地慢 GPU**：★★★ 很适合。LLM 拆解快，GPU 贵，所以这个闸门应该放在任何 GPU 任务之前。

### P3. 角色是项目级资产，在镜头里用 `@` 引用，改一处全局生效
- **谁在做**：LTX Elements、Runway References、Boords、OpenArt Characters、Google Flow Characters（外观 + 声音 + 性格）、Invideo Context、Luma Master Reference Assets。
- **为什么能降复杂度**：消灭「每个镜头重复选参考图、重复写外貌」；镜头只记「谁在场」，不记「长什么样」。Flow 和 LTX 把**声音**也挂在角色上，配音环节就不用再选一次。
- **是否适合本地慢 GPU**：★★★ 很适合。配套做法：角色换装或换脸后，自动标记受影响镜头为「已过期」，不立即重跑（我们现在已经部分这么做了）。

### P4. 镜头卡片网格，就地重做，并支持局部修补
- **谁在做**：LTX（点格子重做、换模型、改描述，不碰其他格）、Luma（单帧重生成）、Katalist、OpenArt 分镜模式；局部修补有 LTX Retake、Higgsfield Genjutsu / Edit、Flow 套索圈选、Runway Aleph「改一帧其余跟随」。
- **为什么能降复杂度**：每个镜头的全部操作收在一张卡片上：一个主按钮加一个「更多」菜单，取代全局工具栏加每镜一排按钮。主按钮随状态变化：生成首帧 → 生成视频 → 重做。
- **是否适合本地慢 GPU**：★★★ 很适合。局部修补（只重做某段或某区域）在本地成本高，可以放到后期。

### P5. 每个镜头一个「候选栈」，用「当前选用」指针做版本，而不是单独的版本历史页
- **谁在做**：Runway 节点执行历史（翻看、Swap 为当前、收藏、Freeze）、Luma（「生成几个是在选」，分组对比）、Flow Agent（多变体并排）、Invideo（在它给的选项里挑）。
- **为什么能降复杂度**：不需要单独的版本管理界面；重做不会覆盖旧结果，用户只是改「选哪一个」，心理负担低。Freeze 防止批量重跑时误伤已经满意的镜头。
- **是否适合本地慢 GPU**：★★★ 很适合，但候选数量要谨慎。**首帧（图）批量出 2–4 张让用户挑，视频默认只出 1 条**，因为视频每条要几分钟。卡片上显示「已锁定」，批量运行时自动跳过。

### P6. 先图后视频：两段式审批闸门（加 animatic）
- **谁在做**：Higgsfield（Popcorn 静帧 → Cinema Studio，厂商称必须先批准 Anchor 图）、Invideo（「先做成图片并批准，再生成视频，积分花在已经正确的镜头上」）、LTX 和 Boords（animatic 先验节奏）、Flow（Nano Banana 出图后直接当 Veo 的帧）。
- **为什么能降复杂度**：把「贵且慢」的视频生成推迟到画面确认之后，返工集中在便宜的阶段。
- **是否适合本地慢 GPU**：★★★ **最关键的一条**。本地视频每条要几分钟，「首帧全部确认 → 一键批量出视频」应该成为默认流程；animatic（首帧 + 字幕 + TTS 拼成预览）能在零视频成本下检查节奏。

### P7. 对话或代理做入口，但产出可编辑的结构化产物；最好是引导式向导，不是空白聊天
- **谁在做**：Runway Agent（概念 → 结构 → 多镜头成片 → 时间线）、Luma Agents（计划 → 类分镜 → 追问）、Google Flow Agent（头脑风暴、对白、批量变体，「在你的控制下」）、Krea Agent、Invideo Workflows（**流程问问题，用户挑选项**）、Descript Underlord。
- **为什么能降复杂度**：用户不用学表单字段，只回答问题、做选择；产物仍然落在镜头卡片和角色库里，可见、可改。
- **是否适合本地慢 GPU**：★★ 部分适合。本地 LLM 做规划很便宜，但不宜做「自主连续生成视频」，因为看不到过程又耗时。建议做成「向导式一键起稿」（故事 → 剧本 → 拆镜 → 角色 → 首帧），在 P2 和 P6 两个闸门停下来。

### P8. 计划 → 成本或耗时预估 → 批准 → 执行，并且只重跑下游
- **谁在做**：Krea Node Agent（执行前按节点显示成本，改上游只重跑下游）、Runway（Freeze、单节点 Run）、Freepik Spaces（生成前显示积分）、Luma 和 heise 提到的计划阶段。
- **为什么能降复杂度**：用户在点之前就知道「会发生什么、要多久」；改动的影响范围由系统算，不用用户自己判断「哪些要重做」。
- **是否适合本地慢 GPU**：★★★ 很适合。把「积分」换成「预计耗时 / 任务数」，例如「将生成 12 个视频，约 36 分钟」；依赖失效后自动标「过期」，一键「只重做过期的」。

### P9. 文本就是时间线（剧本、台词驱动剪辑和字幕配音）
- **谁在做**：Descript（删文字就删片段，改字就重新配音）、CapCut 转录剪辑、Boords（评论锚定在脚本行上）、LTX（剧本挂在分镜上，台词和格子对齐）。
- **为什么能降复杂度**：字幕、配音、时长都从台词文本派生；用户不碰轨道。
- **是否适合本地慢 GPU**：★★★ 适合字幕和配音环节。TTS 和字幕便宜，可以实时重算；时间线只做只读预览加少量拖拽。

### 附：P10. 后台队列加完成通知（贯穿以上各条）
- Google Flow Android 后台生成完成推送；Runway 任务面板可查看和取消，依赖未完成的节点不能运行；Runway Relaxed Mode 闲时批量跑。
- **对本地工具**：★★★ 必需。一个全局「队列抽屉」显示进行中、排队、预计完成时间，支持取消；任务完成时让卡片高亮并发系统通知；支持「排到夜里跑」。

### 反模式汇总
1. **裸节点画布给非专家用**（Freepik 评测提到学习曲线；Krea 自承「不牵着你走」；各家都在节点上再套 Agent 或打包成 App 来补救，如 Runway Apps、Weave「简化工具」、Krea Node Agent、FLORA FAUNA）。单人短剧工具不应该让用户看见管线。
2. **从最新输出链式迭代**，角色逐版漂移（Luma 官方警告）。重做时应该始终回到角色库的原始参考。
3. **子工具和品牌名繁多，产物要手工搬运**（Higgsfield；LTX 三个工作区；Luma 两个额度池）。
4. **全自动成片却没有可视化的中间结构**（纯对话里「删除第 3 场」，或者一键成片后只能回去一格格修）。
5. **每镜一排按钮加全局工具栏**：各家都没有这么做，单镜操作一般是 1 个主按钮 + 菜单。

---

## 4. 对本项目的落地建议（只做映射，不是方案）

| 现状页面 | 对应模式 | 简化方向 |
|---|---|---|
| 剧本 | P2、P7、P9 | 「一句话/梗概 → 一键起稿」向导；生成后停在**结构预览**（场次、镜头、出场角色），确认后再往下走 |
| 角色 | P3 | 改成侧栏资产库（外观 + 声音），镜头描述里用 `@角色`；不单独占一个主页面，深度编辑用抽屉 |
| 导演台 | P1、P4、P5、P6、P8 | 唯一主工作面：镜头卡片网格，每卡一个状态驱动的主按钮，带候选栈和锁定；顶部一条阶段进度条，加「全部下一步（预计 N 分钟）」；首帧全部确认后才批量出视频 |
| 导出 | P9、P10 | 台词文本驱动字幕和配音；animatic 预览；导出作为最后一个阶段按钮，不单独做页面也可以 |
| 全局 | P10 | 队列抽屉（ETA、取消、夜间批跑）、完成通知、「只重做过期镜头」 |

---

## 5. 未核实或存疑清单
- Higgsfield「8 步 Script-to-Screen、必须先批准 Anchor 图」只见于厂商 SEO 子站。
- bonega.ai 关于 Flow「统一分镜 + 时间线、ImageFX 关键帧」的描述来源质量低，与官方表述有出入。
- LTX-2.5（2026-08）原生多镜头只见于搜索摘要（dupple）。
- Pika 2.5 Studio、Pikascenes 的细节只来自单篇评测。
- CapCut Video Studio 只来自 Product Hunt 汇总页。
- Runway Agent 上线时间：官方 2026-05-13，fast.io 写的是 6 月。
- Google Flow「Agent 开关」是上线前代码泄露，最终形态以 I/O 博文为准。
- 各产品的队列 UI、版本历史 UI 大多没有截图级核实。
