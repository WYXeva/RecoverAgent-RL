# CodeAct GRPO：原 OOM 批次显存复测

2026-11-20，分支 `reboot/codeact`，基线 `bb21696`。本次为已有五轮实验的训练分配器调整，未改学习配方、公开协议、任务池或评分。

## 实际修改

- `codeact/experiment.py`：新配置只对 update 子进程设置 `PYTORCH_ALLOC_CONF=expandable_segments:True`；移除该子进程中冲突的旧变量别名。直接 update CLI 在导入Torch/初始化CUDA前使用同一函数。采样、Teacher和Judge不改设置。OOM回执增加完整异常堆栈，仍跳过整批并保留原actor/optimizer。
- `codeact/online.py`、`v2_grpo.py`：记录每个训练片段的前向/反向阶段、完整序列和目标数、allocated/reserved/peak/free显存及耗时；更新回执保存分配器配置、实际环境和backend。观测不改变损失、优势、目标分母或参数更新。
- 独立配置 `codeact_grpo_coevolve_memory_20261120.json` 只比上一配置增加 `cuda_allocator` 和 `record_memory` 两项；新锁 `codeact_online_memory_20261120.lock.json`。历史配置、源码快照及锁不覆写。

## 同批真实 GPU 结果

原02 `attempt_001` 使用原第12、14组（0-based ID 11/13）两组有效生成任务。报错时进程已占43.95 GiB，申请2.79 GiB失败；其中最长序列60,866 token。旧异常未保留堆栈，不能倒推具体失败算子。

03诊断复用完全相同的8条轨迹、原behavior logprob、对应01 `attempt_000` actor/optimizer（当时累计1次更新）。保留BF16、LoRA、64K、lr1e-5、beta0、CPU activation offload、gradient checkpointing、词表分块和原合并上限。

| 项目 | 本次真实复测 |
|---|---:|
| 实际请求回复数 → 合并训练片段 | 393 → 9 |
| 重复前缀工作量 → 实际累计序列token | 5,647,881 → 210,239 |
| 最长完整训练片段 | 60,866 token |
| Assistant目标token | 36,188，全部计算 |
| 独立诊断optimizer更新 | 1 |
| 计算耗时 | 147.67秒 |
| 含加载、保存、重载的诊断worker总时间 | 约204秒 |
| GPU峰值allocated / reserved | 42.17 / 43.33 GiB |
| loss / 梯度L2 norm | -0.000145998 / 0.190931 |
| probability ratio均值 / clip fraction | 1.00000055 / 0.00016580 |
| 保存重载 | 通过 |

实际分配器回执为 `native` backend、`PYTORCH_ALLOC_CONF=expandable_segments:True`，旧变量别名为空。
最长片段前向12.29秒，反向27.75秒，峰值在反向升至42.17 GiB；后续片段全部通过。参数SHA由 `8d7beeec…99865b` 变为 `8e551d6d…65ae4`，重载通过。

这证明原失败批次现在可在同一L40S完成；支持分配/碎片是重要因素，但不是开关分配器的多次严格A/B实验，也不保证所有64K轨迹都能通过。没有截断、删除目标、改奖励或换80GB卡。token工作量下降是合并路径的统计，不当作本次实测速度提升；旧批次中途失败，没有完整耗时可算加速比。

诊断没有新Student采样或Teacher请求，仍计入原GPU墙钟上限。私有原件在 `recoveragent_codeact_evolve_20261120_03/oom_retest`；诊断actor/optimizer单独保留，**不并入主实验，也不增加主实验更新数**。

## 主实验接续

02在主第23组完整结束后停止调度；累计2次有效更新（第二次已实际训练两道T恤生成题），失败的OOM尝试仍保留为跳过。04从02最新有效actor及optimizer接续，继承23组、池、反馈、队列和同一Teacher账本。没有将旧策略的诊断批次重新加入当前训练。

当前目录 `/root/gpufree-data/recoveragent_codeact_evolve_20261120_04/`。仍为五轮每轮40组后dev24，原截止 **11月20日19:52:38** 不变。新配置下的后续正式更新及dev尚待运行验证；本次没有新增能力提升结论。

03诊断使用先冻结的分配器/观测实现；04使用最终整理后的源码锁（补齐观测回调变量绑定和导入格式，数值路径不变）。两份源码锁原件分别保留。诊断回执归档58,600字节，已下载并核验SHA `5aa48ecad4cf6fc0bde2f739646945085c5ee1a890e7be1ba60d9eced99b22e4`。04进度同步及退出后的归档回传已启用。

## 本地验证与命令

```bash
PYTHONPATH=src /home/eva/project/RecoverAgent-RL/.venv-v2/bin/python -m pytest \
  tests/test_codeact_online.py tests/test_grpo_efficiency.py -q \
  -k 'not efficiency_release_lock_and_historical_refusal'
```

**24 passed，1 deselected**。覆盖训练专用分配器、直接/子进程入口一致、仅两项配置变化、前向/反向异常定位且不更新、真实tiny-model合并/逐回复loss与裁剪前梯度等价、现有调度/续跑和新锁/旧锁拒绝。

首次未排除运行得到21通过、1历史锁测试失败；该测试要求旧FC锁匹配当前CodeAct源码，历史锁应拒绝当前修改，故保留原锁并排除该不适用测试。最终新增的两项故障观测测试及配置测试均通过。Ruff check、format check、git diff --check通过；未宣称完整pytest通过。

一次独立同批复测复用已有update入口，输出目录必须全新，且不把结果写入在线state：

```bash
# 在04冻结源码目录，沿用README中的HF环境；03请求绑定原失败批次的actor/optimizer。
export PYTHONPATH=/root/gpufree-data/recoveragent_append_probe_20261118_01/kernels:$PWD/src
/root/gpufree-data/recoveragent_s2_20261116_01/training_env/bin/python \
  -m recoveragent.codeact.experiment update \
  --config configs/codeact_grpo_coevolve_memory_20261120.json \
  --deployment /root/gpufree-data/recoveragent_codeact_evolve_20261120_04/deployment.json \
  --request /root/gpufree-data/recoveragent_codeact_evolve_20261120_03/oom_retest.request.json \
  --output /root/gpufree-data/recoveragent_codeact_evolve_20261120_04/diagnostic_new_run
```

以上仅供复现，本次已完成，不再自动重复诊断。在线训练启动/同授权期续跑见README的04入口；继续观察后续长批显存与轮末dev结果。
