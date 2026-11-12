# 安全策略

## 支持的版本

安全修复会进入最新版本（以及 `main`）。vidroute 尚未到 1.0，报告前请先升级到最新版本。

## 报告漏洞

请通过仓库 *Security* 页上的 **"Report a vulnerability"** 按钮私下报告（私有安全公告）。
安全问题请不要公开提 issue。

请尽量提供：受影响的版本（`vidroute version --json`）、平台、复现步骤和影响。一周内你应该会收到确认。
公开披露前，请给我们留出合理的修复时间。

## 关注范围

我们尤其关心：

- 下载或执行了未在 `models/manifest.yaml` 或带哈希锁定的 `envs/*.lock.txt` 中固定的代码
  （域名白名单、SHA-256 校验、压缩包解压）；
- 在 `VIDROUTE_HOME` 和用户 `--out` 目录之外写入或删除文件；
- 没有 `--accept-raw-video-handoff` 时，原始视频或音频离开了本机；
- 从视频中提取的文本能让 Agent 采取行动的提示注入路径（指南把提取的文本当作数据；如果 vidroute 自己的输出破坏了这一点，请报告）；
- MCP 服务的 `read_outputs` 读到了输出目录之外的文件。

vidroute 从不发送遥测，也从不调用云端模型；如果发现与此相反的情况，就是安全问题。
