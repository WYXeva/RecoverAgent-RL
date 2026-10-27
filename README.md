# RecoverAgent-RL

在 AppWorld 中研究工具调用 Agent 的自然失败、真实状态恢复与恢复学习。正式 Student 为 **Qwen/Qwen3-4B**。

训练路线：Teacher 正常示范 → Base-SFT → Student 自然失败 → Teacher 多检查点恢复 → Recovery-SFT → 在线 GRPO。

先读 [当前阶段门禁](docs/P4_STAGE_GATES.md)与[实际状态](docs/STATUS.md)，需求见 [PROJECT_PLAN.md](PROJECT_PLAN.md)，开发约定见 [AGENTS.md](AGENTS.md)。

## 当前工作入口

| 工作 | 入口 |
| --- | --- |
| Full732 当前工作范围 | [Stage 1 门禁](docs/P4_STAGE_GATES.md)，Stage 2–6 须逐阶段授权 |
| Full732 Stage 1 入口/验收 | [full732_stage1.py](src/recoveragent/full732_stage1.py)、[工程报告](reports/full732_stage1_report.md) |
| 冻结 Full732 候选划分 | [A244/B244/C244](configs/unified_task_split_full_pool_candidate.json) |
| 既有审计与 train 替代方案 | [审计报告](reports/unified_training_plan.md)、[A30/B30/C30](configs/unified_task_split.json) |
| 既有共享评估接口 | [evaluate_shared_stage.py](scripts/evaluate_shared_stage.py)、[train 替代方案配置](configs/shared_stage_validation_v2.json) |
| 当前 System Prompt | [execution_v6.py](src/recoveragent/execution_v6.py)、[协议说明](docs/PROMPTS.md) |
| Teacher/Student 工具执行 | [Harness](docs/HARNESS_V1.md)、[Student 协议](docs/STUDENT_PROTOCOL.md) |
| 排查模型实际输入/输出 | [LLM 请求视图](docs/LLM_VIEW.md) |

Full732 Stage 1 独立准入、软件测试及一个 train 任务脚本链路已通过，原 A/B/C 各 244 题保持不变；涉及受限来源的执行仍 **BLOCKED**。Stage 2–6 未执行。既有 train 替代方案各 30 题，仍有 6 个未知评价、评分预检 **PARTIAL**，不能当作 Full732 主设计。旧 checkpoint 有新验证集训练暴露，不能作为新实验的无泄漏 M1；现有 StudentSession 与 v6 提示声明也存在待修复兼容性阻塞。新三阶段训练和评估尚未执行。

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
- Memory：[v1](reports/egtm_v1_report.md)、[模型驱动 v2](reports/egtm_v2_improvement_report.md)、[Harness 29k 实测](reports/egtm_harness_29k_live_report.md)。共享阶段评估当前关闭 Memory。

完整沿革见 [STATUS](docs/STATUS.md) 与 [DECISIONS](docs/DECISIONS.md)。历史源码、配置和报告保持原路径，以保留证据链和版本锁；这些文件不是默认执行入口。

</details>

Actor 仅访问公开任务信息和工具；隐藏评价在控制侧进行。凭据、原始 AppWorld 数据、轨迹和权重不上传公开 GitHub。Teacher 付费调用、GPU 与训练必须有相应授权。
