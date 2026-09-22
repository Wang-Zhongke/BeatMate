# BeatMate 第一阶段设计（实现前契约）

本文保留 schema_version=1 的原始契约。第二次增量已实现的 CreativeBrief、可选六轨、编曲字段和状态化规划见 [CREATIVE_V2.md](CREATIVE_V2.md)。旧路径继续有效，不迁移或补写历史快照。原验收中的“旋律移调”指 chords；独立 melody 自 schema_version=2 才加入。

## 范围与阶段
1. 状态、音符模型、工具接口、验收场景（本文）。
2. 永久离线 Mock 意图解析、规则模板、多轨 MIDI、参考 WAV 试听。
3. 局部编辑、不可变版本、保护校验、持久化与本地 HTTP/CLI。
4. 可选 OpenAI Responses 结构化解析 Adapter（已实现，真实调用待验收）；后续音乐质量评估与独立 ACE-Step 音频 Adapter。
第一阶段不做多 Agent、RAG、微调、Logic UI 自动化或 .logicx 生成。

## 项目状态
SQLite projects 保存项目 ID 和 head_version；versions 只插入完整 JSON 快照。
每个版本：schema_version、project_id、version_id、parent_id、created_at、spec、tracks、protected_tracks、audit。
创建：intent -> validated BeatSpec -> generated notes -> validated version -> committed。
编辑：intent -> validated EditPlan -> copy parent -> apply -> scope/protection verification -> atomic commit。
试听与导出是派生物，不改变版本。历史版本可读取/导出；首版禁止从旧 head 提交编辑，返回冲突。
事务内再次比较 head，失败不产生版本；成功（包括无变化的编辑）都产生新版本。

## BeatSpec 和音符
BeatSpec: style=boom_bap|trap，bpm=40..220，bars=1..32，key=C/C#/D/D#/E/F/F#/G/G#/A/A#/B，mode=minor|major，swing=0..0.45，seed=0..2^31-1。
首版固定 4/4、PPQ=480，每小节 1920 tick；时间全部用整数绝对 tick，禁止浮点拍累积。
Note: id（稳定标识）、pitch=0..127、start_tick>=0、duration_tick>0、velocity=1..127。
Track: id、name、channel、program、notes；kick/snare/hihat 共用 GM 鼓 channel=9（第10通道），bass/chords 分别 channel=0/1。
音符不能越过项目尾端，同轨同音高不重叠。MIDI 同 tick 先 note_off 后 note_on。
版本记录实际 BeatSpec；局部变更由 audit 与音符快照表达，不伪装成全局 spec 改变。

## 工具接口
所有 JSON 拒绝未知字段；错误不允许静默修正。Mock 不需要网络、密钥或模型。
- parse_intent(text) -> {spec, assumptions, planner}; 未识别创作描述采用显式默认值并告知。
- create_project(text?, spec?, protected_tracks?) -> Version；text/spec 二选一。
- plan_edit(text, track_id, start_bar, end_bar) -> EditPlan；小节从1开始，闭区间。
- edit_project(project_id, base_version, plan, protected_tracks?) -> Version。
- get_version(project_id, version_id?) -> Version。
- export_midi(project_id, version_id) -> MIDI type 1（tempo/meta + 五条乐器轨）。
- preview(project_id, version_id) -> mono PCM WAV（参考合成器），明确不代表 Logic 混音。
EditPlan: track_id、start_bar、end_bar、operation=velocity|transpose|density|mute、value。
velocity 为增量 -126..126；transpose 为半音增量 -24..24（鼓轨禁止）；density=8|16|32 仅 hihat；mute value=0。
编辑只允许修改完全落在范围内的音符，跨界音符导致拒绝；范围外及所有其他轨逐字段保持一致。
保护列表持久保存，可在编辑中追加，首版不提供解锁；不能编辑保护轨。

HTTP（仅默认绑定127.0.0.1）：POST /parse、/plan、/projects；GET /projects/{id}、/projects/{id}/versions/{version}；POST /projects/{id}/edits；GET /projects/{id}/versions/{version}/midi 或 /preview。
400 输入/计划错误，404 不存在，409 旧版本冲突，500 内部错误（不泄漏堆栈）。请求最大64KiB。
Python Planner Protocol 将 LLM 限制为 BeatSpec/EditPlan 生成器；不允许直接写文件、改 DB 或执行任意工具。首版实现永久 MockPlanner 和可选 OpenAIPlanner；真实模型必须显式配置，使用严格 JSON Schema，输出再次验证。编辑范围由调用方锁定；模型只决定 operation/value。拒绝、网络错误或不完整响应返回502，不落版本。
ACE-Step 独立 AudioAdapter Protocol 返回 AudioArtifact(kind=audio)，没有 MIDI 返回类型，也不能替换原生工程快照。

## 验收测试（先定义）
A01 同一 spec/seed 生成相同音符；不同风格有不同鼓型；无密钥离线完成闭环。
A02 MIDI 读回：type1、480PPQ、tempo、4/4、五轨名、鼓通道、所有 note on/off 与快照吻合。
A03 第3–4小节 hihat 加密，仅此范围变化，其他轨与历史版本内容哈希不变。
A04 保护轨编辑失败；伪造计划字段、越界小节、非法音高/力度/密度失败且不写版本。
A05 每次成功编辑有新版本及 parent；旧 head 提交冲突；重启后版本和保护仍在。
A06 WAV 有合法头、预期时长、非静音，且未修改原生 MIDI 状态。
A07 HTTP 创建→计划→编辑→历史读取→MIDI/WAV 导出；无效 JSON/路径/超限请求有清楚错误。
A08 音频 Adapter 的结果类型明确为 audio；第一阶段不引入 ACE-Step 依赖。
Logic 手工验收：将导出 MIDI 导入空白 Logic 工程，核对小节、速度和五条轨，手工指定鼓组/bass/keys，听取局部改动。自动测试不能代替真实 Logic 验收。


## 本轮增量：DeepSeek运行时Planner

保留schema_version=1、全部BeatSpec/Note/Track/Version字段、五条轨、原生成/导出算法。没有数据库迁移。仅CLI选择Provider；Service默认Mock及显式planner注入保持原样。

`DeepSeekPlanner`继承既有`OpenAIPlanner`的BeatSpec/EditPlan翻译与响应校验，覆盖请求构造差异和网络传输；`config.create_planner`用简单分支选择三种Provider，没有新框架或依赖。`--planner`优先于BEATMATE_PLANNER；demo不受环境Provider影响。

兼容性核查（2026-09-16）：DeepSeek官方Responses指南确认instructions作为system消息，text.format完整支持，输出对象兼容；store、previous_response_id和conversation不支持。因此主路径为 `/responses` + 严格json_schema，不发送store、不持有远端会话。保留OpenAI原 `/v1/responses`、store=false和原环境变量。来源：https://api-docs.deepseek.com/zh-cn/guides/responses_api/ 。

共享LLM边界补足严格校验：BeatSpec所有字段必须由模型显式返回，禁止依赖dataclass默认值补齐；拒绝重复JSON键、NaN/Infinity、工具调用项、多个/未完成消息和非法JSON。结构化 `/projects` 请求原有的可省略字段默认行为仍保留；不改变模型Schema。

DeepSeek只返回spec或operation/value；不接收Key作为prompt字段，不传文件工具，不执行代码。调用方的track_id/start_bar/end_bar由本地拼回并验证；保护列表完全不属于模型输出。新增字段一律拒绝，不静默丢弃。原Service事务、范围比较、保护hash、音高力度检查保持不变。

传输：45秒超时、响应最大1MiB、HTTPS基础URL验证、禁跟随重定向、零重试、无任何fallback。鉴权/配置/限流/网络/非法输出统一经PlannerError转HTTP502，只返回经过清理的错误文字。配置缺失在启动Service前失败，Mock不检查供应商配置。

真实smoke需显式--run且后端有Key；最多两次请求。请求失败不会重试或改用Mock。网络不可用/超时与缺Key标记SKIPPED，其余失败标记FAIL。测试使用临时DB并通过旧执行链验证版本、范围与MIDI逐音符读回；不会修改用户工程。


### 真实prompt测试后的接入修正

使用用户的emo hip-hop长prompt，重启并指定系统CA后，供应商返回未完成响应。旧错误信息没有保存具体incomplete reason，不能断言已证实为token耗尽。官方思考模式文档确认DeepSeek默认high，Responses用reasoning.effort=none关闭思考：https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/ 。

本地Planner现在固定传reasoning={effort:none}，保持原模型、Responses协议、2000输出token限制、零重试/回退。仅补安全的max_output_tokens/content_filter未完成原因；不会回显任意供应商文字或思考内容。正在运行的Python服务需重启加载这次修正。
