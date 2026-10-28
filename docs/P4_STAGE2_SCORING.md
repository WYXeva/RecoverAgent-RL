# Stage 2 — AppWorld 官方终态评价完整性诊断（独立阶段）

> 本文件只约束 **Stage 2**。它不是 Agent System Prompt。不得修改 `src/recoveragent/execution_v6.py` 的任何字节。
> Stage 1 已在 `e359d537fd95ef6d818c8e729b337b1b20be7b74` 完成；v6 Student 单项兼容修复已在 `e715bf1331e452beed41bf93724dc4d767a49771` 完成。
> 本阶段用户将通过单独 Codex Prompt 发出启动指令；收到指令后只执行本文件，交付后停止。

## 一、唯一问题和非目标

本阶段**只回答**：项目当前的 AppWorld 评价适配是否能正确处理“干净初态/中间态缺目标对象抛 ValueError”与“合法成功终态可以真实评分”两种不同状态？

已知 `reports/unified_task_runtime_probe.json` 中，`76f2c72_1/2/3` 和 `d0b1f43_1/2/3` 的干净初态出现初始评价 `unknown`，现有 `shared_stage.prepare` 将 `scoring_status=PARTIAL` 作为整体阻断依据。初态 `unknown` **不证明**合法终态不可评分，但也不能擅自推断其成功或把 unknown 设为 0。

**不是**重新验证全部 732 题答案；不是训练；不是引入新数据划分；不是修改 AppWorld 官方评分代码；不是接入 Full732 test 任务或直接跑 C244。

## 二、允许的固定诊断集合

只使用已经允许逐题分析的原始官方 **train** 样本，固定在运行前记录：
- `76f2c72_1`：初态 unknown 的一例；
- `d0b1f43_1`：初态 unknown 的一例；
- `07b42fd_1`：初态可以被正常分类为有效失败的控制例。

除非纯读取现存本地汇总证据，Stage 2 不得扩展任务清单。不能根据成功/失败结果更换样本。其他四个初态 unknown 变体只允许使用原有聚合报告，不单独运行。

优先使用**上游提供的 train 官方验证/示范执行流程**，或者已有可信且可独立恢复的 train Teacher 成功终态，作为正向评价控制；不可使用模型生成新的解答、不可读取 test 任务的 ground truth，也不得将上游参考解答放进 Agent 可见上下文或公开仓库。

## 三、按顺序执行的三个小检查

### 2.1 只读检查（先做，不运行环境）
- 阅读 `src/recoveragent/evaluation.py`、`src/recoveragent/student_evaluation.py`、`scripts/audit_unified_pool.py`、`reports/unified_task_runtime_probe.json` 与本仓库的历史成功示范/官方验证回执。
- 定位两个异常场景的具体公开异常分类边界（只记录必要的异常类型、来源/栈帧，避免在公开报告泄露正确答案或用户数据）。
- 说明“初态失败、终态失败、成功终态、环境故障、未知评价”各自的可信判据；未获合法终态证据时标记 `NOT_VERIFIED`。

### 2.2 三任务最小真实 AppWorld 无模型诊断
- 在三个固定 train 任务上分别用隔离实验目录初始化；只在控制侧对原始初态评分，不给 Actor 看任何评判结果。
- 当官方可用的**合法正向终态**证据可重现时，使用上游已有验证流程或已封存的成功终态独立评价；必须明确是否复用了旧结果还是本轮实际运行。
- 保存官方原始 tracker 和控制侧分类前后依据；原始数据/DB/任务说明/官方 evaluator 一律不改。
- 对无法复现正向终态的任务，仅报告正向评分 `NOT_VERIFIED`，不得伪造模型输出或报告评分 PASS。
- 任何合法初态报错都不能被硬编码改成“模型业务失败”；任何真实有效的成功或失败官方评价也不能因初态报错而被丢弃。

### 2.3 最小诊断结论，不默认修改生产算法
- 若根因仅是评估门槛设计错（例如“所有干净初态必须失败有效”），只提供**独立门槛修正建议与针对性回归测试**；确需实施时局限于新独立诊断代码/配置，不修改现有历史锁绑定源码或强制解锁所有任务。
- 若确有评分类别误判，先记录可重现正反例、原始官方回执与小型单元测试，不改官方 evaluator、不将异常自动转为 reward 0。
- 单次样本验证通过不代表 732 个任务均可评分，不允许生成全量的伪 `scoring_status=PASS`。

## 四、硬性边界

- 原 Full732 A244/B244/C244 候选清单及 SHA **不变**，`configs/unified_task_split.json` 等旧方案也不改。
- `execution_v6` System Prompt 和 `student_execution_v6_20261028` 运行锁 **不改**。如后续确需改模型评估源码，会导致旧锁不匹配，应另起受控版本，不能重算旧锁硬放行。
- 训练、Teacher 付费 API、真实 Student 推理、GPU、SSH、服务器开机/租机全部 **禁止**。
- 测试只用允许的 train 任务，禁止测试来源的逐题指令、隐藏答案、数据库或逐题结果分析；自定义 Full732 目标不等于测试侧的训练许可。
- 不创建 Recovery-SFT/GRPO 接口或新框架，不重写 Harness、Memory、Student、Teacher。
- 只执行 Stage 2，Stage 3–6 必须等用户另行指示。

## 五、交付与验收

仅交付必要的**极小**诊断脚本/测试（能复用既有命令则不要新写）、`reports/full732_stage2_scoring_report.md`、对应脱敏汇总JSON与少量配置/状态说明。

报告必须包括：固定诊断任务、初态与正向合法终态的独立评价结果及证据级别、官方 tracker 与项目控制侧分类是否一致、真实异常根因、对现有 `scoring_status=PARTIAL` 的结论、本轮无模型/无付费/无GPU的实测、剩余阻塞。

针对性 pytest 和 Ruff 必须通过；不追求不必要的测试数量。提交推送后**立即停止**，不进入 Stage 3。
