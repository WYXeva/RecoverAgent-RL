# Stage 3A — Full732 A244 Base-SFT 数据准入与采集计划

> **只执行 Stage 3A，做一次可审计的离线数据准备。** Stage 2B 已于提交 `8360931cb06d534dd459668006480bb0ce50d98e` 验证新兼容环境的单任务快照评分保真度。此文件不是模型 System Prompt。当前不运行 Teacher、Student、GPU 或训练，不实施 Recovery-SFT/GRPO。用户必须另外授权实际计算。
>
> 原 Full732 A244/B244/C244 是**冻结的自定义研究目标**，而不是无条件已获训练许可的任务池。AppWorld 上游规定 train 可教学/RL，dev 用于开发，test 仅最终测试。不得通过更改 `permission_status`、重签 manifest、假冒独立基准等方式解除用途限制；不能读取/生成受限 test 任务的解答或逐题反馈。**不要静默切成 A30/B30/C30 并宣称满足 Full732 设计。**

## 一、本轮唯一目标

建立一份新 M1 Base-SFT 的**候选数据来源及训练准备报告**，明确以下事实：

1. 冻结 A244 的 task_id/scenario_id/原始官方 split 与来源 SHA，不重新划分；B244 仅用于将来的恢复训练/GRPO，C244 不得进入任何训练数据。
2. 以前封存的 95 条成功 Teacher 轨迹在 A/B/C 中各有多少（历史已审计约 A32轨迹/25任务，但须重新核验 SHA 和确切来源）；**只把 A 且允许使用的轨迹作为新 M1 候选**。
3. 逐一核验 A 候选的原始 Teacher 真实执行回执、完整性、成功判定、输出工具/Assistant token 格式、与训练模板兼容性及原有mask。**原始 live 成功不等于快照跨进程成功重评分；不要把曾经失败复评分的历史产物默认为合格。**
4. 明确 `model_hashes.json` 的证据状态：旧采集环境 SQLAlchemy 2.1.5 存在 URI 兼容问题；新采集/检查点只能使用已验收的 SQLAlchemy 2.0.52 兼容环境，必要的评分元数据与真实保存状态必须做准入检查。**空 model_hashes 本身不是统一排除条件**，依据合法写入、实际变更、完整官方评分证据判断。
5. 输出明确的可复用样本计数、缺口、Teacher 采集建议批次与成本上下界（需要缺省单价时不得编造）。
6. 独立列出 **Full732 训练使用许可阻塞**，不能因为划分正确就宣称 A244 已可采集/训练。

本轮是 **数据 readiness** 而非创建新的正式训练数据集：不能把未经完整验收的候选直接命名为 final training samples，不生成新 M1 checkpoint。

## 二、按顺序实施

### 3A-1 先只读复核，拒绝重复审计

查看：
- `AGENTS.md`、`PROJECT_PLAN.md`、`docs/P4_STAGE_GATES.md`、本文件。
- `configs/unified_task_split_full_pool_candidate.json`、`reports/full_task_pool_audit.json`。
- `reports/unified_base_sft_source_inventory.json`、`reports/full_candidate_exposure_audit.json`、`reports/base_sft_dataset_v1_report.md`。
- `reports/full732_stage2_scoring_report.md` 与 `reports/full732_stage2b_snapshot_integrity.md`。
- `src/recoveragent/formal_dataset.py`、`src/recoveragent/sft_quality.py`、`src/recoveragent/base_sft.py` 及旧可信 manifest。

不要在本轮访问官方 test 的逐题内容、运行测试侧任务或重新采集轨迹。

### 3A-2 构造只读来源准入报告

沿用旧 sealed dataset 的 SHA 验证，不改旧文件。

- 用任务 ID 在**全部732任务的元数据**中建立 A/B/C 关系；训练源只从符合用途权限的 A 任务中选。
- 对每条历史成功示范按任务/轨迹分别标记：来源包/原split/当前角色/原始live评估/封存文件SHA/工具模式/Prompt版本/mask与supervised token可用性/快照元数据状态/本轮可用资格。
- 权限、缺文件、重复示范、Prompt/Schema 不兼容、可信评分不足、成功终态重评分不一致、缺失证据各自标为不同原因；**不把未知直接改成失败或成功**。
- 不要求所有历史轨迹使用 execution_v6（这些轨迹属于旧合法协议）；但必须清楚报告与新 v6 学生执行协议的格式、可见性和监督兼容性，不把旧 Prompt 说成 v6。
- 防止同 task 的派生后缀跨越 A/B/C，避免 C 的任何历史 Teacher 文本或训练验证 loss 泄漏进入新的 M1。
- 历史旧 checkpoint 仅作样本溯源；旧 A100 LoRA 不得冒充新 M1。
- **不得盲目复用 Stage 2B 新官方参考解答生成的运行记录**：其用途是诊断，不是为新训练新增 Teacher 数据。

### 3A-3 最小训练配方与成本计划（无执行）

冻结原始 Qwen3-4B revision、tokenizer/chat template、execution_v6 + dynamic_discovery、BF16+LoRA、Assistant-only loss、32768上下文窗口 / 4096单回复输出上限（以实际冻结配置为准）、单轮 output/token预算与训练谱系 M1→M2→M3。

根据真实候选数据统计：
- A244 应收集任务数量及有效示范数、缺口按scenario/原split分类；不得为补足数量复制/造假。
- 一个**很小的试采集批次**建议（只列出**当前有合法训练用途**的A任务和固定seed；不真正采集），允许先测 Teacher 成功率和成本再扩大。不能用 C 的成功率筛选。
- 新数据包构造前必须要求每轨迹真实官方有效成功、原任务状态可信、Assistant-only mask、工具 Schema 合法、源哈希和 dedup/权重策略。
- SFT训练前的短长样本、真正 optimizer step、checkpoint save-reload 及同一 C244 的独立主评估是**未来阶段**，本轮不运行。
- 输出一个**明确的阻塞表**：技术已具备 / 原始用途不允许 / 缺少权限证据 / 需要模型预算 / 缺少成功 Teacher示范等，不得写成所有732题已通过。

### 3A-4 工程验收

仅添加一份独立轻量脚本（如果可用既有模块即可不新增）、2–5个精准单元测试、脱敏来源清单与简洁报告。生产 Runner、Teacher、Student、状态和数据协议不需要改。

要求验证：
1. manifest SHA 与 A/B/C 全量身份一致，A244/B244/C244 对应冻结分组。
2. 只允许 A 中已获使用授权且来源完整的示范入选 M1；禁止 B/C 泄漏，历史 train 任务也不因属于 C 被例外放行。
3. 已知 Stage 2 的旧终态跨进程重评风险作为独立资格标记，不能仅凭旧“success=true”就宣布所有来源无问题。
4. 旧 SQLAlchemy 2.1.5 与当前兼容 2.0.52 的风险披露；空 metadata 只读例外。
5. 收集批次、来源 SHA、数据分母与模型/Teacher/GPU 开销为零，所有计算只作计划。
6. 测试与 Ruff 通过，不修改旧权重、Prompt、数据、评分器与锁。

## 三、硬性停止线

- 不开启远端服务器、不主动调用 API、不生成或评分官方 test 任务，不训练任何模型。
- 不重新划分 A/B/C、不把 Full732 主协议替换为 official_train 90 方案。
- 不为得到更高“合格数”修改评分、mask、工具规则或样本来源。
- 不写 Recovery-SFT/GRPO trainer、P4 Teacher checkpoint 接管代码、不执行正式 C244 验证。
- 旧源码和 experiment lock 不重签放行；新 Stage 3A 只做候选审计。
- 若 A244 中 214 个原 test/dev 来源不能合法用于训练，则如实标记阻塞，给出合法 A 子集的统计，但不将合法子集冒称 Full732 完整实现。

## 四、交付和结束

交付 `reports/full732_stage3a_data_readiness.md` 与一份不含任务指令/答案/轨迹内容的机器可读统计JSON（可选一个小脚本与测试），明确：

- A244 候选分布、合法用途、历史可复用 Teacher 轨迹、合格/未验收/阻塞数；
- 旧成功评分 vs 跨进程评分证据差异与模型哈希风险；
- 新 Base-SFT M1 需要的 Teacher 补采集规模、可审核最小批次及成本计算依据；
- Full732 方案仍需什么权限与真实计算授权；
- 本轮 pytest/Ruff，真正模型/付费调用/GPU步数均为0；
- 下一阶段**只给建议，不实际启动**。

完成后提交并推送当前开发分支，**立即停止**，等待用户审查。
