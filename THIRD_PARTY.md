# 第三方依赖与素材来源

项目代码使用 [MIT 许可证](LICENSE)。第三方组件保留各自许可证；服务和生成内容的使用条件独立于项目代码许可。

## 依赖

| 组件 | 固定版本 | 用途 | 许可证与来源 |
| --- | --- | --- | --- |
| Mido | 1.3.3 | MIDI 编解码 | MIT；[PyPI](https://pypi.org/project/mido/1.3.3/) |
| packaging | 24.2 | Mido 的依赖 | Apache-2.0 OR BSD-2-Clause；[PyPI](https://pypi.org/project/packaging/24.2/) |
| setuptools | 75.6.0 | 可选包构建 | MIT；[PyPI](https://pypi.org/project/setuptools/75.6.0/) |
| Playwright / playwright-core | 1.62.1 | 浏览器回归，仅开发 | Apache-2.0；[npm](https://www.npmjs.com/package/playwright/v/1.62.1) |
| fsevents | 2.3.2 | Playwright 的 macOS 可选依赖 | MIT；[npm](https://www.npmjs.com/package/fsevents/v/2.3.2) |
| Prettier | 3.6.2 | 前端源码格式，仅开发 | MIT；[npm](https://www.npmjs.com/package/prettier/v/3.6.2) |
| Ruff | 0.13.2 | Python 格式，仅开发 | MIT；[PyPI](https://pypi.org/project/ruff/0.13.2/) |

运行依赖固定在 `requirements.txt`，开发依赖固定在 `requirements-dev.txt` 和 `tests/browser/package-lock.json`。Mido / packaging 的许可证副本在 [docs/licenses](docs/licenses)。浏览器二进制由 Playwright 安装器获取，不随仓库分发。

## 服务

- 当前音频产品调用 Mureka API，仓库不包含其模型权重或 SDK。模型、账户权限、计费与生成内容使用条件由供应商服务条款决定。
- 原生 MIDI 实验的可选 Planner 使用 [DeepSeek Responses API](https://api-docs.deepseek.com/zh-cn/guides/responses_api/) 或 [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)，由 Python 标准库发出请求。音频工作台不需要这两项配置。

## 素材

- 机器人封面背景由 image_gen 生成，见 [来源记录](docs/assets/cover-provenance.md)。截图来自实际页面，见 [截图说明](docs/assets/screenshots.md)。
- [真实作品案例](examples/showcase/README.md) 使用项目维护者指定的 Mureka 生成结果。发布文件为标明编码方式的试听副本，未包含账户凭据或供应商下载地址。提供试听不等于额外授予第三方商用或再分发权利。
- [旧版 MIDI 示例](examples/legacy-midi/README.md) 由规则引擎和参考合成器生成，不含外部采样、预训练权重或 SoundFont。
