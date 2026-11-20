# 当前CodeAct依赖清理（2026-11-20）

用户明确授权清理旧实现/测试和运行磁盘。删除前的完整实现保留在 `74f9a28`；原工作区未提交工作及远端冻结源码不动。

## 仓库

按所有CodeAct模块、共享训练对照与脱敏工具为根，检查Python导入、相对导入及显式子进程模块字符串。保留当前可达依赖，不按文件名中的v2/旧日期误删。移除42个不可达旧模块、26个旧入口脚本、76个旧测试文件；保留并迁移仍使用的队列/组反馈测试到 `test_codeact_schedule.py`。

包命令 `recoveragent` 现在指向 `recoveragent.codeact.cli:main`。现有64K模型/LoRA/训练及Teacher组件保留，不迁移旧运行状态。历史配置和源码锁保留原件；当前源码采用独立 `configs/codeact_online_compact_20261120.lock.json`（296文件），旧锁继续拒绝变化后的目录。

当前06远端仍执行已部署的 `74f9a28` 冻结源码与 parallel 锁；清理不会删除其运行快照。后续从当前checkout准备新目录时使用 compact 锁，不能把新锁塞进已启动作业。

## 验证

清理后完整 `PYTHONPATH=src .venv-v2/bin/python -m pytest -q`：**339 passed，12.28秒**。恢复了合并GRPO的三种offload数值对照参数化；保留旧锁的拒绝检查，未重签旧锁。CodeAct采集/训练/评估CLI及在线实验CLI的 `--help` 均可加载；修改范围Ruff与diff通过。

这些是本地软件测试，真实GPU并发及重复错误观测见[并发报告](codeact_parallel_progress_20261120.md)。

## 磁盘

- 六份远端重复压缩包逐SHA/大小对照已有本地副本后删除：4,795,065,658字节，约4.47GiB。
- 两个停用的历史Function Calling运行目录另清理27个大文件，每个都与原工作区 `remote_backup/` 中的本地原件逐SHA/大小核验；释放860,480,724字节。包含历史权重/optimizer等重复文件，原件仍在私有本地备份；当前S0、actor、optimizer及CodeAct轨迹保留。共清理33个远端重复文件、5,655,546,382字节，约5.27GiB。
- 同六份本地归档校验后移到D盘 `RecoverAgent-RL-private/archived_bundles_20261120/`，旧路径保留链接，释放WSL内约4.47GiB。大规模后续归档也写D盘。
- 清除本工作区Python/pytest/Ruff缓存约3.81MiB。当前使用的环境保留。
- WSL内释放不等于Windows C盘立即缩小VHD；本轮未关闭或压缩WSL，避免中断当前工作。

私有清理回执在 `runs/codeact_evolve_20261120_06/`：`remote_cleanup_result.json`、`old_run_cleanup_result.json`、`verified_old_run_copies.json`、`archive_relocation.json`。不存在“先删再补备份”的步骤。

## 删除文件清单

历史原件可从上述Git提交恢复：

### 模块

- `src/recoveragent/buffer.py`
- `src/recoveragent/cli.py`
- `src/recoveragent/collection_report.py`
- `src/recoveragent/dataset_sources.py`
- `src/recoveragent/dataset_transfer.py`
- `src/recoveragent/full732_c244_parallel.py`
- `src/recoveragent/full732_c244_serial_fallback.py`
- `src/recoveragent/full732_stage1.py`
- `src/recoveragent/inference.py`
- `src/recoveragent/m1_vgpu_reference_resume.py`
- `src/recoveragent/official_retry.py`
- `src/recoveragent/s2_backend.py`
- `src/recoveragent/s2_background.py`
- `src/recoveragent/s2_evolution.py`
- `src/recoveragent/s2_experiment.py`
- `src/recoveragent/s2_fresh_probe.py`
- `src/recoveragent/s2_inputs.py`
- `src/recoveragent/s2_llm_pool.py`
- `src/recoveragent/s2_reporting.py`
- `src/recoveragent/s2_review.py`
- `src/recoveragent/s2_reward.py`
- `src/recoveragent/s2_reward_audit.py`
- `src/recoveragent/s2_segment.py`
- `src/recoveragent/s2_storage.py`
- `src/recoveragent/s2_update.py`
- `src/recoveragent/sft_bf16_runtime.py`
- `src/recoveragent/shared_stage.py`
- `src/recoveragent/student_backend.py`
- `src/recoveragent/teacher_escalation.py`
- `src/recoveragent/teacher_retry.py`
- `src/recoveragent/v2_coevolve_resume.py`
- `src/recoveragent/v2_feedback_resume.py`
- `src/recoveragent/v2_finish_resume.py`
- `src/recoveragent/v2_legacy_teacher.py`
- `src/recoveragent/v2_resume.py`
- `src/recoveragent/v2_round.py`
- `src/recoveragent/v2_s1_batch.py`
- `src/recoveragent/v2_s1_retrain.py`
- `src/recoveragent/v2_s1_short.py`
- `src/recoveragent/v2_s1_short_eval.py`
- `src/recoveragent/v2_short_data.py`
- `src/recoveragent/v2_vllm.py`

### 入口

- `scripts/audit_s2_reward_chain.py`
- `scripts/collect_base_sft.py`
- `scripts/evaluate_shared_stage.py`
- `scripts/evaluate_v2_s1_short_vllm.py`
- `scripts/export_fresh_parallel.py`
- `scripts/finish_v2_interrupted.py`
- `scripts/probe_egtm.py`
- `scripts/probe_egtm_v2.py`
- `scripts/probe_s2_append.py`
- `scripts/probe_s2_coevolve.py`
- `scripts/probe_s2_reward_chain.py`
- `scripts/probe_v2_s1_worker.py`
- `scripts/probe_v2_vllm.py`
- `scripts/recheck_s2_judge.py`
- `scripts/recheck_s2_reference.py`
- `scripts/retrain_v2_s1.py`
- `scripts/retry_teacher_followup.py`
- `scripts/review_coevolve_pool.py`
- `scripts/run_p3b_training.py`
- `scripts/run_teacher_escalation.py`
- `scripts/run_v2_round.py`
- `scripts/run_v2_s1.py`
- `scripts/run_v2_s2.py`
- `scripts/seal_interrupted_collection.py`
- `scripts/supervise_s2_background.py`
- `scripts/train_v2_s1_short.py`

### 旧测试

- `tests/test_answer_guard.py`
- `tests/test_append_probe.py`
- `tests/test_appworld_environment.py`
- `tests/test_b10_execution.py`
- `tests/test_b10_recovery_preparation.py`
- `tests/test_base_sft_64k.py`
- `tests/test_coevolve.py`
- `tests/test_coevolve_curriculum.py`
- `tests/test_coevolve_generation_focus.py`
- `tests/test_coevolve_light_llm.py`
- `tests/test_coevolve_quality.py`
- `tests/test_coevolve_resume.py`
- `tests/test_completion_submission_v2.py`
- `tests/test_contracts.py`
- `tests/test_egtm.py`
- `tests/test_egtm_harness_29k.py`
- `tests/test_egtm_improvement.py`
- `tests/test_egtm_v2.py`
- `tests/test_egtm_v2_reporting.py`
- `tests/test_evaluation_classification.py`
- `tests/test_execution_v6.py`
- `tests/test_formal_dataset.py`
- `tests/test_fresh_teacher.py`
- `tests/test_full732_c244_parallel.py`
- `tests/test_full732_c244_serial_fallback.py`
- `tests/test_full732_stage1.py`
- `tests/test_harness.py`
- `tests/test_harness_refactor.py`
- `tests/test_harness_review.py`
- `tests/test_nullable_recovery.py`
- `tests/test_official_retry.py`
- `tests/test_p3b_training_gates.py`
- `tests/test_p3c.py`
- `tests/test_parallel_teacher.py`
- `tests/test_prompt_contracts.py`
- `tests/test_public_goal_reward.py`
- `tests/test_s2_background.py`
- `tests/test_s2_contract_repair.py`
- `tests/test_s2_evolution.py`
- `tests/test_s2_experiment.py`
- `tests/test_s2_generation_fix.py`
- `tests/test_s2_goal_integration.py`
- `tests/test_s2_judge_reference.py`
- `tests/test_s2_judge_thinking.py`
- `tests/test_s2_parallel.py`
- `tests/test_s2_review.py`
- `tests/test_s2_round_stop.py`
- `tests/test_s2_segment.py`
- `tests/test_s2_storage.py`
- `tests/test_s2_teacher_context.py`
- `tests/test_s2_training_cpu.py`
- `tests/test_schema_compatibility.py`
- `tests/test_student_append_tokens.py`
- `tests/test_student_backend.py`
- `tests/test_student_v6.py`
- `tests/test_teacher_collection.py`
- `tests/test_teacher_data.py`
- `tests/test_teacher_escalation.py`
- `tests/test_teacher_followup.py`
- `tests/test_teacher_retry.py`
- `tests/test_todoist_goal.py`
- `tests/test_tool_discovery_compact.py`
- `tests/test_tool_modes.py`
- `tests/test_unified_stage.py`
- `tests/test_unlimited_tools.py`
- `tests/test_v2_clock_handoff.py`
- `tests/test_v2_context.py`
- `tests/test_v2_finish_resume.py`
- `tests/test_v2_interrupted_finish.py`
- `tests/test_v2_learning.py`
- `tests/test_v2_resume.py`
- `tests/test_v2_s1.py`
- `tests/test_v2_s1_retrain.py`
- `tests/test_v2_s1_short.py`
- `tests/test_v2_s1_short_eval.py`
- `tests/test_validated_selection.py`

