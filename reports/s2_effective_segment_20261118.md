# Evolve 有界续跑：旧配置14次更新＋新配置新增1次（用户暂停）

2026-11-18 21:03（UTC+8）用户要求暂停GRPO、先讨论工具查找部分。监督器及所属Student、训练worker、后台Teacher已退出，GPU占用0 MiB；不自动恢复。以下保留冻结设置和实际记录，本段没有完成原定上限或段末配对dev。

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

训练截止22:23:24，绝对截止00:23:24；监督器PID 62709，Student/后台Teacher已运行，本地增量备份启动。

## 执行中记录（20:08，非最终结果）

首个新更新已提交，累计 **14＋1＝15**；实际两个optimizer parameter groups的lr均为1e-5，beta=0，`reference_adapter_loaded=false`。参与组为零基ID `[41,43]`（全局第42、44组），均为继承生成题，各自奖励 `[1,1,0,1]`。同批之前的第41、43组均为原始题、奖励全0，保留反馈而未参与优化。不是用筛选后的6/8代替全部采样成功率。

| 第一更新实际指标 | 值 |
| --- | --- |
| 正式更新耗时 | 3305.86秒（55.10分钟） |
| loss / 梯度L2范数 | -0.0000055143 / 0.0750546 |
| 概率比均值 | 0.9998383 |
| 比值超clip区间比例 / 实际clip比例 | 0.00034890 / 0.000099686 |
| 参与概率比统计的目标token | 20,063 |
| GPU allocated / reserved峰值 | 38.79 / 43.80 GiB |
| 保存重载 | 通过 |

新actor tensor SHA `114dbcb4e28a763674fcbda8d1e80055eb918edf6b90cd60f3642fbb503484ff`，optimizer SHA `47698eea5b6b79e6e2aa070fbb11f103f1c40b835746cae4ffa3fa7eca09b206`。实际梯度涉及256个可训练tensor。后续采样已切换新actor；最终候选及配对效果待段末确认。

首次正常更新前的vLLM/训练端对照通过：两条正常请求各取32个生成token，在对应同actor和同序列下重算。两段平均绝对logprob误差分别0.001548、0.000003148，最大误差0.046478、0.000050409；概率比均值1.001573、0.999998。仅证明这64个检查位置的一致性，不宣称所有长上下文位置完全一致。

该有效批8条轨迹共有261次请求，累计prompt 4,885,823 token、Assistant目标20,063 token、最长单次prompt52,276 token。用同一Qwen tokenizer对输入部分做只读统计，工具返回约71%，工具Schema约11%；API目录/详细说明/Schema合计约47%。这是跨请求重复输入量，非单条上下文或监督token量；部分统计不含模板与序列化开销。长历史与重复前缀计算是本次更新耗时的重要来源；运行日志另显示既有causal_conv1d使用PyTorch fallback，本轮未中途更换训练栈。

对两个失败成员的只读证据核对：第42组member2唯一实际下单调用返回过期卡错误、未有后续成功订单，却继续请求分摊；第44组member2批准收款但没有实际提现调用。两条业务失败判断有记录支持，没有重判或重执行来改变奖励。不能由此推断所有Judge结论均正确。

完整私有检查记录位于本地 `runs/s2_effective_segment_20261118_01/` 的 `first_update_committed.json`、`first_behavior_logprob_audit.json`、`history_token_audit.json`，以及远端同run的 `first_batch_negative_review.json`。实际暂停结果见下一节。

## 用户暂停点

从18:39:11启动至21:03:03暂停请求，墙钟约2小时23分52秒。共10个新增组、40条完整rollout，12成功、28有效失败、0无效；1487次模型请求、143220生成token。不存在尚未结束的Student组。所有成功率按过滤前完整尝试计算。

| 任务来源 | 组数 | 成功/全部尝试 | 差异组 | 已进入实际更新的组 |
| --- | ---: | ---: | ---: | ---: |
| 原始train100 | 5 | 0/20 | 0 | 0 |
| 首轮初始8条生成题 | 0 | 未采样 | 0 | 0 |
| 首轮后来生成、本段继承的任务 | 5 | 12/20 | 4 | 2 |
| 本段新生成题 | 0 | 未采样 | 0 | 0 |

新增1次更新、累计15次。第二次更新已开始约12分钟时被用户暂停中止，没有提交新权重或optimizer，不计为更新；零基组ID `[47,49]` 保留为未消费，不能用不完整梯度冒充checkpoint。最后有效模型仍是 `updates/001/attempt_1795000273069974960/actor`，对应optimizer见同目录。首次更新55.10分钟是完整提交的训练耗时，中止部分耗时另列。

一个八组反馈窗口选中4个context。暂停前已处理至第3个context：共6候选、5有Solver/Judge准入通过回执、1正在Solver时中止；第4个context未执行。5道通过题均未被Student实际采样，控制器尚未在更新后的边界导入这些回执，state仍为继承的37题池；准入原件、发布记录及中断证据保留，不能写成5道题已贡献学习。

直接读取独立用量账本（暂停时尚未回写的state.teacher_usage不作零成本）：已结算生成侧187请求、4,498,261输入/59,686输出token、1543.22秒，项目计价约$0.646108；Student Judge另20请求、818,165输入/15,743输出token、283.74秒、约$0.263024。中断Solver另有1笔未结算操作，预留与已返回原始响应保留，以上不是包含未知调用的最终账单。计价为项目估值，不等同于Go接口实际扣款。

S0与本段候选的dev24×4均未在本段启动。没有本段配对结果，不据训练成功率推断相对S0改善；历史S0/首轮结果只作历史参考。暂停后不自动执行评测或重启付费操作。下一项优先讨论工具文档重复输入与按需查找，任何新输入形式均另存协议，不中途改写本段训练序列。

暂停记录保存在远端 `user_pause_20261118.json`、`pause_summary.json` 和原state。本地 `runs/s2_effective_segment_20261118_01/pause_bundle.tar.gz` 保存完整暂停归档，包含未消费请求、原始轨迹、生成/准入证据、有效actor及optimizer、日志和冻结源码。8776文件、原始977,149,721字节、压缩444,133,908字节，逐文件SHA-256核验全部通过；归档SHA `e56b320bee044e0d9ebf3309e94331debe0ace85a4a427b4458333142ac324cf`，证明文件 `pause_manifest.json`、`pause_backup_verified.json`。私有原件不提交公开仓库。
