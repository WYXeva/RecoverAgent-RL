# P0–P2 真实验收报告 · 2026-10-19

**P0 / P1 / P2 均为 PASS；仅表示本轮执行、恢复与评价基础设施通过。训练未开始，未证明恢复能力提升。**

需求基线 v0.2，AppWorld-only。真实模型实验 run ID：`p0_p2_20261019_a`，Student 为固定 Qwen3-1.7B BF16。所有代码/配置的采样前哈希见 `code_manifest.json`；采样期间未修改这些文件。

## 阶段证据

| 阶段 | 真实结果 | 证据 |
| --- | --- | --- |
| P0 | 官方 train 任务由真实 Student 执行并评价；独立真实写入、命名保存/恢复、四进程隔离和重复导入通过 | `environment_report.md`、`p0_state_evidence.json`、`p0_state_same_worker.json` |
| P1 | 扫描第 1 个 train 任务即得到自然预算耗尽失败；状态、历史、工具和来源匹配；Buffer 重读通过 | `p1_buffer_evidence.json`、`p1_failure_signals.json` |
| P2 | 4 个独立 PID，从同一状态/历史/schema/RNG 恢复，均真实生成后缀并由官方原任务验证；源快照不变 | `p0_p2_evidence.json`、`p2_protocol_checks.json` |

原始任务 ID 为 `82e2fac_1`。初始扫描上限 10 个，实际只执行 1 个；没有按隐藏结果筛测试任务，没有人工注入故障或在 Runner 中补做登录/业务步骤。

## 实际轨迹

| 类型 / seed | 生成回合 | 工具调用 | 输入 token | 生成 token | 秒 | 官方严格奖励 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| normal / 42 | 40 | 22 | 266862 | 1471 | 130.70 | 0 |
| recovery / 10042 | 15 | 7 | 162672 | 673 | 287.82 | 0 |
| recovery / 10142 | 15 | 7 | 162924 | 691 | 409.35 | 0 |
| recovery / 10242 | 15 | 7 | 164142 | 902 | 542.31 | 0 |
| recovery / 10342 | 15 | 7 | 162588 | 667 | 270.58 | 0 |

总计 **100 个模型回合、50 次实际工具调用、919,188 输入 token、4,404 生成 token**。模型实验总壁钟 1647.63 秒（27.46 分钟），含进程/环境开销；每轮输入包含重复的固定历史，因此不能只看输出 token。

五条轨迹均因既定回合预算耗尽而结束。模型实验基础设施错误/重试均为 0；未知生成量预留为 0，预算扣除量与实测生成量同为 4404。首轮上限 150000 生成 token / 90 分钟均未触达。安装、下载、调试和 P0 人为状态探针另外记录在环境报告及本地日志，未冒充模型 rollout。

## 恢复指标与解释

- 4 条恢复官方奖励：`[0, 0, 0, 0]`。
- Recovery Success@1：首分支 0/1；所有后缀平均成功率：0/4；四次至少一次成功：0/1 个起点。
- 起点、实际首轮输入/schema、RNG 与四个不同 PID/seed 均核对通过；官方 API 调用日志与 Runner 计数逐条相符。
- 奖励方差为零，当前采样未发现成功；不能据此证明状态不可恢复。
- 基础设施 PASS 与 GRPO 可学习信号分开：当前没有奖励差异，且 Teacher SFT 尚未执行，`grpo_ready=false`。不进行 SFT 或 GRPO，也不以本结果跳过 Teacher SFT。

## 限制

本例包含 18 次单调用格式协议错误和 2 次可见环境执行错误，重复调用 API 文档后耗尽预算；这些是可观察信号，不是已证明的根因。其失败快照的逻辑数据库记录与同任务初始状态相同（`p1_initial_state_comparison.json`）。因此本例尚未覆盖 Student 误写业务数据后的补救。真实写入后的保存/恢复由单独的 supervisor Task 表探针验证，不能混称为自然失败。

只有 1 个 train 起点和 4 个恢复采样种子，未评测 dev/test，未运行完整场景变体的 SGC，不报告总体性能或算法提升。只验收本机独立进程与共享基础数据根；跨主机迁移、并发 world 和更多业务写 API 尚未验收。GPU 上下文较长时延迟上升，设备总显存曾观察到 7855MiB / 8151MiB；未测 GPU 内核累计时间或完整峰值曲线。

P0–P2 保存了真实 token IDs、完整输入 token IDs 和模板哈希，没有行为 log probabilities，显式 `rl_eligible=false`，不可直接当作在线 GRPO 批次。安全项关闭并标记未测；没有伪报零风险。

## 实际运行命令

以下命令在项目根目录执行，所需环境变量与安装过程见 README / 环境报告：

```bash
APPWORLD_CACHE="$PWD/.cache/appworld" IPYTHONDIR="$PWD/.cache/ipython" PYTHONPATH=src timeout 600 .venv/bin/python scripts/state_probe.py --output runs/p0_state_20261019_d
HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 PYTHONPATH=src .venv-inference/bin/python -m recoveragent.inference --model Qwen/Qwen3-1.7B --revision 70d244cc86ccca08cf5af4e1e306ecf908b1ad5e
APPWORLD_CACHE="$PWD/.cache/appworld" IPYTHONDIR="$PWD/.cache/ipython" PYTHONPATH=src timeout 5400 .venv/bin/python -m recoveragent.cli run --config configs/p0_p2.json --output runs/p0_p2_20261019_a
PYTHONPATH=src .venv/bin/python scripts/summarize_run.py runs/p0_p2_20261019_a --output reports/p0_p2_evidence.json
.venv/bin/python -m pytest -q
.venv/bin/ruff check src scripts tests
```

原始执行日志：`.cache/state-probe-d.log`、`.cache/student-server.log`、`.cache/p0-p2-a.log`；各 worker 日志、逐轮记录、快照和评价在 `runs/p0_p2_20261019_a/`。服务在实验结束后主动停止（进程退出 130 为 Ctrl-C 正常停服，不是 rollout 失败），GPU 显存已释放。

软件测试 26 passed，静态检查通过；这两项不替代上述真实 AppWorld / Student 验收。付费 API 调用、租用算力和训练步数均为 0。

## 下一步

本轮停止在 P2。审查本分支后，如另行授权 P3，应先配置更强 Teacher，从真实 train 失败继续执行并筛选成功示范，再实现并验收 Student 恢复后缀 SFT；完成真实 SFT、重载与开发评价后才考虑 GRPO。
