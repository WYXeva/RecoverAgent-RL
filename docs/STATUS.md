# 项目状态

## 当前摘要（2026-11-09）

- **当前工作**：按 [project.md](../project.md) 交付 1 执行第一轮真实学习。入口、场景隔离任务清单、原生 Qwen3.5 模板、Teacher 恢复/生成、增量 SFT 与 TRL 当前策略 GRPO 接线已实现；没有把软件通过写成闭环完成。
- **模型与数据**：锁定 `Qwen/Qwen3.5-9B` revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`。按 v2 train 场景筛出107条原始成功，新模板编码后95条完整合格、12条超长 deferred；1443样本、254396监督token。原包、4B M1、旧成绩和源码锁保持原样。
- **真实训练状态**：已在用户现有 RTX 4080 SUPER vGPU 32GB 完成两步短烟测和32666 token最长完整样本的一步烟测，均有限梯度、参数变化、保存重载通过；前次正式S0在3步后OOM停止并保留未完成adapter；当前独立 `round_004` 启用可扩展显存分配，两次烟测通过，2026-11-09 16:08（UTC+8）已完成28/181步，仍在训练。没有正式 S0/S1/S2 checkpoint 或 dev 比较成绩。
- **已解决阻塞**：首次重载探针精度不一致导致停止；统一 BF16 后精确重载通过。第二次长烟测 OOM 后停止；独立新配置使用 CPU 激活暂存，长烟测 allocated 峰值29.90 GiB通过。各次尝试分别保留配置、锁和私有输出目录，没有改写失败证据。
- **尚未执行**：v2 Student反馈20、Teacher恢复、生成任务真实成功验证、Recovery-SFT、GRPO及dev24阶段比较；统计均为0，不复用旧B10数量冒充本轮结果。
- **资源**：仅使用 `ssh.txt` 已开机机器；GPU绝对8小时上限与Student训练侧20万生成token保持不变。用户明确Teacher无独立金额上限，仍执行请求/token/时间预算和逐次费用记录；当前没有Teacher生成费用。
- **验证与证据**：完整pytest 758 passed/1 skipped，独立CPU随机小模型测试2 passed，Ruff检查通过；真实无模型AppWorld探针确认187个工具Schema一致。当前[执行报告](../reports/v2_round1_learning_20261109.md)记录软件、环境与模型结果的区别。私有根目录 `runs/v2_round1_20261109/`；远程本轮 `recoveragent_v2_20261109/runs/round_004/`。
- **下一动作**：监控正式S0并验收重载，然后在原冻结规模和剩余预算内连续推进反馈、恢复、生成、S1、S2与固定开发集比较。

## 历史入口

旧正文已从当前文件移除，避免重复维护。完整历史、当时预算和阻塞见[清理前 STATUS](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/STATUS.md)与[历史决策](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/DECISIONS.md)，各次实验原始报告仍保留在 `reports/`。复现旧报告中的源码路径时，使用其记录的历史提交。
