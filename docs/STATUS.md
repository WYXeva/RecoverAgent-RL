# 项目状态

## 当前摘要（2026-11-09）

- **当前工作**：按 [project.md](../project.md) 交付 1 执行第一轮真实学习。入口、场景隔离任务清单、原生 Qwen3.5 模板、Teacher 恢复/生成、增量 SFT 与 TRL 当前策略 GRPO 接线已实现；没有把软件通过写成闭环完成。
- **模型与数据**：锁定 `Qwen/Qwen3.5-9B` revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`。按 v2 train 场景筛出107条原始成功，新模板编码后95条完整合格、12条超长 deferred；1443样本、254396监督token。原包、4B M1、旧成绩和源码锁保持原样。
- **真实训练状态**：Qwen3.5-9B 的正式 S0 已完成181个optimizer steps/1 epoch，1443样本、254396监督token；训练段14934.17秒，allocated峰值30.10 GiB。参数哈希变化，保存与重载参数一致，probe logits完全相等。两次真实烟测均通过。S0位于远程 `runs/round_004/S0_base`，完整私有备份已回传并核验376个文件SHA；S1/S2及dev成绩尚未产生。
- **已解决阻塞**：首次重载探针精度不一致导致停止；统一 BF16 后精确重载通过。第二次长烟测 OOM 后停止；独立新配置使用 CPU 激活暂存，长烟测 allocated 峰值29.90 GiB通过。各次尝试分别保留配置、锁和私有输出目录，没有改写失败证据。
- **当前真实执行**：`round_006` 已尝试冻结清单前9项：1成功、7有效自然失败、1官方评价UNKNOWN（两个未知来源KeyError）；19527生成token。原记录未重判、未重跑。独立feedback续跑版本保留9项及成本，从第10项继续；UNKNOWN不参与Teacher恢复或奖励训练。Teacher恢复/生成、S1、S2与dev24尚未开始。
- **资源**：仅使用 `ssh.txt` 已开机机器；用户明确将当前实例GPU总上限从8小时延长至12小时，绝对截止为2026-11-10 02:12:31（UTC+8），包含已用时间，不租新机器、不充值或续费。Student训练侧20万生成token不变；Teacher无独立金额上限，仍执行请求/token/时间预算和逐次费用记录，当前没有Teacher生成费用。
- **验证与证据**：完整pytest 793 passed/1 skipped，独立CPU随机小模型测试2 passed，Ruff检查通过；真实无模型AppWorld探针确认187个工具Schema一致。当前[执行报告](../reports/v2_round1_learning_20261109.md)记录软件、环境与模型结果的区别。私有根目录 `runs/v2_round1_20261109/`；远程S0为 `recoveragent_v2_20261109/runs/round_004/`，后续为 `runs/round_005/`。
- **下一动作**：在原冻结规模和已批准12小时总预算内连续推进反馈、恢复、生成、S1、S2与固定开发集比较。
- **接续验收**：`--resume-base-from` 已真实复用完整S0和两次烟测，数据/模型/任务清单/来源锁均核对一致；新执行源码为 `7a0c14b`。旧协调进程主动交接的TimeoutError文字保持原样，具体解释及正式S0回执见[本轮报告](../reports/v2_round1_learning_20261109.md)。

## 历史入口

旧正文已从当前文件移除，避免重复维护。完整历史、当时预算和阻塞见[清理前 STATUS](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/STATUS.md)与[历史决策](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/DECISIONS.md)，各次实验原始报告仍保留在 `reports/`。复现旧报告中的源码路径时，使用其记录的历史提交。
