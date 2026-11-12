# 参与贡献

感谢你的帮助！vidroute 刻意保持小巧：一个确定性的路由内核，外面套几层薄壳。较大的改动之前请先读
[docs/DECISIONS.md](docs/DECISIONS.md)——大多数"为什么不用 X？"的问题在那里都有附证据的回答。

## 基本原则

- **内核 + 外壳。** 逻辑放在 `src/vidroute/core` 和 `src/vidroute/api.py`；CLI、MCP 服务和技能启动器只负责
  转换参数和统一外壳（envelope）。
- **编排器没有运行时依赖**（`dependencies = []`）。重型库放进 worker 环境（`envs/*.in` + 带哈希锁定的
  `envs/*.lock.txt`），通过 JSON lines 通信。
- **纯本地，先征得同意。** 不加遥测，不调云端，没有确认过的计划就不下载，不在 `VIDROUTE_HOME` 和 `--out`
  之外写文件，永远不用 `shell=True`。
- **Windows 是一等公民。** 使用 `pathlib`，子进程用参数列表，处处 UTF-8；CI 必须在 `windows-latest` 上通过。
- **凭证据，不凭记忆。** 模型限额、宿主行为和模型许可都需要官方来源和核实日期
  （见 [docs/PROFILE_CONTRIBUTING.md](docs/PROFILE_CONTRIBUTING.md)）。

## 开发环境

```bash
git clone https://github.com/wblearner-mini/vidroute && cd vidroute
python -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-dev.lock.txt   # Windows：.venv\Scripts\pip
.venv/bin/pip install --no-deps -e .
.venv/bin/pytest
```

默认测试离线运行：使用 ffmpeg（找不到时跳过相关测试）和假引擎（`VIDROUTE_FAKE_ENGINES=1`）。装好模型后
（`vidroute install ... --yes`），可以运行 `VIDROUTE_REAL_MODELS=1 pytest tests/test_real_models.py`。

依赖用哈希锁定：

```bash
uv pip compile --universal --generate-hashes --python-version 3.10 requirements-dev.in -o requirements-dev.lock.txt
uv pip compile --universal --generate-hashes --python-version 3.12 envs/ocr.in -o envs/ocr.lock.txt
```

修改锁文件后，运行 `python tools/license_report.py --check <锁文件>` 并更新 `THIRD_PARTY_LICENSES.md`。

## Pull Request

- 每个 PR 只做一件事，并附测试。路由改动需要在 `tests/test_routing.py` 里加一条表驱动用例。
- 先在本地跑 `pytest`；CI 会在 Windows 和 Ubuntu、Python 3.10 与 3.13 上运行。
- 更新 `CHANGELOG.md`（"未发布"一节）；行为变化还要更新 `docs/DECISIONS.md`。
- 提交信息采用 conventional 风格（`feat(prepare): ...`、`fix(probe): ...`）。
- 提交贡献即表示你同意以 MIT 许可证授权你的贡献。

## 报告问题

- Bug 和档案更正：使用 GitHub issue（已提供模板）。请附上 `vidroute doctor --json` 的输出，
  不要附视频本身或任何隐私内容。
- 安全问题：见 [SECURITY.md](SECURITY.md)——请不要公开提 issue。
