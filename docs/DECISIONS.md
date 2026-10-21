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
