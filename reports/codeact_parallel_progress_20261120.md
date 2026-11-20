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

使用独立配置 `configs/codeact_grpo_coevolve_parallel_20261120.json`、338文件锁 `configs/codeact_online_parallel_20261120.lock.json`，远端新目录 `recoveragent_codeact_evolve_20261120_06`。05旧配置尾批收尾后已累计97组/24更新；切换时等待已有Teacher窗口结束，不复采旧任务，不将旧循环规则的未消费组混入新规则更新。

在新目录冻结源码内，启动/续跑命令相同：

```bash
PYTHONPATH=src /root/gpufree-data/recoveragent_s1_retrain_20261114_01/worker_env/bin/python \
  -m recoveragent.codeact.experiment run \
  --config configs/codeact_grpo_coevolve_parallel_20261120.json \
  --deployment /root/gpufree-data/recoveragent_codeact_evolve_20261120_06/deployment.json \
  --output /root/gpufree-data/recoveragent_codeact_evolve_20261120_06/experiment
```

现阶段CPU通过，GPU并发/真实组耗时在切换后补记；不将两个不同任务的耗时比写成加速比。第一版只覆盖可确认的重复错误，不是通用无进展判定；不终止正常重复查询或无法可靠追踪变量状态的代码。

## 空间

六份远端压缩包与已有本地副本逐个SHA及大小核验一致后删除，仅释放重复包 **4,795,065,658字节（4.47GiB）**；未删除任何模型、optimizer或轨迹。原计划的optimizer迁移取消，未完成临时包已清。后续大归档写入D盘，原始数据仍私有。仓库旧实现清理另按当前依赖链核验。
