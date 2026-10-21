# RecoverAgent-RL

Self-Improving Tool Agents through Failure Recovery

在 AppWorld 中研究工具调用 Agent 如何从自身自然失败恢复，并利用恢复结果更新策略。需求基线为 [v0.11 计划](PROJECT_PLAN.md)；工作规则见 [AGENTS.md](AGENTS.md)。

**正式Base-SFT v1数据交付PASS，数量目标PARTIAL**：95条合格完整Teacher轨迹，覆盖75任务/29场景家族；按scenario划分训练82条、内部验证13条，1072个Assistant样本/273003监督token。155测试、真实来源/动态Schema/mask/函数解析、SHA与迁移复验通过。约120条目标因DeepSeek余额不足（HTTP402）停止，全部118次新增尝试及未知费用保留；原数据私有存放，不上传GitHub。正式SFT/GRPO未启动。见[正式报告](reports/base_sft_dataset_v1_report.md)、[可信SHA](reports/base_sft_dataset_v1_release.json)、[远程交接与烟测说明](docs/REMOTE_BASE_SFT.md)。

历史**P3-A v5实现、采集与数据格式 PASS**：新增公开单值格式检查，最多一次模型同值纠正，Teacher/Student共用，拒绝回复仅作masked上下文。固定10个新任务/5家族，真实Teacher A成功7/10、B成功9/10，16条合格示范全部适配当前32k，143目标/44,126监督token。142测试、真实AppWorld探针与Qwen mask检查通过。该清单未触发金额纠正，**不能据此宣称纠正效果提升；未训练或运行真实4B权重。** 见[历史v5报告](reports/answer_guard_report.md)、[协议](docs/ANSWER_GUARD.md)、[STATUS](docs/STATUS.md)。

历史**P3-A v4采集与交接 PASS，训练准备 PARTIAL**：固定10个新train任务/5家族，真实Teacher A成功6/10、B成功8/10，获得14条官方成功示范（8唯一任务/4成功家族）。13条满足当前32k限制，148个Assistant目标、39,658监督token；1条过长示范整条暂缓。117软件测试及真实回执/动态schema/mask检查通过。**未启动SFT/GRPO，未运行真实4B Student或远程训练。** 见[历史v4报告](reports/v4_ab_report.md)、[v4协议](docs/EXECUTION_V4.md)、[STATUS](docs/STATUS.md)和[DECISIONS](docs/DECISIONS.md)。旧提示和全部历史保留，不宣称总体效果提升。

历史v0.8扩样：B在12任务/6家族中成功6/12，详见[历史扩样报告](reports/scenario_expansion_report.md)；更早A/B提示回归见[历史报告](reports/structured_prompts_report.md)。

2026-10-19 真实验收：**P0 / P1 / P2 基础设施 PASS**，取得 1 条 train 自然失败和 4 条独立恢复，官方恢复奖励为 `[0, 0, 0, 0]`；26 项软件测试通过。实际命令、成本、隔离证据与限制见 [阶段报告](reports/p0_p2_report.md)。

当前路线：AppWorld → Teacher 正常成功轨迹 → Qwen3-4B Base-SFT → Student 新自然失败 → Teacher 多检查点真实恢复 → Recovery-SFT → Student 自主恢复 GRPO → 多轮 Teacher 辅助增量学习。两次 SFT 均不可跳过；Multi-Checkpoint Recovery 和自适应检查点评分仍待验证，ReTerm-style 恢复训练为参考基线。

历史阶段新增 [冻结协议与场景采样](docs/TEACHER_EXPANSION.md)、[Student批量函数调用接口](docs/STUDENT_PROTOCOL.md)。当时execution_v3成功包合计10条/8唯一任务/5家族，尚未合并正式集；本轮统一来源重验、分组和权重见上方v1正式报告。

后续[失败分析与A复试](reports/a_failure_retry_report.md)：仅在上述B的6个失败上各试A一次，2条成功、4条仍失败；属于条件诊断，不是总体A/B比较。新增2条示范后execution_v3分包共12条/10任务/6家族，107测试通过。完成参数、精确正文、聚合和认证遗漏的[候选修改方案](docs/PROMPT_FIX_PROPOSAL.md)已记录；随后用户授权独立v4实现及新场景采集，原冻结提示未改，新结果不回写历史。

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
