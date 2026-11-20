# CodeAct：每20次有效更新定义一个训练轮次

2026-11-20，基线 `9d62f91`，分支 `reboot/codeact`。用户最新要求替代每40采样组评测的节奏；不改变模型、学习率、损失、采样规则或已有预算。

## 已实施

- `codeact/experiment.py`：新增可选 `online.round_evaluation.every_optimizer_updates=20`。在累计20/40/60/80/100次成功optimizer更新后评测；已有18次计入第一轮，不从18重新加20。全同奖励、评分无效、OOM跳过不增加更新数。
- 原每40采样组的队列窗口保留，只负责原始/生成槽交替和生成题限频；窗口结束不再触发评测或强制更新单组尾批。真正到达采样上限时仍可更新最后一个有效组；若该更新恰好达20的倍数且尚有时间，执行相应dev24。
- 新评测用 `evaluation_basis=optimizer_updates`、`target_updates` 和独立 `evaluations/updates_000020` 等目录；旧评价视作 `sampled_groups`，不能因旧 `round=1/2` 而跳过新20/40更新评测。已完成的同协议里程碑续跑不重评。
- 新配置 `codeact_grpo_coevolve_updates20_20261120.json` 只比前版增加上述间隔；独立源码锁 `codeact_online_updates20_20261120.lock.json`，旧锁/源码/回执均不修改。

目标仍为5个训练轮次，原200个采样组、100次更新、Teacher调用和费用上限及11月20日19:52:38截止全部保留。若先到采样/时间上限，则不足5轮也正常保存，不为凑更新自动扩预算。

## 题目如何复用

复用未修改的 `s2_schedule.TaskQueue`：

- 原始100题为跨窗口连续打乱队列；100题覆盖完后重新打乱，循环使用。
- 生成题优先选此前未采样的题；同一40采样组窗口最多选一次，下个窗口仍可再次选择。生成题不足时用原题补位，生成题不超过槽位的一半。
- 每次选择同题都用当时的actor、干净初态、新采样种子重新执行4次，不复用旧轨迹或旧behavior logprob。

切换时80组由45个原题组和35个生成题组组成，原题尚未覆盖完100题；35个生成题组均为不同生成题。允许循环不等于目前已经循环多次。第80组反馈的8个新候选已全部准入，池共60题。

## 真实接续

远端05目录 `/root/gpufree-data/recoveragent_codeact_evolve_20261120_05/` 已启动第81个采样组。保留并继承04的80组、18更新、最新actor/optimizer、池、反馈及队列，队列导出状态核验未改变；不回退到S0，不重新采样旧组。

旧dev24评测已完整完成并保留：

| 旧触发点 | 有效更新数 | 成功/失败/未知 |
|---|---:|---|
| 40采样组 | 7 | 9/15/0 |
| 80采样组 | 18 | 9/15/0 |

两份均9/24，共同CodeAct S0为8/24。它们不被重命名为本次20更新轮次的结果。新规则的第20次更新及对应dev24尚未实际发生；调度CPU测试不能当作真实评测成绩。

远端剩余空间约7GiB，退出备份改为直接流式传输并对SHA-256，不再在远端另存整个实验的大压缩副本；原模型、optimizer和原始轨迹未删除。进度同步进程已切换到05。

## 验证

```bash
PYTHONPATH=src /home/eva/project/RecoverAgent-RL/.venv-v2/bin/python -m pytest \
  tests/test_codeact_online.py -q
PYTHONPATH=src /home/eva/project/RecoverAgent-RL/.venv-v2/bin/python -m pytest \
  tests/test_s2_experiment.py::test_cross_round_queue_and_generated_frequency \
  tests/test_s2_experiment.py::test_priority_new_task_live_admission_and_resume_rng \
  tests/test_s2_experiment.py::test_empty_pool_and_fifty_percent_ceiling -q
```

实际21＋3＝**24项通过**。覆盖新更新轮次、旧采样组模式兼容、OOM不计数、窗口不触发单组更新、末尾尾批达里程碑、继承actor、同配置续跑不重复评价、旧评价不抑制新里程碑、任务循环/限频/补位/RNG续跑。Ruff check、format check及git diff --check通过。新锁336文件校验通过，历史锁未重签。未运行完整pytest或额外模型实验。

准确启动/续跑命令见README“当前05入口”；只在现有授权期内续跑，不同时启动第二个controller。
