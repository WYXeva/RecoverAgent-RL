# RecoverAgent-RL

Self-Improving Tool Agents through Failure Recovery

在 AppWorld 中研究工具调用 Agent 如何从自身自然失败恢复，并利用恢复结果更新策略。需求基线为 [v0.7 计划](PROJECT_PLAN.md)；工作规则见 [AGENTS.md](AGENTS.md)。

**P3-A 本机小样本闭环 PASS**：最新规范提示回归中，A/B 在两个已知 train 任务各成功2/2，4条新Teacher轨迹通过官方验证和数据筛选；80项软件测试通过，4B tokenizer/mask准备得到31个Assistant目标。不同轮次配置和历史失败分别保留，不能混成固定协议成功率。**未启动 SFT/GRPO，远程尚未连接，没有总体效果提升结论。** 见 [最新报告](reports/structured_prompts_report.md)、[初始P3-A报告](reports/p3a_report.md)、[STATUS](docs/STATUS.md) 和 [DECISIONS](docs/DECISIONS.md)。

2026-10-19 真实验收：**P0 / P1 / P2 基础设施 PASS**，取得 1 条 train 自然失败和 4 条独立恢复，官方恢复奖励为 `[0, 0, 0, 0]`；26 项软件测试通过。实际命令、成本、隔离证据与限制见 [阶段报告](reports/p0_p2_report.md)。

v0.7 延续路线：AppWorld → Teacher 正常成功轨迹 → Qwen3-4B Base-SFT → Student 新自然失败 → Teacher 多检查点真实恢复 → Recovery-SFT → Student 自主恢复 GRPO → 多轮 Teacher 辅助增量学习。两次 SFT 均不可跳过；Multi-Checkpoint Recovery 和自适应检查点评分仍待验证，ReTerm-style 恢复训练为参考基线。

工具模式新增 [官方 API Predictor 与动态发现的配对比较](docs/TOOL_MODES.md)，当前单回复输出上限 4096 token。[真实小试报告](reports/tool_modes_report.md)区分 Teacher 结果、完整成本和 Student 尚未验证项，不启动训练。

v0.6 按用户要求取消整轨迹/单回复工具调用数量配额，保留其他资源预算；[新诊断报告](reports/unlimited_prompt_report.md)记录原 20 调用批次的真实重放及官方/依赖增强提示对照，旧结果保留。

v0.7 新增 [A/B 提示规范](docs/PROMPTS.md)：A 补全前置依赖并加入两个公开 API 自制选工具正例，A/B 共用六阶段执行规则，B 明确按需发现；官方原提示与历史增强提示保留。自制正例不是 Teacher 成功数据，完整预测成本计入比较。

现在可以离线查看每轮 LLM 实际收到的 messages/tools、原始响应、截断和工具反馈，详见 [LLM 请求视图](docs/LLM_VIEW.md)。无需模型调用或 GPU。

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

**以下仅是历史 P0–P2 基础设施复现命令**：本机 RTX 5050 8GB 曾使用 Qwen3-1.7B，实际版本固定于 `configs/p0_p2.json`。它不再是正式训练 Student。**正式主模型锁定 Qwen/Qwen3-4B**，本机先收集 Teacher 正常成功轨迹，计划在远程 RTX PRO 6000 96GB 上完成 Base-SFT、后续 4B Student 与 GRPO。远程硬件、依赖和训练尚待真实核验：

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

凭据、原始基准内容、解包代码、轨迹、快照和权重不上传 GitHub；仅提交代码、配置和可公开的证据摘要。**后续先扩大 P3-A Teacher 正常成功数据的场景覆盖**，导出带 SHA-256 的结构化消息/manifest（不得用旧 1.7B token ID 代替 4B SFT 标签）；之后在远程 96GB 上准备 Qwen3-4B 基础 SFT，远程 AppWorld 数据/源码版本必须匹配。P0–P2 仍是历史基础设施证据，未证明 4B 提升。
