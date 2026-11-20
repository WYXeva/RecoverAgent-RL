# CodeAct 并发采样与重复错误收敛（2026-11-20）

## 改动

沿用新 CodeAct S0、当前 actor/optimizer、四轨迹组、原始任务官方评分及生成任务 shared Judge。Student loop 新增独立 `codeact_repeated_error_v1` 身份，Teacher 不启用此规则。模型、64K、采样温度、lr1e-5/beta0、20次有效更新后dev24及19:52:38绝对截止不变。

- `experiment.sample_group` 使用 spawn 子进程池；默认配置2路，允许1–4路。每条轨迹一个新进程，共享一份 vLLM 权重；某条连同评分完成后补下一条。四条按 member ID 汇总，同 actor、同初态、不同 seed；旧回执检查、整组有效性和更新规则保留。
- vLLM `max_num_seqs` 与采样并发配置一致。每条记录进程ID、开始/结束时间，组记录墙钟时间。进程隔离避免 AppWorld 全局冻结时间、数据库缓存和 Python 变量相互污染。
- `progress.py` 只检查重复的确定性执行错误；同代码、同返回、可核验 Python 值及业务状态指纹均一致，第3次追加一次明确提醒，第5次结束。状态变化、成功返回、429、超时及未知 Python 对象不触发；基础设施错误仍无效。不会替模型改代码或自动完成业务。结束后仍交给原验收器，非自动给0。
- 全部实际回复、观察和提醒保留。控制侧状态摘要不进入模型输入。执行身份进入轨迹、行为logprob、GRPO读取及checkpoint metadata；旧/新执行配置不能静默混用。旧模型权重允许继续加载，不重训S0。

## 已完成验证

```bash
PYTHONPATH=src .venv-v2/bin/python -m pytest \
  tests/test_codeact_parallel.py tests/test_codeact_online.py tests/test_codeact_reboot.py -q
```

实际使用原工作区 `.venv-v2`：**60 passed，8.95秒**。修改文件 Ruff check/format、`git diff --check` 通过。包括真实CPU子进程重叠、最大2路/4独立PID、同actor/四seed、续跑不重复、旧回执拒绝；重复错误提醒/停止、Python及数据库进展不误停、Teacher不变；实际Qwen模板/vLLM模拟响应/行为回执读取和旧路径兼容。

另用固定 AppWorld 实际安装执行两个干净环境的CPU检查：真实文档查询、`x=7`跨轮读取、新环境没有旧变量；连续5次相同NameError触发结束。**0模型调用**，不声称真实任务成功。私有回执 `runs/codeact_evolve_20261120_06/cpu_probe/`。

## 衔接与命令

使用独立配置 `configs/codeact_grpo_coevolve_parallel_20261120.json`、338文件锁 `configs/codeact_online_parallel_20261120.lock.json`，远端新目录 `recoveragent_codeact_evolve_20261120_06`。05旧配置尾批收尾后累计97组/24更新；已有Teacher窗口完成后已切换，继承74题生成池及采样队列，不复采旧任务，不将旧循环规则的未消费组混入新规则更新。

在新目录冻结源码内，启动/续跑命令相同：

```bash
PYTHONPATH=src /root/gpufree-data/recoveragent_s1_retrain_20261114_01/worker_env/bin/python \
  -m recoveragent.codeact.experiment run \
  --config configs/codeact_grpo_coevolve_parallel_20261120.json \
  --deployment /root/gpufree-data/recoveragent_codeact_evolve_20261120_06/deployment.json \
  --output /root/gpufree-data/recoveragent_codeact_evolve_20261120_06/experiment
```

本命令已在06运行，不应重复启动控制器。当前checkout清理后采用compact锁建立新目录；正在运行的冻结源码和锁不变。

## 真实并发证据

第98组（`group_097`）从同一24步actor、同一初态、四个不同seed执行，组墙钟 **437.635秒**，奖励 **[0,0,0,1]**，全部有效：

| 尝试 | 请求数 | 目标token | 结束原因 |
|---|---:|---:|---|
| 0 | 80 | 4552 | request_limit |
| 1 | 80 | 15338 | request_limit |
| 2 | 17 | 875 | repeated_error_limit |
| 3 | 9 | 631 | task_completed，成功 |

0和1同时启动；0结束后2开始，2结束后3开始，此时1仍在运行。四个独立PID及起止时间已记录。2在同代码、同错误、同状态第三次时收到一次提醒，第五次结束。0的代码持续改变、1的可见变量/业务状态改变，按保守规则未强停；仍可能耗尽80请求，这是当前限制。

使用实际训练读取器核验186次请求、21,396个目标token：actor、初态、seed、原始token、behavior logprob、实际请求及执行身份一致。私有证据为06的 `parallel_live_evidence.json`、`real_receipt_validation.json` 和原始组目录。16:21时完成98组/24更新，首个新有效组待配对；尚未把它写成新规则下已完成的参数更新。

观测到vLLM同时执行2个请求、抢占0次，GPU占用41,039MiB（瞬时值，非峰值），未见本组OOM。缓存使用率一次读数约10%。现有实现允许1–4路，当前配置为2；这些读数支持后续四路试验，但不能保证长上下文四路不抢占或OOM。未做同批串行对照，不报告加速倍数。

第一版只覆盖可确认的重复错误，不是通用无进展判定；不终止正常重复查询或无法可靠追踪变量状态的代码。清理后完整339项CPU测试通过，具体见清理报告。

## 空间

六份远端压缩包与已有本地副本逐个SHA及大小核验一致后删除，释放 **4,795,065,658字节（4.47GiB）**。另外核验两个停用的历史Function Calling目录与本地原件后，删除27份远端重复大文件，释放860,480,724字节；共 **5.27GiB**。当前S0、actor、optimizer和CodeAct原始轨迹保留。原计划的04 optimizer迁移取消，未完成临时包已清；不与已核验的历史重复文件清理混淆。后续大归档写入D盘，原始数据仍私有，详细清单见[清理报告](codeact_cleanup_20261120.md)。
