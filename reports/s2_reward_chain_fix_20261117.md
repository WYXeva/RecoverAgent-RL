# S2 提交与目标奖励链修复（2026-11-17）

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
