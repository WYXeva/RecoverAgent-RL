# Evolve 有界续跑：旧配置14次更新＋新配置新增N次

本段从第一轮最终 actor 和对应 optimizer 继续；共同 S0 仍为9B Base-SFT（lr=5e-5，658步），不是本段训练起点。父 actor tensor SHA `809094850c7951ebdd0576ad32fab57ab875b71cef6c4d741d2dea75c0360cbe`，optimizer SHA `2729a4527b831a96a09d807e18500b14357ebace2e435f9dc4108e9945b96cd1`，进入前累计14次GRPO更新。

## 实现及冻结设置

独立配置/锁 `v2_s2_effective_segment_20261118`，复用正式 `run_v2_s2.py`、BackendRuntime、GroupEvolution、更新worker、监督器。继承原40组引用、37题生成池、队列与反馈历史，新目录保存新增轨迹和更新；不改父目录。保留共同S0身份。

- lr=1e-5，beta=0，每题4次；最多新增20次真实更新或80组/320条采样，先到即停。恢复optimizer后逐个parameter group重新写入lr，保存实际值，保留动量。
- 原更新器已有全同组过滤，本次改变的是调度：同actor积累两个完整、奖励有效且有差异的组再更新。全同组仍进入八组反馈窗口；无效组不进入优势计算。每组只消费一次，待更新请求不提前清理；40组队列边界不清缓存、不评测，段末允许一次单有效组尾批。
- beta=0跳过reference adapter加载、前向及相关hash/delete分支；损失归一化、rank、奖励保持原样。每次保存实际lr、beta、group IDs、loss/梯度、概率比/clip fraction、时间及重载回执。
- 首次正式更新前用正常rollout前两段各最多32个completion token做同actor对照：完整prompt、相同生成前缀，温度0.8/top_p=1/top_k全词表；不另采样，不截断正式训练数据。记录逐token logprob与比值；有限性及预先固定的平均绝对误差≤0.1、最大≤2检查未通过则停在更新前。
- CoEvolve使用最新 `d69df14` 的根题/父题/同源题面、四条反馈及保守去重，GENERATION提示SHA `bee167b91b35af4978a48faa80c63c220652635284547c2377e85829d25be3dd`。探索、Solver/Judge和Student设置不改。

## 时间、费用与评价

沿用11月19日00:23:24（UTC+8）绝对截止，不重新计24小时。预留末尾两小时用于配对dev，训练截止22:23:24；时间到保存最后有效actor与待消费组，不为达到上限增加预算。生成预算从原有限额度扣除已记账的979请求/241360输出token/保守$3.00188832；Student Judge技术额度最多160请求×8192输出token，费用扣除原$1.013309016，均不重置原$100分类上限。

固定种子42、142、242、342，目标S0/final各dev24×4（各96次），官方验收，报告每题成功次数及平均单次成功率，不报告pass@4。完全兼容的同机S0 seed42回执可通过模型、任务顺序、预算、推理及公开协议逐项核对后引用，保存独立复用证明。不足时间如实报告完成/缺失数，不将缺失当失败，不把不同种子直接配对。

## 本地验证

最终相关80项pytest通过（16.89秒），Ruff check/format与diff check通过。针对性调度与CPU微型随机LoRA/TRL测试验证：同actor有效组积累、全同/无效组处理、跨40组缓存、尾批与上限、断点不重复消费、实际lr恢复、beta=0不加载reference、真实小模型更新及保存重载。不是9B效果验证。

```bash
PYTHONPATH=src:. .venv-v2/bin/pytest -q \
  tests/test_s2_segment.py tests/test_s2_training_cpu.py \
  tests/test_s2_experiment.py tests/test_s2_storage.py tests/test_s2_background.py \
  tests/test_coevolve_generation_focus.py tests/test_s2_round_stop.py \
  tests/test_s2_judge_reference.py tests/test_s2_generation_fix.py
```

## 启动和续跑

使用同一私有deployment（固定parent state SHA、原截止和新run目录），原训练环境不重建。监督器自动调用正式入口，同时运行后台Teacher；不运行Static/test。

```bash
export RECOVERAGENT_S2_EXECUTION=v2_s2_effective_segment_20261118
export PYTHONPATH="$PWD/src:$PWD"
# worker_python为既有AppWorld环境；部署文件含训练/vLLM Python与模型路径。
$worker_python scripts/supervise_s2_background.py --deployment "$deployment" --output "$run"
# 已有run的手动续跑（监督器未运行时）：
$worker_python scripts/run_v2_s2.py resume --mode evolve --deployment "$deployment" --output "$run"
```

已于11月18日18:39:11在现有L40S启动，部署提交 `3fa3a30`，独立目录 `/root/gpufree-data/recoveragent_s2_effective_segment_20261118_01`。243文件源码锁、S0/父actor/optimizer与37题继承核验通过；旧池按其原绑定只读核验，公开完成/奖励/Judge身份逐项相同，不重签旧准入。两项初始预检失败（旧池执行版本不匹配、测试夹具缺队列状态）已修复后复验通过，发生在真实采样前。

训练截止22:23:24，绝对截止00:23:24；监督器PID 62709，Student/后台Teacher已运行，本地增量备份启动。真实采样、logprob对照、更新和配对dev结果在执行后追加，不将软件测试写成学习收益。
