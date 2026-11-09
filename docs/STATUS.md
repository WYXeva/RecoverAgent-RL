# 项目状态

## 当前摘要（2026-11-09）

- **当前目标**：在同一轮 S0/S1/S2 中接入 CoEvolve AppWorld 方法生成新任务，实际用于 S2，完成固定 dev24 比较。官方 CoEvolve 仓库当前未发布可执行主体；方法复现、AgentEvolver 提示复用与资源适配分别记录，见 [接入说明](COEVOLVE_APPWORLD_METHOD.md)。
- **S0 已完成**：固定 Qwen/Qwen3.5-9B revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`；95条新场景划分下合格示范、1443样本、254396监督token，181步/1 epoch，14934.17秒，allocated峰值30.10 GiB。短/长烟测和保存重载通过，完整私有备份已核验。
- **真实反馈与恢复**：固定前15项为3成功/11自然失败/1UNKNOWN；被用户取消的第16项另列439已知token及4096未知预留。11次Teacher恢复，6官方成功，4条合格后缀（40样本/5930监督token），2条超长deferred，5次失败。原固定Gmail生成10次均未接纳，费用与原件保留。
- **S1 已完成**：从S0继续8步，59样本/11739监督token，训练998.67秒。LoRA哈希从 `b4b16e43f10d280722ce5544277858b94bcfbc0aa78de6817790bda8e203d235` 变为 `3daf5effa13ed2bcf939861d90112659da62c751996afd932ea10b392d6a496f`；重载哈希与logits精确一致。远程 `runs/round_008/S1_recovery`，本地完整备份325727134字节，30个文件SHA通过。
- **CoEvolve 当前状态**：已实现三类反馈信号、同源多分支探索、实际动作引用抽象、独立Solver及语义/程序验收、训练中动态入池；新配置/源码锁独立。S2加入冻结S1参考adapter的KL约束；概率计算分块但不截断token。真实新方法已有4候选/3合格（其中2条反馈引导）/1拒绝。S1采样首个生成任务的2条轨迹后，发现计数答案字符串/数字比较缺陷；在0个GRPO更新处主动停止，原始错误判分保留，dev未运行。旧finish4/finish16自动接续均在请求前取消。
- **验证**：完整pytest 849 passed/1 skipped，针对性84 passed，独立随机小模型CPU测试3 passed（含固定参考KL/参数更新及分块概率等价），Ruff check/format通过。这些不代表真实任务成功率改善。
- **资源与下一动作**：仅使用现有32GB实例，绝对截止2026-11-10 14:12:31（UTC+8），含已用时间；不租机、充值或续费。保持32k、最多16组×4、固定dev24、训练侧20万token及既有成本/未知预留。新CoEvolve最多10个提案，已发生的旧10次不清零；Teacher无独立金额上限但逐请求技术预算与费用继续记录。新answer配置/锁只修明确单值计数的等价比较；逐任务重新做独立正负验收、从只读终态重评分已完成2条当前S1轨迹，保留原判分/预期答案并继续S2，不重新生成。
- **证据**：详见 [本轮报告](../reports/v2_round1_learning_20261109.md)。私有根目录 `runs/v2_round1_20261109/`；S0为远程 `runs/round_004/S0_base`，S1与原恢复为 `runs/round_008/`。CoEvolve首段为 `runs/round_011/`，记录已完整回传；修正后使用独立 `runs/round_012/`，启动待回执。当前总已知训练生成43481token，加既有4096预留；Teacher累计估价7.6239492元。

## 历史入口

旧正文已从当前文件移除，避免重复维护。完整历史、当时预算和阻塞见[清理前 STATUS](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/STATUS.md)与[历史决策](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/DECISIONS.md)，各次实验原始报告仍保留在 `reports/`。复现旧报告中的源码路径时，使用其记录的历史提交。
