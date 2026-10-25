# P3-C 自然失败采集：评价修复与实现报告

日期：2026-10-25。基线：`5aed1fb3090adc3ff5f33729272468006f67faca`；分支：`codex/p0-p2-recovery`。

**结论：软件实现和真实 AppWorld 状态探针 PASS；P3-C 真实 Student 采集 BLOCKED，尚未通过阶段验收。** 远程 SSH 建立 TCP 连接后未返回 SSH banner，SSH 客户端退出 255；未获得远程 shell，因此不能判断远程 GPU 的实际状态。本机实测 RTX 5050 8151 MiB，不满足正式远程配置的 90000 MiB 容量门槛。没有自动开机、租机、调用 Teacher 或重新训练。

## 1. 本轮真实结果

| 项目 | 结果 |
| --- | --- |
| 预先固定任务 | 12 个官方 train 任务，11 个 scenario 家族，seed=20261025 |
| 数据暴露分层 | 8 个 Base-SFT 未见任务，4 个已见任务；全部属于固定 train 家族 |
| 真实 Student 执行任务 / 有效自然失败 | **0 / 0**；没有可报告的成功率或失败率 |
| Student 失败检查点 / 恢复验证 | **0 / 未执行** |
| 最终真实 AppWorld 脚本探针 | 8 次公开 API 调用，7 个返回；6 个完整边界快照 |
| 最终独立进程恢复 | 同一实际写入后的活动任务快照恢复 2 次，全部一致 |
| 原正式数据 | 95 轨迹 / 75 任务 / 29 家族；18 个来源包、动态 Schema 与原 SHA 复验通过 |
| 已训练 checkpoint | 7 个发布文件 SHA 通过；本轮未重新加载 GPU 权重 |
| 软件检查 | 完整 238 tests PASS；Ruff check PASS，9 个改动 Python 文件 format check PASS |
| 本轮真实模型请求 / 训练步数 / 模型计费 | **0 / 0 / 0** |

脚本探针使用明确标记的 scripted generation 和字符编码器测试执行状态机，**不是 Qwen3-4B 实际输出，不是自然失败，不是 Teacher 示范**。此前同轮探针 `probe_b` 产生 11 个完整快照、2 次独立恢复；最终实现复用相邻 post/pre 同一合法边界，`probe_c` 减为 6 个快照。两个探针均保留，合计 16 次真实 API 调用、4 次独立恢复，不重复计作 Student 样本。第一次受限环境初态探针卡住后中断并保留目录；后续真实探针均使用硬超时，不修改官方实现。

## 2. 评价与 Harness 修复

### 官方分数与评价可靠性分开

`evaluation.py` 新增 `official_result_integrity_20261025`。原始 `tracker.to_dict()` 保存在 `official`，`official_success` 单独记录；不改变官方断言、异常处理或奖励。核对 success、测试计数、passes/failures 和观察到的异常数量是否一致。

- 普通 AssertionError：完整 tracker 下为官方任务失败。
- **直接发生在当前任务 `ground_truth/evaluation.py` 的** KeyError / IndexError / UnboundLocalError：完整 tracker 下保留官方任务失败，不再一律剔除分母。这是官方评分分类，不声称定位了每个错误业务动作。
- OSError、MemoryError、导入错误，以及 SQLAlchemy/SQLite/SQLModel 异常链：环境故障；控制侧 success/reward 为 null，即使官方 tracker 恰好记录了 false，也不作为自然失败。
- 缺失或不一致的 tracker、其他来源/其他类型的异常、顶层未知崩溃：unknown，保留原始可获得结果，不作学习标签。

真实 `e3d6c94_3` 初始状态探针获得完整官方 false，并观察到 IndexError / UnboundLocalError；新分类为 `task_failure`。这是脚本初态检查，没有重新执行历史 Student，也没有覆盖 P3-B 原来的 null 标记。不能据此宣称所有非断言异常都已穷尽；来自库函数的状态异常仍保守为 unknown，后续按原始日志诊断。

### Schema 确定的具体反馈

新可选 Harness revision：`schema_feedback_20261025`。Teacher 与 Student 共用 `check_batch` / `ProtocolHarness.rejection_event`：指出 `missing_fields`、`unknown_fields` / `allowed_fields`，或错误路径与 `expected_type`。不回显参数值，不生成替代参数，不自动选择工具，不改变原始 provider response。整批先校验，拒绝后零执行；仍只有两次纠正机会。旧 `review_20261023` 默认行为及反馈保持兼容，新 P3-C 明确启用新 revision。

既有 execution_v5、AnswerGuard 数值规则、A/B 工具获取规则和 Assistant-only mask 不改。新测试覆盖相同错误的 Teacher 共用路径 / Student 反馈一致性；本轮未新增付费 Teacher 验证。

## 3. P3-C 实现与状态边界

入口：`python -m recoveragent.p3c collect`，配置 `configs/p3c_collection_20261025.json`。使用正式 `Qwen/Qwen3-4B` Base-SFT adapter、non-thinking、execution_v5、dynamic_discovery；32768 总上下文、4096 单回复、40 请求、16384 生成 token、600 秒/任务，工具次数不设配额。12 任务最多 480 请求 / 196608 生成 token；环境与恢复检查另有独立超时。首个基础设施/评价未知故障停止后续任务，不隐式重试；未知模型用量保留预留。

`state.py` 保持默认 format v1 的单调用规则，新增显式带 `agent_runtime` 的 format v2，使用现有完整批次历史验证。新快照包含 DB 导出、合法 messages、当前实际可见 Schema、Python RNG/环境计数/冻结任务时钟、模型和模板身份、预算/seed/纠错状态、任务与基础数据版本，以及 SHA 清单。真实请求、原始响应 token IDs、逐工具调用意图/返回、终态与评价仍分别保留。

每条轨迹从原始 train 初态开始。保存初始边界和每个完整回合后边界；相邻下一轮 pre-action 引用上一轮同一不可变边界，不复制数据库。只有公开工具返回的实际错误才优先标记 pre-error / post-error；Harness 格式重发保留审计快照，不冒充业务恢复。最后活动的完整边界作为 pre-termination/late-failure 候选。仅隐藏最终判失败、没有公开错误信号时，不猜测“第一个错误动作”。Pre-error 只供预防消融，不能声称修复已发生错误。

批次中途失败：真实已执行前缀、调用中状态、真实 DB 副作用都保存于 interruption；不补造缺失工具结果，不把部分批次包装成合法检查点。本轮探针在第二次写入完成后人为丢失返回，验证两次写入均保留，但整条事件分类为基础设施故障，不能进自然失败索引。

恢复检查在独立进程创建同任务原始 world，再导入快照；检查实际逻辑数据库、环境 runtime、历史、公开 Schema、活动状态和源不可变性。Agent 控制 runtime 做独立 SHA 完整性核对；**尚未执行 P4 Teacher 接管或从快照继续模型生成**，没有声称 GPU RNG/模型 KV cache 已恢复。Student 每请求 seed 固定记录，后续继续生成必须明确预算/策略，而非隐式重置纠错状态。

## 4. 任务清单和数据保护

官方 train 只有 90 个任务。排除已有 75 个示范任务、此前固定评测任务和全部内部 validation 家族，剩余 **8 个**可用未见任务。固定方案先纳入全部 8 个，再按场景覆盖补 4 个 train 已见任务。清单中的 `exposure` 明确分层；不得将这 4 个任务的结果声称为未见任务泛化。该选择使用公开 ID、Base-SFT 成员与固定家族划分，不查询隐藏答案、难度或当前 Student 成败。

清单：`configs/p3c_train_plan_20261025.json`。保持官方 dev 和内部 validation 家族隔离，不回灌训练。旧 95 条、原奖励、所有历史 manifest 和源码锁均不修改。

数据 manifest：`5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`。

模型 revision：`1cfa9a7208912126459214e8b04321603b3df60c`；adapter tensor SHA：`aefac6f068361cb5c0fc76c6a26f8b45231953b37d141f1ec9352a29917cf9c0`。来源为 `reports/p3b_checkpoint_release_20261023.json`，非 Raw Base 或重新训练权重。

新源码锁：`configs/p3c_runtime_lock.json`。共享 evaluation/harness/state/student 文件已产生新修订，因此旧运行锁在当前 checkout 不应通过；复现旧 P0–P2 / P3-A / P3-B 必须使用其原 commit 或封存源码，不能重算旧锁冒充一致。

## 5. 命令与证据

实际执行：

```bash
# 在完整独立 Git 克隆中，返回 Already up to date
 git pull --ff-only origin codex/p0-p2-recovery

PYTHONPATH=src:. .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts

PYTHONPATH=src timeout -k 5 300 .venv-harness/bin/python scripts/probe_p3c.py \
  --namespace p3c_probe_20261025_c --output runs/p3c_20261025/probe_c

# 本机只到预检即阻塞，未发模型请求
PYTHONPATH=src timeout -k 5 60 .venv-harness/bin/python -m recoveragent.p3c collect \
  --config configs/p3c_collection_20261025.json \
  --output runs/p3c_20261025/collection_preflight
```

另外实际调用 `formal_dataset.verify(..., official_root='data/appworld')`、`p3c.verify_checkpoint(...)` 及 `validate_config(...)`。公开结果：`reports/p3c_{dataset_audit,checkpoint_audit,validation}_20261025.json`、`reports/p3c_checks_20261025.txt`。私有完整证据：`runs/p3c_20261025/` 和 `.cache/p3c_*`；原始任务、DB、密码、模型权重和官方隐藏测试内容不上传 GitHub。

远程恢复可用后（以下**尚未执行**）：同步新源码及私有原 adapter/数据，核对新锁和 trusted release；复用已验证的 AppWorld 与推理环境。在 `.venv-sft` 中以现有 `recoveragent.student_backend --adapter <原 formal adapter>` 启动 loopback 后端；复制本轮配置到私有路径，仅调整本机/远程目录和连接地址。工作进程需固定 tokenizer 可用且安装兼容 AppWorld；先完成真实 GPU/权重/版本预检，再使用冻结清单串行 `p3c collect`。读取 `summary.json` 的 status，不能只依据命令退出码判断阶段通过。未授权启动新付费实例。

## 6. 验收与进入 P4-A 的条件

| 验收项 | 当前状态 |
| --- | --- |
| 评价分类、具体参数反馈、有限纠错 | PASS：软件测试与真实初态官方评价探针 |
| DB 写入、完整边界快照、独立进程恢复、部分副作用保存 | PASS：真实 AppWorld 脚本验证 |
| 已有数据 / checkpoint 来源完整性 | PASS：原 SHA，不涉及新 GPU 权重加载 |
| 已训练 Student 自主执行固定 10–20 任务 | **BLOCKED：远程 SSH 不可用；本机资源门槛不符** |
| 有效自然失败、多检查点及模型继续执行 | **未获得新模型证据，不能判 P3-C PASS** |
| Teacher 恢复 / Recovery-SFT / GRPO | 未启动，属于后续阶段 |

进入 P4-A 前须：用户恢复已有远程实例可访问性或提供可用连接；核验真实 Base-SFT 后端；按固定 12 任务收集，并分别报告未见/已见结果；至少获得一条排除基础设施与未知评价的自然失败，保留 post-error 或 late-failure 活动完整边界，并对这些**模型产生的**检查点通过独立恢复。然后另行实施 Teacher 接管，继承真实前缀副作用和合法历史，不能以本轮脚本探针或 Harness 重发替代恢复学习。
