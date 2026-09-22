# 来源、固定版本与许可证

第二次创作增量没有新增依赖或借用外部编曲代码。`generator_v2.py` 的音阶规则、短动机及其变化为本项目编写；没有按艺人名称索引歌曲模板，也没有导入录音、歌词或采样。既有固定依赖和许可证保持如下。

本项目的规则、工程状态、编辑校验、HTTP/CLI、测试和参考合成器为本次独立编写；没有复制其他 Beat 网站界面、重命名开源项目或移植开源鼓型代码。无第三方采样、预训练权重、SoundFont 或音乐素材。

| 项目 | 固定版本 | 来源 | 许可证 | 用法 |
|---|---|---|---|---|
| Mido | 1.3.3 | https://pypi.org/project/mido/1.3.3/ ; https://github.com/mido/mido/tree/1.3.3 | MIT | MIDI Type1 编解码；通过依赖调用，未复制源码 |
| packaging | 24.2 | https://pypi.org/project/packaging/24.2/ ; https://github.com/pypa/packaging/tree/24.2 | Apache-2.0 OR BSD-2-Clause | Mido 的传递依赖，显式锁定 |
| setuptools（可选安装构建） | 75.6.0 | https://pypi.org/project/setuptools/75.6.0/ | MIT | pyproject 构建依赖；直接 python -m 运行无需安装该版本 |

已从实际安装的发行包保留 Mido 与 packaging 原始许可证到 `docs/licenses/`。运行依赖在 requirements.txt 和 pyproject.toml 中精确固定。若将来借鉴新项目的代码/模板，必须逐项新增：源 URL、tag/commit、许可证、使用文件范围、修改说明；不得仅记录项目名。

接口参考（参考行为，不复制示例代码）：
- Mido MIDI 文件文档：https://mido.readthedocs.io/en/stable/files/midi.html 。代码依赖固定1.3.3；文档非随包复制，文档许可 CC-BY-4.0。
- OpenAI Structured Outputs：https://developers.openai.com/api/docs/guides/structured-outputs 。访问日期2026-09-16；远端接口不能随本地依赖锁定，模型由部署方显式配置，建议使用模型快照。
- ACE-Step 尚未集成、未复制其代码或模型；接入前另外核实具体代码与权重的版本、来源、许可，不能提前视为同一许可。


## DeepSeek Planner增量

没有引入新第三方代码、SDK或运行依赖。复用本项目已有适配代码，网络使用Python标准库。协议依据为DeepSeek官方Responses API文档（访问2026-09-16）：https://api-docs.deepseek.com/zh-cn/guides/responses_api/ 与 https://api-docs.deepseek.com/ 。没有复制供应商示例代码。

默认模型别名deepseek-flash及Base URL均可配置；模型是远端服务，不能把别名当成固定权重版本，也不声称获得可再分发模型许可。既有Mido/packaging固定版本与许可证不变。

DeepSeek思考模式控制接口补充参考（2026-09-16）：https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/ 。用于显式设置Responses的reasoning.effort=none；无新依赖或复制代码。


## 当前音频产品与界面素材

- 浏览器回归使用 `playwright` / `playwright-core` 1.62.1，Apache-2.0；仅在 `tests/browser` 和 CI 安装，未复制其实现。来源：https://www.npmjs.com/package/playwright/v/1.62.1 ，许可证已核对本地同版本发行包的 `LICENSE`。浏览器二进制由 Playwright 安装器另行下载，不随项目分发。macOS 可选传递依赖 `fsevents` 2.3.2 使用 MIT 许可证；全部测试依赖及完整性摘要固定在 `tests/browser/package-lock.json`。

- Mureka 通过远端 API 提供音乐生成与分离；仓库不包含其模型权重或 SDK。账户权限及产物使用条件由使用者的服务协议决定。
- `beatmate/ui/cover.png` 是使用内置 image_gen 生成的机器人编曲场景，作为产品入口背景。生成方式及提示词见 [封面来源记录](docs/assets/cover-provenance.md)。它不代表 Mureka 生成的音乐效果，也不是工作台截图。
- 项目代码使用 [MIT 许可证](LICENSE)；第三方依赖仍遵循各自许可。生成音频及 MIDI 的使用条件由相应服务条款决定，不因本项目的代码许可而改变。
