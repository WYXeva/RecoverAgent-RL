# v2 冗余文件与磁盘清理（2026-11-09）

基线 `8da347380f29646a23f7537f3c9014e0825ad004`，分支 `codex/p0-p2-recovery`。用户先确认清理冗余文档和临时文件，随后明确扩展到不再使用的旧实现/测试，并要求给C盘腾空间。本轮只清理，不实现或启动 v2 学习运行。

## 实际删除

从当前工作树删除9份被 v2 替代的旧计划文档、20个一次性历史诊断/汇总脚本和2份只针对已删除脚本的测试，共31份受跟踪文件。以下固定提交链接保留删除前原文；不是把旧文件复制到另一个占用空间的归档目录。

- [PROJECT_PLAN.md](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/PROJECT_PLAN.md)
- [docs/P4_STAGE_GATES.md](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/P4_STAGE_GATES.md)
- [docs/P4_STAGE2_SCORING.md](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/P4_STAGE2_SCORING.md)
- [docs/P4_STAGE2B_SNAPSHOT_INTEGRITY.md](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/P4_STAGE2B_SNAPSHOT_INTEGRITY.md)
- [docs/P4_STAGE3A_BASE_SFT_READINESS.md](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/P4_STAGE3A_BASE_SFT_READINESS.md)
- [docs/P4_STAGE3B_BASE_SFT_TRAINING.md](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/P4_STAGE3B_BASE_SFT_TRAINING.md)
- [docs/P3A_HANDOFF.md](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/P3A_HANDOFF.md)
- [docs/PROMPT_FIX_PROPOSAL.md](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/PROMPT_FIX_PROPOSAL.md)
- [docs/TEACHER_EXPANSION.md](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/docs/TEACHER_EXPANSION.md)

- [scripts/audit_answer_guard.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/audit_answer_guard.py)
- [scripts/audit_harness_historical_requests.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/audit_harness_historical_requests.py)
- [scripts/audit_harness_release.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/audit_harness_release.py)
- [scripts/audit_prompt_probe.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/audit_prompt_probe.py)
- [scripts/audit_teacher_pilot.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/audit_teacher_pilot.py)
- [scripts/audit_tool_modes.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/audit_tool_modes.py)
- [scripts/check_prompt_examples.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/check_prompt_examples.py)
- [scripts/compare_predictor_prompts.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/compare_predictor_prompts.py)
- [scripts/diagnose_base_sft_failures.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/diagnose_base_sft_failures.py)
- [scripts/diagnose_p3b.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/diagnose_p3b.py)
- [scripts/download_student.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/download_student.py)
- [scripts/probe_answer_guard.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/probe_answer_guard.py)
- [scripts/probe_appworld_evaluator.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/probe_appworld_evaluator.py)
- [scripts/probe_stage2_scoring.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/probe_stage2_scoring.py)
- [scripts/probe_student_batch.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/probe_student_batch.py)
- [scripts/probe_unlimited_tools.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/probe_unlimited_tools.py)
- [scripts/summarize_full732_m1.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/summarize_full732_m1.py)
- [scripts/summarize_remote_validation.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/summarize_remote_validation.py)
- [scripts/summarize_run.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/summarize_run.py)
- [scripts/write_base_sft_report.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/scripts/write_base_sft_report.py)
- [tests/test_stage2_scoring.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/tests/test_stage2_scoring.py)
- [tests/test_remote_metrics.py](https://github.com/WYXeva/RecoverAgent-RL/blob/8da347380f29646a23f7537f3c9014e0825ad004/tests/test_remote_metrics.py)

STATUS 和 DECISIONS 的重复历史正文改成固定 Git 历史链接，保留当前摘要；README 旧入口及 retained 文档的交接引用同步修正。`project.md`、`AGENTS.md` 原文保持不变。

删除前检查了保留的 `src/`、`scripts/`、`tests/`、`configs/`、依赖文件与 pyproject：没有它们对被删脚本/测试的运行引用。历史报告中的旧路径和哈希仍表示历史提交，不改写报告成绩或证据。复现旧命令应检出报告记录的提交；恢复本次删除前某文件也可使用 `git restore --source=8da347380f29646a23f7537f3c9014e0825ad004 -- <文件路径>`。

## 磁盘空间

删除163个本地缓存文件，合计逻辑大小 **8,268,914,399 bytes**，文件分配大小 **8,269,303,808 bytes（约7.70 GiB）**：

- 已废弃的公开Qwen3-1.7B基座下载缓存，固定revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`；它不是训练产生的checkpoint，需要历史基座时可按该revision重新下载。
- 两个从2026-10-23留下的Qwen3-4B未完成下载，分别2,034,276,941和2,151,954,831 bytes；没有snapshot链接或打开的文件句柄，删除不影响已完成的4B分词器缓存。
- pytest、Ruff和项目源码/脚本/测试的Python字节码缓存。

删除前后立即采样：WSL项目文件系统可用空间从 **949,789,843,456** 增加到 **958,059,831,296 bytes**（约7.70 GiB）；同期Windows C盘可用空间为 **4,652,638,208 → 4,651,921,408 bytes**。两侧数字不能混用，宿主其他活动也会改变C盘计数。本轮没有观察到对应7.70 GiB已经返还C盘，不能声称C盘已释放同等空间。

C盘挂载在当前会话为只读；尝试调用Windows只读管理命令时，WSL返回 `UtilBindVsockAnyPort: socket failed 1`。没有请求额外权限、关闭WSL或执行磁盘压缩。已找到 `C:\Users\yanzu\AppData\Local\wsl\{2e75ad39-b1f7-4c51-a416-3492ee2161ea}\ext4.vhdx`，其发行版映射尚未通过Windows注册表核验。

## 保留与验证

全部 `src/recoveragent/`、配置/旧锁/依赖、已有公开报告、私有训练数据和真实轨迹/快照、旧M1及其他训练checkpoint均保留。`.venv-inference` 虽约7.1 GiB，仍被现有数据过滤/分词流程使用，未当作无用环境删除；其他环境、4B分词器缓存也保留。没有改Actor提示、Schema、安全校验或模型权重。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:.:tests .venv/bin/pytest -q -p no:cacheprovider
.venv/bin/ruff check --no-cache src scripts tests
.venv/bin/ruff format --check --no-cache src scripts tests
```

结果：**743 passed，3条固定上游Pydantic弃用警告**；Ruff check和162文件format check通过。相比原752项，移除的9项属于已删除的一次性评分/汇总脚本，未为消除失败而删除测试。B10现有执行锁再次校验通过：`18941a89eb0bdcbfe34e0069a1fd186a1fff89b46f095532c27745261e683cde`；旧生产锁没有重签。软件回归不等于新模型或真实任务执行。

## Windows侧尚需完成的空间回收

动态VHD在内部删除文件后未必自动缩小，`compact vdisk` 用于回收物理文件空间，要求目标VHD已分离或只读挂载。参见[Microsoft compact vdisk说明](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/compact-vdisk)及[WSL磁盘管理/定位路径说明](https://learn.microsoft.com/en-us/windows/wsl/disk-space)。以下命令**未执行**，须在Windows侧完成，并先保存/退出WSL中的任务。

1. Windows PowerShell中核对发行版及VHD路径（不要凭文件名猜测目标）：

```powershell
wsl --list --verbose
Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss | ForEach-Object {
    Get-ItemProperty $_.PSPath | Select-Object DistributionName, BasePath
}
```

2. 确认上述VHD属于要清理的发行版后，在管理员终端关闭WSL，并进入DiskPart：

```powershell
wsl --shutdown
diskpart
```

3. 在 `DISKPART>` 中选择已确认的路径；若注册表路径不同，替换为实际路径：

```text
select vdisk file="C:\Users\yanzu\AppData\Local\wsl\{2e75ad39-b1f7-4c51-a416-3492ee2161ea}\ext4.vhdx"
attach vdisk readonly
compact vdisk
detach vdisk
exit
```

压缩后重新检查C盘实际可用空间。本报告区分“项目内部清理完成”和“Windows空间回收尚未执行”；不把未运行的宿主机操作写成通过。下一项项目实现仍是 `project.md` 第8节交付1，本轮不开始它。
