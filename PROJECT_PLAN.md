# RecoverAgent-RL：项目计划与需求文档

> **官方134复试完成与最终数据冻结（2026-10-29）**：134项已封存、调度器退出；34官方成功（28原样候选/6超长）、77官方失败、22UNKNOWN、1启动中断。实际133任务发出请求、126收到回复并执行工具，不伪称134均有完整模型执行。两轮共144成功/144任务/144场景，统一整理后130原样+8无损JSON派生=138长度合格候选，6暂缓；2070样本/338413监督token。最终包`data/full732_teacher_successes_complete_20261029/`，manifest `96265f67a5828b101f5db32a804008478d7c419a01e079f6e468b8e5f164735f`。旧包/奖励/锁保持不变；费用峰值估算33.08476232元，账户余额净增加0.65元不能证明实际扣费。无自动新采/重试、训练或B/C。报告见`reports/full732_official_retry_20261029.md`、`reports/full732_teacher_dataset_complete_20261029.md`及D072。

> **官方复试继续（2026-10-29）**：用户在成功数据整理后明确要求“继续采集”。先仅恢复暂停中的7e1be84_3 worker，原第23份回复成功落盘，无重发/新增请求；原协议判定纠错耗尽且官方失败，原墙钟含用户暂停，不伪装为600秒内自主任务。原调度器于该worker退出后恢复，仅继续余64未领取任务。69已封存结果和131成功整理快照保持不变，原80/16384/4096/600预算、官方KEY1、串行/不自动付费重试约定继续。详见D071。

> **成功数据离线汇总与采集暂停（2026-10-29）**：用户已要求暂停官方复试；69/134已封存，21官方成功（18原样合格、3超长）、30失败、17未知、1启动中断，`7e1be84_3`进程现场暂停且不计已完成。本轮只汇总首轮110+复试21共131条成功，不恢复付费调用。新私有包`data/full732_teacher_successes_20261029/`保留全部原件，120原样候选+6仅JSON字符串外空白压缩的派生候选=126，剩5条deferred；不删调用/字段/Assistant目标。来源冻结配置`configs/teacher_success_inventory_20261029_frozen.json`，清单与源码/数据SHA独立；旧95不读、旧包/奖励/锁不改，不训练。详见D070及`reports/teacher_success_inventory_20261029.md`。

> **官方 API 专项复试授权（2026-10-29）**：用户明确要求使用 `.env` 第二组 `DEEPSEEK_API_KEY1` 的官方 DeepSeek API，重跑首轮40失败+94未知共134项；原110成功（含8超长）不重跑。冻结原A身份/顺序，独立目录、从干净初态串行各一次；不自动重发未知收费请求。使用官方 `deepseek-flash`、execution_v6/dynamic_discovery及80请求/16384输出/4096单回复/600秒，沿用无独立金额上限授权但保留技术预算和费用记录。原244回执/102候选/旧锁不改；不训练、不运行B/C。当前入口与来源锁见 `configs/full732_official_retry_current_20261029.json`（早先已执行版本保持原样），这是结果条件化专项复试，不是无偏渠道比较。详见D069。

> **Stage 3A实际结束（2026-10-29）**：冻结A244首轮244项全部尝试，110官方成功、40有效失败、94主链未知；102条合格候选、8条超长暂缓，110成功终态跨进程10项校验全通过。全部错误/用量留存；见`reports/full732_a244_collection_20261028.md`及D068。停止新采集，不自动重试旧题、训练或运行B/C；下列51/193等数字保留为中途记录。

> **持续执行范围（2026-10-28）**：按用户明确要求持续监控冻结A244首轮，51项封存后串行继续余193项，结束再汇总真实成功/未知/成本；新安全HTTP诊断不改模型历史或补写推理。仍不训练、不运行B/C，详见D067。

> **最新中转站身份约定（2026-10-28）**：用户明确要求不要按响应模型名称停止，确认这些名称都是请求的deepseek-v4.1-flash。新`user_asserted_relay_aliases_v1`仅对当前显式relay配置生效：固定请求model/endpoint、保留真实response model/ID/usage，名称作为审计字段，不冒称独立权重身份验证。原39项保留，单worker继续余205项；旧锁与旧严格名称规则不改。见D066。

> **最新调度决定（2026-10-28）**：用户要求“并发错误的话就顺序进行”。两并发已停止派发并让在途任务完整收尾；共35项封存，余209项以`full732_a244_teacher_serial_20261028.json`单worker按原队列继续。旧尝试不重跑，错误与未知费用保留；并发是否造成错误尚未建立因果关系。任务/模型/Prompt/生成预算/过滤条件均不变，详见D065。

> **本轮并行执行补充**：30项后发现按任务完成顺序累计的两次传输错误会误判全局故障（熔断后仍有15份完整响应）。新独立v2续跑保留30项，余214项降为2并发；单项传输/用量未知归档，保留费用预留、不重试，继续其他未执行题。仅认证/余额、未授权模型、状态完整性问题和连续worker环境故障停止新派发。见D064。

> **最新采集执行决定（2026-10-28，覆盖串行/单任务报错即全局停止的旧约定）**：用户授权最多3个独立AppWorld进程并行继续冻结A244，保留本轮已完成5项，不重复付费；余239项逐项尝试，任务/协议/评价/数据超长及已知Provider token超限统一分类留证，后续集中处理，不自动重试。认证/余额/错误模型等全局服务问题及连续服务不可用仍停止新派发，已派发任务保留结果。Teacher thinking仅留原始审计与Teacher续传，Student数据去除此字段后再按32768/4096和Assistant-only mask验收；清理后合格即可接纳，仍超长则保留成功事实与deferred。80请求/16384生成/600秒不变，不重读旧95条，不运行GPU/Student/训练/B/C。详见D063及本轮parallel采集报告。

> **最新用户决策（2026-10-28）**：跳过历史 Teacher 复用/数据准入盘点，直接对冻结 Full732 的 **A244 所有不同任务从零采集正常 Teacher 示范**。当前 Stage 3A 的详细执行规范见 [docs/P4_STAGE3A_BASE_SFT_READINESS.md](docs/P4_STAGE3A_BASE_SFT_READINESS.md)，已有同名路径的旧版「只做历史数据清点」被取代。严格保持 B244 用于后续 Recovery-SFT/GRPO、C244 用于同一验证集；只把本轮真实官方成功且独立恢复评分可靠的 A 轨迹作为后续 M1 候选，不复用旧95示范。独立自定义协议非官方排行榜；保留原split来源、访问与版权条件及付费预算门禁，受限/未授权任务不冒充已完成。Stage 3A 完成即停，不自动训练新 M1，也不运行 Stage 4–6。

> 2026-10-23 P3-B授权更新：用户明确授权在已提供的远程RTX PRO 6000 96GB上，数据/环境/短长烟测全部通过后执行既定Qwen3-4B Base-SFT和固定Raw对照；保留95条原数据与源码锁，结束回传checkpoint并关机。不启动Recovery-SFT或GRPO。旧阶段“不训练”文字为当时范围，当前以本项授权为准；实际验收见`reports/p3b_base_sft_report.md`。

> 2026-10-25 P3-C授权更新：先修正评价分类与具体Schema反馈，使用既有4B Base-SFT采集固定train任务自然失败，保存多检查点并独立进程验证。固定12任务中8个未见、4个已见，分开统计且排除内部validation家族。不自动开启付费机器，不重训Base-SFT，不提前启动P4-A/Recovery-SFT/GRPO。新协议独立锁定，旧证据保留。

> 2026-10-26新授权：在用户已提供SSH的A100 80GB上重新执行原95条既定Base-SFT，门槛全部通过后完成固定Raw/SFT对照及P3-C。新Harness使用独立运行锁；保留旧数据/源码/checkpoint，回传SHA校验后关机，不启动P4或GRPO。

> 英文名称：Self-Improving Tool Agents through Failure Recovery  
> 版本：v0.21
> 创建日期：2026-10-19  
> 状态：本轮Phase0–1全任务池审计及三阶段协议准备；732/244元数据已核验，完整自定义训练受官方用途限制BLOCKED，仅train的30/30/30方案独立冻结；90初态/公开工具通过，84有效评价与6未知，评分门槛PARTIAL。新M1/M2/M3未训练、未授权；历史Harness29k结果及关机确认保留。
> 使用对象：项目负责人、负责实现与实验的 Codex。  
> 配套文件：项目根目录的 `AGENTS.md`。

## 0. 如何使用这份文档

2026-10-27 最新提示变更：用户指定的英文system全文原样成为`execution_v6`，见`src/recoveragent/execution_v6.py`。Teacher/Student使用同一文本，不追加旧提示段落；执行校验、有限纠错、预算和官方评价不改。当前三阶段共享评估入口默认使用`configs/shared_stage_validation_v2.json`及`configs/execution_v6_runtime_lock.json`，任务划分/模型/工具模式/Memory/预算与v1一致，评分PARTIAL仍阻止后续运行。B动态发现使用v6，A和历史实验用原版本；此次只有软件修改，无模型或训练授权。


2026-10-27 最新范围（共享阶段验证）：以真实任务索引审计完整池，不把732/244假设当结果；遵守官方train/dev/test用途。按seed20261028的独立哈希排序，在每个合法三变体场景内分配A正常Teacher/Base-SFT、B新Student失败/Recovery-SFT/在线GRPO、C三个阶段共享开发验证。全池244/244/244是受限候选；仅官方train的30/30/30是操作方案。C不进入任何参数训练，M1需从原始Qwen3-4B新训，M2续M1、M3续M2。旧95条数据、64训练任务、11内部验证任务及历史权重保持原样。

主评估统一从干净原任务初态执行，使用相同Prompt、公开动态工具、40请求/16384生成/600秒、32768窗口/4096回复、seed42、Memory关闭；不加载恢复前缀。输出M1/M2/M3逐task矩阵和完整TGC，未知及缺失不当0。按场景覆盖的已见家族开发验证，不是官方SGC或未见场景泛化。逐阶段验收门槛及接口见`reports/unified_training_plan.md`、`configs/shared_stage_validation.json`。本轮只Phase0–1、本地探针、必要数据接口及软件测试；不采集Teacher、不连GPU、不重训或启动Recovery/GRPO。

以下段落是保留的历史授权/范围，不表示本轮仍可执行已结束的GPU实验。


2026-10-27 当前范围（Harness 29k）：按用户要求暂退Harness管理Memory，无内部模型生成；实际输入达到29000才压缩旧完整批次，保留压缩边界直到再次接近阈值。新独立版本/锁，旧版本及全部数据不改。先完成本地代码、测试、真实AppWorld探针并通知用户；用户负责开机，之后重核现有GPU，沿用原3任务、同Base-SFT、同预算A/B。本地准备完成后用户确认已开机，已按原配置执行3/6次并因协议纠错耗尽停止，实际证据见D052及`reports/egtm_harness_29k_live_report.md`；未训练。前一阶段本地证据见D051及`reports/egtm_harness_29k_report.md`。

历史范围（EGTM v2完善）：默认关闭，保留现有架构；使有效模型L1持续进入业务输入，历史压缩独立控制；L2按原证据精确字段投影及去重。新增v2.1内存/运行锁，原始历史、1/2/3/4快照和旧锁不改。先完成软件/真实AppWorld脚本及直接tokenizer回放；用户随后明确授权现有已开机A100上的固定3任务A/B最多6次小样本，完成后关机。逐任务核验同权重、初态、首次token IDs、预算与真实L1，协议问题停止扩样。正式12任务A/B未获授权，不重训/P4-A/GRPO。详见D050与`reports/egtm_v2_improvement_report.md`。

历史范围（EGTM v2初版）：保留 v1 的 L2/L3、上下文编译与历史快照，增加同一 Student 的独立内部请求，增量生成严格 JSON L1。语义判断保持 unverified，原值/来源可核验的事实区分 observed/stale；按实际输入阈值及完整批次公开事件节流更新。内部请求计入原 40 请求/16384 生成 token/600 秒预算，失败保留旧 L1。编译器只移除有效 L1 已覆盖的旧完整批次，原任务/所有约束不替换，无法安全容纳时明确失败。新 v4 快照验证回执而不重新生成模型记忆；旧 v1/v2/v3 可读、旧源码锁不变。默认关闭；A=原 Base-SFT，B=同权重模型 EGTM，只有这两组。本轮仅软件及无真实模型的 AppWorld/tokenizer 探针，不推理、不训练、不启用 GPU、Teacher Recovery 或 GRPO。详见 D049、`reports/egtm_v2_report.md`。

本文件是项目的需求与实施依据，后续根据实际代码、环境能力和实验结果修改。它规定目标、默认方案和验收条件，不预先宣称算法有效，也不代表文档中列出的程序已经存在。

**用户最新确认的强制顺序：Teacher 从干净初始状态生成正常成功任务轨迹 → Student 基础 SFT → Base-SFT Student 自主执行并暴露真实错误 → Teacher 从错误检查点接管并真实修复 → Student Recovery SFT → Student 自己恢复 rollout 的 GRPO → 多轮 Teacher 辅助增量学习。基础 Teacher SFT 与恢复 Teacher SFT 是两个不同的、都不可跳过的训练阶段。**

开始实现时，先阅读 `AGENTS.md` 和本文件，再检查现有仓库。如果已有代码或阶段报告，应从已经验证的状态继续；不要重新搭建已经完成的部分。首次实现时创建 `docs/STATUS.md`，记录当前阶段、证据、阻塞和下一步。

全文约定：

- **已确定**：当前项目方向，修改时需要记录原因，并遵循用户后续决定。
- **默认方案**：可以根据兼容性、资源和预实验调整，不需要为常规技术取舍逐项询问。
- **待验证假设**：实验问题，不是已经成立的结论。
- **拟实现接口/命令**：本项目计划新增的能力，不能当作上游现成 API。
- **阶段验收**：推进工作的证据条件，不是每一步重新请求确认的要求。

### 当前执行入口

v0.10（2026-10-21）用户确认金额格式检查及一次模型自纠正方案。新 execution_v5 在 v4 上声明数值类型与显式 harness 反馈；规则只读公开指令，保守识别单一总额/平均金额/数量，拒绝明确数值字符串形式的成功完成调用，最多由下一次模型回复重发同值 JSON number。没有执行器自动改值、隐藏标签反馈、额外预算或静默重试。存在歧义时不转换、不猜类型。工具参数必须与原始模型回执一致；真实 AppWorld 调用与 harness 拦截分别计数。拒绝 Assistant 保留完整上下文但不作为 Base-SFT 目标；Teacher 与 Student 共用代码。先四条旧失败离线回归，再固定新任务 A/B，脚本探针不得算成示范或模型成绩。旧协议和数据保留，正式训练边界不变。详细契约见 `docs/ANSWER_GUARD.md`。

2026-10-20 用户新增要求：保留 `dynamic_discovery`，增加 `api_predictor`，比较动态发现与官方 API Predictor 的工具选择方式；单次模型回复输出上限调整为 **4096 token**。本机先以相同 Teacher、相同 train 任务、相同端到端预算进行小样本配对验证，Predictor 请求计入预算与成本。这个比较不代表 Qwen3-4B 正式成绩，不启动 SFT/GRPO。完整协议和结果见 `docs/TOOL_MODES.md`、`docs/STATUS.md`。P0–P2 历史实现与证据保持原样。

v0.6 后续要求：**工具调用次数不限制**。新 P3-A 的 `limits.max_tool_calls` 和 `teacher.max_tool_calls_per_turn` 默认均为 `null`；保留 token/时间/模型请求/费用预算，不擅自截取批次或伪造未执行结果。核对原失败的真实 prompt 和每个请求，区分合理批量、隐藏预算冲突与漏选认证依赖；修改官方 Predictor 提示必须保留原版对照和独立变体名称。无训练授权。

v0.7（2026-10-21）用户确认：A 的 Predictor 明确补齐认证、参数来源、分页/详情和验证等前置依赖，加入两个基于公开 API 人工编写的正例；A/B 共用“理解任务→获取证据→执行→纠错→验证→完成”的全局执行规则，B 明确按需发现与下一轮开放工具。新默认为 `execution_v3` / `dependency_examples_v2`，保留官方原版与旧增强版；示例不是执行成功轨迹，不用 hidden required_apis，不进入 Actor 历史或 SFT 目标。比较计入全部提示/预测成本，仅在 train 做小样本回归，不启动训练。契约见 `docs/PROMPTS.md`。

v0.8（2026-10-21）当前任务：从 cc53bea 冻结上述提示和两种工具选择协议，保留全部历史结果，不再针对旧两个 Spotify 任务调参。使用官方 train 的公开 scenario_id 分组、固定 seed 和可核验任务清单，采样 10–20 个不同任务并覆盖至少 3–5 个家族；不得读取隐藏难度/结果选择任务。报告总尝试、全部失败、唯一成功任务、成功家族、筛选原因及完整 Teacher 成本。扩展配置独立于旧两任务 pilot，本轮采用 B 单模式采集，不宣称 A/B 新比较结果。

同时新增独立 Qwen3-4B Student Function Calling / Batch Tool Calling 接口：独立调用可合批，动态依赖等待真实结果，整批 schema/参数合法后才执行；保留旧 Student Runner 与 P0–P2 历史。用固定 4B tokenizer 检查每回合真实 schema、Assistant-only mask、最长序列/目标、监督量及重复上下文成本；超过上下文/输出预算时整条暂缓，不静默截断。完整数据格式通过不等于真实 4B 模型已运行或训练完成。仍在本机采集；正式 SFT、GRPO 和远程 96GB GPU 训练不启动。操作契约见 `docs/TEACHER_EXPANSION.md`、`docs/STUDENT_PROTOCOL.md`。

同日追加诊断：用户要求分析本轮B失败，并用A各复试一次。允许明确标记的“历史失败条件子集”，来源报告与清单绑定哈希；不是新的无偏任务采样、总体A/B比较或失败状态恢复。A从原始初态执行，不获得B轨迹、错误提示或隐藏评价。当前冻结协议不变；根因与候选修改另见 `docs/PROMPT_FIX_PROPOSAL.md`，未启用的建议不算已修复或已证明有效。

v0.9（2026-10-21）用户明确授权上述方案及新 A/B 采集。新 execution_v4 增加明确的完成参数、精确正文、嵌套统计和认证行为规则；旧 execution_v3 与五文件协议锁不变。A 新增 `selection_protocol=api_predictor_validated_v1`，读取公开参数/成功返回 schema，检查同应用 token 获取及已定义密码来源，缺失时至多一次 Predictor 纠错（完整列表重选），全部开销计入共享预算。工具仍由模型选择并在 Actor 执行前固定；不暗补业务操作、不根据隐藏答案改写动作。该项目变体不能冒称官方原协议，也不证明所有依赖完整。

新采样固定10任务/5新家族，排除此前7个已诊断家族，seed=2026102104，A/B各一次共20尝试。每尝试40模型请求/16384输出/600秒、单回复4096、工具数量不限，交替A/B执行顺序；全失败/失效/成本保留，不追加择优重试。采集前锁定代码与清单；官方成功再经完整性筛选、Qwen3-4B动态schema与Assistant-only mask检查。不得将新旧不同任务的成功率差解释为修正的因果提升，正式SFT/GRPO和远程训练仍未授权。本轮协议见 `docs/EXECUTION_V4.md`。

首次收到“开始实现项目”的指令时，默认完成 **P0–P2 的最小闭环**：

1. 检查环境与已有代码，锁定 AppWorld 版本。
2. 跑通一个能执行真实工具调用的 Agent。
3. 采集至少一条自然产生的失败轨迹。
4. 保存对应环境状态、合法对话历史和工具配置。
5. 从相同失败起点完成四次隔离的恢复尝试。
6. 得到四份可由官方验证器核查的结果与阶段报告。

**P0–P2 基础设施已在开发分支完成最低验收，四条恢复奖励均为 0，不能声称恢复能力提升。** 下一阶段是 P3-A Teacher 正常任务成功示范、P3-B Student 基础 SFT、P3-C Base-SFT Student 新自然失败；多检查点恢复、恢复 SFT 和 GRPO 属于后续 P4。本文档修改不代表启动训练或授权付费服务。

### v0.11：正式 Base-SFT 数据集构建

本轮目标约120条合格完整Teacher轨迹、60以上任务、20以上scenario家族；仅官方train，质量优先。先重验全部历史manifest与来源，包副本去重，原包不可改。冻结execution_v5、工具协议和DeepSeek参数，主要B动态发现，A历史成功作为补充。按成功覆盖缺口采样必须标明结果条件化，不能报告成无偏总体成功率。

采集最多8批×20任务，每任务最多2次新尝试；每批提前固定清单/seed/来源。每轨迹40请求、16384生成token、600秒、USD1；每批800请求/327680生成token/12000秒/USD20。单回复4096、工具次数不设数量配额，不为凑数放宽筛选或冻结协议。

正式集最多2条/task，记录相似行为簇；按全train的scenario固定分组（6个验证家族），对家族/任务/相似簇/轨迹/回合分配平衡loss权重。完整序列32768、目标4096，任一超限整轨迹deferred，不截断。只监督当前真实Teacher Assistant，拒绝回合/历史Assistant与其他角色/padding屏蔽。模型输入与控制评价分开并严格核验。

准备私有SHA交接、远程BF16+LoRA配置及短/长样本烟测入口，本轮不启动Student训练或GRPO。数据编码、远程参数更新/保存重载、正式dev评价分别验收；内部validation并非完全未接触的泛化测试。细则见`docs/REMOTE_BASE_SFT.md`与`reports/base_sft_dataset_v1_report.md`。

## 1. 项目定位与动机

### 1.1 要解决的问题

多步骤工具任务中，一次错误不仅会产生错误文本，还可能改变后续执行所依赖的状态。例如，Agent 更新了错误对象，重复创建记录，或者在未满足前置条件时执行下一步。后续决策需要理解已经发生的操作，检查当前状态，再选择继续、修正或补偿。

本项目研究：**如何把当前 Agent 自己产生的失败，转换成可执行、可验证、可持续更新的训练任务，使同一个策略模型提高正常任务执行与错误后恢复的能力。**

以 AppWorld 的有状态、多应用任务作为实验载体。业务示例用于说明问题，不代表接入了真实企业系统，也不代表已经具备生产部署能力。

### 1.2 基础能力与恢复能力分离

基础能力阶段：固定的更强 Teacher 从 AppWorld train 的干净任务初态自主执行公开工具，得到官方验证成功的完整正常轨迹。Student 模仿这些 Assistant 行为，学习函数调用格式、API 发现与正常任务规划，形成 Base-SFT checkpoint。

恢复学习阶段：只有在 Base-SFT 完成后，Student 才作为新策略自主执行并产生新自然失败。保存合法环境检查点、对应时刻的可见历史和工具 Schema。Teacher 从已失败的真实状态继续操作、修复，并在官方验证成功后输出恢复示范；Student 只模仿 Teacher 的恢复后缀 Assistant token，再从自己的恢复 rollout 中学习 GRPO。

主要研究增量：**Multi-Checkpoint Recovery Learning**。同一失败任务不局限于终态，可从多个状态与历史对应的检查点生成恢复任务，优先 Post-Error 与 Late-Failure（继承已发生的错误），Pre-Error 单独作为错误预防消融。必须控制同源任务权重和 Teacher/Student 预算。

自迭代机制：**Teacher-Assisted Iterative Recovery**。每轮由新 Student 自己执行、生成新失败，再由 Teacher 接管选定检查点，生成新的成功恢复示范进行增量 Recovery SFT，结合当前 Student 自主恢复 GRPO 更新参数。属于 Teacher 辅助的持续改进，不是完全无教师自学习。

ReTerm 已有失败状态恢复、Teacher SFT 与恢复 GRPO。中间状态分叉亦非独创。具体的检查点选择与增量训练效果须由公平消融验证，不预先认定有效。

### 1.3 三个核心研究问题

1. **基础学习是否必要？** Teacher 正常成功轨迹 SFT 能否减少 Qwen 工具格式错误、提升正常任务完成率？
2. **哪种检查点值得恢复？** 在相同 Teacher 成本、SFT token 和任务权重下，Terminal-only、First-Error、Random、Multi-Checkpoint、Adaptive 分别有什么效果？
3. **持续 Teacher 接管是否有价值？** 每轮引入新的自然失败和 Teacher 修复示范，与只做一次恢复 SFT / 只做 GRPO 相比，是否提升完整任务成功率、恢复率和样本效率？

## 2. 目标、范围与成功标准

### 2.1 分层目标与验收

| 层级 | 交付要求 | 不能由此宣称 |
| --- | --- | --- |
| 已验收 P0–P2 | 真实执行、自然失败、检查点与四条恢复分支 | 模型已经能恢复或 RL 已可训练 |
| P3 基础能力 | Teacher 正常成功轨迹 → Student Base-SFT 真训练/重载 → Base-SFT Student 自然失败 | 已完成 Recovery SFT 或 GRPO |
| P4 恢复学习 | Student 多检查点 → Teacher 真实恢复 → Recovery SFT 真训练/重载 → 在线 GRPO | 已证明总体算法提升 |
| P5 自迭代研究 | 至少两轮新失败、增量 Teacher 示范、增量 Recovery SFT 与 GRPO，完成消融 | 无教师完全自进化 |
| P6 最终报告 | 固定官方测试、训练预算、失败案例、可复现结果、Demo | 单次样例可代表完整性能 |

效果提升是待检验研究假设，负结果必须保留。

### 2.2 当前已确定的范围

- 环境只使用 **AppWorld**。
- **唯一正式主线 Student：`Qwen/Qwen3-4B`**，实际版本和 chat template 必须固定。Qwen3-1.7B 仅为 P0–P2 本机基础设施历史记录，不作为正式主模型或混入 4B 结果。另使用参数固定的更强 Teacher 生成示范。
- **强制两次 Teacher SFT：正常任务 Base-SFT 在前，失败后 Teacher Recovery-SFT 在后**；所有主线方法从同一个 Base-SFT checkpoint 初始化，纯 GRPO 采样消融再使用同一个固定 Recovery-SFT checkpoint。
- 基础示范来自 Teacher 在 train 上从原始初态执行的正常成功任务；恢复示范来自 Base-SFT Student 自主执行产生的**新自然失败**及 Teacher 在真实失败状态上的恢复后缀。P0–P2 旧基座失败只作诊断。
- 初版采用文本与结构化工具调用，不做网页视觉或多模态交互。
- 先实现串行隔离恢复，再根据瓶颈增加并发。
- 正常任务与恢复任务共同服务于最终完整任务能力。
- 最终正常评测时，Actor 只能使用任务允许的工具，不能调用环境回滚或隐藏验证器。

### 2.3 首版不纳入的内容

- AWM、自动环境生成、第二套环境迁移。
- 独立 Failure Diagnoser、Fault Evolver、Memory Agent 等多模型系统。
- 以人工注入故障替代自然失败作为主实验数据。
- 直接迁移 ReTerm 的完整分布式训练栈。
- 大规模多卡训练、前端平台、真实业务部署。
- 缺乏可验证定义的安全评分或复杂过程奖励。

这些内容不妨碍未来扩展，但不能阻塞当前闭环。

### 2.4 技术选择与可调整参数

| 项目 | 首版默认 | 调整依据 |
| --- | --- | --- |
| 环境运行方式 | AppWorld 本地 Unified 模式 | 官方兼容性及目标机器能力 |
| Agent 接口 | 原生 Function Calling 的交互方式 | 选定版本的实际接口 |
| 正式 Student | `Qwen/Qwen3-4B`（需固定 revision） | 不因本机 8GB 自动降级正式模型 |
| 本地旧模型 | Qwen3-1.7B 仅用于历史 P0–P2 与无训练的兼容性检查 | 与 4B 正式训练及效果严格分开 |
| 恢复组大小 | 4 | 吞吐、奖励差异和预算 |
| 环境并发 | 1，四分支串行 | 隔离通过后再提高 |
| 正常/恢复训练组比例 | 1:1 | 任务能力保持与开发集结果 |
| 初始奖励 | 官方严格任务成功的 0/1 信号 | 成本/安全项需另行验证 |
| 正式训练设备 | 远程 RTX PRO 6000（目标 96GB），初始采用 4B BF16 + LoRA | GPU/驱动/CUDA/TRL/PEFT/推理协议必须先预检 |
| 训练框架 | 优先评估 TRL，必要时接自定义 rollout | 多轮轨迹、分组、mask 和 worker 契约是否满足 |
| 两阶段 Teacher SFT | 正常 Teacher 成功轨迹 → Base-SFT；新 Student 失败 → Teacher 恢复轨迹 → Recovery-SFT | Teacher、样本规模和参数可调整；顺序不可变 |

### 2.5 本机 Teacher 数据采集与远程 4B 训练（v0.4）

**本机（已验 RTX 5050 8GB）**：负责 AppWorld 正常 Teacher 工具执行、官方成功验证、过滤与结构化轨迹导出。Teacher 可以是另行合法接入的更强远端/本地服务，实际可用性和费用必须核查。不得把 Qwen3-1.7B 生成的 token IDs 当作 4B 训练标签。正式 4B SFT/GRPO 不安排在本机。

**远程（单张 NVIDIA RTX PRO 6000 Blackwell 96GB，2026-10-23已实测）**：负责 P3-B Qwen3-4B 基础 SFT、P3-C Base-SFT Student 执行与新失败、P4 多检查点 Teacher 恢复、Recovery-SFT/GRPO、P5 迭代。远程必须运行匹配版本的 AppWorld worker 才能进行真正的在线 agentic RL；仅复制教师文本不够。

**传输和环境验收契约：**
1. 本机导出未分词的 **结构化多轮消息**（User/Assistant 工具调用和参数/对应真实 Tool 返回），保留任务与场景 ID、模型身份、轨迹合法性、成功/失败分母及调用成本。只能从 train 构建训练数据。
2. 提供带 SHA-256 的 manifest，记录 AppWorld commit、数据版本、任务划分、Teacher 模型/revision、公开工具 schema、消息协议、示范与过滤版本、样本数量及许可说明；不将原始基准数据、权重、凭据或其他受限文件提交到公共 GitHub。
3. 通过经过授权的 SSH/SCP/rsync 或受控存储迁移数据；远程先验证文件哈希、train 来源、schema/消息对齐与版本，再用固定 Qwen/Qwen3-4B tokenizer、chat template 重新分词并生成 Assistant-only SFT loss mask；User/System/Tool/Padding/隐藏评价一律屏蔽。
4. 远程 AppWorld 使用与本机匹配的源码/data 版本，须实际验证跨主机环境快照与历史一致；不要假设本机绝对路径可直接复用。AppWorld 环境依赖与 4B 训练依赖可隔离为两个 venv/进程，通过受控服务通信。
5. 单张 96GB GPU 初始验证 BF16 LoRA SFT，然后验证最小当前策略 GRPO；优先简单生成方案或经过兼容测试的 vLLM colocate，**不得**把需要独立训练 GPU 和推理 GPU 的 vLLM server 模式照搬到同一张卡。控制 context、group size、KV cache 和显存峰值，先小负载验收再扩展。
6. Qwen3-4B 的 thinking/non-thinking 模式、revision、tokenizer、chat template 和工具调用协议必须固定并记录；如调整则重跑格式合法性与损失掩码测试。
7. 远程机器型号、显存、驱动、CUDA、Python、PyTorch、TRL/PEFT/vLLM、SSH/磁盘和权限均待正式预检。没有资源/付费授权时完成不受阻的数据/代码准备，不标记远程训练成功。

参考：[Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B)、[RTX PRO 6000](https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000-family/)、[TRL GRPO](https://huggingface.co/docs/trl/grpo_trainer)。

## 3. 上游依据与必须核验的工程事实

资料核对日期为 2026-10-19。以下记录用于避免沿用未经验证的旧接口；实际开发仍需固定版本并运行验证。

### 3.1 AppWorld

- AppWorld 提供数据库状态保存/加载、任务 API 与状态验证；官方支持 `train`、`dev`、`test_normal`、`test_challenge` 四个划分。[S1]
- 当前核对的源码版本为 `42b5bcf3cd334fee33f0c37c02070a9f5807add5`。这只是文档核对版本，不是项目已经安装的版本。[S2]
- 在该版本中，`save_state()` 无参保存默认输出状态并返回 `None`；显式传入 `state_id` 才生成命名检查点。`load_state(state_id)` 在当前 world 的检查点目录查找该 ID。[S2]
- 新 world 初始化会准备自己的输出目录。不能把原始快照放在可能被重新初始化覆盖的目录中，也不能假定另一个 worker 可以直接使用源 world 的 ID。[S2]
- Unified 模式下，并行独立 world 需要进程隔离；每个执行分支应有唯一输出身份。线程或多个 Python 包装对象不足以证明隔离。[S3]
- 状态恢复不能被视为完整 Agent 快照：messages、工具列表、调用 ID、运行时变量、计数器和其他状态都需要单独管理或证明不影响后续执行。[S2][S3]

同一个 world 内的接口示意如下；这不是跨进程恢复实现：

```python
state_id = "checkpoint_005"
world.save_state(state_id)
# 执行待验证的真实状态修改后，再在同一 world 恢复。
world.load_state(state_id)
```

不要继续使用 `checkpoint = world.save_state()` 后把返回值直接传给 `load_state()` 的旧示例。

### 3.2 ReTerm 与训练框架

ReTerm 的恢复队列、同起点分叉和正常/恢复混合训练作为方法参考。这里实现的是 **AppWorld 上的 ReTerm-style baseline**；除非逐项复现模型、数据、训练器与配置，否则不称为完整复现 ReTerm。[S4][S5]

当前 TRL 文档提供工具、多轮环境与自定义 rollout 接口，但环境相关接口仍带有实验性约束。文档中的 `environment_factory` 要求特定依赖版本，并可能自动暴露环境的公开方法。是否采用它，必须通过本项目的分组、进程隔离和工具白名单验证。[S6]

不要把 `load_task/reset/restore/snapshot/evaluate` 之类的项目能力需求写成已经核对的 ReTerm 精确方法签名。若选择其适配器，先读取固定 commit 的真实协议。

### 3.3 版本记录要求

P0 产生 `reports/environment_report.md` 和机器可读版本记录，至少包括：AppWorld package/commit、数据版本、Python、PyTorch/CUDA（若使用）、Transformers、TRL、PEFT、推理后端、模型与 tokenizer revision、chat template 哈希、设备与资源上限。

未知项写 `NOT_CHECKED` 或 `NOT_INSTALLED`，不能填猜测版本；训练前必须补全实际使用项。

## 4. 基本概念与执行协议

### 4.1 术语

| 术语 | 本项目含义 |
| --- | --- |
| Actor / Policy | 生成任务决策与工具调用的可训练模型 |
| 正常 rollout | 从官方原始任务状态执行的一次完整尝试 |
| 决策回合 | 一次模型生成；与工具调用数分开计数 |
| 工具调用 | 一次实际尝试分发的 API 调用，包括失败调用 |
| 训练更新步 | 一次优化器更新，与 Agent 执行步数无关 |
| 失败记录 | 原任务、源策略、失败轨迹、可恢复起点及离线评价的组合 |
| 恢复起点 | 某个环境状态与同一时刻的可见历史、工具配置的组合 |
| 恢复后缀 | 从恢复起点以后由当前策略新生成的交互 |
| 恢复组 | 同一起点下的 G 条独立恢复后缀，默认 G=4 |
| 自迭代轮次 | 采集新失败、生成训练数据、更新、开发集评测的一次循环 |

### 4.2 必须分开的三种协议

| 协议 | 起点 | 可以证明什么 |
| --- | --- | --- |
| 正常任务执行 | 原始任务状态 | 固定预算下的完整执行能力 |
| 错误后恢复 `post_action` | 错误已经发生的状态，保留其后果 | 能否检查并修补已有错误 |
| 错误前重新分支 `pre_action` | 错误动作尚未发生的状态 | 能否通过另一条决策路径避免错误 |

`pre_action` 是反事实训练，不直接记作“修复了已经发生的错误”。两类数据可以用于训练，但恢复评测必须分开报告。

如果动作 `a_t` 将状态 `s_t` 变为 `s_{t+1}`，那么恢复到 `s_t` 时只能使用执行 `a_t` 之前的历史；恢复到 `s_{t+1}` 时才可以包含它的工具结果。不能混入未来观察。

对于错误宣布完成的任务，可以选择 `complete_task` 之前的状态作为 `pre_termination` 起点，同时截断历史中的终止动作及以后内容，并记录该协议选择。

### 4.3 Actor 可见信息

Actor 可见：任务指令、允许公开的任务信息、可用工具文档、自己此前的决策与真实工具返回、统一的继续执行提示。

训练控制器可以读取：官方验证结果、快照、数据库一致性信息、组内奖励、经验统计。这些内容不自动成为 Actor 输入。

Actor 不可见：隐藏测试、标准答案、目标数据库状态、详细验证失败断言、后续轨迹、其他分支结果、状态管理和评估接口。

默认恢复提示只要求从已有状态继续，避免重复已完成操作，不说明哪个隐藏测试失败。如果后续实验加入“上一轮未通过验证”的告知，必须记录为额外失败通知，所有恢复基线使用同样信息；不能据此宣称 Actor 自主发现了隐藏错误。

### 4.4 完整训练闭环（v0.3）

```text
Teacher 正常 train 任务成功轨迹 → 官方验证 → Clean Dataset
       ↓
Student Base-SFT（基础工具能力）→ 固定初始 checkpoint
       ↓
Base-SFT Student 自主执行新的 train 任务、产生新自然失败
       ↓
合法多检查点（pre-error / post-error / late-failure）
       ↓
Teacher 分别从真实状态接管恢复 → 官方验证与过滤
       ↓
Student Recovery SFT（只训练 Teacher 恢复后缀）
       ↓
Student 自主多次恢复 → GRPO
       ↓
当前 Student 再执行新任务 → 新失败 → Teacher 新示范 → 增量 SFT + GRPO
```

Teacher 与 Student 只获得公开任务和真实可见工具。训练控制器负责快照、隐藏评估和分支奖励，不得泄露到 Actor。

## 5. 功能需求与模块职责

| 编号 | 模块 | 必须提供的能力 | 首次交付阶段 |
| --- | --- | --- | --- |
| FR-01 | AppWorld Adapter | 创建/关闭 world，执行合法工具，获取任务信息 | P0 |
| FR-02 | Agent Runner | 正常执行、格式校验、预算控制、完整历史 | P0 |
| FR-03 | State Manager | 命名快照、导出/导入、历史配对、一致性验证 | P0–P2 |
| FR-04 | Failure Collector | 分类自然失败，排除基础设施故障，保存候选点 | P1 |
| FR-05 | Failure Buffer | 持久化起点与来源，去重、容量管理、版本追踪 | P1 |
| FR-06 | Recovery Runner | 同起点 G 次恢复，隔离目录/状态，输出完整结果 | P2 |
| FR-07 | Reward / Evaluator | 官方严格成功、成本统计、可选验证过的安全项 | P2 |
| FR-08 | Training Bridge | 正常完整 Teacher Assistant-only Base-SFT、Teacher 恢复后缀 Recovery-SFT、多轮 GRPO logprob/mask 与权重同步 | P3-B / P4-B–P4-C |
| FR-09 | Selectors | 多检查点生成、分类、规则/自适应选择、按原始任务限权 | P4 / P5 |
| FR-10 | Self-Improve Loop | 最新 Student 新失败→Teacher 新修复→增量 SFT→当前策略 GRPO→评测 | P5 |
| FR-11 | Evaluation | 完整任务、固定恢复池、预算曲线、消融 | P3–P5 |
| FR-12 | Reproducibility | 配置快照、随机种子、版本、原始数据与报告 | 全阶段 |
| FR-13 | Teacher Recovery Dataset | Base-SFT Student 的自然失败、教师真实恢复与过滤、Assistant-only 后缀示范 | P4-A |
| FR-14 | Clean Teacher Dataset | Teacher 从正常初态成功执行、官方验证、过滤并导出 Base-SFT 数据 | P3-A |
| FR-15 | Multi-Checkpoint Manager | 中间状态/合法历史一一配对、检查点分类、独立恢复分支 | P3-C–P4-A |

Adapter 的状态管理能力是训练控制面的接口。不能为了方便，把 Adapter 的所有公开方法直接注册成模型工具。尤其采用 TRL 环境自动工具暴露时，必须检查实际 tool schema。[S6]

### 5.1 Agent Runner 约束

- 复用或参考 AppWorld 官方 Function Calling Agent 的工具映射方式。[S7]
- 初版默认一个模型回合只执行一个工具调用；如果上游支持多调用，必须在完整工具批次边界建立检查点，并保证每个调用 ID 有相应结果。比较方法使用同一约定。
- 不在 Runner 中按任务答案硬编码工具步骤，不自动替模型完成任务相关登录或业务操作。[S1]
- 工具参数使用结构化解析与类型校验；不要把不受约束的模型字符串拼入执行代码。
- 动态工具选择的结果也属于恢复上下文；恢复时不能悄悄给某个分支增加正确工具。
- `complete_task` 表示 Agent 结束尝试，不等于官方验证成功。
- 记录生成回合、请求调用、实际执行调用、输入/输出 token、耗时及终止原因。

### 5.2 State Manager 约束

每个可恢复起点至少包含：数据库快照及依赖版本、原始 task ID、历史前缀、工具 schema、执行边界、任务时钟与必要运行时信息、预算协议、配置哈希。

快照应移入独立于临时 world 输出目录的不可变存储。新 worker 完成 world 初始化后，再通过选定版本支持的方式导入快照；不能只跨进程传一个未经解析的 ID。

数据库快照可能依赖 AppWorld 基础数据库与增量格式。需要保留基础数据版本，不能默认单个目录可脱离原始数据独立恢复。[S2]

一致性以稳定的逻辑状态指纹为主，例如按固定顺序读取并规范化相关表内容。不要把 SQLite 文件字节相同当作唯一正确性标准。Actor 不得访问这些检查信息。

## 6. 数据、快照与信息隔离

### 6.1 分离三类文件

| 数据 | 用途 | 可进入模型输入 |
| --- | --- | --- |
| Actor 历史、工具文档 | 复现实际可见上下文 | 是，经过边界检查 |
| 环境快照与恢复元数据 | 控制环境起点 | 否 |
| 官方评估、奖励与诊断 | 训练反馈和研究分析 | 不作为文本提示；奖励仅进入优化器 |

历史必须保存原始形式；若需要截断或压缩，另外生成带版本的模型输入记录，保留所用规则和映射。早期优先使用固定截断策略，不增加摘要模型。训练计算概率时使用各回合实际生成所见的上下文窗口，不能用未截断的整段历史代替原采样条件。

### 6.2 最小记录契约

以下为本项目拟定字段，不是 AppWorld 自带数据结构。

**轨迹记录：**

| 字段 | 要求 |
| --- | --- |
| `run_id`, `trajectory_id`, `task_id`, `split` | 唯一运行身份及原任务划分 |
| `policy_id`, `model_revision`, `adapter_revision` | 可追踪到采样时的实际权重 |
| `messages`, `tool_schema_ref` | 完整合法历史及当时可见工具 |
| `events` | 每次生成、调用、观察及其顺序 |
| `checkpoints` | 有效执行边界上的状态与历史引用 |
| `termination_reason` | 完成声明、预算、工具循环、基础设施错误等 |
| `usage` | 输入/输出 token、调用数、时长、恢复新增成本 |
| `evaluation_ref` | 仅指向控制侧评价文件 |

**失败/起点记录：**

| 字段 | 要求 |
| --- | --- |
| `failure_id`, `source_trajectory_id`, `source_policy_id` | 失败的来源，不随重采样覆盖 |
| `task_id`, `split`, `scenario_id` | 场景字段按实际元数据解析，未知不能编造 |
| `checkpoint_id`, `snapshot_ref`, `snapshot_hash` | 不可变快照引用及完整性校验 |
| `checkpoint_position` | `post_action` / `pre_action` / `pre_termination` / `budget_exhausted` |
| `history_ref`, `history_end_index`, `history_hash` | 与状态同一时刻的历史 |
| `tool_schema_hash`, `runtime_ref` | 工具可见范围及必要运行时状态 |
| `failure_type`, `candidate_reason` | 区分观察到的信号与推断原因 |
| `recoverability_status` | `unknown` / `observed_success` / `no_success_observed` / `proven_blocked` |
| `stats` | 当前估计所属策略、尝试数、成功数、最后刷新轮次 |

**恢复组记录：**

- `group_id`、起点记录 ID、行为策略版本、统一恢复提示哈希、统一预算。
- G 个不同采样种子、每条后缀的轨迹引用、真实 reward 及有效性状态。
- 起始状态/历史/tool schema 指纹与隔离检查结果。
- 训练阶段再补全 token IDs、行为 log probabilities、生成位置 mask、模板版本与统计信息。

同组键应覆盖 task、起点状态、历史、工具、提示与预算；仅采样随机性不同。不同检查点不能混为同一个 GRPO 组。

### 6.3 失败类型与入池规则

| 类型 | 处理方式 |
| --- | --- |
| 工具参数/权限等局部错误 | 记录为候选信号；Agent 后来成功则不自动算整任务失败 |
| 最终官方任务失败 | 进入自然失败候选池 |
| 可核验错误状态修改 | 标注证据；不能把所有状态修改都称作错误 |
| 预算耗尽 | 独立标签；恢复获得的额外预算必须统计 |
| 过早宣布完成 | 保留原失败，使用合法的终止前起点协议 |
| 四次恢复均失败 | 标为当前采样未发现成功，不能直接标不可恢复 |
| 明确不可继续的状态 | 保存可核查原因；不靠主观判断永久淘汰 |
| 环境进程/通信/数据库基础设施错误 | 单独计数与有限重试，不作为策略失败奖励 |

入池去重以源任务、状态、历史和配置联合身份为依据。不同历史即使数据库相同，也不随意合并为同一起点。

## 7. 恢复采样与隔离协议

### 7.1 一个恢复组的执行过程

1. 读取一个不可变起点记录并验证版本与文件完整性。
2. 固定行为策略、工具配置、恢复提示与 G 个采样种子。
3. 为当前分支创建唯一执行身份和干净的环境 worker。
4. 导入快照，恢复同一合法历史与必要运行时信息。
5. 核对起始逻辑状态、历史和工具指纹。
6. 让当前策略执行恢复后缀，直到终止或达到预算。
7. 在验证前保留后缀终态，运行官方任务评估，写出结果。
8. 关闭 worker，检查源快照未改变，再执行下一分支。

初版 G=4，按上述过程串行运行。独立恢复不要求并行运行。以后采用多个 worker 时，模型推理服务可以共享，但环境状态和输出身份必须隔离；不要在每个 CPU worker 重复加载整份模型。

### 7.2 官方评估的参照状态

恢复后的任务成功仍按**原始 task 的指令、初始参照和官方验证规则**判断。不能把损坏的失败快照重新定义成任务的干净初始状态，使此前错误修改逃过验证。

如所用版本的评估会影响运行状态，应在副本上执行，或在验证前保存精确终态；禁止让验证器产生的观察或状态变化进入下一条恢复轨迹。

### 7.3 基础设施验收与学习条件分开

基础设施通过要求：四个分支起点一致、互不污染、均有可解释终止和真实评价。四条奖励完全相同，也可以通过这一层验收。

GRPO 学习条件要求：完成强制 Teacher SFT 后，在 SFT 后 Student 的多个真实恢复组中观察到足够的奖励差异。P0–P2 的基座采样用于诊断，不决定是否进行 Teacher SFT。开始 Teacher 示范构建与 SFT 不要求基座事先产生混合奖励组；若 SFT 后仍长期全失败，应检查格式、工具覆盖、预算和示范质量，再调整训练与采样。

禁止人为篡改奖励、强制某分支成功，或把重复结果误报为不同结果来通过验收。

## 8. 方法设计

### 8.1 ReTerm-style 基线与共同基础初始化

以经过官方成功验证的 Teacher 正常任务轨迹训练的 **同一份 Base-SFT checkpoint** 为所有训练方法的基础能力起点。ReTerm-style baseline 在此之上使用终态或终止前检查点，Teacher Recovery-SFT 后进行 Student 自主恢复 GRPO。与多检查点方法相比，Teacher 成功/失败尝试、SFT token、有效任务数及 RL 预算都须报告并尽量控制。

### 8.2 主要算法：Multi-Checkpoint Recovery

一条失败轨迹选择最多 3 个候选合法检查点，保存数据库状态、对应历史、当时可见工具和运行时信息。

- Pre-error：错误动作尚未发生，训练**错误预防**，单独作为辅助/消融。
- Post-error：错误操作已经发生且后果真实保留，训练**错误状态修复**，为核心。
- Late-failure：错误传播后、终止前或终态的合法起点，训练**延迟恢复**，为核心。

V1 先做可复现的规则选择，从工具可见错误、真实状态修改和最终停止附近筛选；没有证据时标为可疑候选，不能把最终失败自动归因给某一步。不同起点必须独立恢复不同真实状态与合法前缀，不许把 C2 的后缀接在 C3 的状态上。

V2 再估计 Teacher/Student 在同一点的恢复成功率用于自适应选择。需记录每次探测成本、样本量、策略版本与同任务权重；没有验证概率时不编造。多检查点数据按原始任务归一化或限权，避免样本数量优势伪装成方法优势。

### 8.3 检查点自适应价值（待验证，不是预设收益）

令 p_T(z) 表示固定 Teacher 从合法检查点 z 真正恢复成功的概率，p_S(z) 表示当前 Student 在相同公开协议的恢复成功概率。启发式：

$$
U_{SFT}(z)=p_T(z)(1-p_S(z)), \qquad
U_{RL}(z)=4p_S(z)(1-p_S(z)).
$$

SFT 优先选择 Teacher 有能力而 Student 不稳定的点；RL 优先选择二元奖励容易产生组内差异的点。后者**不是**已证明的 GRPO 梯度收益。先有真实重复采样，再做平滑估计并处理极小样本不确定性。V1 规则版 Multi-Checkpoint 先行。

### 8.4 初始奖励与可选扩展

令 `S_i` 表示第 i 条轨迹是否通过官方严格任务验证，取值为 0 或 1。P2–P3 的默认奖励：

$$
R_i = S_i.
$$

调用数、安全事件和重复操作先作为统计指标，不急于加入奖励。

若后续启用扩展，先定义：`V_i` 是已经独立验证的违规成本并归一化到 [0,1]；`K_i` 是恢复后缀实际尝试分发的调用数；`B` 是调用预算；`C_i=min(K_i/B,1)`；`lambda`、`mu` 是非负权重。候选形式：

$$
R_i=S_i-\lambda V_i-\mu C_i.
$$

必须满足有界成本与清晰的奖励排序，例如采用 `lambda + mu < 1`，避免失败并立即放弃获得高于成功轨迹的奖励。该约束只解决成功/失败排序，不证明过程奖励设计合理。

安全扩展需满足：

- 不把数据库差异、所有写操作或 `no_op_pass` 标签直接等同于违规。
- 区分失败前缀已经存在的损害、恢复新增损害和任务要求的合法修改。
- 记录检测覆盖范围；没有判定器时标 `NOT_MEASURED`，不能记零风险。
- 官方终态验证未必覆盖所有临时副作用，不能宣称完备安全。
- 说明安全扣分是否与官方严格成功重叠，单独做消融。

安全反馈是选配研究项，不作为完成两项核心增量的前置条件。

### 8.5 强制双阶段 Teacher SFT

#### 8.5.1 P3-A：Teacher 正常成功轨迹（Clean Demonstrations）

Teacher 从 AppWorld train 任务**原始干净状态**独立执行，使用公开工具完成任务；官方评估通过且调用格式、约束和循环检查合格后才能入集。记录完整尝试分母、成功/过滤原因、模型与 revision、推理协议、真实费用/耗时和数据来源。Teacher 是否实际更强应通过小规模任务核查，不能只凭名字。

#### 8.5.2 P3-B：Student 基础 SFT（Base-SFT）

用 Teacher **正常成功完整轨迹**教 Student 工具调用、API 发现和基本任务规划。只对 Teacher 正常轨迹中新生成的 Assistant 文本、工具名、工具参数计算 SFT loss；User/System、工具返回、控制侧隐藏评估和 padding 都必须屏蔽。完成实际权重更新、mask/token 对齐测试、checkpoint 保存重载、固定 dev 正常任务评测，产生 base_teacher_dataset_id、base_sft_run_id、base_sft_checkpoint_hash。保留 Raw Base 对照。

#### 8.5.3 P3-C：基础 SFT Student 产生**新**自然失败

必须加载已验收的 Base-SFT checkpoint，再从 train 正常状态执行。收集新失败、格式问题、真实业务错误、不同合法中间检查点；与 P0–P2 旧失败区分，不能拿基座旧失败冒充训练后 Student 的新弱点。

#### 8.5.4 P4-A：多检查点 Teacher 恢复示范

对上一步真实失败的每个候选检查点，独立加载其真实环境状态、合法历史、公开工具 Schema，固定 Teacher 从该状态继续实际操作，保留 Student 已造成的错误后果。官方严格任务成功、格式合法、无违规/无效循环的恢复后缀可入 Recovery Dataset；Teacher 失败后缀只作失败记录，不是正标签。报告不同检查点的有效/成功数量和成本。

#### 8.5.5 P4-B：Student Recovery SFT

以任务和 Student 失败前缀作为固定上下文，**仅 Teacher 恢复后缀中新生成的 Assistant token**计算 SFT loss；其间工具结果只作上下文，旧前缀一律屏蔽。增量训练时混入一定基础正常示范和旧恢复示范，保留来源、比例和样本权重。完成真实训练参数更新、mask 检查、保存/重载及正常/恢复 dev 评估，得到 recovery_teacher_dataset_id、recovery_sft_run_id、recovery_sft_checkpoint_hash。

### 8.6 P4-C：在线 Recovery GRPO

只能在 Base-SFT **和** Recovery-SFT 均已实际完成的 Student checkpoint 上启动。Student 当前行为策略从同一真实起点独立生成 G 条恢复后缀；工具返回/固定历史不计策略 loss。保存采样 token IDs、行为 log probabilities、生成 mask、模板/模型版本，使用官方严格奖励并处理零方差和无效组。混合正常任务与恢复任务训练组；推理权重更新/同步、真实参数变化、checkpoint 重载必须验收。P0–P2 的旧轨迹没有合法当前策略行为 logprob，不能直接放进在线 GRPO。

### 8.7 P5：Teacher 辅助的增量自迭代

每轮：最新 Student 自主执行新训练任务 → 新自然失败 → 选择多个可用检查点 → Teacher 从真实失败状态提供**新的成功恢复示范** → Student 混合历史与新 Teacher 轨迹做增量 Recovery SFT → Student 当前策略自主恢复 GRPO → 固定开发集评估与存档。先验证两轮并保留失败记录，不应每轮重置为同一个初始 SFT 权重。比较“仅初始 Teacher”与“每轮 Teacher”，控制每轮 Teacher 探测/成功示范/SFT/RL 成本。

## 9. 分阶段实施与验收

### P0：环境、正常 Agent 与状态验证

**工作：**

- 检查现有仓库、Python、磁盘、GPU/推理端和已配置凭据，不打印密钥。
- 选择兼容依赖并记录；先完成环境最小验证，不启动训练栈。
- 参考官方安装方式与当前 CLI 帮助，运行必要的安装验证。[S1]
- 让 Agent 执行少量真实 `train` 任务。
- 用真实可逆写 API 验证命名保存、修改、加载后回到原逻辑状态。
- 检查工具返回、完成声明和官方验证的区别。

**交付：** Adapter、最小 Runner、环境报告、配置快照、状态恢复证据。

**通过条件：** 至少一个真实任务能够执行到明确终止并被官方评价；至少一个真实状态修改能够正确恢复。只有打印文本或 mock 测试不算真实状态验收。

### P1：自然失败与 Buffer

**工作：**

- 初始扫描最多 10 个训练任务；资源允许且需要时在首轮上限内扩至 20 个。
- 发现第一条可用自然失败后，优先完成该起点的 P2 四分支验证，再决定是否继续扫描；10/20 个任务是上限，不是进入 P2 前必须完成的数量。
- 保存任务执行、工具历史、工具配置和有效检查点。
- 区分局部工具错误、整任务失败、预算耗尽和基础设施错误。
- 实现 Buffer 的持久化、来源追踪和读取校验。

**交付：** 至少一条真实自然失败及其完整起点记录，失败采集报告。

**通过条件：** 起点文件可以独立定位，重读后状态/历史身份一致，来源为 `train`，没有隐藏评价进入 Actor 历史。

如果已授权扫描范围内没有发现可用自然失败，报告扫描分母与结果；不能人为制造失败冒充自然失败。

### P2：四分支恢复

**工作：**

- 从同一失败起点串行创建四个独立执行分支。
- 为每分支导入状态与历史，核对身份并执行恢复。
- 保存原始起点不变的证据、各分支轨迹和官方验证结果。
- 可在首轮预算内验证最多三个起点，观察是否存在有效奖励组。

**交付：** Recovery Runner、四分支报告、复现命令、状态隔离检查结果。

**通过条件：** 四分支均从同一合法起点运行，互不污染，结果可核验。分支因基础设施故障中止时，该组不能直接计为四次有效恢复；应按固定重试策略处理并报告。

首轮到此交付，不自动启动 P3。

### P3：正常 Teacher 基础能力训练（下一开发阶段）

**执行地点：** P3-A 在本机收集并验证 Teacher 干净成功示范和可移植数据包；P3-B Base-SFT 与 P3-C Base-SFT Student 自主执行/新自然失败在远程 96GB GPU 环境完成。正式 Student 始终是 Qwen/Qwen3-4B，远程未验证前不得宣称 P3-B 已开始。

- **P3-A Clean Teacher Data**：固定更强 Teacher 从原始 train 任务独立执行，筛选真实官方成功轨迹并输出 Base-SFT 数据与来源；先低负载小样本验证 Teacher 是否真的会做。
- **P3-B Student Base-SFT**：用正常 Teacher 成功轨迹完成真实 Student Assistant-only SFT、mask 检验、参数更新、保存/重载、固定开发集评价。
- **P3-C Student New Failures**：加载基础 SFT Student 独立执行 train 任务，收集新自然失败、多检查点与原始状态/历史；不能拿旧基座失败替代。

### P4：多检查点真实修复与恢复学习

- **P4-A**：规则 V1 选 Pre/Post/Late 合法检查点，优先 Post 和 Late，Teacher 各自从真实状态接管并修复；成功后缀构成 Recovery Dataset。
- **P4-B**：Student Recovery SFT，只监督 Teacher 修复后缀 Assistant token；保存/重载、正常任务与恢复 dev 评测。
- **P4-C**：在线 Recovery GRPO，当前 Student 相同检查点分组、自主探索与官方奖励，验证行为概率/生成掩码/权重同步及最小参数更新。

### P5：多轮增量 Teacher 辅助改进

至少两轮最新 Student 新失败 → Teacher 修复示范 → 混合旧样本增量 SFT → 当前 Student GRPO → 固定 dev 评价；按任务和成本做公平消融。

### P6：最终官方测试和简历交付

冻结方法后运行 AppWorld test_normal / test_challenge 官方评价；不把测试失败回灌训练，交付对比消融、样本效率、成本、复现与 Demo。

## 10. 首轮配置与资源规则

以下是**拟实现的项目配置**，不是上游 TRL/AppWorld 配置文件；实际字段由 Codex 实现后核验。

```yaml
project_version: "0.3"
mode: "p0_p2_smoke"
seed: 42

environment:
  name: "appworld"
  revision: null               # P0 填入实际固定版本
  split: "train"
  num_workers: 1
  output_root: "runs"
  snapshot_root: "data/recovery_snapshots"

policy:
  model_name_or_path: "Qwen/Qwen3-4B"
  model_revision: null
  backend: "resolve_in_preflight"
  temperature: 0.8
  top_p: 0.95
  max_input_tokens: 16384
  max_new_tokens_per_turn: 1024
  thinking_mode: "resolve_and_freeze"

rollout:
  max_tool_calls_per_turn: 1
  normal_max_model_turns: 40
  normal_max_tool_calls: 40
  normal_max_generated_tokens: 8192
  recovery_max_model_turns: 15
  recovery_max_tool_calls: 15
  recovery_max_generated_tokens: 4096
  tool_timeout_seconds: 60
  episode_wall_time_seconds: 900

recovery:
  group_size: 4
  start_protocol: "terminal_or_pre_termination"
  failure_notice: "neutral_continue"
  candidate_limit_per_failure: 3
  experience_sampler: "recent_first"

reward:
  type: "official_strict_success"
  safety_weight: 0.0
  tool_cost_weight: 0.0

training:
  enabled: false
  base_teacher_sft_required: true
  recovery_teacher_sft_required: true
  require_both_verified_before_grpo: true

first_run_limits:
  initial_train_tasks: 10
  max_train_tasks: 20
  max_failure_starts_for_forking: 3
  max_total_generated_tokens: 150000
  max_wall_time_minutes: 90
  max_infrastructure_retries: 1
```

以上数值是小规模首轮上限与起始候选，不是对任务难度或模型性能的判断；任一上限先达到就保存部分结果并报告。执行预算、上下文截断和 thinking 模式必须对比较方法保持一致。

付费 API 金额上限与可使用 GPU 尚未确定，需在目标环境中读取既有授权；没有授权时不默认购买算力或调用付费 Teacher。可先完成不受阻的代码、配置和验证准备。

LoRA 降低可训练参数负担，但 rollout、长历史、KV cache 和优化器仍会消耗资源。不得未经测量承诺 Qwen3-4B 多轮 GRPO 一定能在某张显卡上运行。[S8]

## 11. 实验设计

### 11.1 主比较：分离基础学习、多检查点与增量监督

| 方法 | 基础正常 Teacher SFT | 恢复 Teacher SFT | GRPO | 检查点与迭代 |
| --- | --- | --- | --- | --- |
| Raw Base | 否 | 否 | 否 | 无 |
| Base-SFT-only | 是 | 否 | 否 | 无 |
| Base-SFT + Terminal Recovery-SFT | 是 | 是 | 否 | 单点 |
| ReTerm-style | 是 | 是 | 是 | 单点 |
| First-Error / Random | 是 | 是 | 是 | 单个替代起点 |
| Rule Multi-Checkpoint | 是 | 是 | 是 | 多点（V1） |
| Iterative Teacher Recovery | 是 | 每轮新增 | 是 | 迭代 |
| Full RecoverAgent-RL | 是 | 多点 + 每轮新增 | 是 | 多点 + 迭代 |
| Adaptive Checkpoint（增强） | 是 | 价值选择 | 是 | V2 U_SFT/U_RL |

所有主线方法共享同一份**基础 Base-SFT checkpoint**；不同恢复示范造成不同 Recovery-SFT 权重是被研究的自变量，不能称为同一恢复初始化。仅比较 GRPO 检查点采样器时使用共同冻结的 Recovery-SFT checkpoint。

### 11.2 消融最低集合

1. Raw Base vs Base-SFT：基础工具协议与正常任务成功率。
2. Base-SFT vs Recovery-SFT：失败恢复后缀监督效果。
3. Terminal-only vs First-Error vs Random vs Rule Multi-Checkpoint：固定教师调用/成功示范/监督 token/原任务权重。
4. Recovery-SFT-only vs Recovery-SFT + GRPO：RL 独立贡献。
5. 只做首次 Teacher 恢复 vs 每轮 Teacher 新增：持续外部监督收益与成本。
6. Rule vs Adaptive、Pre-Error vs Post/Late 作为后续增强。

### 11.3 两套评测

**完整任务评测：** 从原始任务状态出发，使用统一 token、模型请求和时间预算；v0.6 新采样不设工具调用数量配额，仍报告实际调用数。Actor 无回滚与隐藏验证能力。它是主结论来源。

**固定恢复池评测：** 在训练之外的开发任务上，由固定参考策略产生并冻结起点；所有模型使用相同起点、提示与预算。它用于分析恢复能力，不用于参数训练。

各模型自己的在线失败池只用于描述训练过程，不能直接用它们各自的恢复率排名，因为起点难度不同。

任何“失败后增加一次恢复机会”或“四次取最好结果”的推理协议另行报告，并设置相同额外预算的继续执行/重试对照。不能把额外预算收益算成免费能力提升。

### 11.4 指标定义

| 指标 | 定义与分母 |
| --- | --- |
| TGC | 采用官方任务目标完成率实现，按任务统计严格成功 |
| SGC | 采用官方场景目标完成率实现；完整场景变体全部成功才算该场景成功 |
| Recovery Success@1 | 每个固定起点的首条预定恢复尝试中成功的起点数 / 有效起点数 |
| Mean Recovery Success | 所有有效恢复轨迹中的成功数 / 有效恢复轨迹数 |
| Recovery Any@4 | 四条均有效的恢复组中至少一条成功的组数 / 四条均有效的组数 |
| 有效奖励组比例 | 奖励存在差异的有效组数 / 全部有效组数 |
| 新增恢复成本 | 从检查点以后累计的调用、输入/输出 token 和耗时 |
| 全尝试成本 | 成功与失败都计入的平均/分位数成本；另外给成功条件下成本 |
| 安全相关指标 | 仅在明确覆盖任务中计算的可验证违规率，并报告覆盖分母 |
| 样本效率 | 累计采样预算对应的固定开发成绩，而不是仅看训练轮数 |
| 基础设施异常率 | 出错尝试数 / 所有已启动尝试数，另列重试与失效组 |

单次 `world.evaluate()` 不产生 SGC；小任务子集若没有完整场景变体，SGC 标为不适用，不能把测试断言通过率误称 SGC。[S1][S9]

基础设施失效样本不能静默删除。报告已启动数、有效数、失效数和重试策略；正式评测不能只保留成功完成运行的任务作分母。

### 11.5 公平预算

主比较优先固定**全部采样阶段累计生成 token**，同时报告输入 token、工具调用、训练 token、GPU 时间和实际耗时。至少计入：

- 正常任务执行与自然失败挖掘。
- 检查点/经验价值探测。
- 正常组和恢复组的 rollout。
- Teacher 与 SFT 数据生成，单独列示。

正常 Teacher 成功轨迹与 Base-SFT 作为共同冷启动成本单独列示；各多检查点 Teacher 恢复尝试（成功和失败）、Recovery-SFT、增量 Teacher 与 GRPO 预算均完整计入；不能漏算 Teacher 失败成本，也不重复计算共同 Base-SFT。

工具查询、前缀长度和训练计算也可能不同，因此固定生成 token 只是一种预算协议，不等于所有计算量完全一致。若阶段性只能固定 rollout 数，明确写出这一局限。

P5 争取至少三个训练随机种子；资源不足时如实报告实际种子数。任务配对比较优先按场景分组计算置信区间，不能把同一任务的四条恢复当四个独立任务扩大显著性。

## 12. 数据划分与评价完整性

| 划分 | 允许用途 | 不用于 |
| --- | --- | --- |
| `train` | 参数训练、自然失败挖掘、Teacher 示范、恢复探测 | 最终泛化成绩冒充 |
| `dev` | 超参数、提示、模型选择、固定恢复池与误差分析 | 参数训练或 Buffer 回灌 |
| `test_normal` | 冻结方法后的官方总体评价 | 调参、逐任务错误分析、失败挖掘 |
| `test_challenge` | 冻结方法后的官方总体评价 | 调参、逐任务错误分析、失败挖掘 |

遵循官方开发限制。[S1]

同一原始任务的所有正常轨迹、快照、恢复后缀和示范继承原划分。如果从 `train` 再划内部验证集，按实际可用的场景/任务家族分组，不随机拆同一任务的多条轨迹。

开发集上的恢复池可用于反复开发，但因此仍属于开发结果，不能称作最终测试结果。测试任务不能进入经验选择统计。

正式评测保留原始数据与脚本版本，不能修改官方成功条件使实验通过，也不能自动修复模型的业务错误后再提交评价。

## 13. 建议目录与后续文档

以下目录为建议，按阶段创建实际需要的文件；不要求一次生成全部空模块。

| 路径 | 内容 |
| --- | --- |
| `AGENTS.md` | Codex 工作约定与科研底线 |
| `PROJECT_PLAN.md` | 本计划、需求、验收与变更记录 |
| `README.md` | 安装、实际可运行命令、项目结果 |
| `pyproject.toml` | 包定义与可复现依赖入口 |
| `configs/` | Agent、首轮、SFT、GRPO、方法开关配置 |
| `src/recoveragent/adapters/` | AppWorld 任务与工具适配 |
| `src/recoveragent/runner/` | 正常与恢复 rollout、预算管理 |
| `src/recoveragent/state/` | 快照、历史、工具配置与校验 |
| `src/recoveragent/buffer/` | 自然失败记录与持久化 |
| `src/recoveragent/selectors/` | 恢复点与经验采样策略 |
| `src/recoveragent/training/` | SFT、GRPO 桥接和自迭代 |
| `src/recoveragent/evaluation/` | 官方评测适配、指标与预算统计 |
| `tests/` | 状态、分叉、信息边界、训练数据对齐测试 |
| `docs/STATUS.md` | 当前阶段、证据、下一步和阻塞 |
| `docs/DECISIONS.md` | 影响实验解释的技术决策 |
| `reports/` | 环境报告、阶段报告、实验总结 |
| `runs/` | 每次执行的配置、原始日志、评价与统计 |
| `data/` | 本地基准数据、Buffer、快照与示范，不直接提交大文件 |
| `checkpoints/` | 模型、优化器和续跑状态，不直接提交大文件 |

数据、权重、密钥和解包的基准私有内容不提交公开仓库。公开 Demo 使用许可允许的内容或自制说明案例；公开前核对上游数据与代码发布要求。[S1]

## 14. 关键验证与完成定义

只为会影响实验正确性的内容编写必要测试；不为文档修改、简单可逆调整大量新增测试。

| 验证 | 要排除的问题 |
| --- | --- |
| 真实写入后的保存/加载 | 只保存了文件名，没有恢复实际状态 |
| 新 worker 导入检查点 | state ID 仅在原 world 有效，跨进程失效 |
| 分支污染检查 | 前一分支的写操作进入后一分支 |
| 状态与历史配对 | 使用未来观察，或遗漏已发生的动作 |
| 工具可见范围检查 | 无意暴露 snapshot、restore、evaluate 或数据库内部能力 |
| 原始任务验证参照 | 恢复后绕过了前缀造成的错误 |
| 数据划分/来源检查 | dev/test 后缀或同源快照混入训练 |
| GRPO 组身份与 mask | 不同起点混组、工具输出被当模型目标 |
| 零奖励方差与异常组 | NaN、无效学习信号或伪造奖励 |
| 参数更新/重载/同步 | 训练日志在变化，实际推理仍使用旧权重 |
| 预算重算 | 探测、重试或失败采集成本被漏记 |

每个阶段的 `PASS` 必须关联命令、日志和结果路径。建议状态值：`NOT_STARTED`、`IN_PROGRESS`、`PASS`、`PARTIAL`、`BLOCKED`。mock 成功只能验证软件逻辑，不能替代真实 AppWorld 或真实模型结果。

## 15. 风险与处理顺序

| 风险 | 优先处理 |
| --- | --- |
| 没有可用模型或凭据 | 完成不依赖模型的部分，准确说明真实闭环未验证 |
| 跨进程快照不兼容 | 先解决导出/导入与版本依赖，不开始 RL |
| 四分支长期全失败 | 优先完成 Teacher 正常示范 Base-SFT，修复工具调用协议，再由 Base-SFT Student 新失败生成恢复任务；不可直接跳 GRPO |
| Teacher 不可用或合格示范不足 | 分别报告正常 Teacher 与恢复 Teacher 数据缺口，继续无付费组件验证，不用 Student 冒充 Teacher 或跳过任一 SFT |
| 小模型能力不足 | 在可用资源内调整模型；保留预算和初始化公平 |
| TRL 与 AppWorld 隔离不匹配 | 增加 worker 桥接，或选择已验证的自定义 rollout 接口 |
| 安全评分无法可靠判定 | 关闭奖励项，保留严格任务成功和可测事件 |
| 自适应方法只学容易样本 | 保留均匀探索，报告覆盖与固定正常任务成绩 |
| 改进没有收益 | 保留负结果，定位是否为难度、预算或方法问题 |
| 训练中断 | 从已保存 checkpoint、Buffer 和随机状态继续 |

## 16. 后续修改规则

可以根据实际证据调整模型大小、框架版本、目录、预算、阶段子任务和默认超参数。修改影响方法解释时，先记录原因，再更新实现与配置。

不要为了通过验收降低科学要求，例如用 mock 替代真实隔离验证、允许测试泄漏、改变官方成功定义、删去负结果或隐藏费用。

若改变项目环境、核心研究目标、两项增量范围或明显扩大资源开销，应先结合用户当前授权处理，并在决策记录中写清取舍。常规修复和已授权范围内的推进不需要逐步审批。

后续维护三类记录：

- `docs/STATUS.md`：发生了什么，证据在哪里，下一步是什么。
- `docs/DECISIONS.md`：为何改变设计，影响哪些实验与接口。
- 本文件变更记录：需求基线如何变化。

## 17. 给下一轮 Codex 的执行边界（v0.3）

P0–P2 已有代码和真实最低验收，不得因新方案重建或覆盖历史报告。下一步从 **P3-A：Teacher 正常任务成功示范**开始，同时以低负载核查旧基座工具格式错误原因。Teacher 必须从原始干净 train 初态执行、由官方验证成功，而不是先接管旧基座自然失败来开展第一次 SFT。

有正式 Teacher 接口、合格数据与已批准训练资源后开展 P3-B Student 基础 SFT；P3-C 加载该 checkpoint 让 Student 自己执行并采集新自然失败。P4 的多检查点 Teacher 恢复、Recovery SFT 和 GRPO 不属于默认下一次无明确授权的执行内容。

所有生成轨迹与权重以实际版本、哈希、结果和成本验收，后续阶段按版本门槛推进。不能假装训练已经发生；不能未经授权调用付费 Teacher。

## 18. 官方资料

以下来源支持上游能力说明；本项目的模块划分、预算、采样公式与验收门槛属于当前设计。

- [S1 — AppWorld 官方仓库：使用、安装、数据划分与开发限制](https://github.com/StonyBrookNLP/appworld)
- [S2 — AppWorld environment.py：本次核对的固定源码版本](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/src/appworld/environment.py)
- [S3 — AppWorld 并行 world 指南](https://github.com/StonyBrookNLP/appworld/blob/main/guides/parallelizing_worlds.md)
- [S4 — ReTerm 官方代码仓库](https://github.com/twothsnd/ReTerm)
- [S5 — ReTerm 论文，arXiv:2610.18484](https://arxiv.org/abs/2610.18484)
- [S6 — TRL GRPO Trainer：Agent Training、Environments 与 Chat Templates](https://huggingface.co/docs/trl/grpo_trainer)
- [S7 — AppWorld 官方 Function Calling Agent](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/experiments/code/simplified/function_calling_agent.py)
- [S8 — TRL 显存优化指南](https://huggingface.co/docs/trl/reducing_memory_usage)
- [S9 — AppWorld evaluator.py：官方 TGC/SGC 聚合实现](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/src/appworld/evaluator.py)
- [S10 — OpenAI：使用 AGENTS.md 配置项目工作约定](https://developers.openai.com/codex/guides/agents-md)

## 18.1 v0.12 Harness工程化补充

用户在失败分析后授权工程化。新增独立`public_protocol_correction_v1`，不更改训练顺序、Student模型或P0–P2历史。完整约定见`docs/HARNESS_V1.md`。

1. Teacher/Student共用执行前整批检查；可见性、Schema参数、调用ID、已观察同应用登录token以及完整回复边界不满足时，整批零执行、显式反馈、模型重新生成。最多2次通用纠正机会，不自动补工具、改参数或业务答案，不执行截断前缀。
2. 相邻相同结果、相同查询条件下连续空页或401可触发最多2条提示性反馈；不能据此自动判任务完成。A工具集仍固定，B新Schema只能下一回合使用。原数值格式纠正只允许紧接着的一次同值回复。
3. 每轨迹40请求/16384输出token/600秒/USD1、每回复4096，工具调用次数不限。拒绝/纠正消耗原预算。Student输入加最大输出不超过32768，无静默截断。新JSON字节保护512KiB独立于训练token门槛；完整超长示范仍deferred。
4. 非法原始生成留在控制回执，合法消息中的harness反馈清楚标识来源；拒绝回合不监督，其他前缀及padding屏蔽。新示范仍必须真实Teacher、官方train、干净初态、官方成功、回执与工具可见性复验。
5. 服务401/402/403立即停止后续任务，连续两次传输/worker故障停止批次和campaign；未知费用保留预留。无余额时不发模型生成请求，完成不受阻验证。
6. 在新隔离依赖环境验证内存URI、model-hash持久化、真实状态恢复与新进程官方评价；不改官方验证器，非断言异常单列。旧95条通过重放原动作建立独立复验证据，不能算新增Teacher数据或反写历史分数。
7. 软件、真实AppWorld、真实tokenizer与真实Teacher自纠正效果分开验收；数据编码通过不等于训练通过。冻结旧提示/锁/原包，新代码有独立源码及参数锁。正式SFT/GRPO继续不启动。

## 19. 变更记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v0.1 | 2026-10-19 | 建立 AppWorld-only 项目范围、P0–P5 路线、两项待验证增量、首轮任务、隔离与训练契约、预算及实验规范；修正无参 save_state 的旧示例。 |
| v0.2 | 2026-10-19 | 根据用户明确要求，将 Teacher SFT 设为主线 GRPO 的强制前置；加入五步示范流程、loss mask 与 SFT 验收、P3-A/B/C 子阶段和共同 Teacher-SFT 初始化，保留 P0–P2 先行验证。 |
| v0.3 | 2026-10-20 | 用户明确要求：先 Teacher 正常成功轨迹教会 Student 基础工具能力，然后 Student 自主执行新任务、Teacher 从多检查点恢复、Student 恢复 SFT + GRPO，并由每轮新错误与 Teacher 新示范增量自迭代。重排 P3–P6 与实验对照。 |
| v0.4 | 2026-10-20 | 正式 Student 固定 Qwen3-4B；本机负责 Teacher 成功数据采集与验证，远程 RTX PRO 6000 96GB 负责 4B Base-SFT、Student 新失败、多检查点 Recovery-SFT、GRPO 和持续迭代；规定可移植数据交接与远程预检门槛。 |
| v0.5 | 2026-10-20 | 按用户要求增加官方 API Predictor 与动态发现的双工具模式配对比较；单回复上限 4096 token，计入预测开销，不改变模型、训练顺序、硬件分工或 P0–P2 历史基线。 |
| v0.6 | 2026-10-20 | 用户取消工具调用次数限制；保留其他资源预算，审计合理批量与提示问题，官方原提示和认证依赖增强提示分别记录。 |
| v0.7 | 2026-10-21 | 用户确认规范 A/B 提示：共享六阶段执行规则，A 依赖补全与两个公开 API 自制正例，B 按需发现；版本、示例来源和全部成本可核验，历史提示保留。 |
| v0.8 | 2026-10-21 | 冻结提示与 A/B 协议，扩大公开 train 场景采样与真实 Teacher 数据；补齐 4B Student 批量接口、动态 schema/mask 与上下文成本检查，保留全部历史，继续禁止正式训练。 |
| v0.9 | 2026-10-21 | 用户授权独立 execution_v4、A 公开认证依赖校验/一次纠错及新场景配对采集，完整保留旧协议与结果，不启动训练。 |
| v0.10 | 2026-10-21 | 公开单值格式检查与一次模型同值纠正，明确harness反馈和拒绝回合loss屏蔽；真实/脚本/历史成绩分开，不训练。 |
| v0.11 | 2026-10-21 | 正式Teacher数据构建：历史重验、冻结B优先扩样、分组/去重/权重/长度门槛、私有迁移与远程LoRA准备；本轮不训练。 |
| v0.12 | 2026-10-22 | 用户授权有限通用协议纠错、共享Teacher/Student执行检查、服务熔断、隔离兼容环境和官方复评；拒绝回合屏蔽，新旧证据分开，不训练。 |

### v0.14 变更记录 · 2026-10-25

按本轮授权进入P3-C工程实现与资源预检。评价官方原始结果与可靠性分列；精确Schema反馈不代做参数；state v2只接受完整批次，基础设施故障和部分执行不作为自然失败。真实采集仍需可用远程GPU，详细验收与剩余条件见P3-C报告及D041–D043。

### v0.15 变更记录 · 2026-10-26

按用户新授权在已运行A100重训既定Base-SFT并补齐测试；训练封存源码与当前Harness分离，原任务清单不择优更换。硬件更换与Harness变化均记录，重跑不能单独归因于SFT效果提升。

### v0.16 变更记录 · 2026-10-27

新增用户授权的 EGTM Task Memory v1：证据来源/有效性/时间隔离、独立 Context Compiler、v3 Memory 快照与旧 v1/v2 兼容；默认关闭、独立运行锁，固定 Base-SFT A/B 两组。阶段验收明确分开软件、无模型真实环境、离线 Token 重算和尚未进行的模型效果验证，不改变既定训练顺序。


### v0.17 变更记录 · 2026-10-27

将 EGTM 的 L1 从规则视图升级为同一 Student 的结构化增量生成；L2/L3 不变。增加严格证据/原值校验、失败原子回退、共享请求与生成预算、v4 回执快照及 v2 独立锁。保留 v1 实验、数据与全部旧快照格式；真实 Qwen Memory 能力须另行获授权后用固定两组实验验证。

### v0.18 变更记录 · 2026-10-27

持续展示已接受模型L1、与压缩解耦；L2去重及带来源哈希的精确字段投影；增量请求提前触发；A/B按完整固定清单、初始DB/首次IDs/实际可见性验收。阶段1完成后，按用户明确授权执行现有GPU的固定3任务小样本并关机；正式实验另行授权。保留原数据、权重、旧锁与所有负结果。

### v0.19 变更记录 · 2026-10-27

暂退Harness证据记忆，实际输入29000阈值、持久压缩边界、无Memory模型开销；新锁独立，保留模型驱动实现/数据。固定3任务重实验，本轮按用户指示先完成本地工作并通知开机，未执行远程推理。

### v0.20 · 全任务池研究方案与三阶段统一验证

Phase0真实清单/权限/初态与评价可用性审计 → Phase1冻结C与A/B隔离 → Phase2 A正常示范及新M1 → Phase3 C评M1 → Phase4 B自然失败/Teacher恢复及M2 → Phase5 C评M2 → Phase6 B在线GRPO得M3 → Phase7 C评M3。每阶段都需要前置验收；本轮授权止于Phase1和后续接口/配置测试。完整池受限部分不执行、不查询隐藏标签，不用成绩重排划分。

### v0.21 · 用户指定System Prompt

新增execution_v6及共享评估v2，只改变system文本与运行身份；保留旧配置/锁/报告与原始数据。395软件测试通过，尚无真实模型效果验证。
