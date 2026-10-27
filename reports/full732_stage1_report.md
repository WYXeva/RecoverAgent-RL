# Full732 Stage 1：协议入口与边界验收

**Stage 1 工程验收 PASS；Full732 实际训练/模型评估仍 BLOCKED。** 本轮只完成元数据、准入检查、软件测试和同一 train 任务的脚本探针。没有执行或实现 Stage 2–6。

## 版本与冻结来源

- 工作分支 `codex/p0-p2-recovery`；起始提交 `71fd3fed3ea0f781ba5a1ac5a9479ff5fbe55b1b`，已确认包含 `8e4b9ddaa875a68abbc6ed7228e2ff10528ae71b`。首次 Git 同步遇到 TLS 握手失败，重试 fetch/pull 成功，工作区无未提交修改。
- 原 Full732 清单：[unified_task_split_full_pool_candidate.json](../configs/unified_task_split_full_pool_candidate.json)。文件 SHA `0466cec29d9ad770a1cfeacb4c93945e5fcbcad68b9e980c5264d447418121a2`；canonical manifest SHA `086c0e2a1ac1143967d7ea8af06e0cc434aecdb39a51e1fe6c5fd42585ae2cab`。
- 复用 seed `20261028` 的既有分配，**没有重新抽样、重算任务内容或修改分配**。732 个不同 Task、244 个 Scenario，每场景 A/B/C 各一个；每侧 244，GRPO 与 Recovery-SFT 共用 B，只有一个 C。
- [新配置](../configs/full732_stage1_protocol.json) SHA `a1e4491783386ede1b9d0f0d0524de9dca0d49854c60f5e67ca2ba45c1eb3730`；[入口源码](../src/recoveragent/full732_stage1.py) SHA `9fea271840c0c093150caab0f1c38da1d32b3b6c8b8e4391a9460541459030be`。
- `execution_v6.py` 文件 SHA 仍为 `352d106b7b052cc1a2b5c249e19c6a512e989355fc60f6495034f32db24a4400`，System Prompt 文本 SHA 仍为 `5ef2bb774a258ba0a0e00d94284a29720bad3d2578c9a2a54ec94a594272dbba`。没有追加门禁文档或旧提示。

## 已实现的最小入口

仅增加一个模块 `full732_stage1.py`，包含 `inventory` 和 `probe` 两个命令；没有新增 Runner、训练器或权限框架。

1. `load_contract` 校验经审查配置的固定 SHA，再校验原清单、审计、原执行配置及版本锁。复用 `unified_split.validate_split` 验证原分配，不调用 `make_split`。
2. `select_tasks` 按用途选择 A/B/C 元数据。B 对 Recovery-SFT 和 GRPO 返回同一集合；错用 C 训练、未知任务/用途及错误角色会拒绝。
3. `inventory` 生成 732 行可重建矩阵，区分 `metadata_eligible`、`usage_authorized`、`execution_ready`，另外标明阶段授权与模型 dispatch。原清单的 `permission_status` 不是凭据。
4. `probe_contract` 在每次实际调用前重新校验来源和用途；不接受调用者提供的权限布尔值或已生成矩阵。只有配置预先固定的 `07b42fd_1` 可进入脚本探针。配置中自增授权、替换哈希或手改 READY 无法放行；本版本也不支持导入自行声明的权限例外。
5. 实际探针复用 `scripts/audit_unified_pool.py worker`、`EvaluationAdapter` 和 `evaluate_terminal`，不复制工具执行或官方评价。只校验一个获准 train 任务的公开初始输入、公共基础 DB/Schema，然后在独立子进程运行一次只读 API 查询、保存终态、进行控制侧官方评价。

未来执行契约引用原有模型/revision、non-thinking 模板、execution_v6、dynamic_discovery、Memory 关闭及 40 请求/16384 累计输出/600 秒/32768 上下文/4096 单回复设置。探针保存该契约，但**没有发送 Actor 请求或使用模型预算**。脚本动作不是 Teacher/Student 策略，也不进入训练数据。

## 用途权限与就绪状态

用途依据为固定 AppWorld revision 的 [Agent Development Restrictions](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/README.md#no_entry_sign-agent-development-restrictions)。配置记录审查来源、revision 和文档摘要；只认可该已审查政策允许的用途。train 可用于教学/训练，dev 仅开发用途；test 只能做最终汇总测试，不能因为本地文件可访问，就用于训练或逐题开发验证。当前 C 是带逐题对照的共享开发验证，不能把它改称官方最终汇总测试来放行。

| 用途 | 原固定任务数 | 符合该用途 | 用途受限 | 当前可开始模型执行 |
| --- | ---: | ---: | ---: | ---: |
| A / Base-SFT | 244 | 30 train | 214 | 0 |
| B / Recovery-SFT + GRPO | 244 | 30 train | 214 | 0 |
| C / 共享逐题开发验证 | 244 | 30 train + 19 dev | 195 | 0 |

合计 109 题在所分配用途上有现有政策依据、623 题受限；这不是新增执行授权，也没有把 Full732 缩成 109 题重新划分。完整池、原始来源和每题阻塞原因均保留。

技术状态未放行有独立原因：没有新划分下已验收的阶段 checkpoint；本轮没有验证成功终态的完整评分能力；现有 Student 初始化与 v6 存在下述兼容性问题。旧 90 题探针的 84 个可用初态评价、6 个 unknown 只作为历史观察载入矩阵。**初态 unknown 不证明成功终态永远不能评分，也不会改变用途权限或被改写成官方失败。** 本轮未修复评价分类。

## 最小真实探针与失败记录

选择规则在执行前冻结：按 task_id 排序的首个官方 train 任务 `07b42fd_1`，其原角色为 B。探针没有执行恢复训练，仅验证工程链路。未读取其他 test 任务的题目、答案或逐题评价。

| 尝试 | 实际结果 |
| --- | --- |
| `train_probe` | 受限执行环境中初始化超过 120 秒；只形成 AppWorld 版本目录，没有工具返回/完整终态回执。保存 `TimeoutExpired`，不计模型失败，不推断业务副作用为零。 |
| `train_probe_diagnostic` | 同机非受限环境、相同唯一任务、独立输出目录；25.167 秒完成，1 次 `api_docs__show_api_descriptions(app_name="api_docs")`，DB 指纹前后一致。官方完整结果为 `valid / task_failure / success=false`。 |

两次初始化尝试、一个不同任务、一次已完成公开工具调用、一次完整官方评价。重跑前执行了一次不加载任务的内存 URI 依赖检查；通过。第二次仅加 30 秒 faulthandler 诊断，无需修改 AppWorld 或提高 120 秒上限，完成前未触发栈输出。第一次超时的具体根因未确定，不能据第二次成功声称已经修复环境。

真实探针只查询文档，没有完成用户业务任务；因此完整官方失败是预期可记录结果，**不是模型成功率，也不是成功终态评分验收**。本轮模型请求、付费 Teacher 请求、GPU 请求、训练步数均为 **0**。

私有证据：`runs/full732_stage1_20261027/` 中的 `inventory.json`、两次 `entry_contract.json`、`result.json`、源码快照、worker 日志及第二次 `official_evaluation.json`。失败与成功尝试分别保存源码 SHA；期间仅补充新探针对已分类评价环境故障不得 PASS 的检查，没有改官方分类器。证据与哈希清单为该目录的 `integrity.json`，原始评价内容不上传 GitHub。

| 私有证据相对路径 | 文件 SHA-256 |
| --- | --- |
| `inventory.json` | `e4618839dc6099e748f33625cad7a736c2416b307681bb654ad727b0586b62f8` |
| `train_probe/result.json` | `9b54bba58edcbc9fc741d5a3555a6d93830ed7cc441542c1d71d5b29581245b3` |
| `train_probe_diagnostic/result.json` | `897ff779be57433d4a807c23a408dd717afd36d2e8d6332c8594a7a213ac623d` |
| `software_checks.txt` | `dca860e1c627aa25a549b9d6e0cf82405a1f631c5e26cd54cd75002558cb2e9f` |
| `integrity.json` | `744e5eef7b0cdd0c3ab03c3da1b560c5273d9512a703d559656146ceac455cef` |

## 软件、兼容性与已知问题

- **427 pytest PASS**，其中本轮新增 32 项；Ruff check PASS，format 检查 128 文件通过。
- 测试覆盖完整 732 元数据/场景覆盖、A/B/C 互斥及 B 复用、清单篡改与自重算哈希、伪造授权/READY、原 test 两种 split 对所有用途的提前拒绝、dev 训练拒绝、只允许固定单任务、原文 Prompt 与干净初态契约、初态 unknown、worker 故障不重试、官方评价环境异常不算探针 PASS。
- 合成 worker 回执只验证接线、原始结果保留和异常传播，不代替上述真实 AppWorld 结果。修改已生成矩阵不能授权 dispatch；明确拒绝示例为 `024c982_3 / base_sft`，在数据读取/worker 启动前返回权限错误。
- 记录了一个**已确认且未绕过**的旧问题：`student.py` 的 `StudentSession.__init__` 要求 system 文本包含旧 `HARNESS_RULES` 和 `ANSWER_RULES`；v6 原文不包含这些段落。软件构造器测试已复现拒绝。此前 v6 测试验证的是文本渲染与合成 Teacher 请求，不是完整 Student 初始化。入口据此保持模型技术状态不可执行；本轮未改 Harness、Student 或 Prompt。
- 文档更新前，547 个已有 Git 文件逐字节不变；旧配置、报告、锁继续保留。95 条正式数据包的 6,616 个文件、原 manifest/归档及两版正式 checkpoint 共 14 个文件全部复核原 SHA，通过。之后仅更新 README、STATUS、DECISIONS 导航/状态。

实际执行命令（项目根目录，已有环境）：

```bash
git fetch origin codex/p0-p2-recovery
git pull --ff-only origin codex/p0-p2-recovery
git merge-base --is-ancestor 8e4b9ddaa875a68abbc6ed7228e2ff10528ae71b HEAD
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m recoveragent.full732_stage1 inventory --output runs/full732_stage1_20261027/inventory.json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src APPWORLD_CACHE="$PWD/.cache/appworld" IPYTHONDIR="$PWD/.cache/ipython" timeout 150 .venv-harness/bin/python -m recoveragent.full732_stage1 probe --output runs/full732_stage1_20261027/train_probe
# 诊断重跑：本机非受限环境；/tmp/recoveragent-stage1-diagnostic/sitecustomize.py
# 仅执行 faulthandler.dump_traceback_later(30, repeat=False)，无模型或业务逻辑。
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/recoveragent-stage1-diagnostic:src APPWORLD_CACHE="$PWD/.cache/appworld" IPYTHONDIR="$PWD/.cache/ipython" timeout 150 .venv-harness/bin/python -m recoveragent.full732_stage1 probe --output runs/full732_stage1_20261027/train_probe_diagnostic
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/ruff check --no-cache src tests scripts
.venv/bin/ruff format --check --no-cache src tests scripts
```

重复执行不得覆盖这些输出目录。后续若获准重做最小探针，应提供新的输出目录；本轮没有继续扩大。

## 交接与停止

Stage 1 的元数据、用途拒绝、独立入口、单任务真实链路和软件门槛已完成。Full732 受限使用、阶段 checkpoint 缺失与 Student 初始化兼容性仍是实际模型执行前置条件，没有因 Stage 1 PASS 自动解除。

**建议 Stage 2 只先解决一个问题：合法来源任务的终态评分资格缺少证据。** 最小工作是在获准 train/合成终态上区分真实任务失败、环境故障和未知评价，保留原 tracker，验证初态 unknown 与可评分终态的边界；不要先扩大任务、修改官方断言或把未知改成 0。用途受限部分继续拒绝，任何进一步权限依据必须另行审查。

提交本报告后停止，等待用户审查和下一阶段授权。
