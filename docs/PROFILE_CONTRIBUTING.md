# 贡献 LLM 与宿主档案

路由的好坏取决于 `routing/llm-profiles.yaml` 和 `routing/host-profiles.yaml` 中的档案。非常欢迎更正——请附证据。

## 规则

1. **必须有官方来源。** 每个改动的值都要附上服务商或宿主自己的文档、API 参考、模型卡或源码链接
   （源码要指明具体文件和版本）。博客文章、论坛回答和第三方汇总表单独都不够。
2. **必须有核实日期。** 把 `verified_at: YYYY-MM-DD` 设为你查看来源的那一天。
3. **无法核实？请标注。** 使用 `unverified: true` 并取保守值（更少的图片、更短的视频、更小的文件）。
   vidroute 会谨慎对待未核实的档案，并提醒用户。
4. **限额按宿主实际发送的单次请求计算。** 对于 base64/内联上传，填写文档中的请求体限额；vidroute 会把它换算成
   原始文件限额（`inline_is_base64`）。
5. **宿主：** 描述 Agent 实际能附带什么（`can_attach_image/video/audio`、`video_delivery`、`video_includes_audio`、
   `max_attachment_mb`），并设置 `confidence`：`verified`（你实测过）、`source-verified`（你读过代码）、
   `docs-only`、`unverified`。把你观察到的情况写进 `notes`。

## 流程

```bash
# 不改内置文件，先试一下你的改动
vidroute llm set my-model --modalities text,image,video --video-max-seconds 600 --video-max-mb 20
vidroute llm resolve --llm my-model --host gemini-cli --json
vidroute plan some-video.mp4 --llm my-model --host gemini-cli --json
```

然后编辑 YAML，运行 `pytest tests/test_data_files.py tests/test_routing.py`（schema 校验、与 PyYAML 解析结果一致、
路由表），再用 **profile correction** 模板提交 pull request。如果改动会改变路线，请补一条路由测试用例。

## 匹配方式

档案按规范化后的模型 id 做通配匹配（`claude-*`、`qwen3.8-flash*`），越具体越优先；`*` 是保守的兜底，必须放在最后。
别名（aliases）用来覆盖其他写法。
