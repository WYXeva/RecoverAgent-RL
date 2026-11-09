# 项目决策

## 当前约定

当前唯一设计与实施顺序见 [project.md](../project.md)，开发约定见 [AGENTS.md](../AGENTS.md)，实际进度见 [STATUS](STATUS.md)。本文件不再复制旧阶段计划或完整年表。

2026-11-09：用户将主线切换为 v2，并要求清理冗余文档、无依赖的旧实现/测试和本地缓存。已完成阶段的事实、配置、源码锁、数据与权重保留；从当前树移除的旧文件可按固定提交恢复。详情见[清理报告](../reports/workspace_cleanup_20261109.md)。此次清理不启动付费采集或训练，后续实现从 `project.md` 第8节“交付1”开始。

## 历史决策 D001–D087

完整原文见[清理前 DECISIONS](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/DECISIONS.md)。其中固定4B、A/B/C划分和逐阶段停等是当时约定，不作为 v2 的执行门禁；历史真实结果与局限不改写。
