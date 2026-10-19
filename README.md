# RecoverAgent-RL

Self-Improving Tool Agents through Failure Recovery

在 AppWorld 中研究工具调用 Agent 如何从自己的自然失败中恢复，并通过恢复结果持续更新策略参数。

**当前处于项目设计阶段。** 仓库包含需求、实施计划和工作约定，尚无可运行的 Agent、真实环境验收、训练结果或效果提升证据。

## 训练路线

1. 由 Student 实际执行训练任务，采集自然失败，并保存同一时刻的环境状态与合法历史。
2. 验证同一起点的四条独立恢复分支，使用官方验证器评价结果。
3. 由更强 Teacher 接手失败状态，执行真实恢复，筛选合格示范并训练 Student SFT。
4. 从共同的 Teacher-SFT checkpoint 出发，开展正常任务与恢复任务混合 GRPO 及后续自迭代。

Teacher 恢复 SFT 是主线 GRPO 的必经前置。Decision-Point Recovery 和 Adaptive Failure Learning 是待验证假设；ReTerm-style 恢复训练作为基线。

## 文档

- [项目计划与验收要求](PROJECT_PLAN.md)
- [项目工作约定](AGENTS.md)
- [当前状态与下一步](docs/STATUS.md)

首轮实现范围为 P0–P2：真实任务执行、自然失败采集、状态保存与恢复、四分支隔离和官方评价。安装与运行命令将在实际验证后补充。

本地凭据、原始基准数据、快照、轨迹和模型权重通过 `.gitignore` 排除。后续发布其他数据或演示材料前，按项目约定核对上游许可与发布要求。
