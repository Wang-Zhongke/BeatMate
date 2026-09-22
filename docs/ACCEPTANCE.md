# 验收记录

[返回产品首页](../README.md) · [改进路线](ROADMAP.md)

## 发布前收尾与导出区布局（2026-09-23）

- 修复导出链接的行内排版：按钮占据实际高度，与说明保留 10px 间距，说明独立换行。修复限定作品导出区，不改变音轨播放按钮布局。桌面与 390px 手机实测间距均为 10px，手机页面宽度与滚动宽度均为 390px。
- 更新 README 分轨截图。添加 MIT LICENSE 及项目元数据，文档明确代码许可不替代供应商生成内容的使用条件。
- `.env.example` 与 README 的首次配置上限统一为 10；已有本机 `.env` 和 SQLite 保存额度未被修改。
- 从官方 npm 注册表安装测试依赖，生成带完整性摘要的 `tests/browser/package-lock.json`，用 `npm ci --ignore-scripts` 验证锁文件；贡献说明和 CI 已改用相同安装方式。
- 首次完整浏览器执行发现搜索等待断言过早的问题，修正为等待单项结果列表；响应式测试在切换尺寸前固定曲库视图，避免页面刷新竞态。最终 `npm test`：**6 项全部通过，12.3 秒**，Chromium 151 / Playwright 1.62.1，macOS 无头运行。新增桌面及手机导出按钮与说明不重叠断言。
- Python 全量离线回归 **161 项通过，18.992 秒**；JavaScript 单元回归 **7 项通过**。测试使用独立临时 Mock 数据，未调用真实付费 API，未改变个人曲库。
- GitHub Actions 配置已就绪，但尚未上传仓库，远程执行结果仍待首次 push 后确认；本地测试结果不冒充远程 CI 结果。

## 曲库分页与浏览器回归配置（2026-09-23）

- 曲库每页 30 首，任务动态每页 10 条；数据库全库搜索/排序/收藏，越界页自动回退。当前播放、队列和 A/B 作品独立保留；批量操作仅作用当前页。
- 新增持久 revision 与服务实例标记，轮询先读变化状态，变化后读取当前页；每 60 秒重新检查当前页文件。旧全量历史接口保持兼容。异步搜索合并相同请求，过期响应与过期错误不覆盖当前结果。
- Python 全量回归：**161 项通过，19.271 秒**。新增 6 项覆盖分页边界、全库筛选、保留试听、文件核验范围、持久变化序号、任务分页与非法参数。
- JavaScript：**7 项通过**，包含快速搜索竞态、重复请求合并、同步失败后的恢复；前端与浏览器脚本语法检查通过。
- 新增 5 条 Playwright 回归脚本及 GitHub Actions browser job，测试发现检查识别到全部场景。**本次没有执行独立无头 Playwright 套件或远程 GitHub Actions**，不宣称流水线已通过。
- 内置浏览器在临时 Mock 库实际验证：35 首分为 30+5，跨页保持播放器音源；全库搜索与零结果保留播放；预览取消/重新确认、双击确认后仅出现一首测试作品并在刷新后保留；作品删除/恢复、失败记录删除/恢复；伴奏与人声 A/B、1–3 秒循环、备注刷新后保留、ZIP 下载事件。未记录浏览器 error。
- 测试未使用真实 API，未修改个人曲库或重启用户的 8767 服务。加载新分页接口需要重启后端并刷新页面；版本门槛已升级，旧服务会提示重启。

## 启动、恢复与试听改进（2026-09-23）

按改进顺序完成：启动与运行检查、无付费连接诊断、任务恢复展示、A/B/循环/备注、本地打包导出、前端职责拆分与离线 CI 配置。

- Python：`.venv/bin/python -m unittest discover -s tests -v`，**155 项全部通过，15.785 秒**。新增验证覆盖配置/代码变化、无 HTTP 的网络检查、端口占用、恢复状态、备注持久化与范围校验、导出内容/哈希/缺失清单/永久删除、缓存失效和同源边界。
- JavaScript：`node --test tests/ui/*.mjs`，**4 项通过**；所有前端模块语法检查通过。覆盖循环范围、文件末尾循环重播、修改循环标记和连续保存备注去重。
- 浏览器：独立临时 Mock 曲库，验证设置检查与更新提示、Mock 跳过网络、已有任务恢复指引、导出入口、A/B 音轨切换、片段循环、备注保存及刷新后仍存在。
- 响应式：390px 宽度下页面无横向溢出（页面宽与滚动宽均为 390px），试听入口可访问，对话框宽 356px。
- 没有调用真实生成/分离 API，没有修改个人曲库，没有重启用户原服务。真实网络诊断用测试替身验证，未以连接检查代替账户验证。
- GitHub Actions 已配置，尚未上传/远程执行；没有宣称自动化浏览器回归已实现。

## GitHub 文档整理与当前离线复验

日期：2026-09-22。本次重排 README，拆出配置、音频 API、原生 MIDI 与改进路线文档；未改变音频业务逻辑。

- 执行 `.venv/bin/python -m unittest discover -s tests -v`：**146 项全部通过，14.563 秒**。允许测试监听临时本机端口，未调用真实付费 API。
- 在独立临时目录执行 README 的 `audio-demo` 命令：返回 `ready`，Provider 为 `mock`，产物为 4 秒 WAV；未改动用户曲库。
- 检查 README、第三方说明及新增文档中的 41 个本地链接，目标均存在。
- 本次没有重新验证真实供应商、人工听感或浏览器交互；下方记录保留各次验收的日期与范围，不把历史结果作为本次复验。

## 第一阶段验收记录

日期：2026-09-16。环境：Python 3.10.5、Mido 1.3.3、packaging 24.2。
执行：`.venv/bin/python -m unittest discover -s tests -v`
结果：18 tests，全部通过（最终运行1.230秒）。HTTP 测试使用临时 loopback 端口，测试结束关闭服务。

| 原设计验收项 | 自动验证 |
|---|---|
| A01 离线可重复 | Mock 禁用网络测试、同seed生成一致、风格差异、最大32小节与极限swing |
| A02 原生MIDI | Mido读回6条轨（meta+5乐器）、480PPQ、tempo/拍号、逐音符起止/力度/通道一致 |
| A03 局部修改 | hihat第3–4小节加密，范围外与其他轨完全相同；chords移调与静音（当时“旋律移调”实际测试该轨，并无独立melody） |
| A04 校验与保护 | 保护轨拒绝、越界/非法计划/力度失败、跨界延音拒绝、失败不写版本 |
| A05 版本 | 新ID/parent、无变化编辑仍新建、历史一致、持久化、并发仅一个成功、SQL触发器防修改 |
| A06 试听 | WAV头/帧数/时长/非静音，原生状态未变 |
| A07 HTTP | 解析→创建→计划→编辑→历史→导出；400/404/409、非法JSON、请求大小与跨域检查 |
| A08 Adapter边界 | AudioArtifact只能初始化为audio，无ACE-Step依赖 |
| 可选LLM | 注入响应验证严格Schema请求、拒绝/不完整/非法输出、固定调用方音轨与小节范围 |

CLI demo 已执行，产物在 `examples/demo/`：
- v1：90 BPM、8小节、C minor、seed7。
- v2：只将第3–4小节hihat改为每小节32音符；原为8，共增加48音符。
- kick/snare/bass/chords 均受保护；每轨前后SHA-256记录在v2.json audit.protected_hashes。
- 每个版本均附原生JSON、Type1 MIDI和参考WAV，音频只作为派生输出。

尚未验收：真实LLM网络调用/账号模型权限、Logic Pro实机导入、音乐审美质量。测试通过不代表Logic实际轨道布局或乐器音色已验证。试听是简单合成器，不承诺采样级音质。

## DeepSeek Planner增量验收（本轮实际执行）

日期：2026-09-16。先核查现有实现，再修改；未把上文历史记录当作本轮复验。

### 基线与差异

- 已阅读README、DESIGN、ACCEPTANCE、Planner、CLI/API、数据模型、Service、生成/导出与原有测试。仓库及适用上级目录未发现AGENTS.md。
- 工作目录没有 `.git`，`git status --short` 返回“not a git repository”；无法据此判断未提交修改，未执行初始化/重置。对既有代码、测试、数据库、示例记录SHA-256核对。
- 首次执行 `.venv/bin/python -m unittest discover -s tests -v`：18项中16项通过、2项ERROR；两项HTTP测试被沙箱禁止监听本机端口。此时不能宣称18项通过。
- 允许loopback监听后用同一命令重新执行：**18项全部通过，1.229秒**。这是本轮真实基线。
- 文档/源码差异：原LLM输出的BeatSpec缺字段会由dataclass补默认值，与完整严格结构化输出契约不符；本轮仅在LLM边界增加完整字段检查，保留结构化创建接口的原默认行为。原CLI只有mock/openai参数，没有Provider环境选择，因此增量加入BEATMATE_PLANNER。

### 修改文件及原因

| 文件 | 变更 |
|---|---|
| beatmate/deepseek.py（新） | 明确DeepSeekPlanner；环境Key/模型/基础URL、Responses单路径、45秒超时、无重试回退、错误清理、禁止重定向 |
| beatmate/config.py（新） | 简单Provider选择和配置隔离，无配置默认mock，保留openai独立配置 |
| beatmate/llm.py | 抽出请求payload覆写点、provider标签复用；补完整字段/严格JSON/响应状态校验，保持原OpenAI请求路径 |
| beatmate/cli.py | 支持deepseek及BEATMATE_PLANNER；CLI优先，demo固定Mock；配置错误在Service初始化前退出 |
| beatmate/smoke_deepseek.py（新） | 显式开启、最多2次真实调用；临时DB、版本/范围校验、MIDI逐音符读回；明确PASS/SKIPPED/FAIL |
| tests/test_deepseek.py（新） | 24项新增离线测试；所有外部网络受测试替身阻断，不使用真实Key |
| .env.example（新）、.gitignore | 不含密钥的环境示例；忽略本地.env，程序不自动加载.env |
| README.md、docs/DESIGN.md、docs/ACCEPTANCE.md | 同步配置、兼容性边界、命令、真实验收状态与手工步骤 |
| THIRD_PARTY.md | 记录DeepSeek官方接口来源；没有新第三方代码、SDK或依赖 |

### 最终离线测试

执行 `.venv/bin/python -m unittest discover -s tests -v`（允许本机临时端口）：
**42项全部通过，3.343秒，包含全部原18项与新增24项。**

新增验证包括：
- 默认 `/responses`、自定义模型/URL、严格text.format、instructions、无store/会话字段、Key不进入payload或快照。
- 401/403、429、400/402/404/422、500/重定向、网络/超时、空/截断/非法JSON等失败；一次请求，无隐式重试/回退，错误不回显正文或凭据。
- 未知/缺失字段、重复JSON键、NaN、非法参数/操作、模型越权返回音轨/小节/保护列表被拒绝。
- 失败前后项目/版本数量一致；指定范围执行后其他轨、范围外音符、历史版本、保护hash不变；旧版本冲突仍拒绝。
- 默认Mock、显式覆盖环境、OpenAI与DeepSeek凭据隔离、非法配置不创建DB、DeepSeek环境下demo仍离线。
- HTTP失败返回502且不写版本；smoke默认跳过、缺Key跳过、网络跳过、鉴权FAIL、错误语义FAIL；注入两次响应后走原执行链导出MIDI。

执行 `.venv/bin/python -m compileall -q beatmate tests`：通过。

### 真实调用与阻塞项

实际执行：

```text
.venv/bin/python -m beatmate.smoke_deepseek
status=SKIPPED, reason=Explicit --run is required, calls=0

.venv/bin/python -m beatmate.smoke_deepseek --run
status=SKIPPED, reason=DEEPSEEK_API_KEY is not configured, calls=0
```

本轮后端环境没有DeepSeek Key。没有发生真实API调用，也没有声称DeepSeek账号、模型可用性或供应商端JSON Schema执行已通过。离线测试内的smoke PASS使用注入响应，仅证明本地验收链工作。

联网时如无网络权限/超时，smoke同样SKIPPED；鉴权/限流/模型或配置拒绝是FAIL。所有结果须查看status，不能只看HTTP200或进程退出码。唯一未完成的外部集成验证是：用户配置Key并显式执行上述--run。无需OpenAI Key。

### 数据与范围保全

本轮没有修改beatmate/model.py、service.py、generator.py、render.py、adapters.py及原tests/test_core.py、tests/test_interfaces.py。现有 `.beatmate/` 数据库和 `examples/` 文件内容SHA-256均与开始时一致；没有清除历史、重建项目或覆盖示例。测试只使用临时目录。未升级编曲、项目Schema、轨道数或前端。

### Logic Pro仍待手工验收

1. 配置后端Key并显式运行smoke，只有status=PASS后，使用报告output目录中的before.mid/after.mid。
2. 在空白Logic工程中分别导入，核对90 BPM、4/4、4小节、五条命名乐器轨；确认鼓在第10通道，手工指定鼓组/bass/keys。
3. 对比第3–4小节hihat：由每小节8音符变成16；第1–2小节及其余四轨保持一致，并试听检查。该smoke导出是4小节，仓库既有demo仍是8小节。
4. 检查音符可以在钢琴卷帘中手动修改。实际Logic轨道布局、音色和导入速度处理不由离线测试保证；本轮未实机执行。

## 原始emo hip-hop prompt真实测试与接入修正

用户重启后，用用户完整原文调用本机 `/parse` 一次。结果HTTP502、`LLM response incomplete`；本轮没有有效BeatSpec、没有继续模型编辑调用、没有生成项目版本或MIDI，不记作PASS。原文与结果保存在 `output/deepseek-prompt-test/2a9e407cdb764bdabad83f000f94003b/`，没有Key或供应商思考正文。

已确认当前接入未显式配置reasoning，而DeepSeek官方文档说明默认high；旧错误没有保留incomplete_details，所以token耗尽只是可能原因，尚未证实。为这类小型结构化任务固定reasoning.effort=none，保留2000输出token、原模型/协议和无回退策略；错误信息只允许回显max_output_tokens/content_filter两种安全原因。

修正后执行 `.venv/bin/python -m unittest discover -s tests -v`：**43项通过，3.388秒**。新增未完成原因白名单测试并更新请求构造断言。该结果仍是离线回归；运行中的后端需重启后再做真实复验。

prompt适配限制已明确告知用户：现有Schema最多32小节，76 BPM/4拍时约101秒，无法达到150秒；五轨规则引擎不含木吉他分解、独立稀疏钢琴/环境铺底、歌词或指定段落结构。未静默缩短后生成，也未把简化模板称为实现了原prompt。是否生成简化草稿等待用户选择。


## 简化emo草稿：真实DeepSeek闭环通过

用户明确选择“继续生成简化草稿”后，通过其本机127.0.0.1:8765服务完成两次模型调用；没有读取API Key。Provider返回deepseek:deepseek-flash，没有替换模型或Mock回退。

1. 将用户原文与已同意的能力范围说明一起传给/parse，验证BeatSpec为76 BPM、4/4、A minor、32小节、boom_bap、swing0.16、seed7。
2. /plan返回调用方指定hihat第9–16小节velocity=-18；经原Service创建与编辑生成两个新版本。
3. 验证其余四轨保护hash、范围外音符和原始版本不变，验证受保护kick编辑被拒绝且不产生版本。原数据库已有版本保持不变，仅新增两个草稿版本。
4. 两份MIDI经Mido逐音符读回，tempo/PPQ/轨道/时长一致；试听WAV帧数和非静音检查通过，约101.053秒。

结果：**真实模型→BeatSpec/EditPlan→版本执行→MIDI/WAV导出 PASS**。不代表音乐审美质量或Logic实机导入已通过。

产物：output/emo-draft/ac8f6ef58a4b498dbe30622ff504ae7c/
项目ID：a8cf8df1b8984f1794efa89dca01885a
编辑前版本：d5003176f377466dbd9a3af1955ce19b
编辑后版本：4c9303ea246e41b5b24e05d550d52e25

仍未实现原始prompt要求的吉他分解音色、独立钢琴/环境轨、808音色设计、歌词、完整段落、拖拍军鼓及150秒时长。草稿音符由既有规则引擎生成，WAV采用既有参考合成器。本轮没有升级编曲算法或项目Schema。

## 第二次增量：CreativeBrief与可执行编曲

日期：2026-09-16。本节为最新实际记录；上文按阶段保留历史状态，不代表新版能力已在此前验证。

### 实际基线与最终结果

- 已读取现有文档、Planner、模型、版本、生成、导出与测试代码，无适用AGENTS.md；工作目录仍无Git仓库，未初始化或重置。
- 基线命令 `.venv/bin/python -m unittest discover -s tests -v`：首次43项中40项通过、3项因沙箱禁止loopback监听报错；获准本机监听后 **43项全部通过，3.432秒**。没有将历史18/42项记录作为基线。
- 新增24项 `tests/test_creative.py`，保留原43项测试文件。开发中发现并修正3/4拍被忽略及否定表达跨分句误匹配；最后完整执行同一测试命令：**67项全部通过，8.610秒**。
- `.venv/bin/python -m compileall -q beatmate tests`：通过。
- `.venv/bin/python -m beatmate creative-demo --output examples/creative_v2`：通过，独立临时数据库、Mock，无API消费。
- 实际执行CLI `creative --planner mock` → `plan-edit --planner mock` → `apply-edit`，使用临时数据库与新输出目录：新版本ID不同，MIDI/WAV存在，完整闭环通过。
- 实际产物另行读回：A/B/C均7个MIDI chunk（meta+6乐器），WAV均50.5263125秒，按保存的spec/seed/generator_version/edit_history重现后逐轨完全相同。结果保存在 `examples/creative_v2/verification.json`。

新增测试包括明确证据/推断分离、用户参数不能被模型或默认值覆盖、否定约束、参考状态、同一艺人不同参数、未知输入披露、每个可执行字段单变量对比真实音符、音域/调内/动机、可选melody、六轨逐音符MIDI读回、减密锚点/范围/保护/速度、旧head冲突、无可删音符拒绝、非法及跨界编辑不落版本、旧快照/导出兼容、可信LLM规划范围、HTTP创建规划提交导出、建议不能提交。

### A/B/C交付

全部固定76 BPM、16小节、A小调、seed7，schema2、rules-v2.0。每例包含 input.txt、creative_brief.json、spec.json、explanation.json、version.json、beat.mid、preview.wav。

| 案例 | 实际选择/变化 | kick / snare / hihat / bass / chords / melody音符数 |
|---|---|---|
| A 克制、伤感、留白 | resolving和声、分解和弦、稀疏旋律/鼓、soft鼓、low bass、roomy | 16 / 32 / 64 / 16 / 48 / 32 |
| B 伤感但有推动力 | 参考A；descending和声、moderate鼓/bass/旋律变化、lift段落，soft鼓不变 | 56 / 34 / 128 / 32 / 48 / 32 |
| C A后半段更克制 | 只对melody第9–16小节thin_notes；该范围16→8个，总数32→24 | 16 / 32 / 64 / 16 / 48 / 24 |

C保存真实parent指向A，parent_hash核验一致；其他五轨保护hash一致，melody范围外事件不变。C附before-version.json、before.mid、proposal.json、diff.json、edit_intent.json、edit_brief.json；创作brief继承A，编辑原话和系统减密解释单独保存，不篡改A的原话。

### 保全与来源

升级前对源码、测试、既有示例和数据库建立SHA-256基线。复验原有测试文件、generator.py、render.py、全部既有示例音乐/JSON及 `.beatmate/state.sqlite3` 文件字节未变；数据库4个旧版本的MIDI/WAV重新导出hash与升级前一致。Finder的 `examples/.DS_Store` 出现变化，未尝试覆写恢复；它不属于音乐数据或版本快照。

旧schema1不补字段、不批量迁移；新增schema2走独立生成版本，继续复用原Service事务/保护/导出。依赖、密钥配置和音色库均未扩展，来源记录见THIRD_PARTY.md。代码文件及理由见CREATIVE_V2.md“文件与交付入口”。

### 验证边界及手工步骤

- 本轮DeepSeek/OpenAI的新CreativeSpec与编辑接口只用注入响应验证，普通测试禁止真实Provider网络。**新版真实DeepSeek调用 NOT_RUN**；此前schema1真实调用成功不能代替本轮实测。Key配置沿用README及.env.example，不读取或输出用户Key。
- 要做新版真实调用，在已配置Key的终端执行README的 `creative --planner deepseek` 和 `plan-edit --planner deepseek`，各一次；apply-edit和导出不调用模型。运行中的旧后端须重启才能加载新路由。没有自动成功回退。
- 人工试听仍是 **PENDING_HUMAN_REVIEW**，表格在 `examples/creative_v2/listening-review.json`，未填情绪匹配、Rap空间、音乐质量评价。未进行音频情绪分析或声称听过结果。没有共鸣评分。
- Logic Pro仍待手工：分别导入A/B的beat.mid，检查76 BPM、4/4、16小节、六条命名乐器轨；手动给鼓/bass/chords/melody选择音源，鼓第10通道、melody第3通道。音色建议供选音源，不是已生成真实吉他。
- 对比A和C：第1–8小节旋律一致，第9–16小节旋律每小节2→1音，首音保留；五条其他轨及速度一致。在钢琴卷帘中验证可编辑并试听。记录A/B情绪、推动力与Rap留白是否符合需求；自动测试不能替代这一步。

本轮没有复杂前端、多Agent、RAG、微调、ACE-Step、大型音色库或Logic界面自动化。仍不支持完整主副歌歌曲结构或150秒请求；没有把参考WAV冒充原生MIDI工程。

## Audio MVP / 音频产品 V1（2026-09-17实际验收）

### 基线与兼容

读取README、DESIGN、ACCEPTANCE、CREATIVE_V2及实际生成器/Planner/Service/API/CLI/测试；无适用AGENTS.md，当前目录无Git元数据，没有初始化或重置。实施前定义验收见AUDIO_MVP.md。

基线执行 `.venv/bin/python -m unittest discover -s tests -v`：67项，沙箱63通过/4项loopback权限错误；允许loopback后67项全部通过，8.897秒。保留原67项测试，新增27项音频测试。最终同命令：**94项全部通过，10.203秒**。开发期新增音频测试的本机监听也曾被沙箱拦截；放行后的最终回归无失败。完整日志：`output/audio-mvp-demo/regression.log`。

旧数据库 `.beatmate/state.sqlite3`、旧示例、原测试、model/service/generator/generator_v2/render共47个基线文件SHA-256全部未变，记录在 `output/audio-mvp-demo/preservation.json`。没有删除数据库、重写快照或全库迁移。旧schema1/schema2读取、生成、编辑、保护、范围、导出与复现由原测试继续覆盖。

### 已实际执行

- `.venv/bin/python -m compileall -q beatmate tests`：PASS。
- `.venv/bin/python -m beatmate audio-demo --audio-dir output/audio-mvp-demo`：PASS，独立测试信号，持久化请求→任务→资产→选择，真实Mureka调用0次。
- `.venv/bin/python -m beatmate audio-preview --text '150秒，64小节，伤感克制' --mode template`：PASS，无MIDI限制、无Planner调用。
- CLI真实子进程执行audio-create（同ID重复）、audio-work --once（三次）、audio-history、audio-select、audio-download、再次新进程audio-history：PASS，两个独立候选，重复请求同任务，下载hash一致，选择跨进程保留；记录 `output/audio-cli-verification/verification.json`。
- HTTP测试：创建→两候选→本地文件试听响应/下载→Range→选择→服务对象重建读取；同源允许、跨域/伪造Host/额外字段拒绝，PASS。
- 真实页面交互：打开127.0.0.1:8766，填写Template描述（150秒/64小节）、n=2、预览、核对、生成；观察等待提交→可用、两候选，选择第二个并刷新后仍“已选中”，PASS。
- 页面布局已截图检查。Codex内置浏览器点击play时崩溃，新标签页可恢复所有历史；**内置浏览器播放未通过**。底层HTTP/Range和WAV完整性通过。系统 `afinfo`读出1ch/16000Hz/Int16/4秒；沙箱 `afplay`曾返回AudioQueueStart -66680，允许音频设备访问后系统播放退出码0。此为可解码/可播放验证，非人工音乐质量评分。
- `.venv/bin/python -m beatmate.smoke_mureka`：**SKIPPED**，默认未显式启用，无付费提交。没有执行整组真实实验。

### 新测试覆盖与边界

Direct原文、Template不补事实/无Key、64小节输入、潜在冲突人工核对和明确BPM冲突拒绝；官方鉴权/端点/prompt/model/n/stream契约与多候选解析；不同候选与可变选择、不可变请求/结果、并发重复请求、两worker互斥、重启queued/generating/submitting、有ID时坏响应仍保留ID；暂时查询失败、下载失败、部分候选保存、损坏/缺失已完成文件恢复、uncertain禁止重发、明确拒绝不回退；坏JSON、空/截断/HTML文件、路径逃逸、私网DNS、重定向、响应长度、预算、Mock/真实隔离及错误不泄漏秘密。

真实账户模型授权、n=1实际支持、额度、计费、CDN实际域名与Mureka原始音频质量均未实测。官网schema模型枚举不代表账户权限；费用unknown。当前支持国际API入口，不自动跨地区；n本地默认1、最高2、真实请求预算默认1持久化。下游格式的系统解码器差异可能阻塞保存，不能因此宣称生成失败或重新付费生成。

### 交付状态

| 分类 | 状态 |
|---|---|
| 已实现 | 独立官方Adapter、Direct/Template、持久化状态/去重/恢复、本地原文件校验、多候选、选择、历史、CLI/API/页面、显式smoke入口 |
| 仅Mock/注入响应验证 | 全部生成工程闭环、网络异常/恢复、请求响应契约；Mock仅4秒测试信号 |
| 真实API已验证 | 无；本轮SKIPPED，未产生Mureka真实音频或账单 |
| 待人工试听 | examples/audio_mvp/A.json、B.json、C.json均NOT_RUN；五项评价为空。真实音乐情绪、质感、留白、段落及写词/录Rap意愿待评审 |

本地实际试听文件：`output/audio-mvp-demo/assets/c57548a6b080cb6b9e038023c0e081ab604cc2e6158708f637a5685bb1f5069c.wav`（4秒合成测试信号）。它不代表Mureka音乐或请求的150秒效果。两个候选的另一个文件hash为12d6ac50d914c950898a86cbd231b5e7d510e40cafa92a3b577c4b6b4779eb39。

启动、环境、CLI/API与真实smoke用法见README前部；文件清单和恢复设计见AUDIO_MVP.md。未实现Producer付费分轨/MIDI转录、多供应商、Agent平台或公网SaaS。

### 真实请求异常后的TLS排障修复

本地请求6e17213b…为uncertain、上游ID为空、HTTP状态码为空，原异常原因未保存。未重发、未提高预算、未修改原记录。无Key/无HTTP生成请求的网络检查：Python默认CA不存在，TLS链校验失败；使用macOS /etc/ssl/cert.pem 后TLS握手PASS。该复现不能作为旧请求未扣费的证明。

新增2项离线测试，验证证书失败发生在HTTP request调用前、错误不泄漏异常原文、状态明确失败且不自动重试。后续TLS错误保留固定分类；已开始发送的失败仍为uncertain。完整回归96项通过；compileall通过。历史94项记录保留为前次验收结果。配置说明新增SSL_CERT_FILE；用户后端需重启才加载修复。旧请求仍待用户核对供应商任务/计费。

### 已取得Mureka任务ID后的查询诊断

请求9bb09569…已取得provider_task_id=161274023641089，返回模型mureka-9；当前错误发生在generating查询阶段，尚无候选地址，不能据此归因为CDN/文件格式。没有重发或提高调用预算。

新增固定白名单错误说明及响应结构摘要（仅状态枚举、字段类型与候选数，不保存任意上游正文/秘密），前端现有error字段可展示具体原因。配置接口diagnostics_version=2可确认运行代码。新测试验证缺失index时保持原任务并清楚报错，异常响应中的任意秘密字段不保存。后端需由原启动终端重启，以保留用户自行配置的Key；不读取运行进程的Key。

### 首次真实Mureka生成成功与可选index兼容修复

2026-09-17，重启诊断版并仅恢复原任务查询后，task 161274023641089返回succeeded、一个候选，id为string、duration为int、url/flac_url/wav_url均为string；index缺失。先前强制index导致本地generating错误，这属于本地解析问题，不是供应商生成失败。已修复：index缺失按响应数组顺序生成展示序号，保存index_source=response_order；供应商候选ID仍保留，明确提供但类型非法的index仍拒绝。没有修改或重发原请求，没有提高预算。

新增缺少index的双候选完整保存回归，更新非法index诊断用例。真实状态更新为“供应商生成成功已确认，本地下载及人工试听待验”，不能继续将本轮所有真实调用概括为SKIPPED。后端须加载本次修复后继续下载原结果。

### 首条真实Mureka音频完整保存通过

2026-09-17，加载可选index修复后，恢复原任务161274023641089的查询/下载，未重新生成。任务9bb095697a804e0e82bc0ad95cd29bbf达到ready，错误清除，保存1个候选8b3036fe532752dd93ad875bc38dc6e0。

返回模型mureka-9，来源mureka_original；官方cdn.mureka.ai原生WAV，本地完整性读取时长160.67秒（约2分41秒，用户请求约2分30秒，并非精确满足），28342232字节。文件SHA-256：73055d7fb40362a3d43576aa2f78fd44beef84003b553ce9e5e785450f0ade8f。本地下载接口HTTP200、下载hash与资产一致。

验证报告output/mureka-recovery/verification.json；原文件.beatmate/audio/assets/73055d7fb40362a3d43576aa2f78fd44beef84003b553ce9e5e785450f0ade8f.wav。真实生成→查询→原文件保存→HTTP下载闭环PASS。人工情绪/音质/Rap留白评价仍PENDING_HUMAN_REVIEW，实际费用仍unknown，未自动替用户选择候选。先前SKIPPED与下载待验记录是历史阶段结果，本段为最新状态。

### 调用上限无需反复export

新增持久化额度设置、页面修改入口、GET/POST /audio/budget及audio-budget CLI。未自动提高用户额度；保存设置不会生成任务。新增跨实例/重启持久化、环境值优先级、即时执行限制、非法更新原子性测试，并扩展HTTP预算读写和跨域拒绝验证。

实际执行 `.venv/bin/python -m unittest discover -s tests -v`：100项全部通过，10.231秒；compileall通过。CLI在独立临时Mock目录保存4后以新进程读取成功，未调用真实API。尝试只读当前8767配置时服务未运行（ConnectionRefused），未因此猜测或覆写用户的当前上限。用户下一次启动新版服务即可通过页面主动保存额度，之后无需重复export或重启。

### 歌词摘要伴奏与歌曲三轨（2026-09-17）

已实现两条页面流程：完整歌词分析为可编辑编曲摘要，再生成纯伴奏；完整歌词通过歌曲接口生成整曲，再以 audio-separation-3 分离伴奏和人声。歌词独立上限5000字符，制作描述/最终音乐提示词上限1024字符。自动分析复用 DeepSeek 配置；也支持手动填写摘要。原始歌词保留，不静默截断。

整曲、伴奏、人声分别持久保存、试听、下载和收藏，记录来源关联与各自文件hash。每次歌曲任务在本地预留1+n个Mureka调用单位。分离提交结果不确定时不自动重发；下载失败只恢复下载；已保存的分离ZIP可用于恢复缺失音轨。

完整回归 `.venv/bin/python -m unittest discover -s tests`：**122项全部通过，11.982秒**。新覆盖包括5000字符歌词与独立提示词限制、分析去重及来源变更、歌曲/分离官方请求字段、多候选三轨、预算预留、下载恢复、提交中断、ZIP安全解析，以及HTTP三轨独立下载与文件名。测试日志：`/tmp/beatmate-final-workflows-tests.log`。

独立临时Mock服务8771的实际页面操作通过：1753字符歌词分析→手动修改摘要→预览→生成纯伴奏；同份完整歌词预览→歌曲生成→整曲/伴奏/人声三张播放器卡片；人声独立播放到4秒结束、独立收藏及筛选通过。浏览器未记录控制台错误。桌面布局已截图检查。

本轮未进行真实DeepSeek、Mureka歌曲生成或分离付费调用，未改变用户现有调用额度或历史音频。Mock仅为4秒测试信号。真实账户权限、分离ZIP实际命名兼容性和音乐/分离音质仍待真实结果验证；不能用上述Mock通过替代音质验收。用户原终端重启后端并刷新页面后加载完整功能。

### 纯伴奏改为仅描述输入（2026-09-17 需求修正）

页面纯伴奏模式移除歌词输入与自动分析/摘要入口，描述原文直接提交纯伴奏接口。歌曲模式保留完整歌词与制作描述以及三轨功能；两种模式均不需要文本模型。旧分析接口、数据和待确认请求保持兼容。

隔离Mock页面实际验证：已有1753字符歌词草稿时切换纯伴奏，歌词隐藏且禁用，音乐描述必填；预览仅含原描述，创建完成的任务为instrumental、lyrics为空，analysis与stem_jobs均为0；切回歌曲，1753字符歌词完整保留且重新成为必填。控制台无错误。本轮没有真实付费调用。README与配置示例同步更新。

现有音频回归55项全部通过（2.973秒），日志 `/tmp/beatmate-description-only-tests.log`。本次前端调整刷新即可加载。

### 歌曲预览入口与旧后端提示修复（2026-09-17）

只读检查用户8767服务，返回creation_modes仅instrumental/lyrics、stem_model为空，确认是后端仍运行旧代码导致歌曲预览按钮禁用。页面改为在按钮旁明确显示原终端重启命令，预览点击重新读取能力配置；不支持时给出错误且不提交生成，重启完成后无需依赖旧的页面配置。实际浏览器验证旧服务下按钮可点击、提示包含正确8767端口、控制台无错误；未发起任何付费调用。完整歌曲后端加载仍需用户在原启动终端重启，以沿用原有环境变量。

### 分离结果 MIDI 导出（2026-09-17）

已核对官方 Stem song 文档：audio-separation-3 支持 WAV/MIDI，响应包含独立 midi_zip_url。保留同次分离返回的MIDI地址，下载并验证原始ZIP及包内文件，页面提供整个ZIP和单文件下载。不增加分离POST或本地调用预留；与三轨音频独立恢复；旧结果缺地址明确提示，不自动再付费分离。

新增9项离线测试，涵盖ZIP与单文件原字节及hash、重启保留、下载失败不阻塞音频、音频失败不阻塞MIDI、单文件缺失/损坏从ZIP修复、缺少地址及旧记录、进程中断后恢复、HTTP下载类型与文件名/跨候选ID拒绝、CDN请求不带API密钥、安全ZIP及MIDI结构验证。完整回归131项通过（13.009秒），日志 `/tmp/beatmate-midi-full-tests.log`；compileall通过。

独立Mock页面8771验证三轨下的MIDI下载区、全部ZIP和两个示例MIDI链接，单文件按钮点击无控制台错误；HTTP回归实际获取ZIP/单文件并验证内容。桌面布局截图检查通过。未触发真实Mureka分离、未改动用户额度；Mock只是示例音符，真实MIDI命名/包格式与转录准确度待实际结果验证。新版后端需要在原终端重启，保留已有Key环境后刷新页面。

### 已扣费分离响应丢失排障（2026-09-18）

用户确认18:12分离接口扣费。本地b3e4ef5b…分离记录为uncertain，未保存ZIP或MIDI地址。created_at到按既有退避公式推算的失败时间约41.36秒，旧transport等待40秒，强烈提示客户端超时，但旧异常类型缺失，不能视为确认根因。未重发分离、未改旧记录或额度。官方公开查询文档针对歌曲生成任务，未找到用扣费记录查询分离结果的公开接口；已准备供应商支持说明output/mureka-recovery/stem-support-2026-09-18.md，未代用户发送。

修复：仅官方POST /v1/song/stem响应读取等待延长至600秒，建连及其他请求仍40秒；超时使用固定安全分类，保存新分离的提交/响应/失败时间、失败代码及明确HTTP拒绝状态。结果不确定时仍禁止重发。MIDI提示不再对失败分离显示持续等待。新增测试验证600秒设置范围、超时分类、不泄密及多次tick不重发。

### 移除累计额度最多10次限制（2026-09-18）

页面、HTTP、CLI及环境变量支持自定义正整数累计额度（例如100/1000），不再限定1–10；为保证JSON/JavaScript与SQLite数值精确，使用安全整数范围校验。保留已有计数和用户已保存额度，未自动更改当前真实额度，未产生付费请求。现有回归改为验证100/1000跨实例持久化、500环境参数以及HTTP接受100；非法参数仍原子拒绝。66项音频测试通过（4.315秒），日志/tmp/beatmate-budget-tests.log。新版后端须重启一次加载新校验，之后页面保存即时生效。

### 持久化音频配置与一键启动（2026-09-18）

按用户要求创建项目.env（权限0600，已被gitignore排除），保存Mureka真实服务开关、mureka-9、官方地址、下载域名、候选数、默认额度及系统CA路径。未读取运行进程秘密；当前执行环境没有API Key，保留空字段由首次启动输入。新增start.command与audio_start启动入口：继承Key可保存，否则getpass首次输入并原子保存，后续自动读取。音频CLI自动加载白名单音频/SSL配置，环境变量优先；audio-demo仍强制离线，旧MIDI Planner配置不变。未启动用户真实服务或触发付费调用，也未改变SQLite已保存额度。

新增4项配置测试覆盖纯文本解析不执行shell、环境优先、Key权限与配置保留、非法路径/输入、首次提示与再次复用、不泄露错误值。全量137项通过（13.265秒），日志/tmp/beatmate-env-tests.log；start.command通过zsh语法检查。

### 2026-09-19 · 创作室与曲库改版

- 保留现有 AudioService、真实生成、下载恢复、分轨与 MIDI 路径，新增独立作品元数据；歌名不写入音乐 prompt，空歌名兼容旧幂等指纹。
- 桌面创作室三栏 + 单一全局播放器；390×844 小屏切换创作/曲库，底部播放器始终保留。石墨灰、烟紫、浅金配色与本地唱片封面；可跳过、记住偏好、尊重 reduced-motion。
- 每首原始候选聚合整曲/伴奏/人声/MIDI；搜索、排序、收藏、重命名、批量选择、回收站及恢复。永久删除清理文件前与 worker 互斥，共享内容哈希文件保留到最后引用删除；不可变生成/计费记录保留，worker 不会复活被永久删除的产物。
- `.venv/bin/python -m unittest discover -s tests`：143 项全部通过。新增 6 项覆盖标题/幂等、元数据持久化、三轨/MIDI 清理及不复活、共享文件、多候选、原子回滚、worker 锁、HTTP/Origin/静态资源。
- 浏览器仅使用独立临时 Mock 服务（8771）：验证歌曲预览→确认→生成、三轨/MIDI 展开、伴奏实际媒体加载（readyState=4）、搜索时保留播放、收藏页、重命名同步播放器、删除→回收站→恢复、纯伴奏预览不带隐藏歌词、封面跳过偏好和手机布局。浏览器无 error/warn 日志。
- 未调用 Mureka、未修改或删除现有真实作品。已有 8767 服务需用户停止后重新运行 `./start.command`，再刷新；不自动替换正在运行的服务。封面为静态摄影背景配 CSS 唱片光泽动效，不是视频。


### 2026-09-19 · 连接诊断与机器人工作台

- 区分 DNS 解析、TCP 连接、TLS 握手与证书失败；查询/下载阶段失败明确说明原生成已提交，避免把“本次 HTTP 尚未发出”误读成整首作品从未提交。仅生成提交前失败才说明未提交生成，不放宽 TLS 或自动重发。
- 使用当前可信 CA 对 api.mureka.ai 做不带 Key、不发送 HTTP 的检查：DNS 4 个公共地址、TCP 成功、TLS 1.3 校验成功。历史泛化错误不能还原当时具体失败阶段；新的错误记录包含具体阶段。
- 用户要求清理的两条明确失败记录（5d0540be、9216264d）已移入可恢复回收站；一条 uncertain 记录保留。调用记录数量不变，真实音频未删除。
- 版本上限为 1 时显示文字提示，不再显示单选下拉；上限大于 1 仍可选择。音乐列表播放入口常驻、独立音轨按钮、创作类型标签；任务动态与已生成音乐分开；新增复用原描述/歌词到草稿，不自动生成。
- 封面由内置 image_gen 生成机器人在电脑、MPC、键盘和 DJ 工作台前编曲，文件 beatmate/ui/cover.png，完整提示词见 output/design-review/robot-cover-prompt.md。图片只做轻微 CSS 镜头/光效，不宣称机器人动作视频。
- 146 项离线测试通过，包括新增分阶段网络异常、查询阶段不误报未提交、失败任务软删除/恢复及拒绝移除未确认任务。独立 Mock 浏览器验证单版本展示、失败记录删除恢复、复用歌词与制作要求、音轨入口及封面。未调用付费生成 API。


### 2026-09-19 · 任务动态删除入口修复

- 实际运行的 8767 服务仍返回 library_version=1，旧前端条件因此隐藏了删除按钮。改为失败/待核对记录始终显示“删除记录”；旧服务显示准确重启说明，不静默隐藏操作。
- 新接口支持未产出音频的 failed/uncertain 任务软删除与恢复；不修改提交状态、不重发请求，保留全部调用记录。版本能力升为 3；旧 trash_failed/restore_failed 行为保持兼容。
- 7 项作品管理测试通过；新增检查 uncertain 删除/恢复后 tick 仍为 uncertain，成功作品拒绝按失败任务移除。JavaScript 语法检查通过。未操作真实任务状态或调用付费 API。
