# RecoverAgent-RL：CodeAct 重启主线

更新：2026-11-19。本文是唯一当前计划；实际运行事实见 [STATUS](docs/STATUS.md)，命令见 [README](README.md)。

## 1. 目标与边界

研究三项相互衔接的能力：Student 失败后的 Badcase 回流 SFT、反馈驱动的 CoEvolve 任务演化、真实环境奖励下的 GRPO 自进化。本轮先实现它们共同依赖的基础：**固定 CodeAct → 全新 Teacher 数据 → 官方验收 → 多轮 SFT → 全新 S0 入口**。

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

训练入口先独立从官方基座做少量完整短样本和最长样本检查，正式训练再重新从基座加载。保存有限梯度、实际 updates、参数哈希、损失、显存、耗时与保存重载回执。临时 smoke adapter 不作为正式 S0。完整 S0 训练在采集完成后另行启动；本轮用户选择不开 GPU，真实9B训练烟测待资源安排。

11月19日采集完成后，用户已开机并授权开始新S0，使用独立 `configs/codeact_s0_20261119.json`；无OOM时252个完整样本、1 epoch、预计32步。按最新指示，样本前后向OOM时记录并跳过完整样本，清除该批部分梯度后重算剩余项；最长烟测可改试下一条较长样本，不静默截断。非有限梯度、参数未变化和重载失败仍停止。正式数据与更新量按实际成功参与训练的样本记录。

## 5. 后续路线（本轮不实现）

1. S0：全新 CodeAct Teacher 数据的 Base-SFT。
2. S1：Student 失败后 Teacher 在同一个仍可继续的实例接手；只监督有效恢复后缀，失败前缀作为上下文。
3. Static：从 S1 在固定任务池 GRPO。
4. Full：从同一 S1 利用当前 Student 执行反馈生成下一批任务，再 GRPO。

Static/Full 对齐 Student 训练预算，额外 Teacher 成本分列。任务演化必须连到真实执行与可验证完成条件；不提前建设通用恢复、记忆或评审平台。

## 6. 本轮验收与下一动作

优先确认真实 CodeAct 业务执行、跨轮变量和实例隔离；原始题官方评分不回传给模型；多轮 mask 与梯度计算正确。CPU测试、真实Teacher/AppWorld、真实GPU更新和学习收益分别报告。

当前交付以[重启报告](reports/codeact_reboot_20261119.md)及 STATUS 的最新数量为准。11月19日16:20已收齐256条合格成功示范并自动停止，最终导出252条64K内完整多轮样本、4条超长deferred，未截断。下一项是安排GPU完成新 S0 的真实短/长烟测，再启动正式训练；本地采集不再继续，旧 GRPO 不自动恢复。
