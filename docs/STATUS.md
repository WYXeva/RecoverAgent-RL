# 项目状态

## 当前摘要（2026-11-09）

- **本轮完成**：接入用户提供的 v2 [唯一主计划](../project.md)与[开发约定](../AGENTS.md)，同步 README；本次移除已被替代的旧计划和独立诊断脚本，状态与决策旧正文改用固定 Git 历史链接。当前实施顺序以 `project.md` 为准。
- **可复用事实**：已有真实 AppWorld 执行、Nullable Schema 修复、状态/历史一致恢复、Teacher 数据过滤和 assistant-only 编码；历史 4B M1 已训练并重载。旧 Teacher 最终包有138条合格候选，但尚未按 v2 新划分筛选或按新模型重新编码。
- **最近真实结果**：B10 完成10项 Student 执行、9条有效自然失败；13次 Teacher 恢复中5次官方成功，3条合格后缀（23个 assistant 样本、5066监督 token）、2条超长 deferred。产物已回传；当次关机延误及 SSH 检查见[最终报告](../reports/b10_teacher_recovery_execution_20261104.md)。这是恢复数据生产结果，尚未执行 Recovery-SFT 或 GRPO。
- **v2 尚未执行**：新模型适配与 Base-SFT、场景隔离划分、任务生成、增量 Recovery-SFT、在线 GRPO 和固定 dev/test 评价。历史4B权重和 A/B/C 成绩不作为这些步骤的完成证据。
- **下一动作**：从 [project.md 第8节“交付1：一轮真实学习结果”](../project.md#交付-1一轮真实学习结果) 开始，优先补齐从当前 adapter 继续训练、共享数据入口、一个可验证任务生成器和多轮 GRPO 适配，完成计划中的一轮真实学习与 dev 快评。
- **资源与阻塞**：本轮仅仓库与本地缓存清理，未检查机器/Teacher 服务的当前可用性，未启动 SSH、付费采集、模型推理、GPU 或训练。真实运行所需机器、服务配置和费用上限须按主计划写入运行配置，当前不把它们标为已就绪。
- **验证范围**：删除项已核对依赖；保留源码、配置、数据和运行证据。具体回归检查与磁盘释放量见[清理报告](../reports/workspace_cleanup_20261109.md)；没有新增模型或效果验证。

## 历史入口

旧正文已从当前文件移除，避免重复维护。完整历史、当时预算和阻塞见[清理前 STATUS](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/STATUS.md)与[历史决策](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/DECISIONS.md)，各次实验原始报告仍保留在 `reports/`。复现旧报告中的源码路径时，使用其记录的历史提交。
