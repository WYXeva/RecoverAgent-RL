# RecoverAgent-RL · 分阶段实施门禁（2026-10-27）

> **2026-10-30 用户补充覆盖范围**：本轮授权 M1 验收后运行固定 C244。先完成短/长烟测和全量一轮训练重载，随后独立评估，不让C进入训练loss或checkpoint选择；本条覆盖下文历史“不运行C244”的范围限制。执行时SSH恢复，实际A100 80GB经用户确认启用；远程数据及两次真实烟测通过，正式2070样本一轮训练/259步及保存重载验收PASS，固定C244已在46项后停止（5成功/37有效失败/4UNKNOWN，余198项未执行），完整TGC不成立。用户随后报告欠费关机；M1权重/训练证据已完整回传并核验，C244完整原始轨迹未全部回传。本轮停止，不再SSH或启动远端计算，不开机或续费，见D078。Recovery-SFT/GRPO仍不在本轮范围。

> **当前唯一工作：Stage 3B — 使用冻结的138条Teacher候选训练新的Base-SFT M1。** 用户已确认数据可用于训练；不再重复做历史数据/服务条款审查。严格按 [docs/P4_STAGE3B_BASE_SFT_TRAINING.md](P4_STAGE3B_BASE_SFT_TRAINING.md) 先数据复验，再短烟测、最长样本烟测、正式训练，M1通过后按本轮补充授权执行固定C244，再停止等待审查。C244是唯一验证集；不得创建第二个validation split或以C选checkpoint，不得开始Recovery-SFT/GRPO。
> 本文是面向 Codex 的**工作范围控制文件**，不是 Student/Teacher 的 System Prompt。
> 业务执行 System Prompt 唯一源码保持 `src/recoveragent/execution_v6.py`，不得修改其文本、拼接额外段落或悄悄切回 v5。
> 原始 AppWorld 官方划分、历史实验与发布 checkpoint 均保持不变。每一阶段只有用户明确下达下一阶段指令才可推进。

## 0. 总目标、事实与边界

- 用户选定 **732 Task / 244 Scenario、每场景 3 个变体** 的独立研究设计。目标是 A244（正常 Teacher → Base-SFT）、B244（Student 自然失败 → Teacher 修复 → Recovery-SFT；同时复用做在线 GRPO）、**唯一 C244**（同一批任务从干净初态端到端对比 M1/M2/M3）。
- 现有完整候选清单：`configs/unified_task_split_full_pool_candidate.json`；既有 90 任务版属于历史受限替代方案，不能将其静默冒充 Full732 主设计。**不改变既有确定性 A/B/C 任务分配**，不按表现替换任务。
- 全池任务元数据审计已完成：`reports/full_task_pool_audit.json`。不要重复做大规模审计或按结果重新划分。
- **独立自定义研究与官方 benchmark 分开管理**：原 AppWorld 的 train/dev/test 开发建议规定官方可比评测应如何用数据；用户选择的 Full732 A244/B244/C244 是**非官方自定义训练与验证实验**，不能把所得成绩写成官方 leaderboard 或未污染的官方 test 泛化。不得因为任务最初属于官方 test 就把独立自定义研究自动缩回 A30，但也**不能把本地存在文件或研究意愿等同于合法访问和使用许可**。Codex 必须单独审查现有数据许可证、受保护部分的访问/再分发条件以及实际取得的使用权限：有真实依据时走显式版本化的 custom research runner；没有依据时按任务报告 BLOCKED，不造假、不绕过加密/权限。不修改既有官方 benchmark 模式的原始限制，不对外公开原任务、测试内容、正确答案或数据库。
- 当前仓库已存在 `execution_v6`，必须保证 **Agent Prompt 与评测 Harness 相互独立**：本项目分阶段文档不可进入 Actor 的模型输入。
- 任何阶段不得自行租用/启动 GPU、请求付费 Teacher、重训 Base-SFT、使用模型完成批量任务，除非用户在新一轮明确授权该具体阶段的预算与设备。不要将旧授权延伸到新的训练项目。
- 旧 Base-SFT 的 64 训练任务/82 训练轨迹、A100 adapter 及 P3-C 真实失败是历史结果；新的 A/B/C 分配和历史权重有暴露交叉，旧 adapter **不能冒充新 M1**。

## 1. 固定阶段顺序（未来工作，不代表现在全部执行）

| 阶段 | 唯一交付目标 | 开始条件 | 结束门槛 |
| --- | --- | --- | --- |
| Stage 1（完成） | Full732 元数据契约、来源权限门禁、可测试的统一入口最小垂直切片 | 当前用户指令 | 代码/配置/最小 train 探针、拒绝测试、报告；受限执行正确阻断 |
| Stage 2（已完成诊断） | train 初态unknown与合法终态评分边界 | 用户已授权且已运行 | PASS；旧封存成功终态跨进程复评PARTIAL |
| Stage 2B（已完成） | 固定train写入任务的快照变更元数据与评分等价性 | 已按用户指令执行 | 新兼容环境单任务往返5/5 PASS；旧快照未修复 |
| Stage 3A（已完成） | A244全量新Teacher采集及候选整理 | 已完成244首轮与134项专项复试 | 144成功任务，138条长度合格候选；见最终manifest |
| **Stage 3B（当前）** | 使用最终138条候选训练新Base-SFT M1 | 用户已确认数据可用于训练；远程资源与数据哈希检查通过 | 短烟测、长样本烟测、正式训练、权重变化与重载均有真实证据；然后停止 |
| Stage 4 | B侧Student自然失败、Teacher接管、Recovery-SFT M2 | 新M1验收且用户另行授权 | 真实恢复后缀与新权重验收 |
| Stage 5 | B 侧当前策略在线 GRPO，产出 M3 | M2 已验收，奖励有效，GPU 单独授权 | 同起点 G=4、行为 logprobs/mask、有效奖励、真实参数更新 |
| Stage 6 | **唯一 C244** 上从原任务初态评测 M1/M2/M3 | 三个 checkpoint 验收；C 的合法测试用途与统一评分可用 | 244 行逐任务配对矩阵、完整分母和失败/未知/成本；仅在全套有效时给 TGC 增量 |

每阶段结束**立即停止**；只提交本阶段实际成果与阻塞项，等待用户审查。**Codex 不得把表中后续阶段顺便实现。**

## Stage 3B 当前工作与前序阶段历史

**当前唯一执行文档：[docs/P4_STAGE3B_BASE_SFT_TRAINING.md](P4_STAGE3B_BASE_SFT_TRAINING.md)。** Stage 3A已完成，最终私有数据包与manifest已冻结。用户已确认数据可用于训练；不再要求额外的服务条款审查。Stage 3B完成后停止，不能自行进入Stage 4–6。

**当前唯一新执行文档：[docs/P4_STAGE3A_BASE_SFT_READINESS.md](P4_STAGE3A_BASE_SFT_READINESS.md)。** 本文件同名但已经替换为全 A244 从零 Teacher 采集规范。Stage 1–2B 均为历史，不要反复审计旧轨迹，也不要提前训练 M1。

Stage 2B 已完成；旧版Stage 2B执行提示是历史记录，不属于当前任务。

Stage 1–2 已完成，后文为历史执行记录；不得重复调用 Stage 1 或 Stage 2 的旧命令。

Stage 2 固定三个官方 train 诊断任务（`76f2c72_1`、`d0b1f43_1`、`07b42fd_1`），只验证初态 `unknown` 与可信正向终态评分之间的差别，不改官方 AppWorld evaluator、System Prompt、已冻结任务划分及旧源码锁。Stage 2 不授权模型推理、Teacher API、GPU、训练或测试来源逐题分析。完成报告后提交并停止。

---

## 2. Stage 1 本轮具体实施范围（历史已完成）

### 2.1 首先只读确认与冻结引用

1. `git fetch`、核对最新 HEAD，并阅读 `AGENTS.md`、本文件、`PROJECT_PLAN.md`、`docs/PROMPTS.md`。
2. 检查 `configs/unified_task_split_full_pool_candidate.json`、`reports/full_task_pool_audit.json`、`src/recoveragent/unified_split.py`、`src/recoveragent/shared_stage.py` 和现有适配器。
3. **复用**已验证的 A/B/C 244 分配及来源 SHA；Stage 1 不重算全量任务内容，不从 test 读取题目/ground truth，不访问模型。

### 2.2 新建最小独立协议入口

围绕现有代码添加一个**独立于官方 train-only 路线**的 Full732 自定义实验配置/准入入口，建议配置路径 `configs/full732_stage1_protocol.json`；实际模块数量应尽量少。

- 清楚区分三层事实：`metadata_eligible`（ID/场景/划分合法）、`usage_authorized`（用途权限与证据）、`execution_ready`（技术环境和奖励能力满足）。不要将存在文件、散列匹配、官方评测脚本存在或用户的研究意愿自动等同于 `usage_authorized=true`。
- 将 `scope=full_pool_candidate` 保留为目标设计，不删除官方用途保护，也不把所有非 train 的 `BLOCKED` 改成 `READY`。新协议应能够生成 A/B/C 全量任务元数据以及带阻塞原因的可执行性矩阵。
- 定义单一来源权限门禁，训练 A/B、只读验证 C 的用途分别鉴权；授权证据不存在时应 fail closed。**不要设计虚构的“custom_protocol=true 即全部获得授权”捷径。**
- 为未来真实执行准备干净初态、相同 Prompt/工具/模型预算、独立控制侧官方评价及任务级结果留痕的清晰接口；调用项目已有组件，不复制 Runner，不写新的 Teacher、SFT 或 GRPO 主程序。
- Stage 1 的最小正向演示仅可在**官方 train 的一条预先固定任务**或完全合成 fixture 上进行。演示可为脚本预设动作，不是 Teacher/Student 模型成绩，不可探测受限 test 任务内容。
- **保持 `src/recoveragent/execution_v6.py` 的原文字节不变**，不改其唯一 Prompt 来源，不碰用户确认的 System Prompt。

### 2.3 最少必要验收

必须包含：
1. 732/244/3×244 的元数据数量和 A/B/C 无重叠、GRPO 复用 B、唯一 C。
2. Manifest 改动/错配会被拒绝；不同运行阶段不会读错 A/B/C。
3. 缺少用途证明时，原 test 侧训练与逐题开发调用确实被阻断；即使任务文件存在也不放行。
4. 未获用户明确许可时，GPU、Teacher API、实际模型生成和训练全为 0。
5. 在单个允许的 train 任务或合成夹具上，入口能够与现有执行/评价组件对接（不要重复执行历史大量 probe）。
6. 不把干净初态评价 `unknown` 当成“未来有效成功终态永不可评分”；不把 `unknown` 改成官方失败。Stage 1 **仅报告**此问题，正式修复留到 Stage 2。
7. 不改变 `execution_v6`、现有 AppWorld、原始数据、旧 checkpoint 与已发布实验结果。
8. 运行针对性 pytest、Ruff；列出实际执行命令、次数、是否使用模型和费用情况。

### 2.4 必须停止的范围

**禁止在本轮执行或实现**：
- 全 732 任务重跑、批量评测、测试任务逐题评分/错误分析；
- 改官方评价器、模型 Prompt、Harness 算法、Memory；
- 改动旧 M1 权重，正式 A244 Teacher 收集、B244 失败采集/恢复；
- Recovery-SFT、GRPO Trainer、完整训练框架、自动化开机/租 GPU；
- 为了让检查“全绿”偷偷修改 upstream 权限、测试断言或训练标签；
- 把本 Stage 1 的工程连通性冒充全 732 模型评估成功。

## 3. Stage 1 报告与交接规范（历史已完成）

本轮只新增必要源文件、测试、`configs/full732_stage1_protocol.json` 和 `reports/full732_stage1_report.md`；需要其他文件时先说明其必要性。不要一次生成未来六阶段所有空文件。

报告只需：
- 对齐的 Git 提交 SHA、现有候选 Manifest SHA 和任务分配统计；
- 完成/未完成/受限项：实际可使用的任务、受限操作及依据；
- 正向 train/合成探针结果；权限拒绝结果；独立测试与 Ruff；
- 本轮模型请求、GPU训练、付费 Teacher 调用次数（目标全部 **0**）；
- 明确下一阶段 Stage 2 的**单个阻塞问题**和最小建议，不自行实施。

结束时提交并推送开发分支，然后只回复用户一个简洁验收摘要。不要编写下一阶段代码。

## 4. Stage 1 历史执行 Prompt（已执行，禁止再次当作当前指令）

> 从最新 `codex/p0-p2-recovery` 拉取。先阅读 `AGENTS.md` 与 `docs/P4_STAGE_GATES.md`。**仅执行 Stage 1**：复用既有 Full732 候选与 A/B/C 244 分配，创建独立元数据/权限/执行就绪门禁，并在一条允许的 train 任务或合成夹具上验通现有 Runner 的最小切片。保持 execution_v6 原文、旧实验、官方测试限制及所有模型权重不变；未经授权不得读取测试任务内容、调用模型或训练。完成针对性测试和报告，提交推送后停下，禁止开始 Stage 2–6。
