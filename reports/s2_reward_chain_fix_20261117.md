# S2 提交与目标奖励链修复（2026-11-17）

## 已审查 f164fa7 后的本轮补修

本轮基线 `f164fa7`，分支 `codex/p0-p2-recovery`。只做本地代码、软件验证和 fresh16 准备；远端暂停指令继续有效。下方保留上一轮的实现与证据说明，**新运行使用本节的新配置和命令**。

### 实际修复

- **缺失与 null**：数据库模型字段通过固定 AppWorld revision 的 JSON Schema 核对，覆盖 `rows.where`、`get`、`values`、`filter` 和绑定表达式，先验证再读行，空表也能识别无效列。非法字段/状态路径抛 `ScoringGap`；真实 null 正常比较。缺少业务对象、Student 漏报字段、空的业务结果不会等同于 null，也不会仅因缺失就标成评分故障；按业务条件返回有效失败。模型元数据随私有目标证据保存。
- **语义响应**：在访问 `.get()` 前检查响应对象、`conditions` 数组、每个条件对象和 ID，以及布尔结论、理由和引用。`[null]`、`["body"]`、`{"body":true}` 等错误结构统一成为 `ScoringGap`，经实际 BackendRuntime 奖励收尾保存未知/无效奖励，其他组可继续。客观失败仍不能被语义 true 覆盖。
- **数值集合**：数字递归按数值等价比较，包括嵌套数组/对象和 `unique`、集合比较；`27.0` 等于 `27`，但字符串 `"27"`、布尔值 `true` 与数字不同。`equal` 保留顺序；`set_equal` 保留原有重复元素数量要求，未扩大业务容忍度。
- **一个候选一次 repair**：结构检查、真实 Solver 的评分缺口、覆盖审核 `contract_complete=false` 和程序验收缺口共用同一个名额。调用前写 `contract_repair_claim.json`，保存首个缺口、原提案/元数据 SHA、次数与结果；未知响应和续跑不会再获名额。仅替换 `success_conditions`，不修改原公开业务目标、解法或要求；修复后重新走准入。真实 Solver 业务失败不触发 repair，仍不能评分则 `deferred_scoring`。
- **元数据与证据复用**：抽象和 repair 获得相关应用的精简表/字段/类型信息，不含数据库行；这些信息不进入 Student/Solver 模型可见消息。若公开指令和输出契约相同，且旧 Solver 的初态、终态、完整动作和历史对应，可在独立目录重新评分并准入；仅评分器故障的原记录可保留原 invalid 状态并另记修复验收。公开输出改变或证据不足时保持待验证，不能靠修改旧轨迹通过。修复后的合格任务仍发布到现有入池位置；自动复核读取对应的新验收目录。
- **协议绑定**：run、Experiment state、evaluation binding、派生 reward receipt 和 fresh16 冻结文件绑定执行/完成提交/奖励协议。切换到新协议后不能复用旧 actor、步数或评价结果；无绑定的历史评价也不会自动补签。同协议接续及已消费槽位不重采规则保留。

独立配置为 `configs/v2_s2_goal_repair_20261117.json`，奖励身份 `coevolve_goal_scoring_v2`；公开完成协议仍为 `completion_submission_v2`，目标表达式格式仍为 `coevolve_public_goal_v1`。新源码锁覆盖 **216 文件**；已审查的旧配置、旧锁、原模型和历史结果均未覆盖。

### 验证与边界

首次运行要求的三个测试文件：**39 passed**。本轮最终回归结果在下方列出；测试使用合成执行器/状态/模型响应，只有固定 AppWorld 模型声明是本地只读真实源码。没有新真实 AppWorld episode，没有新语义模型调用，没有远端服务或正式模型训练。

```bash
.venv-v2/bin/python -m pytest -q tests/test_completion_submission_v2.py \
  tests/test_public_goal_reward.py tests/test_s2_goal_integration.py \
  tests/test_s2_contract_repair.py tests/test_s2_experiment.py \
  tests/test_s2_evolution.py tests/test_s2_parallel.py tests/test_s2_generation_fix.py \
  tests/test_s2_review.py tests/test_coevolve.py tests/test_coevolve_quality.py
.venv-v2/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

最终结果：相关回归 **144 passed**；完整 pytest **1281 passed**（13项既有依赖警告）；Ruff check 通过，format check **275文件**通过，`git diff --check` 通过。完整套件含既有 CPU 微型随机模型参数更新测试，不是9B模型训练。首轮完整回归发现新元数据测试触发 AppWorld 的 dotenv 导入副作用，已在合成/元数据测试中隔离；最终完整复跑通过，生产凭据逻辑未改。

48条备份只读复核仍是 **245/245 完成响应 Schema 合法、7条确认误罚通过目标判定、5条真实负例失败**。文字判断仍复用上次绑定证据的人工结论，没有冒充新的语义调用。原件/旧奖励未修改；本轮私有日志及派生审计位于 `runs/s2_reward_fix_20261117/repair_validation/`。

### fresh16 就绪入口

新配方：`configs/s2_reward_fresh16_repair_20261117.json`。继续使用原 `fresh16_plan_v3.json`，任务定义 SHA 与上一轮一致；固定已验收 S0，单值数量、购物车 JSON、商品提问、报价邮件各四次。新目录 **`runs/s2_reward_fix_20261117/fresh16_REPAIR_FINAL/`** 已冻结，并完成三条生成诊断定义的实际模型字段核对；没有运行任何槽位。旧 READY 目录保留。

本地准备（不启动模型/环境）：

```bash
PYTHONPATH=src:. .venv-v2/bin/python scripts/probe_s2_reward_chain.py prepare \
  --plan runs/s2_reward_fix_20261117/fresh16_plan_v3.json \
  --output runs/s2_reward_fix_20261117/fresh16_REPAIR_FINAL
```

待用户安排资源后，将新源码、私有计划和上述冻结目录部署，按 `configs/v2_s2_deployment.example.json` 核对 S0、固定基座缓存、CPU/AppWorld 和 vLLM 路径，设置新的授权绝对 `deadline_epoch`。不得沿用已过期截止。随后运行：

```bash
export PYTHONPATH="$PWD/src:$PWD"
export RECOVERAGENT_S2_EXECUTION=v2_s2_goal_repair_20261117
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
PROBE_PY=/root/gpufree-data/recoveragent_s1_retrain_20261114_01/worker_env/bin/python
"$PROBE_PY" scripts/probe_s2_reward_chain.py run \
  --deployment runs/s2_reward_fix_20261117/deployment.json \
  --output runs/s2_reward_fix_20261117/fresh16_REPAIR_FINAL
# 中断后使用同一目录，不重采已经消费的槽位：
"$PROBE_PY" scripts/probe_s2_reward_chain.py resume \
  --deployment runs/s2_reward_fix_20261117/deployment.json \
  --output runs/s2_reward_fix_20261117/fresh16_REPAIR_FINAL
```

逐条核对位置仍为 `session/requests`、`turns`、实际 `result.json` / `goal_evidence.json` 和独立 `reward_result.json`；汇总 `summary.json` 与 `reward_judgments` 保留请求、token、时间、未知预留与估算费用。Student 合计上限 1,280 请求/262,144 生成 token，单题 1,200 秒；语义评分独立最多 16 请求/65,536 生成 token/1,920 秒，未执行即没有新实测成本。

**真实验证尚未完成**：未证明在线语义评分可靠、16条能全部合法完成或成功率提高。fresh16 后恢复正式 Static/Evolve 前，仍需让两分支共同使用新协议的初始任务池（原 train100＋经新目标协议验收的初始9题），建立同协议 S0 评价；还要按两分支训练规模设置独立语义评分预算，不能沿用 fresh16 的16请求额度。正式任务池迁移和整轮训练不在本轮实施。

---

## 前次修复记录（基线 80c86be，交付 f164fa7）

基线 `80c86be`，分支 `codex/p0-p2-recovery`。本轮仅本地实现、合成回归、私有备份只读核验及 fresh16 准备。没有连接远端，没有启动 GPU、9B训练、Student 或 Teacher 服务。此前停止指令仍有效。

## 实际改动

- 新 `completion_submission_v2` 由公开任务输入选择，Teacher/Student 共用。复用原 `AnswerGuard` 和 Harness，没有再叠加 Guard，也没有修改 execution_v6。嵌套 nullable 中允许 string 时，JSON 答案文本保持原样；明确单值数值契约的有限裸数字按 number/integer 解析。名称、前导零 ID、显式字符串及真正 object/array 参数各保留语义。所有转换来自本次原始输出与公开契约，不读参考答案。
- 解析结果与原文/Schema 的转换核对能确认错误转换或类型契约冲突时，立即记 `parser_harness` 故障与 `reward_valid=false`。重复模型错误本身不构成系统故障。完成提交的 XML、Schema、缺答案和数值错误共用一次纠错机会；普通业务工具原纠错预算不变。反馈含字段/期望/实际类型或具体问题、联合类型细节及“未执行”。真实格式耗尽仍是有效失败。
- 生成题入池前冻结并公开 `required/encoding/schema`。金额、数量、ID、文件名和结构化回报要求 answer；纯动作可省略或 null；status=fail 不强迫编答案。原始题未能可靠分类时不扩大关键词限制。旧示范、快照与工具 Schema 不机械改写。
- 新 `coevolve_public_goal_v1` 在原抽象调用中提出 `success_conditions`：有限可组合的状态行、变化集合、动作/读回、计数、金额和报告字段比较。数组可按集合或顺序比较；新 ID 可绑定本次实际新对象。初态/终态相关数据及动作证据保存于私有目录。整库 hash 仅作诊断；原始任务仍走官方验收。
- 邮件等文字条件使用已有 Teacher 文本调用组件，但新增 **Student reward-time** 接线：客观条件先通过，再把本次 Student 的实际公开证据交给逐条件语义判断，保存引用/理由/用量。客观失败不能被语义 true 覆盖；评审或评分故障返回未知而非 0。Teacher 准入另检查目标覆盖与是否过度限定解法；不是把 Teacher 的全文或整库结果当唯一正确做法。
- 保留自由探索、源码抽象模板、两次探索、独立 Solver、新颖性和程序验收，不加题型白名单。缺失/不支持的结构化评分先有一次契约补齐机会；仍不能评分、或覆盖审核不完整的候选记 `deferred_scoring`，与重复/任务缺陷、Solver 失败分开统计。新目标或新评分身份须重新验收才能进训练池；旧池不能直接沿用旧 hash 奖励冒充新协议。
- GRPO、组反馈和恢复负例选择共用有效性约束。任意一个 member 奖励无效，整组不算优势、不产生 all_failed/forgetting，不从剩余三个成员训练。同批其他完整组继续。新的语义结果保存为独立 `reward_result.json`，绑定原始 result SHA；恢复不重复已完成判定或付费请求。

## 证据与验证边界

| 类型 | 本轮事实 |
| --- | --- |
| 本地程序/合成测试 | 实际 parser → Schema → AnswerGuard → Student dispatch；目标状态与过程负例；Teacher/Student 共用目标；实际 BackendRuntime reward 接线（模拟语义响应）；无效整组过滤；16槽中断接续 |
| 历史48条只读程序复核 | 245个原始完成响应均通过修复后的完成工具 Schema；不等于245次业务成功，也没有重新提交到环境 |
| 已确认7条误罚 | 购物报价2、商品提问4、付款提现1，通过公开目标检查；报价自由文字部分复用与原证据 SHA 绑定的既有人工复核，没有伪称为新的模型判断 |
| 历史真实负例 | 另核验5条：缺必需草稿、跨月批准、漏报ID、漏附件、额外发送，仍失败。错误金额、归属、额外/越界变更及合法替代操作另有合成测试 |
| 真实语义模型调用 | 0；相同意义不同措辞的在线 judge 可靠性尚待验证 |
| 新真实 AppWorld 执行 | 0；仅只读检查本地固定版模型字段元数据，不算任务执行 |
| 新真实 Student/GPU/9B参数更新 | 0；fresh16 尚未运行，不能声称成功率提高 |

历史证据不改写。原 `episodes.json` SHA-256：`42a55654574fc37729bfa39173e04f5c44d50c5e1405cce5ccea344f09bb9a9a`。
脱敏逐项证据 SHA-256：`6e84bc7d2a749e1878b4f8f9965ed20712dfb6ee247ac4e5a2a08d5e9df445c6`。
本轮派生回归计划 SHA-256：`26d8160d0715f8b0a2e1c1861d963c054dfdd019e915288bca3ea3a6f2aea624`。
私有原件仍在 `runs/v2_s2_20261116_01/retrieval/48_trajectory_review_20261117/`；新审计在 `runs/s2_reward_fix_20261117/audit_FINAL/`，含逐提交解析、目标结果及独立历史奖励可用性标记。它们不是新 on-policy 轨迹。

验证命令（均无模型服务调用）：

```bash
.venv-v2/bin/python -m pytest -q tests/test_completion_submission_v2.py \
  tests/test_public_goal_reward.py tests/test_s2_goal_integration.py
.venv-v2/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
PYTHONPATH=src .venv-v2/bin/python scripts/audit_s2_reward_chain.py \
  --evidence runs/v2_s2_20261116_01/retrieval/48_trajectory_review_20261117/48条逐项证据.json \
  --plan runs/s2_reward_fix_20261117/regression_plan_v2.json \
  --output runs/s2_reward_fix_20261117/audit_NEW
```

验证结果：新增针对性 **39 passed**，包含受影响路径的回归 **158 passed**，完整 pytest **1243 passed**（13项既有警告）；Ruff check通过，format check **272文件**通过。完整软件套件包含4项CPU微型随机模型/TRL参数更新回归；它们不使用预训练9B、GPU或真实任务，不是本轮学习训练。独立执行配置 `configs/v2_s2_goal_reward_20261117.json` 与新源码锁覆盖本轮依赖；所有历史锁原样保留并继续拒绝变更源码。

## 固定16条验证入口

配方：`configs/s2_reward_fresh16_20261117.json`；私有定义 `runs/s2_reward_fix_20261117/fresh16_plan_v3.json`。固定顺序：原 train 单值未读数量题、购物车 JSON、商品提问、报价邮件，各4次，从相同干净初态、同一已验收 S0、不同固定 seed 开始。64k、vLLM 和既有单题预算保持；不要求全成功，不按结果补题。

三个生成题有新的诊断定义身份和公开输出契约，不编辑旧题/旧验证。它们仅是协议诊断，**不伪造为已重新验收的训练池任务**。真实执行将检查本次状态/动作并同组核对初态；是否可进入后续训练池仍需正式新目标验收。

准备不启动服务：

```bash
PYTHONPATH=src:. .venv-v2/bin/python scripts/probe_s2_reward_chain.py prepare \
  --plan runs/s2_reward_fix_20261117/fresh16_plan_v3.json \
  --output runs/s2_reward_fix_20261117/fresh16_READY_v2
```

用户安排资源后，在**新源码目录**部署代码、冻结的私有 probe 目录及计划，复用既有基座缓存、S0、AppWorld/CPU/vLLM 环境和 `.env`。拷贝 `configs/v2_s2_deployment.example.json` 为私有部署文件，核对实际路径，并填写本次授权的绝对 `deadline_epoch`；不能沿用已过期截止。

```bash
export PYTHONPATH="$PWD/src:$PWD"
export RECOVERAGENT_S2_EXECUTION=v2_s2_goal_reward_20261117
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
# HF_HOME 指向既有包含固定 revision 的私有缓存。
PROBE_PY=/root/gpufree-data/recoveragent_s1_retrain_20261114_01/worker_env/bin/python
"$PROBE_PY" scripts/probe_s2_reward_chain.py run \
  --deployment runs/s2_reward_fix_20261117/deployment.json \
  --output runs/s2_reward_fix_20261117/fresh16_READY_v2
# 中断后相同输出目录接续；已消费/不确定的 slot 不重采。
"$PROBE_PY" scripts/probe_s2_reward_chain.py resume \
  --deployment runs/s2_reward_fix_20261117/deployment.json \
  --output runs/s2_reward_fix_20261117/fresh16_READY_v2
```

S0 文件身份按原 adapter SHA 核验。Student 上限合计1,280请求/262,144生成token，单题1,200秒；这是预算上界，不是耗时预测。语义判断独立最多16请求/65,536生成token/1,920秒，未知调用保留预留；不做 Teacher 任务生成。金额估算采用现有 USD 价格口径、单独有限技术上限，不当作实际账单。该小额度仅供 fresh16，后续长实验需要重新配置该额度与资源期限。

用量/结果：`summary.json`、每槽 claim/receipt、`session/requests` 和 `turns`、原始 `result.json`、`goal_evidence.json`、派生 `reward_result.json`、`reward_judgments/*/{s2_operation,usage,reward_result}.json`。记录原始尝试、拒绝阶段、实际接受的提交、reward有效性、Student/Teacher token与时间。实例不会由本轮自动启动。

下一步先验收 fresh16 的真实业务/提交/评分一致性，再在同一修复协议下重建 S0 基线与 Static/Evolve 对照。历史奖励更正和修复后分数变化均不能当作学习收益。
