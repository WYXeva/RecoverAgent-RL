# v2 第一轮学习与 CoEvolve AppWorld 接入

更新：2026-11-10。**S0、S1、S2均已真实更新、保存重载并完成固定dev24×3评价；本轮没有观察到成功率提升：2/24 → 1/24 → 1/24。** GRPO第三次更新因长上下文OOM提前结束，保留最后两次完整更新，不声称完成16组。此前逐时记录保留在[8bef14a版本](https://github.com/WYXeva/RecoverAgent-RL/blob/8bef14a78dc1822ee0e5384036f31142a79b749c/reports/v2_round1_learning_20261109.md)。

## 实验范围与结果

Student固定`Qwen/Qwen3.5-9B`，revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`；non-thinking原生模板SHA `a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715`。BF16+LoRA rank16，仅使用已授权32GB实例，没有更换模型、租机或续费。

Full732场景隔离自定义划分为170/37/37个train/dev/test场景，510/111/111个任务，seed20261109；清单SHA `ff24d1c05f16e82795e0baa4dd855c03d8482bb32d6e2e07ce386a9af59bfe6b`。保留官方来源split；这些不是官方排行榜成绩。dev/test内容和轨迹不进入本轮训练、恢复或任务生成；历史项目已接触Full732，不能声称完全未见测试。

| 阶段 | 实际参数更新 | 固定dev24成功 | 状态 |
| --- | ---: | ---: | --- |
| S0_base | 181 optimizer steps，1 epoch | 2/24（8.33%） | 训练、重载和备份通过 |
| S1_recovery | 从S0继续8 steps，1 epoch | 1/24（4.17%） | 训练、重载和备份通过 |
| S2_grpo | 从S1继续2 steps | 1/24（4.17%） | OOM后验收最后完整更新，重载和备份通过 |

三阶段评价均从原任务干净初态执行，同execution_v6、nullable v2、动态工具发现、seed42、temperature0.8/top_p1；80请求/16384累计生成/4096单回复/32768上下文/1200秒，15次通用纠错。关闭训练快照；未知评价不当作自然失败，也不以训练loss代替验证成绩。

## 同条件评价与主要失败

72次原任务官方评价全部有效，0 UNKNOWN/基础设施异常。逐任务核对三个模型的实际初态SHA、首次输入token IDs、首次工具Schema、所有episode预算和加载策略身份；24项均一致。S0→S1新增成功0项、丢失成功1项；S1→S2的24项成功/失败结果完全相同。共同条件manifest SHA `5c9dc94dabcb1a67a3d340f540ebd9dbedfaad05d92d4c9aaae95f65a017b275`。

| 失败终止类型 | S0 | S1 | S2 |
| --- | ---: | ---: | ---: |
| 32k上下文不足 | 10 | 15 | 12 |
| 宣布完成但未通过官方验收 | 11 | 8 | 11 |
| 请求预算耗尽 | 1 | 0 | 0 |
| 未知/基础设施异常 | 0 | 0 | 0 |

这些是可核验的终止类型，不把它们直接等同于全部业务根因。失败轨迹中的API文档查询分别194/223/199次，完全相同的工具名和参数重复调用分别111/46/37次；重复查询可能有合法复核用途，未统一判定为无效调用。长历史与任务未完成仍是主要现象。

| 阶段 | 请求数 | 输入token累计 | 生成token | episode墙钟秒 | 模型生成秒 | 工具执行秒 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| S0_base | 874 | 15218045 | 73505 | 8885.82 | 7627.81 | 46.34 |
| S1_recovery | 770 | 13827552 | 74788 | 8635.78 | 7369.61 | 46.83 |
| S2_grpo | 649 | 10653067 | 63223 | 7231.38 | 5979.95 | 43.52 |

三阶段合计2293请求、39698664输入token、211516生成token、24752.97秒episode墙钟。模型生成占20977.36秒，工具执行136.70秒；其余包括分词、worker启动和官方评分。S2调用和耗时下降，但成功数保持1/24，不能由此认定复杂任务能力改善。

## S0：共同起点

按新train场景筛选已有Teacher原始成功：107条来源中95合格、12超长暂缓；这里的95不是旧历史95条数据包。重新使用9B tokenizer/template编码，1443样本、254396监督token，完整输入和Assistant目标不截断。

短烟测2步/4样本、最长完整32666-token样本1步均通过后，正式181步，14934.17秒；训练loss0.09527896，allocated峰值30.10 GiB。训练loss不是开发成绩。数据SHA `561041f89875778d50a3e7c13e822c404deba8668165c649811d83c393d4da24`；原Teacher manifest `96265f67a5828b101f5db32a804008478d7c419a01e079f6e468b8e5f164735f`。

## 真实反馈与 Recovery-SFT

固定前15个train任务：3官方成功、11有效自然失败、1评价UNKNOWN。11次Teacher接管保留真实失败状态、同刻历史和可见工具，按原任务起点评分；6官方成功，其中4条后缀合格、2条超长deferred，5次失败。合格后缀共40样本/5930监督token，旧Student前缀、工具输出和控制信息不监督。

S1从S0的可训练adapter继续：40恢复样本+19正常成功样本，共59样本/11739监督token；恢复/正常监督token为5930/5809，按50/50权重混合。8步、998.67秒，全部有限梯度，allocated峰值约29.96 GiB；保存后LoRA哈希和探针logits精确重载。数据SHA `50236a3482cf6b7026a9a739e7e8172659f5be1096940fa482f18289f56d549e`。

用户取消的第16项保留439已知生成token和4096未知预留，不纳入15项结果或恢复训练；不重跑。旧固定Gmail生成器10次实际探索均未接纳，原件和费用保留。

## CoEvolve 方法与本轮新任务

[CoEvolve论文](https://arxiv.org/html/2604.15840v1)和[官方仓库](https://github.com/StoneHanaMori/CoEvolve)固定commit `d4560239c80ce327808c0fd2a0d3a2c106f17e63`：仓库当时只有README、许可证和图片，没有可执行核心。实际复用[AgentEvolver](https://github.com/modelscope/AgentEvolver)固定commit `a5a8db8689d6493b028107dcb4c27415441581dc`的Apache-2.0任务抽象提示，原文、许可证和来源SHA在`third_party/agentevolver/`。因此交付的是AppWorld方法链路的单卡适配实现，不是运行CoEvolve官方完整源码或复现论文规模/成绩。

实现初始非引导探索、能力边界/稀有动作/遗忘信号、反馈上下文、多分支真实探索、动作证据抽象、独立Solver、语义与正负程序验收，以及GRPO期间再次反馈并入池。本轮真实触发了边界和稀有信号；不把仅有软件测试的遗忘逻辑写成已观察到遗忘。详见[方法与差异](../docs/COEVOLVE_APPWORLD_METHOD.md)。

| 新方法产物 | 实际数量 |
| --- | ---: |
| 候选提案 | 8 |
| 独立Solver及4类正负程序检查通过 | 7 |
| 最终合格新目标 | 4（1初始探索、3反馈引导） |
| 输出契约不能可靠识别，暂缓 | 2 |
| 仅改措辞/格式，目标重复 | 1 |
| 缺少3个真实成功动作引用，拒绝 | 1 |
| 实际进入Student GRPO采样 | 2 |
| 贡献非零优势及真实参数更新 | 2（其中1条来自S0真实反馈） |

一次反馈上下文输出不合约被拒绝，不自动重试。新颖性检查复用独立Teacher，对照原始公开train目标及同来源已接纳目标，不读取隐藏答案。程序验收绑定可重放原初态、真实Solver参考业务终态和公开答案契约；不执行、漏完成、错误答案必须失败，正解必须通过。严格参考终态可能误拒其他合法解；该oracle以终态和答案条件为核心，不声称对任意自然语言过程约束完备，新颖性判定也不是形式证明。

发现Teacher字符串"3"与Student数字3的误拒绝后，原round_011在0更新处停止；只读导入原终态确认业务SHA一致，在独立revision下重验3任务/12项正负检查并重评分2条已有轨迹。保留原错误判分，0模型重跑；不改目标、期望状态或历史数据。后续发现两种未识别计数问法，明确暂缓并在采样前保护；实际使用的两条均通过独立质量审查。只读审查输出`quality_review_001/qualified_pool.json`，原池保持不变。

## S2：实际 GRPO 更新与中断

真实当前策略32条/8组：10成功、22自然失败、0未知。前6组形成3个update batch，2组有奖励差异、产生2次optimizer更新；另一个batch为全成功/全失败组，不伪造更新。全部8组中3组有奖励差异（37.5%，均为2成功/2失败），另1组全成功、4组全失败。两个生成任务组都有差异；6个原始任务组仅1组有差异。最后2组已采样，第三次更新在反向传播时OOM，未执行optimizer.step，不计训练完成。

两次更新loss分别`-8.157978300005198e-05`、`3.510737878158307e-05`，有限梯度L2分别`0.04845981504593288`、`0.07693566241293463`。生成任务`coevolve_d2a9cab7bd1db4158115fac3`和`coevolve_623bedfd0698a4e062dcd032`分别贡献非零优势；第一步256个LoRA tensor均变化，后续真实采样使用更新后的策略哈希。

复用TRL clipped GRPO loss，learning rate1e-6、beta0.001、固定S1参考adapter、同组同任务/初态/策略及真实行为logprob。每次真实请求单独编码，动态Schema不强拼为一个序列；只计算生成Assistant位置。词表概率分块与CPU activation offload不截断token。单卡LoRA、4轨迹/组、每轨迹token归一化及少量更新均与论文大规模配置不同。

OOM后停止剩余GRPO，不重采、不跳过长样本继续凑组。`finish_v2_interrupted.py`以原源码锁验证update_02与随后真实采样策略完全一致，原样保存为S2并重载；不使用dev选择checkpoint。原中间保存没有Adam状态，无法精确续训；OOM前未写最终冻结参考adapter哈希及正式峰值统计，二者如实缺失，不补造。OOM日志显示申请1.49 GiB时仅剩1.47 GiB。

## 成本与预算

- 反馈15项：427请求，40183生成token，墙钟5166.96秒；其中模型3795.15秒、环境20.65秒，其余包含快照与进程处理。
- Teacher恢复11次：107请求、56891生成token，1485.19秒，按usage估价2.56253576元。
- CoEvolve探索/Solver共15条真实轨迹、169请求、97359生成token；另15次文本调用5770生成token、5次质量审查。新方法估价3.54881040元。
- GRPO采样32条：870请求、88723生成token，墙钟10074.56秒；模型8041.51秒、环境54.90秒。round_012包含采样、更新和演化的总时长17197.63秒。
- 训练侧累计已知生成129345token，另保留4096未知预留；没有用满20万token，停止原因是OOM。
- Teacher全轮含旧失败生成、恢复、CoEvolve及质量审查累计usage估价**8.85577808元**；不是账户扣费账单。开发评估成本见上表；估价不含此前Base-SFT原始示范的历史采集费用，也不是GPU租赁账单。
- GPU绝对截止为2026-11-10 14:12:31（UTC+8），含此前全部用时，未因接续重置。

## 私有 checkpoint 与复现入口

远端根目录`/root/autodl-tmp/recoveragent_v2_20261109/`；本地私有备份根目录`runs/v2_round1_20261109/`。

| 阶段 | 远端相对路径 | LoRA tensor SHA-256 |
| --- | --- | --- |
| S0 | `runs/round_004/S0_base` | `b4b16e43f10d280722ce5544277858b94bcfbc0aa78de6817790bda8e203d235` |
| S1 | `runs/round_008/S1_recovery` | `3daf5effa13ed2bcf939861d90112659da62c751996afd932ea10b392d6a496f` |
| S2 | `runs/round_013/S2_grpo` | `6b3b93bf917b3da22314d47221f8434f07b60c50bbcb5b632e79f9a0aad74f07` |

私有压缩包均已逐文件核验：S0 `round004_S0_base.tar.gz`（376文件），S1 `round008_S1_recovery.tar.gz`（30文件），S2 `round013_S2_grpo.tar.gz`（8文件）。S2归档SHA `8f1eda0bd96ffd8b00f8be4d7d1723625f9df53b6d98d628287d41aa7a5171a4`，111782169字节。

使用锁定环境和同一模型revision加载base，再用`PeftModel.from_pretrained(base, checkpoint_path, is_trainable=False)`加载对应adapter；先运行`verify_checkpoint(checkpoint_path, identity)`核验模型、所有文件SHA和重载回执。不要直接将adapter目录当作完整9B基座。

新完整运行入口：`PYTHONPATH=src python scripts/run_v2_round.py run --input <私有部署JSON> --data <重新编码数据目录> --output <新目录>`；当前默认质量准入配置为`configs/v2_round1_coevolve_quality_20261109.json`。历史真实GRPO执行源码为`febe0cf`，lock SHA `12c13adb21ea0a3d4b71090ca14aaeef7b6905141e22792b5b274ee3bbc00dc7`；独立质量审查源码`0cc8fff`。收尾入口commit`8bef14a`配合原febe0cf的PYTHONPATH运行：

```bash
PYTHONPATH=<原febe0cf源码>/src python scripts/finish_v2_interrupted.py \
  --input <包含原run/部署/S0/S1/新输出路径的私有JSON> \
  --config configs/v2_round1_finish_interrupted_20261110.json
```

所有接续复用同一绝对截止，原运行和旧锁不改写。`round_012`保持OOM原始摘要，`round_013`记录S2验收与独立评价；不得把前者改成正常结束。

## 软件验收与结论边界

`PYTHONPATH=.:src .venv/bin/python -m pytest -q`：872 passed、1 skipped、3条上游弃用警告；新增中断收尾9项测试通过，质量准入83项针对性测试通过。先前独立CPU随机小模型3项验证了SFT/GRPO参数更新、参考KL及分块概率等价；本次未修改这些训练公式。Ruff check和format check通过（193文件）。

真实AppWorld反馈、Teacher恢复、新任务解答与正负验证、Student GRPO采样、S0/S1/S2参数更新及全部72次评价均有独立记录。三个checkpoint的保存前后LoRA哈希一致，重载探针logits最大差为0。软件契约与实际更新通过；本轮开发成功率未提升，Recovery-SFT少成功1项，GRPO没有新增成功。

本轮只有4条合格恢复后缀、2次GRPO更新，dev24来自8个场景，且没有Static对照、生成器消融或多种子实验。不能将这份结果归因为Teacher数据质量、CoEvolve生成机制或方法普遍有效/无效。

**下一轮优先修复长样本GRPO反向的显存峰值**，让已有合法、奖励有差异的组能完成计划内更新，并在每次完整更新后保存优化器状态以支持精确续训。32k上下文耗尽仍需作为执行侧失败单列。本轮不继续实现或运行下一轮。

## 最终归档与停止

`round_013/round_summary.json`状态为`evaluated_three_stages_after_interrupted_grpo`，SHA `6a8652b9ea4a9ece60cfeefc8b758edcc27f3b008dfdf90bef3dc0de92e1b11b`。`round_012`的OOM事实及未应用的最后2组保留。

- `round013_records.tar.gz`：122319076字节，SHA `ae6fcf0fd329f01b081b9cb250ad5a12d163ba3c9109204f8329b99f9ff3cea5`。已核验72份结果、2293对请求/回复及最终摘要；权重另存于三个完整checkpoint归档。
- `round013_external_terminals.tar.gz`：276097字节，SHA `f90a0078015a439b11da75140f0634f6c03357dcb82bd8d28a30c505dabae260`。含72次评价和原2条重评分轨迹的74份实际终态，962个文件逐项SHA通过。
- `quality_review001.tar.gz`：20512字节，SHA `b0ef0d08a752f009aeeb74994a08c497e0d91412e2bb9f05a23c3a3b84fc22a5`；合格任务可从`quality_review_001/qualified_pool.json`加载。
- 独立最终核验为私有`final_verification013.json`、`final_backup_verification013.json`；此前反馈、恢复、生成、训练和中断产物归档均保留。

2026-11-10 12:05（UTC+8）在备份核验和GPU进程退出后执行平台关机命令，命令返回0，SSH随后被远端关闭。关机前本轮作业累计约21.88小时，早于原24小时截止；未读取平台扣费账单。未启动第二轮、追加训练或Teacher请求。

运行中用户授权清理旧4B模型：核验固定revision及当前9B缓存路径后，仅删除远端旧`models--Qwen--Qwen3-4B`基座缓存，释放8060919808字节（约7.5 GiB）；数据盘使用率92%降至77%，可用约11.5 GiB。三个9B checkpoint回执SHA前后不变，评估未中断；共享Python环境、AppWorld、旧原始记录和adapter保留。私有审计已回传至本地备份根目录的`disk_cleanup_20261110_result.json`。
