# 技术决策

当前需求为 `AGENTS.md` / `PROJECT_PLAN.md` v0.4；D001–D006 是 2026-10-19 P0–P2 1.7B 的历史工程记录，不因本次模型/硬件调整而改写。

## D001 · 2026-10-19 · 固定源码与独立环境

AppWorld 固定到计划已核对的 commit `42b5bcf3cd334fee33f0c37c02070a9f5807add5`，实际包版本为 `0.2.0.dev0`。[官方 pyproject](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/pyproject.toml) 明确支持 Python 3.14，因此沿用本机 3.14.4。

直接安装该源码归档时，最新 Pydantic 2.14.1 与现有补丁冲突，导入失败：`match_type() takes 2 positional arguments but 3 were given`。固定到 2.12.3 后可导入。ZIP 中 `.source/*.bundle` 是 LFS 指针，必须下载同 commit 的真实对象并核对指针内的 SHA-256 和大小，再使用官方 `appworld install`。解包缓存设置 `APPWORLD_CACHE` 到项目 `.cache/appworld`。没有修改依赖源码。

AppWorld 与推理分别使用 `.venv`、`.venv-inference`；实际依赖另存 freeze。基础数据、解包内容和权重均不提交。

## D002 · 2026-10-19 · 恢复完整性与时钟兼容

`save_state(state_id)` 导出目录在 world 输出目录之外。manifest 同时绑定逻辑记录指纹、文件哈希、合法历史、完整工具 schema、Python RNG、交互计数、任务时钟、基础数据库和原始任务文件哈希。每个 worker 创建唯一输出目录，再复制快照到其检查点目录并调用 `load_state`。用数据库记录的规范化内容验证状态，文件哈希仅作完整性校验。

真实探针发现，[上游 `load_state`](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/src/appworld/environment.py) 调用 `close_all()` 后重建 requester 时钟，却保留 world 的旧 freezer；随后 `close()` 出现 `fake_names` 属性错误和 freezer 栈空错误。适配层在 load 后调用该版本已有的 `_set_datetime()` 重建 world 时钟。该内部方法依赖是明确的兼容点，升级上游时必须重跑真实状态探针。失败日志保留于 `.cache/state-probe-b.log`，不通过吞掉异常掩盖问题。

仅允许无跨轮 Python 变量的直接函数调用，禁止 Actor 改时钟；环境 RNG 在各恢复分支相同，只有模型采样 seed 改变。真实墙钟预算使用 `clock_gettime(CLOCK_MONOTONIC)`，避免被 AppWorld 的 frozen time 冻结。

真实跨进程验证另发现官方 evaluator 会生成任务目录中的 `__pycache__`。基础输入指纹排除 Python 字节码缓存，仍校验原始任务文件、基础数据库和版本文件；快照自身的完整性检查不排除任何文件。完整四进程写入/恢复探针最终通过，证据为 `reports/p0_state_evidence.json`，原始记录位于 `runs/p0_state_20261019_d`。

## D003 · 2026-10-19 · 工具与评价隔离

执行 world 使用 `load_ground_truth=False`。Actor 只得到任务公开信息、官方公开工具、自己的历史及中性的继续提示。初始开放 `api_docs` 与 `supervisor`；Actor 调用 `show_api_doc` 后，才把选中的公开函数 schema 加入后续上下文。此工具发现协议、每回合一个调用和 `enable_thinking=False` 对四个恢复分支保持一致。

参数先解析 JSON、按官方 schema 验证，再用受白名单限制的函数名和 Python 数据字面量执行。多调用响应整轮拒绝，不执行其子集。所有登录与业务步骤由 Student 决定。

终态快照及官方输出保存后关闭 world，再由控制侧调用官方 `evaluate_task`；它重新加载原始任务作为参照。不能使用要求 world 加载 ground truth 的 `world.evaluate()`。官方断言、奖励和其他分支结果不会加入 Actor 消息。

## D004 · 2026-10-19 · 模型、预算与结果解释

本机实际 GPU 为 RTX 5050 8GB。按 v0.2 已允许的资源回退，首轮候选采用 `Qwen/Qwen3-1.7B`，固定 revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`。使用本地 Transformers、BF16、禁用 thinking，不调用付费服务，不训练。

首个有效自然失败出现后立即尝试同起点四分支。预算沿用计划：正常 40 回合/40 调用/8192 token，恢复 15/15/4096；总 150000 生成 token、90 分钟、10 个 train 任务上限、基础设施最多重试一次。大规模权重下载与环境安装属于准备阶段，另行记录；采样预算从实际实验开始计时。

保存实际生成 token IDs 和输入 token IDs，尚无行为 log probabilities，明确 `rl_eligible=false`；这些 P0–P2 数据不能直接当在线 GRPO 训练批次。四个同奖励分支可以通过基础设施验收；P3 的 Teacher SFT、GRPO 条件以及效果提升均独立验收。

中断导致生成量不可确认时，单独记录 `generation_reservation_tokens` 并从总预算扣除，绝不将预留量记为真实生成量。进程超时不补零奖励；失败尝试和重试均保留日志。网络下载中断日志位于 `.cache/download-student.log`，采用一次限时断点续传。

## D006 · 2026-10-19 · ReTerm 参考范围

已核对 [ReTerm 官方说明](https://github.com/twothsnd/ReTerm)：保留基座失败状态与历史、由更强 Teacher 继续执行、只监督 Teacher 恢复后缀，以及同起点分支验证、有限恢复队列，是本项目参考的方法。当前工程只完成 AppWorld 所需的 P0–P2 基础设施，没有复制其分布式训练栈，也不把这些机制宣称为原创或完整 ReTerm 复现。

## D005 · 2026-10-19 · Git 工作目录

原始工作目录 `.git` 为空且只读。保留其现状，使用 `/tmp/recoveragent-dev-20261019` 的真实克隆进行版本管理，remote 为 `https://github.com/WYXeva/RecoverAgent-RL.git`。开发分支 `codex/p0-p2-recovery` 从远端 `14be212003e2af85457377eaf4e154208ea978c7` 创建；保留所有既有提交，不修改 main、不强制推送。源码在用户工作区实现后，显式同步已审查的小型文件到该克隆。

## D007 · 2026-10-20 · 两阶段 Teacher SFT、Multi-Checkpoint 与增量恢复

- 用户明确要求先用固定更强 Teacher 的正常成功轨迹训练 Student **基础执行能力**；只有完成 Base-SFT 后 Student 才自主执行、暴露新错误，由 Teacher 从多个真实检查点接管修复，再训练 Recovery-SFT，随后 Student 在线恢复 GRPO。
- 改造阶段顺序：P3-A Teacher Clean Data；P3-B Student Base-SFT；P3-C 新 Student Failure Mining；P4-A Multi-Checkpoint Teacher Recovery；P4-B Recovery-SFT；P4-C GRPO；P5 Teacher 辅助增量 SFT+GRPO；P6 官方最终评价。
- Base-SFT 只监督 Teacher 正常成功全轨迹 Assistant token；Recovery-SFT 仅监督 Student 失败状态后 Teacher 恢复后缀的 Assistant token。系统、用户、工具输出、隐藏评价都不能成为 SFT target。
- 多检查点 V1 为规则选择，主研究对象为 Post-error 与 Late-failure；Pre-error 仅作错误预防消融。V2 U_SFT=p_T(1-p_S)、U_RL=4p_S(1-p_S) 为待验证启发式。必须控制 Teacher 全尝试成本、每任务数据权重及 SFT/RL token。
- P0–P2 老实验仍有效但只说明基础设施；本次仅更新 GitHub 文档，未运行新实验、没有 Teacher 数据、两阶段 SFT 或 GRPO 的通过证据。无授权不得调用付费 Teacher。

## D008 · 2026-10-20 · Qwen3-4B 正式主模型与本机 Teacher / 远程 96GB 训练

- 用户指定正式 Student 使用 `Qwen/Qwen3-4B`；本机保留 AppWorld 与 Teacher 正常成功示范采集及验证，Teacher 数据完成后在计划中的单张 NVIDIA RTX PRO 6000 96GB 上远程执行 4B Base-SFT、Student 新自然失败、Multi-Checkpoint Teacher 恢复、Recovery-SFT、GRPO 和增量迭代。
- 本机 P0–P2 的 Qwen3-1.7B 不再作为正式主模型；旧轨迹、模型配置和已通过的基础设施证据原样保存，不重标为 4B。
- 要求 train-only、未分词结构化完整多轮 Teacher 消息与可验证 success/filter/成本清单，manifest 绑定 AppWorld data/commit、Teacher revision、工具 schema、sha256；远程使用固定 Qwen3-4B tokenizer 重建 Assistant-only SFT mask。通过有权限的安全传输，不把受限 AppWorld 数据/凭据上传公开 GitHub。
- 96GB 机器尚未核验。远程 AppWorld worker/数据版本/快照一致性、模型 revision、驱动/CUDA/Torch/TRl/PEFT/vLLM、显存峰值和单卡推理训练共存需正式预检。优先 BF16 LoRA 和低负载 GRPO 试跑；未经授权不得租 GPU 或调用付费 Teacher。
- 本次是方案文档修改，没有新的教师数据、训练结果或 4B 实验。参考：Qwen3-4B 官方模型卡与 NVIDIA RTX PRO 6000 Blackwell 官方规格。

## D009 · 2026-10-20 · 初始 Teacher 协议与授权前预检（后续授权及批量修订见 D012）

新增独立 P3-A 入口，复用已验收的 AppWorld Adapter/状态模块，不改变 P0–P2 采样代码与历史配置。Teacher 使用原生 `tool_calls`，保留 ID、文本和参数；一回合多调用或截断整轮拒绝。普通文本是合法 Assistant 消息，但没有实际工具动作时结束并评价，不伪造调用。模型始终从原始 train 初态执行，官方评价在保存终态/关闭 world 后进行。

本地 `.env` 有 DeepSeek 配置，但本轮用户明确禁止未授权付费服务；凭据存在不构成费用授权。默认配置关闭 paid 调用且费率未填，预检在任何请求前阻断。费用按供应商 usage 和核验费率计算，区分估算与账单；中断预留未知费用并停止，无自动付费重试。

供应商文档当前将 `deepseek-flash` 对应到 DeepSeek-V4.1-Flash-1017，但 API alias 不等于不可变权重 revision。记录文档版本来源、实际响应 model/fingerprint，并拒绝本次采样内身份变化；真实更强能力仍需 train 试点证据。此预检阶段没有发出 Teacher 请求，后续真实试点见 D012/D013。[官方 API 契约](https://api-docs.deepseek.com/api/create-chat-completion/)、[官方版本说明](https://api-docs.deepseek.com/)。

## D010 · 2026-10-20 · 未分词 Teacher 数据与可核验交接

成功示范保留完整原始结构化消息及逐回合实际 schema。原始请求/响应和官方评价放在控制侧 evidence；Base-SFT 只读取明确的 messages/schema 字段。manifest 绑定 Teacher/AppWorld/正式 Student、文件 SHA-256、数据顺序、官方成功与筛选分母、成本。拒绝来源为 Student、非 train、非干净初态、格式错误或已确认违规；连续相同调用与相同观察三次是明确标注的无进展过滤启发式，额外安全未测。

迁移核验重新检查训练划分、原始回执、来源、顺序和哈希。数据不依赖本机绝对路径；远程须从可信交接记录获取 manifest SHA-256，不能用同目录校验和代替真实性来源。初始预检只有零样本包和 pytest 合成 fixture 验证；获得费用授权后形成真实非空数据，见 D013。远程迁移仍未执行。

## D011 · 2026-10-20 · 4B 逐回合 Assistant-only mask 与格式诊断

正式 Qwen3-4B model/tokenizer revision 固定为 `1cfa9a7208912126459214e8b04321603b3df60c`，non-thinking，模板哈希 `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`。只下载 tokenizer/配置，不在本机加载 4B 权重。

每个 Teacher Assistant 回合单独构建有完整上下文的训练样本，使用该回合实际可见 schema，仅对新 Assistant 后缀和结束格式计算 loss；此前 Assistant、system/user/tool、固定 non-thinking 提示与 padding 全部屏蔽。每回合字符串和 token 两层前缀保持检查必须通过，不静默截断，不用最终 schema 重渲染所有早期目标。这样既覆盖正常全轨迹全部 Assistant 行为，又防止未来工具泄漏和多轮模板改写导致错位。

对旧 100 回合重算发现：50 次解析拒绝全部是无 tool tags 的普通文本，均提及 access token；不是已观察到的坏 JSON 或批量调用。真实 4B tokenizer 合成样例的参数往返、工具输出屏蔽与动态 schema 检查通过，但没有新模型生成来证明错误已消失。保留历史协议与结果，正式改进须由后续 Base-SFT 和真实执行验证。[Qwen 官方工具协议](https://qwen.readthedocs.io/en/latest/framework/function_call.html)、[固定 tokenizer 文件](https://huggingface.co/Qwen/Qwen3-4B/blob/1cfa9a7208912126459214e8b04321603b3df60c/tokenizer_config.json)。

## D012 · P3-A 原生批量调用的完整边界（2026-10-20）

- 用户在异步授权问题中回复“费用不限”，授权现有 DeepSeek Teacher 实际试点；不再将费用授权列为阻塞。本轮仍限制两个唯一 train 任务、累计 80 次模型请求、32,768 生成 token、1,800 秒采样时间，保留操作性 USD 5 上限，不启动训练。
- 实际 A/B 两轮共 14 次请求、2,860 生成 token、4 次官方失败。A 的单调用拒绝与 B 的 `parallel_tool_calls=false` 都不能阻止服务返回多调用；失败批次未执行任何子调用。官方 Chat Completions 文档允许多个 tool_calls，不承诺该额外参数能强制单调用。原始证据位于 `runs/p3a_teacher_20261020_{a,b}/teacher_*/requests/`。
- P3-A 接受有上限的原生 batch（最多 64 调用/回合），整批先检查当时可见 schema、参数、唯一 ID、剩余工具预算及完成调用的位置，然后顺序执行。所有真实结果返回后才建立历史边界、请求下一轮或保存终态。不会截取第一条调用，也不会将一条 Teacher Assistant 改写为多条。
- P3-A 的 `teacher_history.py` 单独实现完整 batch 历史校验与评价前终态证据（格式 `p3a-terminal-batch-v1`）。它不冒充 P0/P2 可重放快照；P0/P2 adapter/state/runner/io、原配置及旧结果保持原样。若未来需要多调用恢复，另做实际重放验收。
- 重试仍从两个原始 train 初态独立开始，不注入任务解法、隐藏标签或前轮验证结果。默认配置继续关闭付费授权；本次授权与费率保存在忽略的本地配置。USD 价格计算是公开高峰费率估计，实际人民币扣费另查账户差额；两者不可混称。

## D013 · 2026-10-20 · 小样本成功、完整分母与真实 mask 验收

C 轮启用完整批量协议后，两个任务中一个因 2048 token 单响应截断失败，一个从原始初态经 29 次真实工具调用完成并获官方成功。保留 A/B/C 共六次尝试，不将最终 1/2 的协议试点结果冒充全部采集成功率（总计 1/6），也不作为模型公平比较或恢复效果证据。Teacher 成功示范是 `teacher_ad808a15ce5a`，不是 Student 或合成 fixture。

合并包 `data/p3a_clean_20261020_all/` 保存全部尝试/回执/成本与一条成功示范，SHA-256、官方 train 成员、来源和顺序可重算。账户余额差额人民币 0.04 元与 token 高峰费率估算 USD 0.027231792 分别记录，不将估算当账单，不公开账户绝对余额。来源和命令见 `reports/p3a_pilot_audit.json`、`reports/p3a_report.md`。

真实 4B tokenizer 上得到 8 个 Assistant 训练格式样本、1146 个监督 token；批量调用保持一条 Assistant，所有 29 次函数参数往返、完整前缀及非目标上下文/padding mask 均通过。这个结果只验收格式，不代表 SFT 参数更新、4B 自主任务成功或效果提升。一条示范/一个场景不能支持正式训练规模和开发集划分。

同机独立目录的真实数据迁移演练通过。远程主机未连接，没有宣称 96GB 环境、跨主机快照或远程训练通过。下一阶段须保留数据 SHA-256 和按场景分组规则；当前正式训练仍禁止启动。采样结束后的中断计费修复只做软件测试：从已落盘回执恢复已知用量，仅对缺失响应保留未知费用，不新增模型调用。


## D014 · 2026-10-20 · 以实际请求展示 LLM 可见信息

排错查看器从每轮持久化 API request 的 messages/tools 读取输入，不使用最终轨迹拼接早期上下文，也不使用当前最新 schema。后续工具结果和控制侧奖励分开显示；不读取 evaluator 文件。供应商内部模板、token IDs 和未记录的思考不在可观察范围。

默认隐藏已识别凭据，包括 JSON 字符串及后续文本中的重复值；保留字段名称和 schema。原始日志只读，拒绝在原轨迹目录内生成文件或覆盖已有输出。视图是展示派生物，不用于训练/重放，不上传公开 GitHub。出现回执哈希或摘要不一致时显示警告而不是替换证据。

本轮仅解释既有预算语义，不修改判定：累计预算耗尽先停止再由官方验证决定奖励；单次 `finish_reason=length` 则整响应拒绝，标为 invalid_completion，格式过滤拒绝入集。`budget_exhausted` 目前也可能来自其他回合/工具/请求/时间上限，不能自动解释为 token 耗尽。官方成功、数据格式合格与预算终止是不同维度。

## D015 · 2026-10-20 · 官方 Predictor 组件与动态发现配对比较

用户明确要求保留两种 tool mode 并将单回复上限改为 4096 token，需求升至 v0.5，模型与两次 SFT 顺序不变。保持 P0–P2 源码和旧结果，在 P3-A Runner 上增加 `api_predictor`；默认仍 `dynamic_discovery`。业务行为仍全由模型决定，未增设隐式登录或控制侧补做操作。

检查固定 AppWorld 源码发现：官方 `APIPredictor` 支持 predicted/ground_truth/all，并可用带 hidden required_apis 的任务做 few-shot。本项目约束不允许隐藏标签进入模型，故复用官方类/提示的 **predicted + demo_task_ids=[]**，不加载 ground truth；`include_cache_control=False` 避免零示例时官方代码对空 `demo_messages[-1]` 取值。官方提示/解析源码 SHA-256 固定并每次验证；通过额外固定 Jinja2/MarkupSafe 支持上游提示渲染，不修改依赖。资料和哈希见 [TOOL_MODES.md](TOOL_MODES.md)；这不是完整官方 benchmark 复现。

每个 Actor 回合只获得当时的 schema。A 选定之后不扩展，即便漏了登录工具也不能补入，否则改变被比较方法。B 仍由模型先查询 show_api_doc、下一请求获得 schema。共同 Runner 仅替换与工具模式有关的提示句子，保留相同任务、模型、温度、环境种子和批量协议。

两任务四次，使用预先固定顺序 B1/A1/A2/B2，均分原 80 请求/32768 生成 token/1800 秒额度；每次 20 请求/8192 token/450 秒、40 工具调用。Predictor 全部用量、失败和未知计费预留计入同一额度，总成本再分项报告。预测输入/响应持久化为 `000`，与 Actor `001...` 分开；选择对话不作为 Base-SFT 目标。携带 tool_mode 的成功数据仍经现有官方/格式/循环筛选和 SHA-256 验证。

真实 A 两次漏掉登录/凭据工具、0/2 成功；B 1/2，另一条请求 20 次工具动作时仅剩 12 次预算，整批拒绝。此失败是预算约束下的策略行为，不是基础设施故障，也不是 4096 输出截断。四次共 21 模型响应，官方评价有效、两组初态相同、进程独立、backend 一致。保留全部尝试，不为得到更好成绩补工具或追加重跑。两任务同族不能支持总体优势；更广场景/重复采样和正式 Student 比较均未完成。

公开高峰费率估计 USD 0.023085432 与账户前后观察差额 CNY 0.00 分开记录，逐请求实际实扣未知，不据此声称零成本。成功数据只作 tokenizer/mask 格式验证，无参数更新。原始证据与精确命令见 `reports/tool_modes_report.md`。

## D016 · 2026-10-20 · 取消工具数量配额，区分合理批量与认证漏选

用户明确要求工具调用次数不限制，并检查 prompt。新默认整轨迹及每回复 quota 均用 null 表示无数量检查；历史整数仍可重现旧配置。取消的是独立调用数量配额，4096 单回复、总生成 token、模型请求、时间、费用和完整历史校验保留。没有用另一个小整数代替 40/64。

固定上游 AppWorld 要求 `max_interactions` 是 int 且直接比较计数，不能传 None。P3-A 通过官方构造参数传 sys.maxsize 作为兼容哨兵，避免旧 adapter 默认 100 次形成底层截停；不修改上游源码、官方评价或 P0–P2 文件。记录实际传入值并验证真实 101 次调用。该值不是数学无限，但在保留的资源预算内不可触达。每个 execute 仍只包装一条公开函数，与模型单次回复可包含多条工具请求不冲突。

原 B1 的 20+20 个 song_id 全来自已见歌单，无重复、互不依赖；旧 hidden 40 次额度才是拒绝原因。真实重放原 28 次观察逐条一致后，20 个旧未执行调用全部成功执行；这是控制侧缓存重放，不冒充新 Teacher 示范/自主恢复成绩。新 Actor v2 保持完整批次，不擅自丢弃后半批，明确预算及参数/依赖/输出长度规则，不要求每轮只调用一次。token 截断规则仍保留。

官方 Predictor 的零示例提示只泛化要求高召回，并未明确提供凭据/登录依赖；因此保留原 `official_zero_shot`，新增 `dependency_aware_v1` 通用依赖提醒，模型仍自己选工具，不在 Runner 强制塞入登录或补做业务。增强版记录提示版本/哈希，数据中与官方版区分，不把它叫官方原始基线。

同一个已分析 train 任务上做三次原始初态新执行、共用 Actor v2 与20请求/8192输出/450秒预算、无调用quota。B 用67次调用成功；A官方5次失败；A增强61次成功且自行选中登录与凭据工具。A两组Actor文字提示相同，Predictor只追加通用依赖提醒；初态、模型/backend、预算一致。单次有随机性且任务已参与诊断，不能证明总体prompt收益；B同时改了配额与Actor提示，也不能把成功全归因于提示。负结果和旧实验完整保留。

此轮实际21模型请求、133 Teacher工具调用；额外149次控制侧重放/阈值探测单列。估算USD0.020253828与账户观察CNY0.00分开，实扣未知。两个新成功样本只做4B tokenizer/mask验证，无训练。结果、源哈希、命令、限制见 `reports/unlimited_prompt_report.md`。


## D017 · 2026-10-21 · 共享执行规范、显式依赖与人工选工具正例

用户确认讨论方案后将需求升至 v0.7。默认 `execution_v3` 规定全局规则和理解→证据→执行→纠错→验证→完成循环；A/B 共用完整文本和资源规则，只替换固定工具/动态发现段落。公开自查与控制侧官方评价分离；不因为提示写了验证就让 Actor 调 evaluator。B 明确缺能力时先查简介、再查 schema、下一请求才使用新工具，补齐依赖并复用证据。

A 默认 `dependency_examples_v2` 复用固定官方 Predictor 头部/公开简介目录/解析实现，追加初态依赖契约，插入两对人工 user/assistant 选工具示例。例1为 Simple Note 精确标题笔记置顶并保留字段，例2为 Amazon 愿望单逐商品分页统计一星评价；只依据公开 API 的认证、参数与返回字段编写，没有加载真实示例 task 或隐藏 required_apis。保留 `demo_task_ids=[]`，两个示例有单独 provenance/count/SHA-256，篡改不能保留同一版本标签。它们不是 Teacher 执行轨迹，不标官方成功，不进入 Actor 历史、Clean Dataset 目标或Base-SFT loss。

官方简介目录不含完整参数/返回契约，提示无法保证依赖永远选全；本次不把所有完整 schema 额外塞给 Predictor。保留官方最多20种API选择边界，并在提示明确；该边界不同于已取消的调用次数配额。Runner仍不自动补登录、追加未选工具或替模型完成业务。旧 `official_zero_shot` / `dependency_aware_v1` 与旧 Actor 原样可用；默认增强版绝不冒称官方原始基线。

固定前两个已分析 train 任务、同族，B1/A1/A2/B2各一次、同初态/预算/backend、独立进程。A Predictor全部目录/规则/示例/token/请求成本纳入端到端预算；两方案一起修改提示，结果是完整方案回归，不是依赖提醒/示例/模式的因果消融。本轮4条全通过官方验证及现有过滤，但A第二任务先查19次不含所需字段的私有信息，再改查详情；该冗余作为真实负面诊断保留，没有按成绩继续改提示重跑。

真实33请求/204调用、11959生成token，估算USD0.036879636非账单；80软件测试、33新回执审计和旧21回执兼容检查通过。4条数据的真实4B tokenizer/mask检查通过，但无模型权重加载或训练。提示全文、命令、费用与限制见 `docs/PROMPTS.md` 和 `reports/structured_prompts_report.md`。下一项为更多train场景与重复采样；当前证据不支持总体优势。

## D018 · 2026-10-21 · 冻结提示后按公开场景固定扩样

用户要求冻结 execution_v3 与 A/B 协议，扩大10–20个任务并避免继续对旧两个Spotify任务调参。从拉取核对的cc53bea建立五个现有文件的SHA-256锁；扩样启动前强制检查提示名、文件内容、4096单回复和无工具数量配额。旧数据、旧提示变体和P0–P2原17项全部保留。新`scenario_expansion_v1`配置单独扩大控制器任务/请求/token/时间容量，原pilot默认与硬上限不改变。

官方依据为固定AppWorld `task.py`：`load_task_ids("train")`返回90个任务；`task_id_to_generator_id`与Task.generator_id是公开ID映射。项目scenario_id据此分组，共30家族。不能使用上游difficulty筛选，因它读取ground_truth metadata。采样器不加载任务/解答，只用成员表、家族映射、配置seed，支持家族均衡和显式固定清单。实际seed20261021、6家族各2任务，排除已反复诊断的82e2fac；清单及canonical digest在执行前固定，失败不替换。这里的家族不是应用名，新采样仍可包含其他Spotify家族。

本轮用B dynamic_discovery收集多场景正常示范，A协议原样保留但不另做配对；这样12个不同任务均有一次真实尝试，不混同工具模式与场景变化。每任务40请求/16384输出/600秒/USD1，总12任务/480请求/196608输出/7200秒/USD12，工具数不限，用户“费用不限”授权继续有效。扩样允许基础设施故障后进入下一独立任务，但不自动付费重试，未知费用/token预留扣减；测试验证分母保留及预算不重复使用。

真实12/12评价有效，成功6/12、6唯一成功任务、4成功家族；全部6成功通过现有筛选，其余6为official_not_successful，无任务替换或提示修改。159请求/382调用、USD0.229693416费率估算、逐请求实扣未知。完整记录见`reports/scenario_expansion_report.md`。50%仅为预定小样本观察，不是新A/B优势或总体性能。

## D019 · 2026-10-21 · 新Student批量接口与真实tokenizer契约

旧Student Runner只能单调用，不能直接承接现有Teacher批量Base-SFT格式。新增独立`student.py`，不迁移或覆写P0–P2历史。固定Qwen3-4B非思考模板、实际input/output token IDs、checkpoint身份；JSONDecoder解析多个完整工具块，整批按请求前schema校验后串行执行，全部结果返回才进入下一生成。B同批发现不能立即使用新工具，A选定schema保持固定，动态返回值没有同批占位符替换。该接口保证可见性/时间边界，不声称通用判定动作语义独立；依赖真实token/ID须跨回合，猜参数等行为需真实4B测试。

首次真实Qwen tokenizer回读发现模板消息结尾是im_end加换行，而推理一般在EOS处停止；新解析器据实际输出接受两种完整形式并增加回归测试。Teacher冻结文件不改。对新6条和旧4条成功数据的113个Assistant目标逐条回读通过。新session有完整回执与中断证据，缺log probabilities，显式rl_eligible=false，不能作为主线GRPO或已有恢复checkpoint接口。

真实AppWorld协议探针使用脚本式调用，不使用LLM。受限沙箱两次卡在官方Starlette TestClient/AnyIO初始化，第二次faulthandler栈确认位置；在与Teacher相同的经批准环境运行通过，未更改依赖源码。两个隔离world各4次真实公开读取，检验独立合批、从真实返回选择下一次参数、A固定/B动态schema和合法历史。0模型请求、0任务成功成绩、0Teacher示范。真实4B权重后端、超时和checkpoint核验仍是P3-B前待办。

## D020 · 2026-10-21 · 完整示范与实际训练成本分开计量

新`sft_quality`沿用现有逐回合Assistant目标构建，要求完整数据manifest/官方train验证回执及固定Qwen模板；核对真实当时schema、前缀token一致性、只有新Assistant参与loss和padding屏蔽。Teacher供应商token不能代替Qwen长度，单独检查32768完整序列/4096目标token；任一目标超过时整条轨迹暂缓，不静默截断或拿片段冒充完整成功任务。原始Clean Dataset保持不可变。

新6条数据均通过：82目标、34559监督token，最长完整序列29039、最长prefix28922、最长目标3161。逐回合输入927022，96.27%为屏蔽loss的上下文；相邻相同prefix累计646951，总输入/每轨迹最终回合长度合计=7.456。这个比值只描述当前逐回合构建的计算开销，不等于语义去重率，动态schema变化也可能缩短相同prefix。文件系统家族监督量25283，占73.16%；正式训练集需要更多成功家族和明确的任务/家族权重，当前未应用权重或packing。

旧execution_v3包只读回归4条/2任务/1家族，与新包合计10条/8任务/5家族，但不合并不同历史协议成功率，也不把曾用于提示诊断的任务当独立验证。正式数据规模阈值尚未固定；需按scenario划分，远程安全传输重新校验manifest，再以明确训练授权验收真正Base-SFT更新、保存和重载。本轮仅准备与验证。

加入目标长度统计后再次发布到已存在报告名，被append-only机制拒绝；质量输出已完整保存，原样发布为`*_v2.json`，保留旧版。CLI现提前检查输出/报告路径，减少这类无效重复。证据包含初次失败日志、两个质量版本及原始数据哈希，不把保护性拒绝写成数据通过了未执行的检查。

## D021 · 2026-10-21 · 历史失败条件复试、根因与未启用修正

用户明确要求“分析其他任务失败原因，用A再试一次”，随后要求排查为何发生及如何修改。选择B扩样中全部6个有效失败，各从原始初态跑冻结A一次；这与先前按公开场景随机抽样不同。为避免错误声明“不使用结果”，采样器支持显式diagnostic_selection，绑定来源报告SHA，检查清单包含每个有效失败一次，并标记条件子集。旧随机manifest完全不变。不能用这个子集比较总体A/B优势，也不称为失败状态恢复。

根因依据真实回执、公开supervisor.complete_task参数说明、官方离线评价和公开观察重算。B四条只有动作任务answer误填，业务状态断言全过；另一条同时多加短信前缀、遇到不按词边界的子串检查；最后一条已完整分页却统计错多作者推荐。公开schema与execution_v3均已有相关约束，字符串参数合法不代表语义正确；“no invented answer”可能仍允许模型把真实摘要当答案，这是推断而非已知内部原因。官方状态/奖励不改，不能把这些失败清空answer后伪装成功训练样本。

A仅两条短信成功，均省略answer且正文只保留列表；两个播放任务仍误填answer，推荐仍统计错误。付款任务A只选14API、未达到20上限，漏simple_note.login；Actor在固定工具集内反复尝试无效凭据，23次执行错误，最后一条4096输出截断，累计12498仍低于16384。原B付款业务状态已完成，A反而未执行付款。这说明仅靠现有依赖提示与两个正例不保证选全；也暴露Actor未遵守“缺依赖则报告、不要猜凭据”的规则。全部负例保留。

六次模型/backend/原始状态/预算一致，Predictor输入仅由公开Task与目录重新构建；Actor未得到诊断。71请求/97调用、USD0.052638696费率估算，逐请求账单未知。两条官方成功及mask合格，新增2349监督token；107软件测试通过，没有训练。截断响应单独核对哈希、真实finish_reason和未执行边界，合法历史前缀另验证，不能让完整示范验证函数把截断伪装为已接受Assistant。

保持当前execution_v3及A/B五文件冻结。将可审查候选修正写在`docs/PROMPT_FIX_PROPOSAL.md`，不启用：动作任务省略answer的明确例子；严格正文格式；按完整嵌套字段与稳定ID聚合/校验；新命名的A依赖校验变体可在一次性开放前根据公开schema做最多一次Predictor纠正，成本计入，仍由模型选API。不要由Runner静默改参数、补登录或向Actor提供隐藏答案。新增计算工具/停机规则也须另记版本与公平预算。本轮只是证据与方案，未宣称提示修正已有效；下一步应在新的预定场景检验，避免只修这6个已知案例。

## D022 · 2026-10-21 · 用户授权独立v4与公开认证校验、新任务配对采集

用户随后明确要求按方案修改并重采A/B。旧execution_v3五文件逐字保留，以新增execution_v4和api_predictor_validated_v1实现，不修改已失败轨迹或奖励。前者共享明确的完成参数/正文/聚合/认证规则，后者保留官方选择组件及两个公开正例，检查公开schema中的同App token产出与已定义密码来源，缺失时最多一次模型纠错。该检查仅覆盖有限认证路径，不证明业务任务可解；无公开可识别来源的依赖保持未验证。控制器不执行登录、不静默增工具、不删answer；两个选择回执和全部成本进入数据证据，预测对话不监督。

为避免仅对6个旧失败过拟合，在新场景预固定10任务/5家族、每族2变体，排除先前7家族，seed=2026102104。只使用官方train成员与公开scenario映射；A/B各一次、独立初态与进程、交替顺序，每次40请求/16384输出/600秒/USD1，单回复4096，工具数量不限。全部失败/失效保留且不追试成功；A的认证纠错消耗其自身端到端预算。新旧任务不同，不能比较成功率后声称因果提升；新A有额外检查组件，也不能把比较结果单独归因于Prompt。

实现测试和真实公开schema预检后启动 `runs/v4_ab_20261021_a`；最终数据、负例和完整成本见 `reports/v4_ab_report.md`。训练授权边界不变，未加载4B权重、未启动SFT/GRPO或远程作业。

最终20次真实尝试A6/10、B8/10，14条官方成功且筛选通过，8唯一任务/4家族；新增认证校验真实触发一次密码来源纠错并成功。6条失败中4条为数值金额字符串不满足官方float规范化，另2条A缺业务关系证据（其中1条猜测全量付款并输出截断）。这证实有限认证检查不等于完整业务依赖检查，提示也未保证模型行为正确；本轮不继续在这些任务调参。全部221请求/680调用，USD0.280044588估算、实扣未知，0基础设施错误。

14条真实Qwen tokenizer目标回读/mask通过，但1条B完整序列34507超过现有32768门槛，按D020整条暂缓，保留原始成功证据；13条/148Assistant目标/39658监督token可用于当前格式。没有通过增加上下文门槛或截断来凑合格数。数据包本机复制复验通过、20份LLM视图完成，117软件测试通过；正式数据设计、真实4B后端、远程预检和训练授权仍待完成。

## D023 · 2026-10-21 · 数值正确与答案表示兼容性分开；harness方案未启用

用户认为账单金额答案应算正确，并询问harness处理。对两任务各A/B共4条进行只读控制侧复核，真实公开账单合计与模型数值一致；使用同版官方assert_plus及其真实参考值，原始带货币符号字符串均在normalize=float阶段抛ValueError，仅去掉符号后均通过。该诊断没有环境动作、模型请求、全任务重评或改写数据；隐藏参考值只用于控制侧比较，没有进入Actor。公开证据currency_answer_diagnostic_20261021.json省略原始/参考答案。

因此这4条应解释为最终答案表示不兼容，而非已证明金额算错。官方原始成绩仍保留；可以额外报告格式规范化诊断，不能当作新Teacher rollout或直接将旧失败改成合格SFT示范。

建议后续新建显式最终答案适配协议：依据公开任务及事先声明的number/money/text类型，在工具执行前将无歧义单一金额表示序列化为数值；不重算金额、不换汇、不猜百分比/区间/混合货币，不对任意字符串提取数字。保留raw_llm_call、executed_api_call和normalization_event；暴露相同工具契约给模型，训练/推理复用同一适配层。当前原始回执必须与执行参数完全一致的验证器需识别合法适配事件，不能靠改写Assistant消息绕过。若希望Student亲自生成规范形式，则返回明确harness_format_error让模型重发，额外请求计入预算。所有方法共用规则、冻结版本并保留原始/适配后结果。该方案属于可审查建议，本次没有启用、重采或新增训练。

## D024 · 2026-10-21 · 公开单值检查、一次模型同值纠正与上下文监督边界

用户随后确认方案，采用execution_v5 / numeric_answer_self_correction_v1：harness在执行前拦截明确单值数字/金额字符串，提供标明来源的harness_format_error，让模型下一次回复同值JSON number。选择模型重发是为了保留原始模型输出与实际参数一致，并让Student学习合法表示；不使用D023讨论的自动序列化。规则只依公开指令的保守可复核词形，不根据task/scenario/隐藏答案分类或判断算术。多问句、格式要求、列表、ID、百分比、范围和不可明确解析的表示不转换。该启发式覆盖有限，不能宣称所有金额任务都被识别。

纠正只有紧接着的一次回复，拒绝夹带业务工具或改变数值，原请求/token/时间/费用边界继续计数；预算已耗尽则没有纠正机会。保留原始Assistant，harness消息明确未发送AppWorld，分开统计tool_requests、真实tool_calls和harness_rejections。同批其他已执行业务调用不会回滚。Teacher/Student使用同一检查器；成功示范中含拒绝的Assistant整回合只作loss=-100上下文，之后真实Teacher纠正输出可监督。数据导出/迁移重放检查器核对反馈、mask元数据及实际调度数量，原始provider回执一致性仍强制。

旧4条金额失败只读离线回归通过，不读取隐藏答案、不改奖励；同值纠正是合成调用，不能计作新增Teacher数据。真实AppWorld探针验证拒绝不改变状态、不结束任务，随后脚本数值调用才完成环境动作；0模型请求、1真实调用、无官方成功成绩。真实固定Qwen tokenizer的合成回归检验错误金额与harness反馈都在屏蔽上下文，只有26个纠正目标token监督，没有权重加载或训练。软件回归142项通过。证据见answer_guard_offline/world/qwen_mask报告。

新清单仅用公开ID与scenario，seed2026102105固定10任务/5个此前未使用家族，A/B各一次、同预算和独立初态，失败不替换。这批恰无上述启发式可分类问句，不为了触发检查而换清单；真实采集用于兼容性和更多示范，不能证明金额自纠正实际成功率。旧四条失败仍失败，新旧不同任务成功率不作因果比较。

保留execution_v3五文件、execution_v4提示和选择器源码。共享p3a驱动因新增协议更新，历史v4锁不改；用f5ce059逐文件复核旧8文件源锁，精确复现旧实验需checkout该commit。新v5源锁绑定独立提示/检查器、共享驱动、Student及数据/mask路径。P0–P2原17项不变。新版本协议与结果见docs/ANSWER_GUARD.md和reports/answer_guard_report.md；P3-B及之后不启动。

本轮最终20次有效，A7/10、B9/10，16合格示范覆盖10唯一任务/5家族；真实harness拦截与纠正均0，分母0不能报告纠正成功率。194模型请求/474实际工具调用，USD0.216517860估算、实扣未知；4个失败均是关系证据/专辑删除集合问题。16条全部通过固定4B tokenizer及32k/4096目标门槛，143Assistant目标/44126监督token，最长序列30474。完整回执、公开输入、费用、独立本机迁移与20份LLM视图通过，旧4包26条示范只读复验。真实4B运行/训练与数值纠正效果仍未验收，不用新增总成功数填补这两个证据缺口。

## D025 · 2026-10-21 · 正式集以来源重验为起点，不累计包副本

用户授权正式P3-A约120条/60任务/20家族目标，仍不训练。对所有runs/data Clean Dataset逐包verify、重算来源与实际token；原包保持不可变。15包78条条目实际只有46个trajectory_id、28任务、15家族，75个独立尝试；别名和合并包不重复计样本或成本。manifest/回执/官方train成员/Teacher身份/成功证据须一致，冲突立即拒绝；无法验证的来源不能默默入集。

精确去重规范化调用ID后比较完整消息与每回合Schema；同任务业务调用名称+参数序列做SequenceMatcher相似度，凭据仅在相似度指纹中忽略，不改训练原文。阈值0.9是工程启发式，不是语义等价证明。每任务最多2条，优先不同相似簇、较新Prompt、B和较低输入成本，其余放reserve；完整超长轨迹deferred。原46条中45条32k合格，cap后41条。历史同任务多次成功不是新增任务。

## D026 · 2026-10-21 · 冻结v5与有界覆盖采集，承认结果条件化

继承DeepSeek“费用不限”授权，但预声明最多8批×20、每任务最多2次新尝试；cap=2成功轨迹/task。每批800请求/327680生成token/12000秒/USD20，每轨迹40请求/16384生成token/600秒/USD1；单回复4096，工具调用无数量配额。以未成功家族、未成功任务、尚未新尝试任务为先，固定seed与带SHA的coverage ledger生成清单，顺序执行新原始环境。

选择器仅使用官方train ID/scenario映射及历史成功/长度状态，Actor只获得原始公开任务与工具，不获得来源失败、评价或答案。由于使用历史成功结果选任务，清单显式hidden_labels_or_results_used=true、hidden_answers_used=false；成功率分母包括全部尝试，另列有效尝试/基础设施错误，不能称为无偏B总体成绩或A/B新比较。失败不覆盖，至多下一批按预定规则重试。

固定execution_v5源锁和新增collection锁绑定Prompt、协议、模型参数、采样算法和预算。文档v0.11描述本轮数据要求；冻结采集配置仍用project_version=0.10，以保持已验证入口和14文件源锁，不为文档升版改动Actor。采集中发现未开放工具调用、回复截断及131072字节请求边界，不临时修改Actor协议或上限。旧审计器仅重放JSON解析而不能识别“已解析但未开放”的工具拒绝，扩展审计为原预执行校验，不修改采集代码或奖励。

## D027 · 2026-10-21 · 场景隔离、训练权重及控制证据边界

在采集前对官方90个train任务/30家族固定split_seed=2026102120，6家族内部validation、24家族train；同task所有变体继承scenario划分，官方dev不进入数据包。历史Prompt曾使用部分场景，内部validation只承诺家族与训练区分离，不宣称是从未接触的开发测试。

每回合重新渲染当时真实Schema并监督当前Assistant目标，历史Assistant和所有其他角色mask；被公开harness拒绝回合保留但不监督。完整序列32768、单目标4096，超限整条暂缓，拒绝截断；用固定Qwen3 tokenizer检查前缀、调用名/参数/结果ID、Student解析回读、padding。模型输入不含来源、隐藏评价或初始数据库状态；原始完整来源包和失败证据仅在control目录。来源模型别名可能变更，记录实际backend指纹，不冒称不可变Teacher revision。

loss质量按家族→任务→相似簇→轨迹→Assistant回合平衡，分区内example_weight均值归一1。目标是样本监督token CE均值乘权重、再平均microbatch；长轨迹/大批次不会仅凭token多支配训练，但重复前缀的计算开销仍需报告，不能声称权重消除了计算成本。保留所有raw token统计与实际目标数。

## D028 · 2026-10-21 · 私有数据交接与未来SFT入口不等于已训练

正式包附逐文件SHA与可信manifest，原来源包不可变副本、schema/消息、分组/选择/权重/源码锁均可复验；归档使用确定性元数据。导入先核对外部可信SHA并拒绝路径越界/链接，再复核来源和mask，远程还须逐字节重新分词。本轮只在本机预演迁移，不声称已连接96GB服务器。

准备固定Qwen3-4B BF16+LoRA单GPU配方、短样本2步与最长完整样本1步烟测配置；非execute模式不训练。所需PEFT/Accelerate组合仅按官方约束准备，必须远程安装/实测。固定transformers的Qwen3 logits_to_keep仅计算目标前一位置logits；微型随机Qwen3 CPU测试检查加权loss与梯度等价，0 optimizer step，不是正式Student更新。

未来真实执行要求参数前后SHA变化、有限loss、显存、adapter保存/新基座重载后tensor SHA及logits比较；即便通过仍需真实4B批量后端和固定dev的Raw Base/Base-SFT-only评测。当前入口不提供自动恢复optimizer/RNG续训，明确留作后续验收。P3-B、Recovery-SFT与GRPO本轮均不启动。

## D029 · 2026-10-21 · 余额阻塞、中断封存与缺响应成本

第5批后段出现连续TeacherTransportError。只读余额查询HTTP200且is_available=true，但CNY余额无正值；一次独立16-output-token上限诊断返回HTTP402。[官方错误码](https://api-docs.deepseek.com/quick_start/error_codes/)将402定义为Insufficient Balance。既有“费用不限”授权不等于账户已有可用资金，也不能自行购买其他服务；没有请求重新授权原服务，已提示充值。本轮停止提交模型请求，继续完成不受阻的数据交付。

先暂停本次campaign和batch控制进程，等已启动worker各自写完trajectory，再终止控制进程，未打断工具批次。第6批固定清单20任务中实际启动并结束18个，剩余2个标为unattempted，不能计成失败或悄悄删去清单。离线seal脚本核验实际任务正好是原计划前缀，仅新增停止记录、summary和Clean Dataset；不改原始轨迹、模型请求、评价或历史采样。采集源码与原锁不变，正式包记录external interruption；约120目标未达而质量门槛不降低。

旧驱动只保存异常类型，没有保留这些传输错误的HTTP状态，因此不能将此前每个缺响应请求倒填为402。正式审计核对所有已收到的响应前缀、request/response差集、reservation和工具反馈；缺少完整响应的请求保留生成/费用上界，billed_cost仍未知。旧manifest的unknown_usage_attempts依赖未设置的usage_known标志而出现0，不能据此声称无未知费用；原包不改，正式汇总按原始回执统计。独立诊断请求单列并纳入总账，0 AppWorld调用、0训练示范。

第6批中断控制器没有monotonic结束记录，墙钟用config文件mtime到外部停止时刻估算并标明basis，其余完整批次仍沿用原控制器计时。后续补采应使用新run/dataset版本并保留本包；提前核对正余额，另行版本化HTTP状态记录和连续基础设施错误停止机制。现有95条可迁移数据不因本次资金阻塞失效，远程真实训练与4B后端仍未验收。

## D030 · 2026-10-22 · 区分官方未成功与评价运行错误，先修环境兼容再训练

用户要求排查v1失败。118次均B；原始SHA复核后60次未成功互斥分为25传输、3请求大小、7可见性、6截断、3预算、16完成后评价失败。对播放家族不能仅凭success=false判策略错误：两个不同任务三次原动作重放66调用全结果一致，官方关闭异常抑制均报SQLite缺songs表。异常对象与类的memory home出现一次/两次file%3A；最小真实探针确认SQLAlchemy2.1.5 URL渲染与固定AppWorld旧字符串反解析不兼容。90个终态model_hashes文件为空；新进程单例复评不能重现原判分的修改集合，需修复后全面核验。

保持原始分数、v1数据包、Prompt及依赖锁不可变，不修改官方验证器来“变成功”。旧58/90只是原valid标记分母，不能继续宣称每次均为无评价运行异常的有效尝试。95条原始成功示范不据此一概删除或自动宣告无效，但正式SFT就绪需在新隔离环境完成兼容性、状态/model-hash持久化和独立进程官方复评验收。优先验证兼容依赖组合或官方实现，未测试的降级/修补不能写PASS。

HTTP状态丢失发生在项目捕获层，应只保存安全的结构化状态码/异常类型；对401/402全局阻塞停止，对临时故障有限重试，并增加跨任务熔断。可见性/截断/停滞反馈需独立版本化并保留原预算，控制器不得自行开放API、拼接截断动作、改凭据或补业务步骤。当前仅诊断及复现，没有实施这些运行协议改动。证据与成本见reports/base_sft_failure_diagnosis_20261022.md。

## D031 · 2026-10-22 · 用户授权 Harness v1，先检查再有限模型纠正

用户要求整理并工程化上一轮方案。新增`public_protocol_correction_v1`及独立配置/源码参数锁；execution_v5正文、v3五文件及历史锁不改。Teacher和4B Student共享完整批次检查，最多两次通用纠正和两条提示性无进展反馈；数值规则仍只能使用紧接着的一次回复。纠正不增加原预算，A保持固定工具集，B通过下一回合公开发现工具获取Schema。

不合法/截断生成只存原始回执，不能以修好的Assistant消息冒充模型输出。用已在system声明的`role=user` JSON承载显式harness反馈，不伪造工具调用ID或AppWorld返回；数据turn标记拒绝且不监督，只有真实合法后续Assistant可成为目标。这样也避免把不可见API或半个JSON放入后续合法消息。回执审计重新解析原始输出并重算拒绝，反馈/历史边界/Schema/费用均复验。正常任务成功含这种显式纠错仍是Base-SFT正常示范，不冒充Recovery-SFT。

access_token只在有此前同应用真实login证据时检查一致性，不从控制侧注入或替换；无进展判断只使用相邻实际工具观察，不强制判任务完成。集合/排名/目标选择与额外业务写入不由harness代做。JSON请求字节上限新设524288，仍独立于完整32768训练序列门槛；Student实际输入加输出共同受32768约束。单回复4096、请求/token/时间/费用仍有限，工具数量不限。

Teacher旧transport已生成安全HTTP错误文本，新增捕获层只解析这一固定格式，保留状态码和未知费用预留，不存provider正文。401/402/403立即停止后续任务，连续两次传输/worker故障停止；batch停止信号传到campaign。首版不自动重发可能已计费的请求，不将未执行任务计入分母。独立评价运行异常也停止采集。

## D032 · 2026-10-22 · 兼容依赖与只读官方评价观测，历史成功另建复验证据

在独立`.venv-harness`验证SQLAlchemy2.0.52、SQLModel0.0.48、greenlet3.2.4，旧`.venv`/原锁不动。最小URI探针通过；此前播放失败的24次动作返回全一致、官方成功，另一独立重放保存非空model_hashes、状态恢复一致、新进程评价成功。选择依据是固定AppWorld源码、实际重现以及[SQLAlchemy官方2.0.52说明](https://www.sqlalchemy.org/blog/2026/08/11/sqlalchemy-2.0.52-released/)和[SQLModel官方兼容记录](https://sqlmodel.tiangolo.com/release-notes/)，不是假定降级一定有效。未编辑依赖仓库或官方测试。

新采集把官方评价放到单独进程；使用Python profile只读观察未修改TestTracker.__exit__的异常类型，同时让官方原实现完成全部测试。普通AssertionError保留官方失败；非断言/顶层异常标为evaluation_error，不视为有效0/1标签，不向Actor发送隐藏信息。不能将所有非断言异常武断解释成环境错误，需进一步控制侧诊断。比仅用suppress_errors=False更完整：后者会在首个普通断言处退出，掩盖后面的运行错误。

旧终态空model_hashes不回填、不覆盖。固定原95条选择顺序，在新进程从原始train初态重放真实Teacher历史动作，要求每个公开结果严格一致，保存/恢复新终态，再独立官方评价；新证据绑定原轨迹与数据包SHA。无模型请求，不计新增Teacher数据，也不反写旧分数。完整结果见`reports/harness_v1_report.md`及复验机器报告；本机通过仍不代表远程GPU/训练通过。

本轮只读DeepSeek余额回执HTTP200但is_available=false、无正余额，未发生成请求。已有费用授权继续有效；真实Teacher自纠正效果需账户恢复后在新run中验证，不能用软件夹具或脚本动作冒充。SFT/GRPO均未启动。

## D033 · 2026-10-23 · Harness审查修订，保留原Prompt与数据

按用户要求完成工程审查而不开发Harness v2。确认活跃任务纯文本提前停止、普通初始化异常漏计连续熔断、批次中断终态保存不足，以及评价子进程异常分类缺口。使用 `public_protocol_correction_v1` + `runtime_revision=review_20261023` 明确版本；旧源码锁不变，新配置/锁绑定修复。RULES、execution_v3/v4/v5、AnswerGuard、官方Adapter和evaluator不改。新运行缺失revision拒绝配置，不将新行为冒称旧协议。

Teacher/Student共用完整批次校验和rejection_event；意外停止使用原2次纠正机会，反馈只要求继续工具或正式status=fail。数值pending时该回复消耗唯一纠正，不延长预算。保留原始响应，拒绝不监督。已完成world不生成新请求。Provider状态只取安全回执，不从业务工具401推断；普通infra连续2次停止，评价错误立即停批。未知费用保留，无自动重发。

新增的必要证据仅为执行前intent与两端共用中断导出。逐个真实返回记录；写入后丢失返回时记录未知call ID、实际状态、部分历史和schema，明确不可直接恢复，不能补造tool结果。执行前历史指针不宣称拥有执行前数据库快照。SIGKILL/磁盘故障的终态保证仍有限，保存失败不能遮蔽原异常。未来Recovery Runner仍需真正的完整边界快照，本轮不提前实现。

真实AppWorld探针证实未完成业务状态也可能触发官方IndexError/UnboundLocalError。因此evaluation_error是一类运行结果，不直接断言环境坏了，也不变成策略0分。保守停批暂保留；若细分必须新增控制侧证据，不改断言。本轮首次探针错误预期退出1的原记录保留，第二次只修正测试预期，没有改奖励。

原95条完整审计、18独立来源包、原manifest/archive SHA均通过且不变；仅重放封存顺序首条成功历史，40真实调用及官方成功，不计新Teacher数据。加两次中断探针4调用和A/B发现2调用，本轮46真实工具调用、0生成/训练。账户余额仍不可用，真实Teacher效果BLOCKED。201软件测试、真实状态、tokenizer/mask通过不能替代4B训练通过；最终报告见reports/harness_review_report.md。

## D034 · 2026-10-23 · 充值后固定两任务验证，区分正常执行与纠错效果

用户确认已有DeepSeek余额恢复并要求验证。沿用既有服务授权及冻结review_20261023，不调Prompt/模型参数；只读确认资金后，从旧固定v5清单取前两任务做B单模式串行烟测，预先固定seed2026102301和USD2/80请求/32768输出token/1200秒总预算，不为了制造纠错事件增加任务、降低输出上限或注入错误。

实际两任务官方成功/过滤均2/2，15请求、49工具、6327生成token，形成独立新包2条；固定Qwen3-4B编码15目标/5730监督token通过，原95条不变。无任何通用/数值拒绝或业务API错误，因此只确认真实执行可用，Harness自纠正效果仍未观察到，而非继续标记余额BLOCKED或虚报修复成功率。实际token按官方峰时价估算USD0.015351492；前后余额差CNY0.03仅作为账户级证据，billed_cost不改成估算。无正式训练，完整报告见reports/harness_funded_validation_20261023.md。

## D035 · 2026-10-23 · P3-B授权、封存训练源码与独立真实评估后端

用户明确授权在提供的远程GPU完成真实Qwen3-4B Base-SFT，先数据/环境/短长烟测再正式1epoch，不沿用历史阶段“本轮不训练”的限制。原95条数据manifest与50文件锁均保持不变；训练checkout固定473693c，当前2145a93的AppWorld/Harness用于独立评估。配置通过已有CLI显式绑定可信manifest，原锁内配置不编辑。

新增本机私有HTTP推理后端（仅远程loopback）：固定Qwen revision/BF16/non-thinking/chat template，逐请求复核完整输入token和checkpoint身份；只负责真实生成，不代选业务API。评估worker复用现有StudentSession、Harness、公开工具和官方评价；原train-only Adapter不改，独立评估构造器只放行明确train/dev成员，评价结果不回传Actor。调用/回执/真实状态/工具结果与控制评价分别保存。

预先固定seed20261023，原训练/内部验证/官方dev各2个公开任务ID，两种权重共享相同6任务、4096单回复/32768总窗口/40请求/16384生成token/600秒预算。此小样本是管线及行为验收，内部validation不冒称未接触泛化集，不报告完整官方benchmark。dev只评价、不训练；原Teacher数据与历史P0–P2不变。最后先备份checkpoint和证据，再按用户要求关闭租用实例。实际结果以P3-B报告为准。

## D036 · 2026-10-23 · 实测修复BF16重载上下文，不降低门槛或改旧锁

run_001在权重加载前失败：新编排器Path.resolve解引用venv Python链接而使用系统环境；改为absolute保留解释器身份，加软链接回归测试。run_002真实短烟测完成2个optimizer step、有限loss、参数更新与保存，重载tensor哈希一致，但logits检查失败，后续阶段正确停止。

使用保存的真实adapter和官方Accelerator.prepare_model做独立诊断：默认独立推理与Trainer BF16包装最大差0.421875；两端显式CUDA BF16 autocast后最大差0，重新加载新基座/adapter也为0。原0.01绝对/相对阈值不调整。根因是FP32 LoRA运算的autocast上下文不同，非权重丢失。

原sft_recipe.py及50文件封存锁不改。新增sft_bf16_runtime.py保留封存execute主体，仅增加两次推理probe的显式autocast、补充源码锁校验与runtime_contract元数据；复用原loss/梯度/训练配置/数据检查，依然要求原50文件全部匹配。configs/p3b_bf16_runtime_lock.json额外绑定新入口与原recipe哈希。这一小段版本化重复是为了保持历史原样，后续新数据/runtime发布再合并，不动态猴补丁或绕过旧校验。独立Student后端使用同一BF16上下文。run_003从头重做两项烟测，旧失败目录及其参数更新事实保留；未通过的烟测不能记为PASS。

## D037 · 2026-10-23 · P3-B真实墙钟预算与独立评价运行锁

在实际官方appworld.common.utils.freeze_time探针中，睡眠0.1秒后time.monotonic差为0，Linux clock_gettime(CLOCK_MONOTONIC)差约0.100秒。原Student内部wall_seconds依赖前者，因此不能单独保证冻结world中的真实执行时限。正式SFT不使用AppWorld冻结时间，不受影响。

StudentSession仅增加可选clock注入，旧默认及历史源码/锁不回写；本轮评估显式使用系统CLOCK_MONOTONIC，替换其3处预算计时，并用于外层运行耗时。工具内的任务日期仍按官方冻结，未修改AppWorld或伪造时间。父进程仍有独立worker超时，未知回执/部分副作用保留原规则。新configs/p3b_evaluation_runtime_lock.json（p3b_real_clock_v1）绑定本轮全部运行源码；每个episode执行前检查，Raw与SFT共用。旧Harness review精确重跑使用其历史checkout，不能用新源码冒称旧锁通过。原50文件训练checkout保持不变。

新增注入时钟预算回归测试、远程官方freeze_time探针；209软件测试通过。此发现不据此改写任何历史Teacher/P0–P2用时、奖励或95条数据，也不宣称已全面修复旧Teacher计时路径。实际评估尚未开始时完成修复，固定任务/seed/Prompt/工具协议/600秒额度均未按结果调整。

## D038 · 2026-10-23 · 官方冻结时间与Transformers延迟导入隔离

首轮真实Raw/SFT各执行了固定清单首个任务，终态保存后关闭world时，Freezegun扫描新增Transformers延迟模块，触发integrations.bitnet中无torch的NameError；finally再次关闭又触发freezer栈IndexError，掩盖主错误并使result未写出。两个worker异常均由采集控制器记录，未赋予模型0分，也未删除。

检查官方Freezegun configure/teardown实现后，采用公开configure(extend_ignore_list=['transformers'])，仅排除控制侧tokenizer模块，并在world创建前加载tokenizer；不改AppWorld/验证器源码。真实远程探针确认公开工具可执行、任务时间仍冻结、输入token IDs不变、真实墙钟正常、close成功。正常关闭前解除adapter的finally所有权，异常清理错误另记，保证不遮蔽主错误与结果落盘。相应软件回归通过。

新增独立p3b_real_clock_transformers_v2锁，原v1锁及两个失败运行保留。evaluations_v2从原6任务/seed/预算/Prompt重新进行两模型对照，不据结果选任务或调模型。首轮2个基础设施尝试的模型请求/token/工具成本从原始回执重算后计入全部成本；有效官方成功率与基础设施异常分开。此修复不改变训练参数、正式adapter或原95条数据。


## D039 · 2026-10-23 · Base-SFT交付与小样本效果分开验收

真实run_003完成短/长烟测2/1步和正式116步、1epoch；全部参数更新/梯度/保存/重新加载通过，原95条与可信SHA不变。最终固定checkpoint及optimizer状态私有回传，公开交接报告绑定adapter文件/tensor、数据/model/template/runtime与归档哈希。没有因为任务效果有限重训、换任务或调整Prompt。

两端固定6任务各4个有效官方结果、2个非断言运行异常，Raw成功0/4，Base-SFT1/4，唯一成功在train。严格区分有效结果成功率、总尝试已确认成功比例及unknown，保留异常为null、不转换为奖励0。内部validation/dev没有成功，不能由loss下降或训练集成功推断泛化提升。语法/截断、参数Schema及可见性分别统计，不能把全部协议拒绝混称语法格式错误。

首次2条Freezegun基础设施尝试及失败smoke仍计入资源：本轮121实际optimizer steps、14任务尝试/190模型请求/213工具调用。安装/下载/空闲时间不包含在阶段墙钟中，GPU实扣未知。三包共2318088201字节已在本机核验后执行平台shell shutdown，远端断开、SSH不可达；不把连接证据冒称平台账单核验。没有自动进入P3-C或两阶段后的GRPO，后续需先明确官方评价异常边界，再按已保存Base-SFT策略产生新train失败。


## D040 · 2026-10-24 · 分开官方原始评分、项目异常标记与业务失败诊断

用户要求检查训练问题、整体效果及六个验收任务未成功原因。离线重查原12份回执：官方tracker测试计数完整，原始success为Raw0/6、SFT1/6；项目evaluation.py把一切非AssertionError标为unknown，使此前有效结果分母变4。此次空歌单IndexError/未赋值、未生成目标路径KeyError有对应模型未完成业务的证据，不能因此从总体成绩排除或断言纯环境故障。新报告同时展示官方原始分数与原guard标记，原报告/95条奖励/轨迹不回写，运行分类暂不改。

对未来版本，应区分完整官方tracker返回、业务状态相关测试异常、真实数据库/加载错误与顶层评价崩溃；不能只根据Python异常类型判定可靠性。隐藏官方内容仍留控制侧，不反馈Actor，也不进入SFT。数据审计确认全部1072目标及105次文档调用参数监督合法，5/6任务漏参不是源示范本身缺app_name。原数据缺新Harness提示/纠错示范的分布差异是风险，未做消融不作为确定因果。

本轮只新增只读诊断脚本、公开计数/来源SHA和解释；未改冻结运行协议、没有重新生成或训练。后续先精确字段反馈和匹配部署协议的诊断，不能用业务代执行、放宽奖励或无上限重试掩盖基础能力不足。

## D041 · 2026-10-25 · 官方评分与评价可靠性分列，不按异常类型一刀切

按用户授权，将 D040 的诊断落实为 `official_result_integrity_20261025`。原 tracker 完整返回、计数/成功标志/异常数一致时，普通断言和直接当前任务 evaluation.py 的 KeyError/IndexError/UnboundLocalError保留官方失败；数据库/系统故障和未知来源不作模型失败。原始官方字典与 official_success 始终独立保留，未知可用奖励为null。真实初态探针重现 e3d6c94_3 的完整false及空对象异常，不修改官方实现或旧结果。未穷尽的异常继续保守未知。

精确参数反馈采用独立 `schema_feedback_20261025`，复用Teacher/Student共有校验：仅给Schema能确定的缺字段、未知字段和类型/路径，不回显值、不代填、不增加纠错次数。旧review_20261023行为继续保留；新共享源码绑定P3-C锁，不能更改旧锁让历史运行看似兼容。

## D042 · 2026-10-25 · P3-C完整边界快照与基础设施故障隔离

复用state.py新增显式format v2，默认v1仍仅支持历史单调用。v2将批量历史、工具Schema、DB/时钟/RNG、Agent身份/预算/seed/纠错状态与SHA绑定。初始及每回合完整边界保存，相邻post/pre引用同一不可变快照，不重复复制。公开工具错误定位pre/post-error；格式重发快照只作Harness审计，不称恢复学习；无可靠局部错误时保留late-failure，不利用隐藏断言猜错误动作。

中途写入后丢返回保存真实interruption及未完成调用，不能补结果、回滚或当作合法恢复样本。服务/worker/评价未知阻断后续采集，保留原先有效结果和未知token预留。独立进程只做状态恢复核验，不执行Teacher补救。真实探针通过，尚不能替代由4B模型自然失败产生的检查点验收。

## D043 · 2026-10-25 · 小任务池分层采样与资源阻塞

原95条覆盖75/90个官方train任务；再排除固定内部validation家族和旧评测任务，仅余8个可用未见任务。预固定seed20261025的12任务/11家族清单：先8个未见，再按家族覆盖补4个train已见任务，明确exposure；不能据混合清单声称纯未见泛化，也不将validation/dev导入恢复训练。

本轮SSH客户端退出255，端口TCP连接后未给SSH握手，远程GPU未核验。本地RTX5050 8151MiB未通过90000MiB配置门槛，采集在0模型请求前阻止。遵循“不自动启动/租用付费实例”；P3-C实现与真实AppWorld软件探针PASS，真实模型采集BLOCKED。进入P4-A前先在用户恢复可用的远程实例上完成固定清单采集、自然失败筛选及模型检查点独立恢复。不能因有已训练adapter或脚本探针就宣称P3-C通过。

## D044 · 2026-10-25 · Harness有限重构：反馈审计、共享返回记录与错误分层

用户要求可靠且易维护，不重写环境/执行框架。基线0d59b00已有精确Schema反馈和评价完整性分类；不重复实现。确认schema详情在validate_record重放时丢失，补齐重放并由validate_receipts从原响应/当轮schema独立核验，防止合法纠错误过滤与伪造详情。反馈不作Assistant目标，原始非法输出仅存审计。

两端共用record_tool_result处理观察追加、实际返回/guard反馈计数、in-flight解除与落盘；保留原Runner预算/真实执行/数值guard，不引入ToolGraph或新状态机。Teacher在线与中断用量重算统一verified_response_usage；身份/边界不可信时保留原费用/token预留，Student同样不将后端故障算模型错误。原请求不自动重发。

A协议错误有限纠正；B真实工具错误保留观察；C provider/worker故障按现有熔断；D评价未知无reward；E完整官方失败为0。局部事件与最终结果分层，返回数不是全部执行次数，部分副作用和未知调用保留不可恢复标记。保留AnswerGuard、无进展提醒及A/B协议，不补业务动作。

新运行标识shared_execution_20261025与新配置/锁；默认Student精确反馈，新Teacher配置匹配。不是新Prompt或额外重试规则。旧264个保护文件和95条数据/adapter不改，旧锁拒绝新源码是预期，历史复现使用旧commit。真实AppWorld脚本与合成tokenizer只验工程，不声明模型效果；本轮不访问GPU、不启动训练或恢复学习。

## D045 · 2026-10-26 · A100重训授权与独立新运行锁

用户明确授权使用新SSH服务器重训SFT、补齐测试并在备份后关机。实测A100-SXM4-80GB 81920MiB、驱动580.126.09、Ubuntu24.04/Python3.12.3、12vCPU/120GiB容器配额；持久盘49GiB。使用原95条、单epoch配置和473693c训练源码+原BF16补充锁，不改变样本、loss或旧哈希。新checkpoint须通过短/长/正式门槛与保存重载后发布独立交接摘要。

P3-C运行改为p3c_shared_execution_20261026，明确绑定shared_execution_20261025；paired evaluation单独锁定同Harness。旧两份P3-C/P3-B锁和配置保留。prepare_student_validation.py从新训练三阶段真实证据生成新release/配置，任何未通过阶段拒绝启动后续验证；默认A100容量门槛75000MiB，原90000配置不改写。旧六任务作为已检查开发评价，12任务按原清单执行，禁止隐藏标签筛选。数据、checkpoint、实验日志迁移限授权SSH；所有结果回传SHA验证后关闭实例。

A100训练预检补充：原sft_recipe.py还要求型号包含RTX PRO 6000且>=90GiB，不能直接在A100运行。保留原文件及BF16历史运行文件，新增冻结sft_a100_runtime.py，只替换硬件门槛、补充锁和硬件元数据；完整训练/loss/更新/重载体保持一致。A100必须单卡、>=75GiB且支持BF16，所有依赖版本/数据锁/梯度/重载检查仍执行。该冻结副本是历史兼容所需的有限变体，后续不要无限复制新训练器。用相同数据重训不预先声称效果改善。


## D046：已证实的零派发预算中断不阻止未尝试任务（2026-10-26）

- 证据：A100真实P3-C `3c13f5a_1`第23回复完整返回，批次第一调用前耗尽600秒；Student记录`TimeoutError/interrupted_batch`、0派发/0尝试/无未知token，保存了不完整历史。原控制器将其归为基础设施故障并停止其余任务。原记录/评价/奖励不改，也不将这条不完整批次加入自然失败恢复池。
- 处理：新增小型控制入口`p3c_unattempted_continuation_20261026`，复用原`p3c episode/restore`入口，不修改Student/Harness/Prompt/预算。核对前缀任务、原manifest/源码/配置/模型身份；仅续跑固定12任务清单中尚未尝试的10项。新旧任务与成本一起报告，绝不重复请求来择优。
- 继续条件：零派发、零调用意图、无inflight/未知用量，且中断DB与最后完整边界DB相同、批次前历史哈希相同。只在控制侧另列`budget_interruption_before_dispatch`；其他基础设施、未知评价、部分副作用仍停止。拒绝把不完整批次伪装为可恢复快照；原自然失败过滤不放宽。
- 新控制器和旧运行锁由独立补充锁绑定；旧锁不改。8个软件用例覆盖固定清单后缀、未知用量/部分执行/状态和历史不一致拒绝；真实证据复核与续跑结果见本轮A100报告。


## D047：A100实际验收与公开审查边界（2026-10-26）

- 正式116步、参数变化/重载、固定Raw0/6与SFT4/6、P3-C12任务7自然失败/5隔离、225边界/11独立恢复分别验收。训练通过不等同广泛泛化；2个pre-error不计错误修复，5个预算中断不作自然失败示范。
- 公开代码、汇总JSON、SHA清单和报告可审查；原始AppWorld轨迹、快照、官方tracker和权重仅私有保存。审查者没有私有包时，不能将汇总PASS当作独立重放通过。
- 平台shutdown返回缺少关机密钥，自动关机失败；最终以用户报告手动关机记录，未独立查平台账单。实验结束到首次关机请求约5小时32分，明确与训练耗时分开，不隐去空闲/回传资源时间。

## D048 · 2026-10-27 · EGTM 独立上下文、证据有效性与 v3 快照

- 用户授权轻量 Task Memory，默认关闭；只新增 `task_memory.py`、`memory_store.py`、`context_compiler.py` 三个核心模块。复用 StudentSession、完整批次记录、state.py 和原评价入口，不新增 Memory Agent、Actor 工具、LLM、向量库或业务规划器。
- L1 复制真实 JSON 标量及来源，保留 ID/日期/路径/金额原值。observed 表示该时刻确实返回，非任务已经完成；Assistant 原文计划为 inferred。非 JSON/大文本留引用，原文在 L2/L3。操作记录仅声称 returned/error_observed，剩余业务要求无法确定时明确 unknown。
- L2 为内存精确索引 + JSONL 可移植快照，按 task_id、source/tool/field/typed entity ID（含ID数组）和完整批次截止位置查询。查询条件含全部分页/过滤/排序参数，空页不推广为全局结论。写入分类只用已经观察的官方 API 文档 method；未知方法或非 GET/HEAD 保守视为可能写入，跨应用使旧业务事实 stale，即使工具报错也不假定无副作用；无法提取标量的原始证据也单独记录stale来源。重新读取只替代同一精确查询的旧事实，不替其他页面下结论。
- Compiler 保留全部 system/user 原文、当前工具集及最近两完整批次，移除较早完整配对，附有来源的 Memory 与确定性检索证据。相同请求/返回不重复装入检索；视图省略量和延后原文引用显式记录。原始 messages、请求/回执、工具返回与部分副作用不改；开启后另存实际 compiled messages/token IDs 和全量原始 history。禁止切片 token 或截断目标，不能同时容纳必要上下文和剩余允许回复时明确 context_budget_exhausted。
- v3 快照要求同刻 Memory identity、索引、来源历史和 EGTM Agent runtime；恢复从原始合法历史重建事实及索引后比较，而不只相信自报哈希。v1/v2 读取规则不变。部分执行只留原中断证据，Memory 保持上一完整边界且不能附会成当前已执行状态。Harness 纠错保持原预算，不计恢复学习。
- 新 `configs/egtm_v1_runtime_lock.json` 绑定当前运行源码，旧锁一字不改并应拒绝当前变更文件。默认关闭时请求/token/动作/使用量与原路径一致。完整官方评价、AnswerGuard、Prompt、SFT mask/权重实现均不改；v3 开发探针的中间版本单列，正式运行从新锁开始。
- A/B 配置绑定 A100 正式 adapter tensor `34b87e77...e0ccd`、原固定 12 train/11 家族和同预算；这份任务清单已被检查过，不能冒充独立最终 benchmark。已有 218 请求的只读重算只证明输入压缩，模型响应/成功率、错误副作用和实际重复查询改善仍未测；不增设普通摘要基线，不因开发重算结果调任务或预算。


## D049 · 2026-10-27 · EGTM v2 模型 L1、同预算内部请求与 v4 快照

- **依据与范围**：用户要求改进 v1 缺少模型语义总结的问题。v1 `working_view()` 是规则事实视图，Student 只有业务生成，v3 从工具历史确定性重建；不重复实现索引或重新设计框架。本轮无推理/GPU授权，合成回执只作软件验收。
- **职责**：同一 Student/backend/policy/non-thinking template 产生完整 JSON L1，含九类字段。progress/completed/pending/errors/uncertainties 一律 unverified；important_facts 只能是可精确核验的原始 JSON scalar+path+source，日期/ID/路径不转换。Harness 不写业务计划或语义摘要。v1 的索引、失效元数据与原始证据仍使用原模块；写操作后的失效覆盖旧 L1 标签，不改其原始回执。
- **增量与检索**：更新载荷是 OriginalGoal+旧L1+上次成功更新之后的新历史+旧证据。新历史中已有结果不重复检索；need_evidence 的精确请求优先，未请求时最多附一个确定性旧证据兜底。没有命中的开放实体/字段查询不虚构结果；指定不存在/未来 source_id 则拒绝 L1。
- **触发**：实际下一轮编译输入达到20000 token，或最新完整批次公开工具错误/可能写操作边界；至少间隔3个新完整批次，失败也节流。可能写操作只表示可见事件，不表示阶段完成。内部更新是独立回合，P3-C 在其后保存同刻边界，下一业务动作前的快照因此包含正确 L1。协议/数值拒绝之后不插队，不增加业务纠错预算。
- **预算**：内部生成默认上限2048（业务仍4096），JSON≤6000字符，输入+输出≤32768；请求、输入/输出token、wall time共享原40/16384/600上限。记录专门计数及耗时，传输未知输出保留预留且停止，不自动重发。净输入收益必须扣除内部更新输入，不能只报业务压缩收益。
- **安全压缩**：编译器展示模型L1及证据有效性，不生成摘要。只移除成功L1已经覆盖且不属于最近2批次的旧完整交互；保留全部原始system/user约束和反馈。要求的可检索证据容不下时回退完整上下文或明确失败，不静默丢弃；更新失败后未覆盖的新历史不能删。L3始终不变。
- **快照**：新增 `egtm-model-working-memory-v2`、`egtm_model_memory_20261027` 与格式4。保存L1、索引、更新原始请求/响应、引用SHA和Agent使用量；按历史边界交错重建L2并验证已记录L1，绝不重新采样。实时校验token/text一致；离线核验可信回执SHA、请求历史与策略、输出完成、证据原值和时间边界。旧格式1/2/3继续原路径。完整Agent运行时续跑不是本轮恢复学习范围；禁止把已恢复的v2 Memory装入预算重置的新Session冒充续跑。
- **兼容与实验**：冻结 v2 独立配置/锁，历史锁原样，精确复现 v1 使用091b8f1。A/B只改变enabled，固定原A100 adapter/12 train任务/11家族/采样预算；不增加普通摘要基线。旧95条数据、checkpoint与训练mask不变；内部请求单独purpose标识，不作为AppWorld动作或Teacher正向示范。本轮不生成训练数据。

## D050 · 2026-10-27 · EGTM v2.1持续可见、证据投影与真实小样本门槛

- 起点a2dbcb2的编译器在阈值以下直接返回原历史，已接受模型L1不一定进入下一请求；L2仍可能重复长原文，缺精确字段投影。修改原三模块及必要Session接口，不新增Agent/框架。新版本与锁独立，旧源码精确复现仍使用原commit。
- 有效L1以`internal_model_memory_data`标识，明确不是新用户指令；原始system/user、当前schema、最近完整交互与未覆盖历史保留。展示可增加短上下文token，按实测计入负节省；仅覆盖旧完整批次可压缩。没有成功模型L1时不能用规则facts替代。
- need_evidence优先，原文已在实际输入时不再插入；同内容不同历史ID只附引用与原时间/有效性。JSON path精确投影保留原值、source、内容和字段SHA；field返回同名实际路径，不推断entity到对象的关联，定位对象须明确path。opaque/歧义JSON保留原文引用/必要原文，不补值。索引仍限当前任务与截止历史，stale覆盖层不改原始回执。
- 更新触发还考虑实际增量Memory请求长度；上下文压力可提前，不受三批事件间隔拖延；同边界失败不可立即重发。仍严格JSON/EOS/证据校验，预算不增。原40/16384/600计入所有业务和内部生成。
- 最终本地探针使用直接tokenizer再回放全部回执。发现脚本TokenizerPipe曾用排序JSON改变schema嵌套键顺序，Qwen模板tojson保留插入顺序，导致管道内自检不足。修复仅该脚本RPC传输，保留失败开发证据，正式模型backend原本直接渲染且未使用该管道。旧脚本token数不反写；新28请求直接回放通过。
- A/B报告需两组全部预定任务、有效评价、相同模型/配置/初始DB/首次IDs及实际L1载荷可见；未知与infra不算任务失败。不把Counter标志代替实际载荷检查。`pilot_protocol_ready`另需被接受且被后续使用的L1及无协议问题，语义审核不自动判定。
- 用户授权现有A100、原adapter、3固定任务逐对A/B共最多6次，完成后关机。控制器在运行前固定：A后B完成当前对；若任何协议/Memory校验错误，停止后续任务；infra立即停止。无重采样或结果替换，缺项必须INCOMPLETE。正式12任务未获授权；所有本轮模型错误保留，不现场改prompt重试来择优。

- **本轮真实结果**：固定首任务A/B有效失败；两组缺app_name均被反馈并纠正，B唯一L1因Schema/来源/path问题拒绝。未扩大、未改提示重试，真实L1可用性未通过。既有seed按总请求计数，内部请求使后续业务seed位移，且没有L1被接受，不能将两条后缀差异归为Memory效果。把这个限制保留在报告，暂不为此修改执行协议。
- **关机结果**：158证据文件回传核验、停止模型后平台shutdown报缺密钥；用户确认已手动关机。自动关机失败和空闲资源时间分别记录，不冒称账单已核验。
