# Audio MVP / 音频产品 V1

## 初始实施前验收契约（历史版本）

独立音频项目、请求、不可变结果与可变选择；不依赖 CreativeSpec、Planner 或 MIDI。保留所有 schema1/schema2 代码、快照、数据库和测试。

- Direct 原样保留描述；Template 只整理显式字段，空白不补偏好。保存输入、约束、假设（默认空）、最终描述和模板版本；1024字符上限，不受小节限制。原文与选项共同出现时必须人工核对冲突后提交。
- 官方 POST /v1/instrumental/generate，GET /v1/instrumental/query/{task_id}；Bearer 后端鉴权。显式模型，默认 mock，无真实失败回退。默认候选1，上限2，真实提交总预算默认1（持久化）。
- 创建请求先落盘并绑定幂等标识；重复标识相同内容返回原请求，不同内容409。queued → submitting → generating → downloading → ready；failed 为明确失败；uncertain 为无任务ID且提交结果不确定，禁止自动重发。
- 单进程文件锁保护后台执行；重启遇到 submitting 标记 uncertain；有ID只查询，下载失败只恢复下载。查询临时错误保留 generating。保存所有候选，用户选择独立，不自动选中晚到结果。
- 下载限定 HTTPS、公网IP与后端配置域名、禁重定向、大小上限，临时文件验证后原子落盘；资产按hash寻址且只读，读取验证hash，损坏/空文件不报告可用。优先WAV，其他格式据实保存。
- HTTP/CLI/页面完整闭环：创建→任务→所有候选→本地试听下载→选择→重启读取；默认127.0.0.1，64KiB请求限制，跨域禁止。
- 离线覆盖故障恢复、并发去重、模型契约、输入来源、文件安全、旧功能回归。真实smoke默认SKIPPED，显式一次提交、n=1；不能以Mock代替音乐质量验收。

## 核查与差异

目录无Git元数据、无适用AGENTS.md。原README无网页、DESIGN中ACE-Step仅属历史规划，本轮改用Mureka。原AudioAdapter.render绑定BeatSpec，保留兼容，新增异步InstrumentalAdapter边界。原生MIDI Creative V2不是未来Producer版本。

基线实际执行67项：沙箱63通过、4项loopback权限错误；允许loopback后67全部通过（8.897秒）。旧文档18/43/24不是本次结果。

## 官方契约来源

2026-09-17读取官方接口页面及该页面加载的公开OpenAPI schema：
https://platform.mureka.ai/docs/api/operations/post-v1-instrumental-generate.html
https://platform.mureka.ai/docs/api/operations/get-v1-instrumental-query-%7Btask_id%7D.html
https://platform.mureka.ai/docs/en/quickstart.html
https://platform.mureka.ai/docs/en/changelog.html

InstrumentalGenerateReq: model枚举auto、mureka-7.6、mureka-8、mureka-9、mureka-9.5；本地禁auto。prompt最多1024字符；n默认2、最多3，按候选计费，本地显式n=1起步。stream=false，不使用instrumental_id或参考上传。官方schema未标n最小值，n=1为待账户smoke核实项。Task: id、created_at、finished_at、model、status、choices；状态preparing/queued/running/streaming/succeeded/failed/timeouted/cancelled。候选id/index/url/flac_url/wav_url/duration，duration单位毫秒。API没有返回费用字段，实际费用unknown。

只使用国际官方https://api.mureka.ai，不自动切换地区；其他地区入口本轮不开放。模型在schema存在不代表账户获授权。网页会员不等于API额度。账户模型可用性、额度、n=1及CDN实际域名待真实验证。

## 实现与恢复细节

音频根目录默认 `.beatmate/audio`，独立 `audio.sqlite3` 与 `assets/`。audio_requests不可更新/删除；audio_tasks持久化状态、上游ID、临时链接、退避时间；audio_results不可更新/删除；audio_projects.selected可变。本地ID、候选ID独立，重复内容允许按SHA-256共享物理字节，但不共享候选身份或选择记录。

后台每秒扫描，一次只允许一个进程持有flock，真实查询默认5秒，故障指数退避最多300秒。不使用Redis/Celery。恢复提交中且无ID→uncertain；有ID→generating。即使上游返回未知状态或坏候选，只要ID有效先保存，后续查询原任务。uncertain不提供自动重发/认领接口，请先核对供应商账单；本地幂等不保证上游在所有网络故障下绝无重复扣费。新请求ID表示显式新生成。

已完成但缺失/损坏本地文件会从原任务恢复；已有资产只接受与原hash完全相同的字节，不把改变后的下载内容覆写成旧结果。下载失败时保留已保存候选并刷新原任务链接；不调用generate。默认不自动选择任何候选。

下载顺序wav_url→flac_url→url；优先保留WAV。仅HTTPS精确域名白名单与公网IP，DNS解析后固定连接IP并保留TLS主机名验证，不跟随重定向。默认cdn.mureka.ai来自官方示例，实际账户可能返回其他CDN；只在确认供应商真实域名后由后端配置MUREKA_DOWNLOAD_HOSTS，客户端不能传下载地址。单文件100MiB，API响应1MiB，请求64KiB。先写临时文件并fsync，再检查完整PCM帧/真实解码并原子保存、只读权限与hash核验。PCM WAV无外部依赖；其他格式或float/extensible WAV需系统afconvert（macOS）或ffmpeg，验证过程不转换所交付的原始文件。缺少解码器会保留downloading并明确不报告可用。

源素材未来导出：每个结果已有source_audio_asset_id和sha256，未来独立export记录引用这两个值和产物hash；本轮无分轨、转录或伪造MIDI。再次生成可传本项目source_audio_asset_id，只记录来源关系，未向Mureka发送音频条件，不能精确保留声部。

CLI/API输出原文、最终描述、请求模型/返回模型、provider时长（毫秒）与读取时长（秒），BPM/调性检测值为空。费用unknown，不计算或硬编码价格。Mock固定4秒测试信号，不按文字生成音乐、不证明情绪/结构/音质。

## 文件清单

| 文件 | 用途 |
|---|---|
| beatmate/audio_input.py | 独立Direct/Template输入、来源与冲突核对 |
| beatmate/audio_provider.py | 官方Mureka契约、受限HTTPS、独立Mock测试信号 |
| beatmate/audio.py | 独立持久化、状态机、后台恢复、资产校验、选择 |
| beatmate/audio_http.py、audio.html | API与最薄本地试听页面 |
| beatmate/audio_cli.py、smoke_mureka.py | 无Planner依赖的CLI与显式单次真实验证 |
| beatmate/adapters.py | 保留旧AudioAdapter，新增异步InstrumentalAdapter协议 |
| beatmate/api.py、cli.py | 增量挂接新路径，保留旧接口 |
| pyproject.toml | 安装时打包页面，不增加Python依赖 |
| tests/test_audio.py | 离线契约、恢复、文件与HTTP闭环验证 |
| .env.example | 后端配置说明，无秘密 |
| examples/audio_mvp/A.json、B.json、C.json | 人工实验输入/提示词与空评价表 |
| README.md、docs/ACCEPTANCE.md、docs/AUDIO_MVP.md | 使用方法、实测记录与边界 |

原生成器、Planner、模型、Service、渲染器和旧测试文件不修改。旧数据库与历史示例做字节hash核对；不迁移旧库。

## TLS连接排障（2026-09-17）

一次本地真实请求6e17213b…进入uncertain，未保存provider_task_id或HTTP状态码；旧实现吞掉具体网络错误，不能回溯证明上游是否收到。独立无Key握手检查复现Python默认CA缺失、证书链校验失败；显式使用macOS `/etc/ssl/cert.pem` 后TLS握手PASS，检查未发送任何生成请求。

修复仅对新发生的错误分类：DNS/连接/目标校验或证书失败发生在HTTP请求发送前时标记failed，并持久化白名单failure_code；HTTP开始发送后仍按uncertain保守处理。无自动重试、不释放预算、不修改旧uncertain记录。错误不保存异常原文或证书内容。

在macOS相同环境，可在启动后端的同一终端 `export SSL_CERT_FILE=/etc/ssl/cert.pem`，再重启后端；仍开启证书与主机名校验。其他系统使用管理员提供的可信CA路径。不要用关闭TLS验证的方法绕过问题。旧请求应先在Mureka API控制台核对任务与计费，确认没有任务/扣费后才由用户明确决定提高本地总预算并创建新请求。

实际响应兼容：Instrumental.index为可选；缺失时仅展示序号使用response_order，记录index_source，与供应商候选ID分开。首个真实已完成任务省略该字段，不应因此拒绝音频结果。

## 本地额度持久设置

新增audio_settings表仅保存max_submissions，不保存密钥。页面「调用额度设置」、GET/POST /audio/budget、CLI audio-budget [--limit N]共用此设置，可设置正整数累计上限（2026-09-18移除最多10次限制；数值须在JSON安全整数范围内）。保存值优先于环境变量，未保存时保留旧环境配置行为。创建任务在原BEGIN IMMEDIATE事务中读取最新额度并计数，已有服务实例无需重启即可识别变化；保存设置不提交任务、不清零历史、不改变已排队任务。返回used_submissions/remaining_submissions/budget_source方便页面说明。旧后端尚未重启时页面隐藏新控件，历史页仍可使用。


## 完整歌词摘要与三轨工作流（2026-09-17 首版，摘要入口已取消）

页面统一保留原话。`lyrics_summary` 使用最多5000字符完整歌词，经DeepSeek生成<=700字符编曲建议，用户可编辑到980字符；最终加入无唱词指令后<=1024字符。完整原文与摘要分开保存。`song_stems` 使用官方song/generate的独立lyrics字段（<=5000），prompt<=1024；查询走song/query。同源POST /audio/analyze-lyrics显式触发分析，preview/create不会隐式调用LLM。分析ID绑定完整来源，旧ID不同来源返回冲突；中断或失败不会自动重发。可手动摘要，无新增第三方Python依赖。

官方契约本次通过渲染后的官方文档核对：
- https://platform.mureka.ai/docs/api/operations/post-v1-song-generate.html
- https://platform.mureka.ai/docs/api/operations/post-v1-song-stem.html
- https://api-docs.deepseek.com/api/create-response/

分离请求POST /v1/song/stem，url来自已确认候选的url（MP3/M4A入口），model固定audio-separation-3。官方返回zip_url、expires_at，可选midi_zip_url。此版本只下载音频ZIP，不下载或宣称提供MIDI。原始整曲优先WAV保存，分离产物分别标记mix/instrumental/vocals，保留共同source_audio_asset_id、候选ID及各自hash。候选不等于音轨数量，选择仍显式进行。

新增audio_analyses、audio_stem_jobs表，保留旧请求/结果表。歌曲创建事务一次预留1+n个Mureka调用单位，失败或不确定不自动释放；DeepSeek分析费用独立，不是供应商账单。分离在主worker文件锁下运行，paid POST前持久化submitting；进程中断进入uncertain，不再次提交。成功响应先持久化ZIP地址再下载，下载/解析失败只处理原包；已保存ZIP保留hash并用于修复缺失分轨。ZIP地址不暴露到history。额外返回、未预留的候选只保存整曲，标记skipped，不自动付费分离。

ZIP通过现有CDN白名单与公网TLS通道读取，上限100MiB，展开总量200MiB、最多32个条目、单轨100MiB；拒绝目录穿越、绝对路径、符号链接、加密包和重复/不明确音轨名。只按明确vocals/vocal/voice与instrumental/accompaniment/no_vocals（或人声/伴奏）识别WAV，按字节验证音频后hash命名保存，绝不使用ZIP路径落盘。实际供应商包命名差异、账户分离权限及音质需真实测试；不依据Mock声称分离音质通过。

新增代码：audio_analysis.py、audio_stems.py，扩展audio_input/provider/service/routes及原页面。原生MIDI的模型、生成器、Planner逻辑及数据库不修改。旧Direct/Template/lyrics契约保留供历史幂等恢复，页面不再提供该切换。


## 当前两条创作流程（2026-09-17 需求修正）

纯伴奏只接收音乐描述与可选制作偏好，原话提交 instrumental/generate，不展示或发送歌词，不调用文本分析。歌词歌曲与分轨保留完整歌词、可选制作描述及三轨保存/播放/下载。页面移除分析与摘要入口；切换到纯伴奏时歌词字段隐藏且禁用，请求歌词为空；切回歌曲模式仍保留歌词草稿。旧分析接口和请求契约只作兼容保留，不自动分析或重发历史请求。


## Mureka 分离 MIDI 导出（2026-09-17 增量）

本节取代上文首版“只下载音频ZIP”的限制。官方 `audio-separation-3` 同时支持 WAV/MIDI，响应的 `midi_zip_url` 是独立ZIP；请求仍为相同 `POST /v1/song/stem`，未加入新付费接口或改变模型。成功响应时一起保存音频及MIDI地址，worker分别处理两种下载，MIDI状态为downloading/ready/unavailable。保留原始ZIP字节与hash，并将验证后的包内MIDI按内容hash独立落盘，关联源候选；history不暴露CDN地址。

页面每个已分离候选提供全部MIDI ZIP与单文件下载；缺失地址、旧结果未保存地址、下载失败有独立提示。只允许恢复已保存地址的GET下载，不自动再POST分离。服务重启后继续下载，单文件丢失或损坏可由本地ZIP恢复；ZIP丢失可从原地址恢复，hash变化则拒绝覆盖。

新增 `audio_midi.py` 使用已有mido依赖验证MIDI事件与轨道结构。ZIP最多20MiB、32项、展开20MiB、单文件5MiB；拒绝不安全路径、加密、符号链接、重复路径、非MIDI内容及无效/不完整轨道，供应商路径不用于本地落盘。音频和MIDI分别验证，任一下载失败不阻塞另一种产物。Mock提供明确标注的示例音符，不声称转录真实音频。

HTTP：GET `/audio/stems/{source_asset_id}/midi/download` 为原始ZIP；GET `/audio/stems/{source_asset_id}/midi/{file_id}/download` 为单个MIDI；POST `/audio/stems/{source_asset_id}/midi/retry` 仅恢复原下载。原生MIDI生成器及其既有导出保持独立。
