# 音频 CLI 与本地 API

[返回产品首页](../README.md) · [配置与排障](CONFIGURATION.md)

先从仓库根目录启动音频服务。以下命令使用已有 `.venv`；HTTP 示例的基地址是 `http://127.0.0.1:8767`，请求使用 `Content-Type: application/json`。`audio-serve` 与 `start.command` 默认端口统一为 8767。接口仅面向本机，不是公网服务。

## CLI 与 API

```bash
.venv/bin/python -m beatmate audio-preview --text '温暖怀旧、不绝望，适合讲述过去的关系。' --mode direct
# 首次创建使用新UUID；网络重试必须复用同一个REQUEST_ID，明确再生成才换ID
.venv/bin/python -m beatmate audio-create --request-id REQUEST_ID --text '伤感、克制，给Rap留白。' --mode direct
# 不启动网页时运行后台；Ctrl-C停止，下次继续恢复
.venv/bin/python -m beatmate audio-work
.venv/bin/python -m beatmate audio-history
.venv/bin/python -m beatmate audio-task --task TASK_ID
.venv/bin/python -m beatmate audio-select --project PROJECT_ID --asset ASSET_ID
.venv/bin/python -m beatmate audio-download --asset ASSET_ID --output my-original.wav
```

REQUEST_ID需32位小写十六进制，可用 `.venv/bin/python -c 'import uuid; print(uuid.uuid4().hex)'` 生成并保存。每条音频命令可加 `--audio-dir DIR`，须指向同一个目录。下载扩展名请按实际format填写，命令不转换文件。`audio-create --project PROJECT_ID`在同一项目新增候选；`--constraints '{"purpose":"录Rap"}' --reviewed`保存明确选项和人工核对标记。新页面统一保留原文；旧CLI/API的prompt_mode仅为兼容旧请求继续保留。语义冲突不能完整自动识别，有选项时要求核对；识别出的数值BPM冲突必须修改。

| HTTP | 用途 |
|---|---|
| GET /audio/config、/audio/history | 无密钥配置状态、历史/选择 |
| GET /audio/library/revision | 持久变化序号、活跃任务标记与服务实例；不读取音频文件 |
| POST /audio/library/page | 分页曲库与任务动态；字段见下文 |
| GET /audio/health | 本机配置、解码器、证书、版本与目录检查；不联网 |
| POST /audio/health/network | `{}`，仅 DNS/TCP/TLS 检查，无 HTTP/付费请求 |
| GET /audio/works/{source_asset_id}/export | 将已保存原文件、MIDI、备注及清单打包为 ZIP；不补下载，不创建分离 |
| GET /audio/assets/{id}/notes | 当前音轨的时间点备注 |
| POST /audio/assets/{id}/notes | 新增 `{action:"add",seconds,text}`；删除 `{action:"delete",note_id}` |
| GET /audio/budget | 当前音频库的累计调用额度与占用情况 |
| POST /audio/budget | `{max_submissions}`，保存累计调用上限；不发起生成 |
| POST /audio/analyze-lyrics | 旧版兼容接口，当前页面不使用 |
| POST /audio/preview | `{raw_text,creation_mode?,lyrics?,constraints?,arrangement_summary?,analysis_id?}` 查看最终描述，不调用LLM |
| POST /audio/tasks | 上述字段加request_id、title?、n?、project_id?、reviewed?、source_audio_asset_id?；立即返回202 |
| POST /audio/library | `{ids, action, title?, favorite?}`；rename / favorite / trash / restore / purge / trash_failed / restore_failed / trash_task / restore_task |
| GET /audio/tasks/{id} | 持久化真实状态 |
| POST /audio/tasks/{id}/retry | `{}`，仅恢复查询/下载，不重新付费提交 |
| GET /audio/stems/{source_asset_id}/midi/download | 下载原始 MIDI ZIP |
| GET /audio/stems/{source_asset_id}/midi/{file_id}/download | 下载包内单个 MIDI 文件 |
| POST /audio/stems/{source_asset_id}/midi/retry | `{}`，仅恢复原 MIDI 下载，不再付费分离 |
| POST /audio/stems/{source_asset_id}/retry | `{}`，仅恢复原分离ZIP下载，不重复付费分离 |
| POST /audio/projects/{id}/select | `{asset_id}` |
| GET /audio/assets/{id}、/download | 已校验本地原文件，支持单段Range |

`creation_mode`：描述生成纯伴奏使用 `instrumental`，`raw_text` 必填且不提交歌词；歌词歌曲分轨使用 `song_stems`，歌词必填，制作描述可空，创建须 `reviewed:true`。旧 `lyrics`、`lyrics_summary`、分析接口与prompt_mode保留兼容历史请求，当前页面不提供歌词分析或摘要入口。CLI保留原直接描述命令，歌曲分轨通过页面或上述HTTP接口操作。

任务的 `progress` 包含当前步骤、最后已确认步骤、最近尝试、恢复指引与 `can_resume`；旧记录没有时间字段时不推测补造。备注最多 500 字符，每条音轨最多 500 条；永久删除作品时一并清理。导出总文件量上限 512 MB，清单明确标记缺失文件；后台正在处理音频时返回可稍后再试的提示。

状态：queued等待提交、submitting提交中、generating生成中、downloading等待下载、ready可用、failed明确失败、uncertain提交结果不确定。uncertain无自动重发入口，请人工核对供应商账户。多候选分别保存；迟到任务不替换选择。原文BPM/情绪/配器是请求值，供应商返回时长和本地读取时长单列，实际BPM/调性为空，费用unknown。

## 分页读取

`POST /audio/library/page` 接受以下可选字段，未知字段会被拒绝：

```json
{"offset":0,"limit":30,"view":"music","filter":"all","search":"","sort":"newest","include_ids":[],"task_offset":0}
```

- `limit` 为 1–50；`offset`、`task_offset` 为 0–10000000 的整数，超出当前结果范围时回到最后一页。
- `view` 为 `studio` / `music` / `favorites` / `trash`；`filter` 为 `all` / `favorites` / `mock`；`sort` 为 `newest` / `oldest` / `title`。搜索最多 200 字符，按作品名全库匹配，忽略大小写，`%` 和 `_` 按普通文字处理。
- `include_ids` 最多 50 个原作品资产 ID，用于保留当前播放、队列和 A/B 试听的数据；这些额外作品不计入当前页或匹配总数。
- 响应保留历史数据结构，但仅展开本页、保留试听作品和本页任务相关的数据。`page` 返回 `ids/offset/limit/total`；`activity` 返回独立任务页，同样四个字段，每页 10 条。
- `revision/active/instance` 用于判断数据库变化及服务重启。页面先检查这些轻量字段，变化后取当前页；无变化时每 60 秒重新校验当前页文件。活跃任务约 3 秒检查一次，空闲约 12 秒，隐藏标签页暂停。

旧 `GET /audio/history` 与 CLI 历史查询仍返回全量结果，保持兼容。分页减少传输和文件核验范围；总数统计与文字搜索仍在本地数据库中完成，并不代表任意规模曲库都具有固定查询成本。

## 真实 smoke 与人工试听

```bash
.venv/bin/python -m beatmate.smoke_mureka       # 默认SKIPPED，零调用
```

先按 [配置指南](CONFIGURATION.md) 保存真实服务配置。独立的 `smoke_mureka` 模块只读取进程环境，不会自行加载 `.env`；以下命令从仓库根目录加载配置，然后**显式运行一次真实付费提交，n=1**：

```bash
.venv/bin/python - <<'PY'
import os
from beatmate.audio_env import load_audio_env
from beatmate.smoke_mureka import main

os.environ.update(load_audio_env())
raise SystemExit(main(["--run", "--audio-dir", "output/mureka-smoke"]))
PY
```

再次运行同一目录恢复同一个请求，后续仅查询/下载。超时为PENDING，可继续运行；无Key/未启用/账户权限额度阻塞为SKIPPED；明确生成或契约错误为FAIL。PASS只证明工程闭环，听感仍待人工。报告保留输入、最终提示词、模型、文件和hash，不包含Key。不要用不同目录反复运行来绕过预算。

[A/B/C人工实验表](../examples/audio_mvp) 已准备，均NOT_RUN，无真实输出、无人工评分；不自动批量付费。核心边界、状态恢复和文件清单见 [AUDIO_MVP](AUDIO_MVP.md)，实际回归见 [ACCEPTANCE](ACCEPTANCE.md)。
