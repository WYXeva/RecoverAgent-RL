# 项目状态

## 当前摘要（2026-11-09）

- **当前工作**：按 [project.md](../project.md) 交付 1 执行第一轮真实学习。入口、场景隔离任务清单、原生 Qwen3.5 模板、Teacher 恢复/生成、增量 SFT 与 TRL 当前策略 GRPO 接线已实现；没有把软件通过写成闭环完成。
- **模型与数据**：锁定 `Qwen/Qwen3.5-9B` revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`。按 v2 train 场景筛出107条原始成功，新模板编码后95条完整合格、12条超长 deferred；1443样本、254396监督token。原包、4B M1、旧成绩和源码锁保持原样。
- **真实训练状态**：Qwen3.5-9B 的正式 S0 已完成181个optimizer steps/1 epoch，1443样本、254396监督token；训练段14934.17秒，allocated峰值30.10 GiB。参数哈希变化，保存与重载参数一致，probe logits完全相等。两次真实烟测均通过。S0位于远程 `runs/round_004/S0_base`，完整私有备份已回传并核验376个文件SHA；S1/S2及dev成绩尚未产生。
- **已解决阻塞**：首次重载探针精度不一致导致停止；统一 BF16 后精确重载通过。第二次长烟测 OOM 后停止；独立新配置使用 CPU 激活暂存，长烟测 allocated 峰值29.90 GiB通过。各次尝试分别保留配置、锁和私有输出目录，没有改写失败证据。
- **当前真实执行**：用户将S0反馈改为原冻结清单前15项，已完成3成功/11有效自然失败/1UNKNOWN，共40183生成token。指令到达时第16项已经启动，已明确中断并单列439已知token与4096未完成生成预留，不继续、不计完成、不用于训练。独立feedback15配置从已有15项进入Teacher恢复，S0及原任务顺序保持原样；Teacher恢复11次已完成，6次官方成功，其中4条后缀合格、2条超长deferred；任务生成进行中，S1/S2与开发集比较未完成。
- **资源**：仅使用 `ssh.txt` 已开机机器；用户明确将当前实例GPU总上限从8小时延长至12小时，绝对截止为2026-11-10 02:12:31（UTC+8），包含已用时间，不租新机器、不充值或续费。Student训练侧20万生成token不变；Teacher无独立金额上限，仍执行请求/token/时间预算和逐次费用记录，Teacher恢复和探索已发生实际调用，费用按真实usage单列；估算不冒称实际账单。
- **验证与证据**：完整pytest 812 passed/1 skipped，独立CPU随机小模型测试2 passed，Ruff检查通过；真实无模型AppWorld探针确认187个工具Schema一致。当前[执行报告](../reports/v2_round1_learning_20261109.md)记录软件、环境与模型结果的区别。私有根目录 `runs/v2_round1_20261109/`；远程S0为 `recoveragent_v2_20261109/runs/round_004/`，恢复/生成当前为 `runs/round_008/`。
- **最新规模与下一动作**：用户明确保留32k窗口，将GRPO缩为最多4组×4轨迹（3原始+1生成），开发集缩为原dev24顺序中首个不同场景的3项，三个阶段同题比较；原15项反馈和恢复不重跑。独立finish4配置/锁与 `--resume-s1-from` 从验收通过的S1接续，保持原12小时截止和累计成本。
- **接续验收**：`--resume-base-from` 已真实复用完整S0和两次烟测，数据/模型/任务清单/来源锁均核对一致；新执行源码为 `7a0c14b`。旧协调进程主动交接的TimeoutError文字保持原样，具体解释及正式S0回执见[本轮报告](../reports/v2_round1_learning_20261109.md)。

## 历史入口

旧正文已从当前文件移除，避免重复维护。完整历史、当时预算和阻塞见[清理前 STATUS](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/STATUS.md)与[历史决策](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/DECISIONS.md)，各次实验原始报告仍保留在 `reports/`。复现旧报告中的源码路径时，使用其记录的历史提交。
