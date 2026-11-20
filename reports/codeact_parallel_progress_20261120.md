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

## 四路试跑（16:41完成首组）

用户授权后只将并发2改为4，新增 `codeact_grpo_coevolve_parallel4_20261120.json` 和297文件 `codeact_online_parallel4_20261120.lock.json`，实现提交 `af000ad`。模型、采样分布、重复错误规则、训练参数、Teacher/Judge、任务队列、原19:52:38截止不变。CPU并发测试同时覆盖2/4路真实spawn、4独立PID、同策略/初态、四seed及缓存续读：**13 passed，1.96秒**，Ruff check/format通过。

07在完整组边界继承06的102组、25更新、74题生成池，以及未消费的零基组ID `[100,101]`。两组的actor/执行身份相同，继续按原规则消费，未强制单组尾批、未丢弃采样。继承optimizer SHA `c60dae43d75b8e815c295cf0c164043c40487629ebf8c1689f770662543f6de0`，队列核验未变。

这两组真实完成第26次更新：训练74.27秒、实际lr为两参数组均1e-5、beta0、梯度L2约0.18595、重载通过。156个回复合并9段，9,743目标token全部计算。它们是先前双路采样，不写成四路产生的数据。

随后使用第26步actor，首个四路组（总第103组，原始train任务 `21abae1_2`）从同一干净初态开始，四个PID起始相差不足2毫秒。**整组108.644秒，官方奖励[0,1,0,1]，四条有效**：

| 尝试 | 请求数 | 目标token | 结束与评分 |
|---|---:|---:|---|
| 0 | 22 | 2733 | 提交完成，但官方业务验收失败 |
| 1 | 14 | 1318 | 提交完成，官方成功 |
| 2 | 80 | 3197 | 请求预算耗尽，官方失败 |
| 3 | 12 | 1109 | 提交完成，官方成功 |

实际GRPO读取器核验128份请求、8,357个目标token及其behavior logprob通过；同actor、初态、四seed及执行身份全部一致。5秒间隔测量中最大运行请求数4、等待请求0、缓存抢占0；KV缓存最高约5.30%，显存采样最高41,077MiB（约40.11GiB，非连续采样/框架峰值）。未见本组OOM或基础设施无效奖励。未做4条同时达到64K的压力试验，也未重采同任务作双路对照，不报告速度倍数。

16:41完成103组/26更新，第103组作为有效组等待配对，四路继续后续组；不是已经完成五轮。私有回执位于本地 `runs/codeact_evolve_20261120_07/` 及远端同名运行目录，包括handoff、真实行为读取核验、逐5秒容量测量。退出后的归档监护已切到07，覆盖01–07原件并流式写D盘；原预算不延长。

当前07冻结源码中的启动/续跑命令（已运行，不重复启动）：

```bash
PYTHONPATH=src /root/gpufree-data/recoveragent_s1_retrain_20261114_01/worker_env/bin/python \
  -m recoveragent.codeact.experiment run \
  --config configs/codeact_grpo_coevolve_parallel4_20261120.json \
  --deployment /root/gpufree-data/recoveragent_codeact_evolve_20261120_07/deployment.json \
  --output /root/gpufree-data/recoveragent_codeact_evolve_20261120_07/experiment
```

## 每题独立4→2降级

用户随后要求每题先4路，容量故障时仅本题降至2路，下一题恢复4路。新增配置
`codeact_grpo_coevolve_adaptive4_20261120.json` 与299文件独立源码锁；历史配置/锁不修改。
代码提交 `c3dc9c3`、`6b2e7dc`。

- 新采样路径在 `experiment.resilient_group` / `sampling.sample_with_fallback`。4路正常结果留在 `primary/`；只对OOM、vLLM连接/服务故障、进程崩溃等采样基础设施故障补跑，正常业务失败、格式预算耗尽、Judge故障及官方评分异常均不触发重采。
- 失败阶段原件保留；受影响成员在 `retry_2/` 使用同actor、同seed、同任务干净初态补跑一次，不声称恢复中断的Python解释器。已经完成的有效成功/失败保留，不通过多次重试选择好结果。
- 降级会同步重建2路vLLM容量；下一题重新请求4路服务，没有全局永久降级。2路仍失败时记录无效奖励、整组不更新/不作为all_failed或forgetting；其他任务继续。未知契约/配置错误不借降并发绕过检查。
- 预先保存phase intent、retry plan及actor/任务/config绑定；中断续跑不增加第三次干净尝试。进程池崩溃后先停止其遗留子进程，再读取原件；截止时间阻止新增补跑。
- 汇总继续以四条最终有效性判定组奖励，另外记录补跑次数和被替换的基础设施失败记录，其已知token/请求计入总成本。崩溃导致用量不完整时明确标记unknown，不假定零成本。
- 切换时可按PID生命周期与实际命令身份接管现有Teacher worker，继续等待它原输出目录的结果，不重新发起同一生成批次。Teacher提示、配置和评分逻辑不变。

实际本地命令：

```bash
PYTHONPATH=src /home/eva/project/RecoverAgent-RL/.venv-v2/bin/python -m pytest \
  tests/test_codeact_sampling_fallback.py tests/test_codeact_parallel.py \
  tests/test_codeact_online.py tests/test_codeact_schedule.py tests/test_codeact_reboot.py -q
```

**89 passed，9.73秒**，修改范围Ruff check/format和diff通过。包含实际CPU子进程退出引起的BrokenProcessPool、模拟容量故障4→2、有效结果保留、同协议续跑不重复、下一题恢复4、成本累计、截止和评分无效边界。未为验证故意制造真实GPU OOM；模拟故障不冒充真实降级。

17:00已切换至08，继承106组/27更新及未消费零基组 `[104,105]`；原窗口104的Teacher进程保持运行并接管结果等待。当前正常更新仍在进行，未发生真实容量故障降级。原件及回传监护覆盖01–08，原截止不变。

08的启动/续跑命令：

```bash
PYTHONPATH=src /root/gpufree-data/recoveragent_s1_retrain_20261114_01/worker_env/bin/python \
  -m recoveragent.codeact.experiment run \
  --config configs/codeact_grpo_coevolve_adaptive4_20261120.json \
  --deployment /root/gpufree-data/recoveragent_codeact_evolve_20261120_08/deployment.json \
  --output /root/gpufree-data/recoveragent_codeact_evolve_20261120_08/experiment
```

## 追加12小时及近期吞吐（18:27）

用户要求续上12小时。09独立运行从原截止追加43200秒，实际截止为 **2026-11-21 07:52:38 CST**；
配置总墙钟129600秒。新配置/300文件源码锁只增加运行身份及时间，不更改200组/100更新、
Teacher有限用量、模型、奖励或并发规则。完整组边界继承125组/32更新、97题生成池、
actor/optimizer、反馈/队列与未消费组 `[124]`，随后第126组也完成，待更新组为 `[124,125]`。
新controller已运行，vLLM实测4个同时请求；未重新采集旧组。

以下为实际已完成组的观测统计，不是同任务同actor/seed的受控对照：

| 采样方式 | 区间（从1计数） | 组数 | 平均分钟/组 | 中位分钟/组 | 平均生成token/组 |
|---|---|---:|---:|---:|---:|
| 单路 | 78–97 | 20 | 11.74 | 12.78 | 24,811.85 |
| 双路 | 98–102 | 5 | 4.79 | 3.21 | 14,059.40 |
| 四路 | 103–124 | 22 | 3.59 | 3.15 | 12,471.45 |

双路与四路均使用当前重复错误规则；更早单路的规则、任务构成及长度也不同。
四路这批平均墙钟比双路约短25%，但双路只有5组且中位数接近，不能将其解释为纯并发收益。
四路22组中未记录容量故障降级。近期第25–32次参数更新为55.11–183.12秒，
这是训练耗时，不是四路采样耗时。下一次dev在累计40更新，尚无新dev结果。

单路旧记录无显式采样耗时，使用远端原始 `result.json` 与 `group.request.json` 文件时间差；
并发使用实际 `sampling_wall_seconds`。125组在交接时只补全已有phase的汇总记录，
其汇总计时不代表真实采样耗时，独立保存 `handoff_sampling_timing.json`；上述统计不包含该组。
原始phase及四条奖励不修改。私有统计 `timing_readonly_20261120.json`、
`extension_verification.json`、handoff及deadline回执留在09本地运行目录。

本次未改执行代码：核验新配置仅revision/wall_seconds变化、300份源码SHA、部署deadline与
继承组/optimizer/pending一致性；未重复运行此前89项CPU测试，也未新增GPU测试。
README给出09当前续跑命令，已运行时不得重复启动。

远端空间不足2GiB，已启动29份历史optimizer迁移到私有D盘目录，计划约6.29GiB；
每份大小/SHA与远端原件核验后才删除远端副本，保留当前及最近三个optimizer。
一次SSH传输断开已记录，未核验文件的远端原件保留。此刻迁移仍在进行，不能把计划量写成已释放量。
每份来源/归档地址/SHA另有回执；旧更新回执不改写。09退出备份包含迁移索引及前序依赖，
历史optimizer原件还原时使用D盘归档与索引，不假定原远端路径仍存在。

18:33补充：进度127组/33更新。旧S1 short及其vLLM评价目录中52份大文件与已有本地归档
逐SHA/大小相符后删除远端副本，释放1,686,317,371字节（1.57GiB），可用空间约2.92GiB。
没有匹配到完整备份的文件不清除；CodeAct原始轨迹及当前模型不动。
私有核验索引 `verified_completed_archive_copies.json` 保存归档路径、member、SHA和大小；
远端 `completed_history_verified_receipt.json` 保留删除前核验记录。
前两份历史optimizer已核验迁走；其余传输改为按字节偏移续传，完整SHA通过后才移除远端副本。

## 用户指定保留40更新模型（21:00）

第二个20次有效更新轮次已完成，累计40更新时固定dev24官方结果为16成功/8失败/0未知，
66.7%。24个任务ID及顺序、逐题结果与汇总核验一致；评价累计任务耗时1819.70秒，
728请求、12,684,326输入token、62,652生成token。20更新为10/24，新CodeAct S0为8/24。
相对20更新新增7题成功、1题退步；相对S0新增8题成功、无退步。
40更新评价启用了重复错误提醒/终止规则，S0及20更新尚未启用，不能把全部差异单独归因于训练。

用户明确要求保留这一版。已在09的 `retained_models/codeact_evolve_u040_dev16of24_20261120/`
复制40步LoRA及匹配optimizer，连同tokenizer/template、原重载回执、当前配置/源码锁、
dev24 binding/summary/逐题result和来源状态副本，共44份文件、382,347,188字节。
逐文件大小/SHA与来源相符；复制不是指向原updates目录的软链接或硬链接。
文件设为只读，`DO_NOT_DELETE`及项目约定明确排除清理，原40步目录仍保留。

| 保留身份 | SHA-256 |
|---|---|
| adapter文件 | `12bfa18338ecde4283cd36079aa39a4a610c8de76ff33ef5c403d911f93de0e8` |
| optimizer文件 | `a6fa244735c42de6855d29fcd71d63afc739f47ae12b87a2eca01acd25f16829` |
| 保留清单 | `d564adc6fce0815e006f613e86a17ebdf0bf2f762940b03ddc415c83c3ac8d89` |

加载使用 `actor/`，配固定官方Qwen3.5-9B revision
`c202236235762e1c871ad0ccb60c8ee5ba337b9a`，non-thinking/64K/CodeAct。
这份备份是LoRA与匹配optimizer，不是19GB官方基座的重复副本；现有基座缓存继续保留。
40步真实训练时已有保存重载通过回执。本次保留只做文件完整性核验，没有为备份再启动GPU重载或评测。

D盘独立副本正在传输、逐文件核验，路径见README；仅在44份全部核验后写入 `backup_verified.json`。
控制器状态仅为来源记录，恢复完整实验仍依赖原任务池/反馈/轨迹；不把它描述为完整代码会话快照。
已把保留目录纳入09退出回传；后续主训练继续写新checkpoint，不覆盖该模型，不增加墙钟或采样预算。

## 前次空间清理

六份远端压缩包与已有本地副本逐个SHA及大小核验一致后删除，释放 **4,795,065,658字节（4.47GiB）**。另外核验两个停用的历史Function Calling目录与本地原件后，删除27份远端重复大文件，释放860,480,724字节；共 **5.27GiB**。当前S0、actor、optimizer和CodeAct原始轨迹保留。原计划的04 optimizer迁移取消，未完成临时包已清；不与已核验的历史重复文件清理混淆。后续大归档写入D盘，原始数据仍私有，详细清单见[清理报告](codeact_cleanup_20261120.md)。
