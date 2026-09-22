# 第二次增量：CreativeBrief 与可执行编曲（实施契约）

旧的 /parse、/projects、/plan 和 schema_version=1 五轨路径保持不变。原“旋律移调”测试实际是 chords 移调；不是 melody。新建创作项目显式使用 /creative/parse 或 /creative/projects，schema_version=2；可选 melody 为第六轨。无批量迁移，不重写旧快照，generator.py 和旧 MIDI/WAV 渲染路径保留。

CreativeBrief 保存 raw_text、引用原文的 explicit（情绪、场景、表达方式、参考、用途、禁止项）、可识别的明确参数 constraints、系统 assumptions、参考解释和限制。不能把选择的BPM/调性/音色当作用户原话。未知描述保留原文并说明识别限制。艺人名称不作为模板索引；参考解释来自最终选择，禁止复制歌曲/歌词/采样。共鸣无自动评分。

CreativeSpec在旧BeatSpec基础上添加arrangement。每个字段严格枚举并完整标准化：harmony、chord_style、melody_enabled、melody_density、melody_variation、drum_density、drum_velocity、section_variation、bass_presence、vocal_space、timbre_hint。最后一项仅为制作建议，不改变音符或宣称真实乐器音色。情绪类别记录在brief，与boom_bap/trap节奏基础分开。

新规则生成器版本 rules-v2.0。保存spec/seed/generator_version；重现从原spec生成，再重放edit_history。局部编辑不篡改全局spec。解释由本地已应用参数、实际音符统计和编辑记录产生，不能由LLM自由编写效果声明。

新thin_notes仅melody、value=50：按小节保留第一个锚点及偶数序号音符，删除其余；不能减少时失败。跨界延音仍拒绝。其他velocity/transpose/density/mute语义保留（density仍只hihat）。所有修改沿用旧Service的事务、保护哈希和范围比较。

新 /projects/{id}/plan 从服务器读取指定版本（无客户端快照），返回base_version、保护集合和plan或suggestion。显式调用方范围优先；可根据可信总小节解析“后半段旋律”。无明确范围/轨道时仅建议；温暖/空灵等不支持执行的描述仅建议。规划不提交，/edits显式提交且再次验证旧head/保护/音符。

新增验收：原43项保留；逐字段单变量对比真实音符；否定/约束优先；六轨MIDI读回；melody原创规则动机/变化/音域/和声约束；范围减密与保护；失败原子性；v1历史和MIDI/WAV哈希不变；固定spec/seed和edit_history复现。A/B/C导出raw input、brief、spec、说明、快照、MIDI/WAV，C附差异。人工听感、Rap适用性和Logic导入保持待评审记录，不用测试代替。

## 已实现字段、作用与验证

全部新字段属于 `spec.arrangement`，解析要求完整对象、严格枚举；旧 BeatSpec 不接受新字段。测试位于 `tests/test_creative.py`，单变量测试固定 seed、BPM、其他参数，对比实际事件及未受影响的轨道，不仅对比 JSON 标签。

| 字段 / 取值 | 生成器实际行为 | 验证与状态 |
|---|---|---|
| harmony: descending / resolving | 调内四小节级数分别0/6/5/6及0/5/2/4，从0起算；改变和弦、bass、melody音高 | 已生效；三轨音高变化、鼓不变，调内及首音和弦音检查 |
| chord_style: block / arpeggio | 三音同时进入，或每拍逐音进入 | 已生效；chords起音/时值变化，其他轨不变 |
| melody_enabled: true / false | 增加或省略melody轨，不给旧项目补轨 | 已生效；五/六轨集合、MIDI读回 |
| melody_density: sparse / moderate | 基础每小节3 / 5个位置；roomy进一步减为2 / 4个 | 已生效；目标音符数增减，其他轨不变。无melody时标记inactive |
| melody_variation: low / moderate | 重复短动机 / 四小节句末改变部分非首音音高 | 已生效；音高差异、音符数保持、其他轨不变。无melody时inactive；不足四小节时无句末触发 |
| drum_density: sparse / moderate | 基础kick每小节1 / 3个，hihat 4 / 8个；snare维持boom_bap/trap基础 | 已生效；鼓事件数，非鼓轨不变 |
| drum_velocity: soft / medium / strong | 基础68 / 88 / 108，固定seed微变化；hihat再减18 | 已生效；仅鼓力度变化，起音/音高/时值不变；不是母带响度承诺 |
| section_variation: steady / lift | lift在第5–8、13–16等小节加kick、鼓力度+6、句尾加snare并变化旋律末音 | 已生效；指定段落的鼓/旋律差异，其他轨不变；少于5小节无lift触发 |
| bass_presence: low / medium | 每小节1个600tick/力度65，或2个840tick/力度88的bass音 | 已生效；事件/时值/力度，其他轨不变；不是特殊808音色 |
| vocal_space: roomy / balanced | roomy省掉melody末拍位置；block缩至960tick、分解和弦省末拍；balanced保留末拍/较长和弦 | 已生效；chords/melody占用变化，鼓及bass不变；Rap适用性仍需人工判断 |
| timbre_hint: soft_piano / nylon_guitar / muted_keys | 保存在建议中，供Logic选乐器；不改变程序音色或音符 | **advice_only**；单变量测试确认所有事件相同，说明不得声称真实吉他演奏 |

旋律来自seed选择的短规则动机，结合和声作重复与小变化；音域MIDI 60–83、调内、每小节首音为和弦音、无越界/重叠。不是从指定艺人的歌曲提取。短模式在数学上可能与其他音乐偶合，不声称做过全曲库相似度检索。

## 输入证据与限制

`raw_text` 原样保留；`explicit` 各分类保留原文片段，分类不是自由改写。`constraints` / `constraint_sources` 保存可识别的BPM、小节、seed、swing、调性、节奏基础和直接编曲要求。系统选定的所有其他参数进入 `assumptions`，包括音色提示、密度及表达强度。

“不要太密”采用 sparse melody/drums；“不要太压抑”采用 resolving、low bass、非strong鼓力度；“不要很吵”限制非strong力度。这些是有说明的产品解释规则，不能等同情绪的普遍定义。LLM输出必须通过相同本地检查，冲突直接拒绝，不改写明确约束。否定的直接编曲短语若暂不支持会要求改为明确支持的选项，不把“不要分解和弦”当成“分解和弦”。

Mock仅识别约定词汇及三个演示描述，默认80 BPM、8小节、A小调、swing0.12、seed7；伤感/emo/凌晨等可将未指定速度选为76。艺人名称不会选择固定模板；LLM可参考名字提出参数，但它的原话分类仍由本地有限提取器生成。未知内容保留原文并标明限制。新增音乐结构不等于完整自然语言理解。

“比刚才”须传可信参考项目/版本；缺少参考明确披露限制，不假称有会话记忆。用户明确的参数优先于参考。时长描述和非4/4不静默转换；当前仍要求1–32小节，不声称实现150秒歌曲。

## 状态、复现和权限

schema2仅在原快照基础上新增 `generator_version`、`creative_brief`、`edit_history`、`explanation`，不更改Note结构。`spec.seed` 和完整标准化arrangement始终保存。初始规格表达生成参数，局部变化写入edit_history/audit；重现调用 `generator_v2.reproduce(version)`，按 `rules-v2.0` 生成后重放编辑。它验证生成器版本，不要求远端模型对同一句话重复输出一致。

schema1继续使用 `generator.generate(BeatSpec.parse(snapshot['spec']))` 和原渲染器；旧JSON不会因读取被补字段，SQLite结构及不可变触发器不变。旧版本原始hash和导出字节保持不变；新CLI可导出旧版编辑结果，但不会伪造其CreativeBrief。未来生成器修改需保留已发行版本复现路径。

LLM仅返回完整CreativeSpec或operation/value。它不能直接生成音符、执行代码、写数据库或修改调用方范围。规划读取指定版本和实际轨道/保护；提交再次校验head、范围、跨界音符及所有受保护轨。模型错误不产生版本，没有自动协议/模型/Mock回退。建议状态不是可提交计划。

`thin_notes` 的50是固定规则标识：每小节按起音/音高排序保留索引0/2/4等音符，因此奇数音符数不承诺恰好删一半；保留首音锚点，不改变剩余音符。只有锚点可留、没有可删除音符时失败，不新增版本。

## 文件与交付入口

- `creative.py`：CreativeBrief / CreativeSpec / 编辑扩展、约束和Mock/LLM参数规划。
- `generator_v2.py`：实际音符生成、范围减密、版本复现及本地说明。
- `creative_edit.py`：可信状态、范围解析、仅建议与可提交计划的分界。
- `service.py`：复用版本提交及保护链；`model.py` 只扩展轨道集合校验的可选参数。
- `planner.py` / `llm.py`：新增创作规划入口；DeepSeek继承既有响应接口，无新协议。
- `api.py` / `cli.py`：新创作及规划入口，已有入口保留。
- `creative_demo.py` / `tests/test_creative.py`：离线A/B/C产物与24项新增测试。

命令与API请求见 [README](../README.md)；实际验证状态见 [ACCEPTANCE](ACCEPTANCE.md)。演示C保留继承自A的创作brief，编辑原话、单独CreativeBrief及解释在 `input.txt` / `edit_brief.json` / `edit_intent.json`，差异及保护hash在 `diff.json`。这是A的真实子版本，没有覆写A。
