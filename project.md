# RecoverAgent-RL：CodeAct 重启主线

更新：2026-11-19。本文是唯一当前计划；实际运行事实见 [STATUS](docs/STATUS.md)，命令见 [README](README.md)。

## 1. 目标与边界

研究三项相互衔接的能力：Student 失败后的 Badcase 回流 SFT、反馈驱动的 CoEvolve 任务演化、真实环境奖励下的 GRPO 自进化。基础 **固定 CodeAct → 全新 Teacher 数据 → 官方验收 → 多轮 SFT → 全新 S0** 已完成；当前按用户最新授权直接从新S0运行GRPO＋CoEvolve，目标5个训练轮次，每累计新增20次有效更新后dev24；暂跳过Recovery-SFT，已授权截止为11月21日12:52:38。

用户指定保留累计40次GRPO更新、dev24为16/24的版本 `codeact_evolve_u040_dev16of24_20261120`。
独立保留LoRA、对应optimizer、模板、配置及评测回执，后续更新不得覆盖或自动清理这一版；位置见README。
其余训练按原预算继续，保留模型不意味着恢复旧任务或增加评测。

主分支为 `reboot/codeact`，起点为原开发分支 `bdaf6a3`。原工作区未提交工作保持原样；旧执行协议、Teacher 数据、模型与运行记录只供历史追溯，不进入本轮训练。历史计划可在[重启前固定版本](https://github.com/WYXeva/RecoverAgent-RL/blob/bdaf6a3cead063f7aec29989bd5bb5000abf7afd/project.md)查看。

## 2. 固定接口与共享执行

模型只看一个原生 Function Calling 工具 `appworld(code: string)`。代码在同一任务的 AppWorld Python 环境内执行，变量跨轮保留，支持循环、条件、一次代码内的多个 API 调用。业务调用采用 `apis.<app>.<api>(...)`，文档查询同样通过代码。初始工具 Schema 不变，不解锁、不动态注册、不追加工具头。

共享核心提示、环境与 agent loop 位于 `src/recoveragent/codeact/`。三种文档入口为 `api_docs.show_app_descriptions()`、`show_api_descriptions(app_name=...)`、`show_api_doc(app_name=..., api_name=...)`；保留原文档参数与约束。使用 AppWorld 原生安全限制，不继承旧 Harness 的单业务调用或无状态约束。参考 [AgentEvolver 固定执行入口](https://github.com/modelscope/AgentEvolver/blob/main/env_service/environments/appworld/appworld_env.py)，不迁入其服务框架。

Teacher 使用服务商原生消息；Student 使用固定 Qwen 原生模板，non-thinking。两者底层序列化可以不同，工具语义、公共输入和执行 loop 一致。隐藏评测始终在控制侧，`load_ground_truth=False` 的 Actor 不接收答案或评分细节。

## 3. 数据来源与正式采集

沿用冻结的 scenario 级自定义划分：train 170/510、dev 37/111、test 37/111（场景/任务）。三个变体不跨 split，保留每题官方来源 split。这不是官方排行榜划分，也不宣称 test 在项目历史中从未接触。

Teacher 只采 train510，不复用任何旧示范。队列先覆盖 170 个不同场景，再轮转其余变体。首批为队列前 20 个不同场景的任务，每题一次，失败最多再从干净初态尝试一次；每题最多导出一条合格成功示范。所有尝试保留，官方失败正常继续。首批完成时自动核查实际工具请求、环境/官方评分与新 tokenizer 导出；无共性实现故障后继续同一队列，不另设审批。

11月19日最新采集目标为**累计256条合格官方成功示范**，达到即停并导出；最多遍历既定train510，不扩任务。仅官方评分异常且执行轨迹无基础设施故障时，另存跳过回执并跳过整题，原结果仍是未知、不得进入成功数据。超长示范另存deferred，因此成功示范数与64K可训练数分列。调度控制独立保存，不改变已采轨迹的模型配置或提示。

配置为 `configs/codeact_reboot_20261119.json`：现有 DeepSeek V4.1 Flash / thinking，单回复 4096、累计输出 32768、最多80请求、1200秒，代码块60秒；文档/业务 API 不限为一次调用，原生块内 API 上限1000。Teacher 窗口独立为1M，采用匹配 tokenizer 的完整请求估算，实际用量以接口为准。每尝试最多两次传输重试，未知用量保守预留；凭据、额度或环境故障停止并保留记录，续跑不覆盖已完成成功任务。

原始消息、代码、返回、错误、官方结果、token/时间/估价分别保存。`appworld` 调用数与实际文档/业务/监督 API 数分别统计。采集启动器冻结源码和配置，在本地独立进程运行；不用远端 GPU。

## 4. 多轮 SFT 与新 S0

Student 从官方 `Qwen/Qwen3.5-9B`、revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a` 创建新 LoRA；禁止旧 adapter/optimizer 自动恢复。模板 SHA 固定，non-thinking，64K；沿用 BF16、r16/alpha32/dropout0、lr5e-5、micro batch1/accumulation8、1 epoch。

成功轨迹通过真实模板编码。兼容多轮合并为完整序列，所有 Assistant 内容/调用目标恰好一次；system/user/tool 及服务商隐藏 reasoning 不监督。前缀不兼容则另开保留完整前文的序列，不强拼。任何必要片段超64K时整轨迹 deferred，不截断、不删除操作。

目标是每条轨迹全部 Assistant token 的平均 CE，再均匀平均轨迹；若一条轨迹因前缀不兼容分段，则按实际目标数计权。模型主体每序列一次前向，目标位置 LM-head 分块 checkpoint，避免完整长序列词表 logits。报告累计输入/目标 token 与重复前缀工作量，不把工作量差当实测速度。

训练入口先独立从官方基座做少量完整短样本和最长样本检查，正式训练再重新从基座加载。保存有限梯度、实际 updates、参数哈希、损失、显存、耗时与保存重载回执。临时 smoke adapter 不作为正式 S0。初始采集期间用户选择不开GPU；采集完成后已授权启动训练，实际进度见STATUS。

11月19日采集完成后，用户已开机并授权开始新S0，使用独立 `configs/codeact_s0_20261119.json`；无OOM时252个完整样本、1 epoch、预计32步。按最新指示，样本前后向OOM时记录并跳过完整样本，清除该批部分梯度后重算剩余项；最长烟测可改试下一条较长样本，不静默截断。非有限梯度、参数未变化和重载失败仍停止。正式数据与更新量按实际成功参与训练的样本记录。

## 5. 长期比较路线与本轮先行实验

1. S0：全新 CodeAct Teacher 数据的 Base-SFT。
2. S1：Student 失败后 Teacher 在同一个仍可继续的实例接手；只监督有效恢复后缀，失败前缀作为上下文。
3. Static：从 S1 在固定任务池 GRPO。
4. Full：从同一 S1 利用当前 Student 执行反馈生成下一批任务，再 GRPO。

Static/Full 对齐 Student 训练预算，额外 Teacher 成本分列。任务演化必须连到真实执行与可验证完成条件；不提前建设通用恢复、记忆或评审平台。

## 6. 本轮验收与下一动作

优先确认真实 CodeAct 业务执行、跨轮变量和实例隔离；原始题官方评分不回传给模型；多轮 mask 与梯度计算正确。CPU测试、真实Teacher/AppWorld、真实GPU更新和学习收益分别报告。

当前交付以[重启报告](reports/codeact_reboot_20261119.md)、[S0训练报告](reports/codeact_s0_training_20261119.md)及 STATUS 为准。11月19日16:20收齐256条合格成功示范并停止，导出252条64K内完整多轮样本、4条超长deferred。新S0已于17:27完成全部252样本/1 epoch/32次正式更新，0 OOM；短/长及正式模型保存重载通过，18:19本地备份完成。

用户随后授权固定dev24：使用同一新S0、现有L40S、vLLM 0.19.0、固定CodeAct/non-thinking/64K，24题按既定顺序各执行一次，官方评分，成功/失败/未知分列。独立配置 `codeact_s0_dev24_20261119.json`，不扩到dev111/test，不调用Teacher或训练。19:24已完成，**8成功/16失败/0未知，33.33%**；8条“提交完成但业务失败”已核对，主要为遗漏目标、筛选对象错误和购物车已有状态未处理。最终本地备份已核验；用户最新授权按第7节直接从新S0进行一轮GRPO＋CoEvolve，dev不回流。见[dev24报告](reports/codeact_s0_dev24_20261119.md)。旧接口历史成绩仅作参考，不直接当作本次同条件效果对照，不自动恢复旧GRPO。


## 7. 当前有界在线学习

使用新CodeAct S0（32次SFT更新）的现有权重，不承接旧接口14/15次GRPO。
本阶段optimizer从0计数并连续继承；用户最新定义每20次有效optimizer更新为一个训练轮次，
在累计20/40/60/80/100次更新后跑dev24。已有18次计入第一轮，旧7/18次更新时的评测保留为历史。
预算仍为最多200采样组×4次、最多100次更新，墙钟按当前授权截止；
可能先触及采样或时间上限，不为凑满5轮增加预算。lr1e-5、beta0、temperature0.8/top_p1不变。
同组干净初态、同一actor、不同种子；两组成败混合且四条评分有效的组积累后更新，
积累期间actor不变，剩余单组可做尾批。0/4与4/4仍留反馈，未知不当作0。

复用既定train100任务ID与原scenario划分，原始/生成优先槽交替；当前生成池从空开始，
不足时原始任务补位。每8个实际采样组最多选4个context，沿用最新轻量出题提示、
原题/父题/同源题面对照、可选一次探索、每context最多2候选。完整四次交互进入Teacher；
全新CodeAct Solver从干净初态执行，shared Judge准入，Student奖励使用同题同初态Solver参考。
最多24批、96context、192候选，最后一组结束后不新增出题批次；Teacher在后台运行，入池后参与尚未执行槽。

Student保持64K、non-thinking、固定appworld(code)，vLLM记录真实token及processed behavior logprob；
训练使用相同actor、相同序列和temperature，首次更新前做小量logprob核对。
复用TRL clipped GRPO、完整轨迹目标归一化、兼容前缀合并、目标位置词表分块与已验收快速内核。
beta0不加载参考adapter或执行参考前向。每次更新恢复optimizer动量并重设实际lr，
保留参数变化、有限梯度、optimizer、可重载adapter与完整组消费状态。
OOM跳过整批更新并保留上一actor及原始轨迹，不用部分梯度冒充完整组更新。

当前配置 `codeact_grpo_coevolve_extend5_20261120.json`；新源码锁、独立目录和绑定保证新旧运行不混用。
Teacher总预留有限：4096请求、250万输出token、约42.95亿输入token、100美元公开价估算上限，
同时受已授权墙钟限制（当前总计41小时）；它是现有套餐用量估算，不是新增采购或扣款授权。Judge保持Flash thinking/high/8192；
Teacher窗口1M独立于Student64K。普通任务失败继续，系统性模型/参数校验失败保存后停止。
本轮不跑Static/test；是否有学习收益仍需后续同协议固定dev比较，在线训练奖励不能替代dev成绩。


### 11月19日晚：当前执行调整

用户确认改为连续5轮，每轮40组×4次、最多20次有效更新，轮末跑固定dev24后继续当前actor及optimizer。
总目标200组/最多100次更新；不重置本轮已经完成的组、更新、任务池或采样队列。
当时截止11月20日19:52:38；11月20日先追加12小时、再追加5小时后，**当前截止11月21日12:52:38，包含已用时间**。未跑完的轮次和评价保存后等待后续资源授权。
Teacher的4096请求/250万输出token/100美元公开价估算上限保持不变，不因五轮目标自动扩费。

新配置 `codeact_grpo_coevolve_five_rounds_20261119.json` 使用官方结果分类v3，补齐明确数字答案为None导致float转换TypeError的有效失败分类。
旧第3组另存真实官方复评 `[0,0,1,0]`；官方报告与原始轨迹不变，不能将旧策略轨迹补入当前策略更新。
轮末先处理有效尾批再评测；dev24始终为官方验收、temperature0、每题一次，不回流训练。
真实截止中断的dev尝试保留为未知，续跑只处理尚未尝试的题。

### 11月20日：训练显存分配调整

原60,866-token批次在L40S上OOM后，用户授权原批复测。新配置
`codeact_grpo_coevolve_memory_20261120.json`仅启用训练进程可扩展CUDA分配和分段显存记录；
同批全部36,188目标、9段真实更新及重载通过，峰值42.17 GiB。诊断从当时的旧actor独立开始，
其权重不并入主实验。主线在04目录继承02的23组/2更新，原24小时截止不变；
不截断、换模型或扩预算。继续观察新配置实际更新，不能将单批通过当成全部长序列保证。

### 11月20日：有效更新定义训练轮次

从05目录继续04的80组/18更新，轮次按累计20次有效更新计数。
只有成功更新计数；全同奖励、评分无效、OOM跳过不增加更新数。
旧40采样组轮末评测不冒充新的更新里程碑；新评测另存 `evaluations/updates_000020` 等目录。
40采样组仍为任务队列窗口，仅控制生成题每窗口一次，窗口切换不清空待更新有效组。
原100题先打乱覆盖，再重新打乱循环；生成题优先未采样题，后续窗口可再次使用。
每次选题都由当前actor从干净初态独立运行4次；不复用旧采样token或logprob。
本次两份旧dev24均9/24，新20更新评价尚待发生。截止和Teacher预算保持原样。

### 11月20日：并发与重复错误收敛

Student采样改为两个独立AppWorld子进程共享vLLM，位置空出后补入下一次尝试；
同组四次依然同actor、同初态、不同seed，全部结束后汇总并按原有效组规则更新。
新配置 `codeact_grpo_coevolve_parallel_20261120.json`，并发数可配置1–4。
用户随后授权四路试跑，采用独立 `codeact_grpo_coevolve_parallel4_20261120.json` 和07目录，
从06的完整组边界继承当前actor、optimizer、未消费有效组、队列和池；
只有并发数改为4，执行协议及原19:52:38截止不变，实测结果见STATUS。
随后采用独立adaptive4配置：每题默认4路，仅采样容量/进程故障时对受影响尝试用2路补跑一次；
正常业务失败不重采，故障原件保留、补跑从干净初态同seed开始，下一题恢复4路。
不增加总墙钟/费用预算；2路仍无效的组排除更新和能力负反馈。配置与实际部署见STATUS。
仅对相同代码、确定性错误和可核验状态重复的情形，三次提醒、五次结束；
正常执行、状态变化和无法确认无进展时不提前终止。官方评分/Judge与Teacher设置不变。
新执行身份进入回执/训练绑定，切换前收尾旧有效组；保留原actor、optimizer和原截止。
CPU与真实AppWorld控制检查已通过，GPU并发情况以当前STATUS和[报告](reports/codeact_parallel_progress_20261120.md)为准。

11月20日追加12小时采用09独立目录、`codeact_grpo_coevolve_extend12_20261120.json` 和
`codeact_online_extend12_20261120.lock.json`，从完整组边界继承32次更新和未消费组，
不重置actor、optimizer、任务池、反馈或队列。总墙钟129600秒，截止11月21日07:52:38；
200组/100更新及Teacher有限预算不扩张，可能先触及采样上限而未达到100更新。

随后再加5小时采用10独立目录、`codeact_grpo_coevolve_extend5_20261120.json` 和
`codeact_online_extend5_20261120.lock.json`，继承166组/44更新及未消费组、actor/optimizer和全部队列反馈。
总墙钟147600秒，截止11月21日12:52:38；其余预算和执行协议保持不变。


当前仓库已清理CodeAct依赖链外的旧高层执行器及配套入口、测试；历史版本留在Git，
共享组件、原始证据、旧配置和锁继续保留。新checkout部署使用compact源码锁；
正在运行的06独立快照不随仓库清理变化。清理后的完整339项软件测试通过。
本次改变了Student的循环预算策略，20更新dev24的10/24是切换前结果；
后续新规则的dev应标注执行配置，不能把与旧规则S0的差值单独归因于参数学习。
