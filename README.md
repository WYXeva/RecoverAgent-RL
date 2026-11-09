# RecoverAgent-RL

在 AppWorld 中研究反馈驱动的任务演化、真实失败恢复和在线强化学习：同一个 Student 执行任务，Teacher 接续修复 badcase，并探索、生成可验证的新任务；通过迭代 Recovery-SFT 与当前策略 GRPO 更新模型。

**[project.md](project.md) 是唯一当前主计划；[AGENTS.md](AGENTS.md) 是开发约定；[STATUS 当前摘要](docs/STATUS.md#当前摘要2026-11-09) 记录实际进度。** 后续实现从 [第8节“交付1：一轮真实学习结果”](project.md#交付-1一轮真实学习结果) 开始。

## 当前起点

v2 文档已更新，学习运行尚未执行。已有 AppWorld 执行、恢复、数据过滤和4B Base-SFT 底座；B10 真实试点产出3条合格恢复后缀、23个 assistant 样本、5066监督 token，尚未执行 Recovery-SFT 或 GRPO。[B10 验收报告](reports/b10_teacher_recovery_execution_20261104.md)保留完整结果与局限。

新模型、场景隔离训练/开发/测试划分、预算和主实验设计以 `project.md` 为准。旧固定4B、A244/B244/C244、多检查点主算法及 P4 阶段门禁属于历史方案，不是 v2 的默认入口。文档更新不启动付费采集或训练。

## 文档与已有部件

| 内容 | 入口 |
| --- | --- |
| v2 设计与实施顺序 | [project.md](project.md) |
| 开发约定与执行事实 | [AGENTS](AGENTS.md)、[STATUS](docs/STATUS.md) |
| 历史计划与决策 | [已归档 PROJECT_PLAN](PROJECT_PLAN.md)、[DECISIONS](docs/DECISIONS.md) |
| 已有 Teacher 成功数据 | [最终数据报告](reports/full732_teacher_dataset_complete_20261029.md)；复用前按 v2 train 场景筛选并重新编码 |
| 工具执行与状态恢复 | [Adapter](src/recoveragent/adapter.py)、[State](src/recoveragent/state.py)、[Nullable 修复](reports/schema_nullable_compatibility_v2.md) |
| Teacher 恢复与后缀数据 | [恢复部件](src/recoveragent/recovery_suffix.py)、[B10 结果](reports/b10_teacher_recovery_execution_20261104.md) |
| 现有执行协议起点 | [execution_v6](src/recoveragent/execution_v6.py)、[Harness](docs/HARNESS_V1.md)、[Student 协议](docs/STUDENT_PROTOCOL.md) |
| 排查模型实际输入/输出 | [LLM 请求视图](docs/LLM_VIEW.md) |

现有代码和协议文档描述已实现的部件，不表示已兼容 v2 新模型或完成新训练链路；历史运行用其固定提交、配置和锁复现。

## 目录与本地验证

| 目录 | 用途 |
| --- | --- |
| `src/recoveragent/`、`tests/` | 运行代码与软件契约测试 |
| `scripts/`、`configs/` | 当前入口和历史复现脚本、任务清单、版本锁 |
| `docs/`、`reports/` | 规则、决策、公开实验报告与校验摘要 |
| `requirements/` | 分环境依赖锁 |
| `data/`、`runs/` | 私有数据、原始轨迹、快照、checkpoint 和完整归档，不提交 Git |
| `.cache/`、`.venv*/` | 模型/分词缓存、历史诊断回执、独立环境；不能整体当临时文件删除 |

在已有本地开发环境执行以下软件检查，不启动模型、AppWorld 任务或训练：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/ruff check --no-cache src scripts tests
.venv/bin/ruff format --check --no-cache src scripts tests
```

首次安装和环境分工见 [环境报告](reports/environment_report.md)、[远程 Base-SFT 说明](docs/REMOTE_BASE_SFT.md)及 `requirements/`。复现历史运行应使用报告绑定的原提交和依赖；不要重算旧源码锁来适配新代码。软件通过不代表真实模型效果通过。

<details>
<summary>历史实验与复现入口</summary>

- [P0–P2 基础设施](reports/p0_p2_report.md)：旧 1.7B、状态恢复和四分支，非正式 Student 成绩。
- [P3-A 正式 Teacher 数据](reports/base_sft_dataset_v1_report.md)：95 条轨迹、75 个任务、29 个场景；[原始可信摘要](reports/base_sft_dataset_v1_release.json)。
- [96GB Base-SFT](reports/p3b_base_sft_report.md)及[评价诊断](reports/p3b_diagnosis_20261024.md)。
- [A100 重训与 P3-C](reports/a100_sft_p3c_20261026_report.md)：历史权重与自然失败采集。
- [Harness 审查与修复](reports/harness_refactor_report.md)。
- Memory：[v1](reports/egtm_v1_report.md)、[模型驱动 v2](reports/egtm_v2_improvement_report.md)、[Harness 29k 实测](reports/egtm_harness_29k_live_report.md)。当时的共享阶段评估关闭 Memory。

完整沿革见 [STATUS](docs/STATUS.md) 与 [DECISIONS](docs/DECISIONS.md)。历史源码、配置和报告保持原路径，以保留证据链和版本锁；这些文件不是默认执行入口。

</details>

Actor 仅访问公开任务信息和工具；隐藏评价在控制侧进行。凭据、原始 AppWorld 数据、轨迹和权重不上传公开 GitHub。Teacher 付费调用、GPU 与训练必须有相应授权。
