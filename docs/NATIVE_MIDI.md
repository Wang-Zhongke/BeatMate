# 原生 MIDI 实验路径

[返回产品首页](../README.md)

这是保留的独立 CLI/API 实验功能，用规则或可选 Planner 生成音符工程，并验证局部编辑范围。它不编辑 Mureka 生成的音频，也不是歌曲分离得到的 MIDI 转录。以下包含早期实现与验证背景；当前音频产品从 `audio-serve` 进入。

## 情绪与场景创作

新入口保存原话及 CreativeBrief，将系统推断独立记为 assumptions；规则生成器把高层参数变成音符。新增可选 melody、分解和弦、密度/力度/留白及四小节段落变化。艺人参考只转成宽泛创作特征，不读取具体歌曲、歌词或采样。完整字段行为与兼容策略见 [Creative v2 设计](archive/CREATIVE_V2.md)。

```bash
# 永久离线 A/B/C 演示，临时独立数据库；输出目录必须不存在
.venv/bin/python -m beatmate creative-demo --output output/creative-demo

# 创建可继续编辑的项目，返回 project_id / version_id
.venv/bin/python -m beatmate creative --planner mock \
  --text '凌晨一个人回家，想念一个人，但不想太压抑，给旋律Rap留空间。76 BPM，16小节。' \
  --output output/my-creative-beat

# 将 PROJECT_ID 替换为上一步结果；规划不提交
.venv/bin/python -m beatmate plan-edit --planner mock --project PROJECT_ID \
  --text '只让后半段旋律更克制，鼓和贝斯不要动。' --output output/my-plan.json
.venv/bin/python -m beatmate apply-edit --project PROJECT_ID \
  --proposal output/my-plan.json --output output/my-edited-beat
```

可在子命令之前添加 `--db path/to/database.sqlite3`。比较“比刚才更有推动力”时，`creative` 使用 `--reference-project PROJECT_ID --reference-version VERSION_ID` 读取可信参考。明确范围可用 `plan-edit --track melody --start-bar 9 --end-bar 16`；缺少明确范围只返回建议，不能 apply。`thin_notes` 每小节保留首音及交替音符；其他轨、范围外事件和速度不变，沿用保护与事务校验。

现成 [A/B/C 示例](../examples/legacy-midi/creative_v2/README.md) 包含原始输入、brief、规格、参数说明、MIDI、参考WAV及C的差异与保护哈希。三例均为76 BPM、16小节，约50.5秒；人工试听记录尚未填写。音色建议不是实际吉他音色，基础WAV不代表成品混音，也不声称共鸣或已经听过结果。

启动仍使用 `.venv/bin/python -m beatmate serve`，无配置默认Mock。已运行的旧服务需重启才会加载新入口：

```text
POST /creative/parse       {"text":"伤感、克制，76 BPM，16小节。"}
POST /creative/projects    {"text":"伤感、克制，76 BPM，16小节。","protected_tracks":["kick","bass"]}
POST /projects/{id}/plan   {"text":"只让后半段旋律更克制，鼓和贝斯不要动。","base_version":"VERSION_ID"}
```

两个创作接口可传 `reference_project_id` / `reference_version`；创建接口可额外传完整 `spec`，跳过模型但仍校验原话约束。规划接口可传 `track_id/start_bar/end_bar/protected_tracks`；模型不能扩大范围。把 ready 响应的 `base_version/plan/protected_tracks` 交给既有 `/projects/{id}/edits` 提交。导出接口保持不变。

DeepSeek 环境配置沿用下文，无新密钥或SDK。配置后将上述 `creative` / `plan-edit` 的 `--planner mock` 改为 `--planner deepseek` 即分别显式调用一次真实模型；`apply-edit` 不调用模型。v2 自动测试覆盖注入响应及离线闭环，**未实测新版结构化规格的真实DeepSeek调用**；下文既有 smoke test 覆盖的是 v1。不会失败后回退Mock。

现有限制：4/4、1–32小节；有短动机和四小节起伏，没有完整主歌/副歌歌曲结构、歌词、真实木吉他/808音色设计。不支持的“更温暖/更空灵”编辑只返回建议。旧五轨入口与历史快照保持 schema_version=1，新创作入口才使用2，不批量迁移。

## 五轨离线演示

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m beatmate demo
.venv/bin/python -m beatmate serve
```

`demo` 全程离线生成 90 BPM、8小节的 boom bap；保护 kick/snare/bass/chords，只把第3–4小节 hihat 改为每小节32个音符。`output/` 内含两个版本的 JSON、MIDI 和 WAV。默认 Mock 永久可用，不读取密钥、不调用网络。依赖只在首次安装时下载。

仓库已包含 [前后对比样例](../examples/legacy-midi/demo)：v1/v2 的 MIDI、WAV 和完整状态。WAV 是由原生音符驱动的简单参考合成器，不是成品混音。MIDI 是 Type 1，包含一条速度/拍号轨和 kick、snare、hihat、bass、chords 五条命名轨。

## 核心契约

先定义的 [设计与验收标准](archive/DESIGN.md) 说明项目状态、Note/Track/BeatSpec、工具接口和版本约束。

- 固定4/4，480 PPQ，整数 tick；支持 boom_bap / trap，1–32小节。
- 每次成功编辑保存完整新版本，记录 parent、编辑方案和受保护音轨的 SHA-256 前后值。
- 提交前验证所有其他音轨、所选音轨范围外音符及元数据完全不变。
- SQLite 事务比较 base_version；过期或并发写入返回409。历史记录带禁止更新/删除的数据库触发器。
- 音轨保护跨版本继承，可以追加；首版没有解锁操作。
- 原始音符数据是事实来源。试听音频不会回写或冒充 MIDI 工程。

## API 使用

默认 `127.0.0.1:8765`。请求 `Content-Type: application/json`。仅供本地开发使用，不应直接暴露到公网。服务拒绝浏览器跨域请求。

```bash
curl http://127.0.0.1:8765/parse -H 'Content-Type: application/json' \
  -d '{"text":"90 BPM boom bap 8小节 C minor seed 7"}'

curl http://127.0.0.1:8765/projects -H 'Content-Type: application/json' \
  -d '{"text":"90 BPM boom bap 8小节 C minor seed 7","protected_tracks":["kick","bass"]}'

curl http://127.0.0.1:8765/plan -H 'Content-Type: application/json' \
  -d '{"text":"加密32","track_id":"hihat","start_bar":3,"end_bar":4}'
```

将创建响应中的 `project_id` 和 `version_id` 用于后续请求。`/plan` 只返回方案，不改变工程。用 `/parse` 检查最终参数，再将 `spec` 提交给 `/projects`，可以将意图解析和正式生成分开。

```text
POST /projects/{project_id}/edits
{
  "base_version": "创建或上一次编辑返回的 version_id",
  "plan": {"track_id":"hihat","start_bar":3,"end_bar":4,"operation":"density","value":32},
  "protected_tracks": ["snare"]
}

GET /projects/{project_id}
GET /projects/{project_id}/versions/{version_id}
GET /projects/{project_id}/versions/{version_id}/midi
GET /projects/{project_id}/versions/{version_id}/preview
```

支持编辑：`velocity` 力度增量、`transpose` 半音增量、`density` hihat每小节8/16/32音符、`mute` 删除范围内音符。力度或音高超界会拒绝，不做静默裁剪。小节从1开始、包含终点。跨边界延音拒绝编辑。没有撤销覆盖操作；可随时导出老版本。

Mock 识别有限关键词，未识别的创作描述会返回 defaults/assumptions；不支持的编辑会明确失败，不宣称理解了任意自然语言。

## 运行时 Planner：Mock / DeepSeek / OpenAI

第一次增量新增 `DeepSeekPlanner`，复用已有 Responses 请求格式、响应解析和本地校验。第二次增量增加上文可选创作路径，旧五轨路径保留。Key只从后端进程环境读取，不写项目状态或日志。

选择顺序：`serve --planner ...` > `BEATMATE_PLANNER` > `mock`。`demo` 永远使用离线 Mock，即使环境选择了 DeepSeek。直接使用 `Service()` 仍默认Mock；Python调用方可显式传入 `create_planner()` 的结果。

不含密钥的示例：[config/native-midi.env.example](../config/native-midi.env.example)。**原生 MIDI Planner 不会自动加载 `.env`**，需要进程环境变量。音频工作台的 `audio-*` 命令会加载音频配置，两者是独立入口。

| 环境变量 | 默认值 | 作用 |
|---|---|---|
| `BEATMATE_PLANNER` | `mock` | `mock` / `deepseek` / `openai` |
| `DEEPSEEK_API_KEY` | 无 | 仅deepseek需要；不接受OpenAI Key代替 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | HTTPS基础URL；追加 `/responses`，可保留网关路径前缀 |
| `DEEPSEEK_MODEL` | `deepseek-flash` | 模型名可配置，空值会报错 |
| `OPENAI_API_KEY`、`BEATMATE_MODEL` | 无 | 仅显式选择openai时需要；保留原配置方式 |

在macOS默认zsh终端中配置（密钥输入不回显，不作为命令字面量）：

```zsh
export BEATMATE_PLANNER=deepseek
export DEEPSEEK_BASE_URL=https://api.deepseek.com
export DEEPSEEK_MODEL=deepseek-flash
read -rs 'DEEPSEEK_API_KEY?DeepSeek API Key: '
export DEEPSEEK_API_KEY
.venv/bin/python -m beatmate serve
```

也可显式选择Provider：

```bash
.venv/bin/python -m beatmate serve --planner deepseek
.venv/bin/python -m beatmate serve --planner mock
# 仅在自行配置OPENAI_API_KEY与BEATMATE_MODEL后：
.venv/bin/python -m beatmate serve --planner openai
```

DeepSeek主调用路径是 `POST {DEEPSEEK_BASE_URL}/responses`，官方默认地址为 `https://api.deepseek.com/responses`。使用 `instructions`、`input`、`max_output_tokens=2000`、`reasoning={effort:none}` 和 `text.format={type:json_schema,strict:true,...}`；不发送DeepSeek不支持的 `store`，不使用 `previous_response_id` 或供应商会话。请求45秒超时，不重试、不跟随重定向、不切换协议/模型/Provider。

本地要求模型返回完整BeatSpec字段；拒绝未知字段、重复JSON键、非JSON数值及非法业务参数。编辑只允许模型返回operation/value，调用方指定音轨与小节始终保留。工程保护列表由本地执行链管理。网络/鉴权/限流/响应格式错误返回502且不提交版本；最终音高、力度、保护轨、范围和旧版本冲突仍由原Service校验。

启用DeepSeek/OpenAI时，创作请求发送至对应供应商；Mock完全离线。DeepSeek自定义Base URL必须是可信HTTPS基础地址，不能包含凭据、query或fragment，也不能填写完整 `/responses` 端点。错误信息不回显供应商响应正文或Key。

兼容性依据：[DeepSeek Responses API](https://api-docs.deepseek.com/zh-cn/guides/responses_api/)、[DeepSeek官方首页](https://api-docs.deepseek.com/)。既有OpenAI路径依据：[OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)。原生 MIDI 的自动测试使用 Mock 或注入响应；真实 Planner 和 DAW 导入需在各自环境中单独验证。

## 显式真实调用 smoke test

默认不联网：

```bash
.venv/bin/python -m beatmate.smoke_deepseek
```

在上面的后端环境配置完成后，显式允许最多两次付费请求：

```bash
.venv/bin/python -m beatmate.smoke_deepseek --run
```

该命令只调用DeepSeek，不受 `BEATMATE_PLANNER` 影响。先验证自然语言得到指定BeatSpec，再验证hihat第3–4小节EditPlan，经原Service执行、保护校验、历史版本校验，再导出并逐音符读回MIDI。没有拿HTTP 200作为成功标准。

- `PASS`：以上所有环节通过；在 `output/deepseek-smoke/<唯一ID>/` 保存 before.mid、after.mid、无密钥report.json。
- `SKIPPED`：未显式启用、没有Key，或联网不可用/超时；绝不等同PASS。
- `FAIL`：鉴权/限流/配置错误、不完整或非法输出、约束不匹配、执行/导出失败。

FAIL退出码为1，PASS/SKIPPED为0；自动化判断必须读取JSON的 `status`，不能只看退出码。使用临时独立数据库，成功后仅保留MIDI与验收报告；不访问既有数据库、不覆盖示例。普通单元测试仅注入响应，不调用真实API。


## DAW 手工验证

在空白工程导入 `examples/legacy-midi/demo/v1.mid`，检查 90 BPM、4/4、8 小节及五条乐器音轨；鼓音符在第 10 通道，需要手动选鼓组、bass、keys。再导入 v2，核对只有第 3–4 小节 hihat 变密。Logic Pro 实际导入表现仍需实机验证；不生成 `.logicx`，不复制插件音色。
