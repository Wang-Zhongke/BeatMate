# 测试与验证

[产品首页](../README.md) · [贡献指南](../CONTRIBUTING.md)

## 自动测试

| 层次 | 覆盖范围 | 执行方式 |
| --- | --- | --- |
| Python（161 项） | 生成状态机、请求去重、预算、文件校验、分轨/MIDI、删除恢复、导出、分页及原生 MIDI 兼容性 | `python -m unittest discover -s tests -v` |
| JavaScript（7 项） | 搜索竞态、重复请求合并、循环范围、音轨末尾重播、备注保存去重 | `node --test tests/ui/*.mjs` |
| Chromium（6 项） | 全库搜索与跨页播放、预览与重复确认、作品/失败记录恢复、A/B/循环/备注/ZIP、桌面及手机导出布局 | 在 `tests/browser` 中运行 `npm ci --ignore-scripts`、`npx playwright install chromium`、`npm test` |

测试使用临时目录、Mock 或注入响应，不需要真实 API Key，也不提交付费生成。浏览器套件在独立的 8773 端口启动服务，不复用个人曲库。

## 已验证环境

[首次远端运行](https://github.com/Wang-Zhongke/BeatMate/actions/runs/35768225453) 对提交 `61d2a6b` 全部通过：Python 3.10 / 3.12 × Ubuntu / macOS、Node.js 22 前端测试，以及 Ubuntu Chromium 浏览器测试。后续提交的结果见 [GitHub Actions](https://github.com/Wang-Zhongke/BeatMate/actions)。

本地还验证了 390px 手机视口无横向溢出、导出按钮与说明不重叠、试听备注刷新后保留，以及截图中使用的实际操作流程。Windows 原生运行不受支持。

## 真实作品与验证边界

[半杯常温与 Jazz Beat](../examples/showcase/README.md) 是已有真实 Mureka 生成结果，提供制作描述、完整时长的试听副本和来源清单；半杯常温另含分离伴奏与人声。案例文件与原始 WAV 的哈希、时长已核对。

这些案例证明有实际输出，不代表所有模型、账户、网络或音乐要求都已覆盖。当前没有正式人工音质评分，BPM/调性也未做自动检测。歌词歌曲的串音、纯伴奏中的人声残留、MIDI 准确度和 DAW 导入体验仍应逐首检查。

## 手工验收建议

1. 在独立库完成预览、取消、确认和刷新，检查相同请求不会重复提交。
2. 切换音轨、分页和筛选，检查播放器、循环与备注。
3. 删除并恢复作品或失败记录，检查原任务不被重发。
4. 检查打包文件、缺失清单及文件哈希；窄屏与键盘均可操作。
5. 真实付费检查单独执行，方法见 [Audio API](AUDIO_API.md)。不要将自动测试结果当作音乐质量承诺。
