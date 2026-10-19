# RecoverAgent-RL

Self-Improving Tool Agents through Failure Recovery

在 AppWorld 中研究工具调用 Agent 如何从自身自然失败恢复，并利用恢复结果更新策略。需求基线为 [v0.2 计划](PROJECT_PLAN.md)；工作规则见 [AGENTS.md](AGENTS.md)。

本轮仅实现 P0–P2：公开工具调用、自然失败采集、状态/历史检查点、串行四进程恢复与官方评价。**没有启动训练，也没有效果提升结论。** 最新验收、错误与限制见 [STATUS](docs/STATUS.md)，技术选择见 [DECISIONS](docs/DECISIONS.md)。

2026-10-19 真实验收：**P0 / P1 / P2 基础设施 PASS**，取得 1 条 train 自然失败和 4 条独立恢复，官方恢复奖励为 `[0, 0, 0, 0]`；26 项软件测试通过。实际命令、成本、隔离证据与限制见 [阶段报告](reports/p0_p2_report.md)。

路线仍为 AppWorld → Student 自主执行 → 自然失败 → Teacher 真实恢复示范 → Student SFT → 自主恢复 GRPO → 多轮迭代。Teacher SFT 不可跳过；Decision-Point Recovery 与 Adaptive Failure Learning 是后续待验证假设，ReTerm-style 恢复训练为参考基线。

## 安装

已验证环境为 Python 3.14.4、AppWorld 固定 commit `42b5bcf3cd334fee33f0c37c02070a9f5807add5`。使用独立环境；完整实测版本见 `requirements/*.lock.txt`。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements/appworld.lock.txt
.venv/bin/python scripts/complete_appworld_install.py
export PYTHONPATH="$PWD/src"
export APPWORLD_CACHE="$PWD/.cache/appworld"
export IPYTHONDIR="$PWD/.cache/ipython"
```

安装脚本补全源码归档中的 Git LFS 对象，核验 SHA-256 后调用官方安装器；已有数据不会自动删除。Pydantic 固定到 2.12.3，原因见 D001。

## 验证与执行

软件测试与真实环境探针分开运行。探针通过公开 `supervisor.complete_task` 修改真实数据库，保存非初始状态，验证恢复与四个独立进程互不污染；它是人为编排的基础设施测试，不进入自然失败 Buffer。

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src scripts tests
.venv/bin/python scripts/state_probe.py --output runs/p0_state_new_id
```

本地 Student 使用 Qwen3-1.7B，以适应 8GB GPU；model/tokenizer revision 固定在 `configs/p0_p2.json`。GPU 推理服务与 AppWorld worker 分进程、分环境。下面是复现入口，逐项实测情况以阶段报告为准：

```bash
python3 -m venv .venv-inference
.venv-inference/bin/python -m pip install -r requirements/inference.lock.txt
export HF_HOME="$PWD/.cache/huggingface"
HF_HUB_DISABLE_XET=1 .venv-inference/bin/python scripts/download_student.py
HF_HUB_OFFLINE=1 .venv-inference/bin/python -m recoveragent.inference \
  --model Qwen/Qwen3-1.7B \
  --revision 70d244cc86ccca08cf5af4e1e306ecf908b1ad5e
```

在另一终端设置相同 `PYTHONPATH`、`APPWORLD_CACHE`、`IPYTHONDIR` 后运行：

```bash
.venv/bin/python -m recoveragent.cli run --config configs/p0_p2.json --output runs/p0_p2_new_id
.venv/bin/python scripts/summarize_run.py runs/p0_p2_new_id --output reports/p0_p2_new_id.json
```

每次使用新的输出身份。按官方顺序最多扫描 10 个 train 任务，首个有效自然失败出现后立即执行四条恢复；正常/恢复回合、调用、token、总时间和基础设施重试均有上限。`max_train_tasks` 最多 20，默认不扩大。所有 Actor 业务动作由 Student 决定。

## 证据与边界

- `runs/<id>/`：配置、任务选择、逐轮原始生成、可见历史、快照、控制侧官方评价、Failure Buffer 和恢复组。
- `reports/p0_state_evidence.json`：已完成的真实状态写入与四进程隔离摘要；不含原始任务内容。
- `.cache/`：安装、下载、排错日志与模型缓存；`data/appworld/`：本地数据及官方 world 输出。
- `tests/`：软件契约测试使用明确的 fake state machine；真实 AppWorld 证据另列。

执行 world 不加载 ground truth。官方 `evaluate_task` 在控制侧、终态保存后运行，以原始任务为参照。恢复 schema、历史、环境 RNG 和预算一致，仅改变模型采样种子；同奖励组可以通过基础设施验收。P0–P2 轨迹没有行为 log probabilities，明确禁止直接作为在线 GRPO 批次。

凭据、原始基准内容、解包代码、轨迹、快照和权重不上传 GitHub；仅提交代码、配置和可公开的证据摘要。下一阶段仍须先完成经验证的 Teacher 恢复数据与真实 Student SFT。
