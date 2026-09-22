# 参与开发

BeatMate 当前面向个人本地使用。先阅读 [README](README.md) 与 [改进路线](docs/ROADMAP.md)，围绕实际创作流程做小步改进。

## 本地开发

从仓库根目录运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
BEATMATE_AUDIO_PROVIDER=mock .venv/bin/python -m beatmate audio-serve --audio-dir output/dev-audio
```

打开 `http://127.0.0.1:8767`。使用独立演示库，不在个人音乐库上验证删除和恢复。前端为浏览器原生模块，无打包步骤；修改 Python 后重启服务。

## 提交前验证

```bash
.venv/bin/python -m unittest discover -s tests -v
```

前端改动额外运行以下检查，需要 Node.js 22+，不需要安装 npm 依赖：

```bash
node --test tests/ui/*.mjs
```

并检查修改过的模块，例如 `node --check beatmate/ui/studio.js`。GitHub Actions 对 Python 3.10 / 3.12、Ubuntu / macOS 配置离线回归，并检查前端模块。各次远程执行结果见仓库的 Actions 页面。

浏览器回归另需安装测试依赖与 Chromium（仅开发和 CI 使用，日常运行无需 Node）：

```bash
source .venv/bin/activate
cd tests/browser
npm ci --ignore-scripts
npx playwright install chromium
npm test
```

测试自动在 `127.0.0.1:8773` 启动临时 Mock 库，结束后清理；不会复用已有服务或读取 `.env`。端口已占用时先关闭占用它的测试服务，勿改用个人曲库。6 个场景覆盖全库搜索和跨页播放、预览及重复确认、删除恢复、失败记录恢复、分轨试听/循环/备注/导出，以及桌面和手机导出区不重叠。GitHub Actions 在 Ubuntu 安装 Chromium 及系统依赖，失败时保留 trace。

涉及视觉布局时仍需手工验证窄屏、键盘操作和播放体验；自动流程不能代替人工听感评价。各次实际执行情况见 [验收记录](docs/ACCEPTANCE.md)。

## 保持的约束

- 普通测试与 CI 使用 Mock 或注入响应，不读取真实 Key，不调用付费接口。
- 恢复操作仅查询已有任务或下载原文件。不要把结果不确定的付费提交自动重发。
- 原始请求和资产记录不可改写；名称、收藏、备注等可编辑状态单独保存。
- 下载、导出和恢复保留原文件校验；不得用转码文件冒充供应商原文件。
- 前端不持有 API Key。错误和测试报告不保存凭据或临时下载 URL。

## 问题反馈与 PR

描述触发步骤、期望结果、实际结果、系统/Python 版本和是否使用 Mock。网络问题提供设置中的失败步骤；不要附 `.env`、API Key、个人曲库或未经清理的日志。

PR 说明具体问题、改变后的行为以及实际完成的验证。没有验证的真实服务、听感或 DAW 表现应明确列出，不用离线测试结果代替。

## 源码格式

Python 使用 Ruff，前端使用 Prettier。两者都是开发依赖，不增加产品运行依赖。

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff format beatmate tests
npm ci --ignore-scripts --prefix tests/browser
npm run format --prefix tests/browser
```

提交前以 `ruff format --check beatmate tests` 和 `npm run format:check --prefix tests/browser` 检查。配置和版本已固定，避免手工压缩源码。

真实音频案例在 `examples/showcase/`，旧规则引擎示例在 `examples/legacy-midi/`。新增案例应核对音频来源、编码和使用授权，不提交整个本地曲库或原始任务数据库。
