# CoEvolve 上下文解耦与小规模真实正确性验收

日期：2026-11-17；基线 `cc482d6`，分支 `codex/p0-p2-recovery`。范围为相关本地测试、固定4条历史Judge、一个真实context、最多一道新题的4次S0推理。无Static/正式Evolve、SFT/GRPO更新、dev/test或整个旧池重新准入。

## 实现与配置

独立执行版 `v2_s2_teacher_context_20261117`，228文件源码锁。`teacher_context.py` 负责完整Teacher请求计数；`v2_teacher.configuration(..., purpose=...)` 只对S2文本、探索、Solver传递独立窗口。`GroupEvolution.raw_text` 在新版本中取消重复的Student预筛，进入 `Evolution.text` 的真实请求构造、Teacher检查与预算预留；父类不再二次套用Student窗口。`p3a` 在构造实际含工具及provider reasoning历史的请求后检查Teacher窗口。普通示范采集和Recovery-SFT没有这个purpose，原 `learning_context` 不变。

| 项目 | 当前值/依据 |
| --- | --- |
| Teacher | OpenCode Go `deepseek-v4.1-flash`，服务元数据canonical ID为 `deepseek/deepseek-v4.1-flash-1017` |
| Teacher输入+输出窗口 | **1,000,000**；2026-11-17实际GET `/models` 返回 `limit.context=1000000`，公开[Go模型元数据](https://models.opencode.ai/providers/opencode-go/)同值 |
| Teacher计数 | 官方匹配tokenizer `deepseek-ai/DeepSeek-V4.1-Flash-1017`，revision `08c8657f02291e11b6f6b63e0e16014f8e748518`，tokenizer.json SHA `c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b` |
| 本地计数方法 | 完整messages（含reasoning）、tools及thinking字段JSON序列化计数，另加1024 framing预留；**这是估算，不是Go服务端精确计数**，不再用Qwen tokenizer冒充DeepSeek计数 |
| 输出/字节/计费预留 | 每请求4096生成token；4MiB请求体上限；billing_context_tokens仍1048576，三者与上下文窗口分别处理，未替换旧配置数字 |
| Student与训练 | Qwen3.5-9B S0、non-thinking、vLLM、65,536上下文不变；本轮不训练 |

实际API usage完整保存，作为用量依据。未声称本地估算可保证靠近1M边界时与服务器完全一致；没有1M付费压力测试、裁剪或自动摘要系统。

## 本地验证

新增6项上下文测试使用真实请求构造、两层调用链和匹配的真实tokenizer；仅Teacher网络响应及探索的CPU环境使用夹具。测试超过64K仍在Teacher窗口内可发送、超过Teacher窗口拒绝且不预留付费调用、输出空间预留、完整工具/reasoning计数、字节上限独立、普通Teacher/Student保留64K，以及 `p3a` 探索不会再被Student窗口拦截。另补Solver子进程异常暂缓测试，避免异常逸出后把候选算成业务失败。

实际执行：

```bash
.venv-v2/bin/python -m pytest -q \
  tests/test_s2_teacher_context.py tests/test_coevolve_light_llm.py \
  tests/test_completion_submission_v2.py tests/test_public_goal_reward.py \
  tests/test_s2_goal_integration.py tests/test_s2_evolution.py \
  tests/test_teacher_collection.py tests/test_s2_parallel.py tests/test_v2_context.py
.venv-v2/bin/python -m pytest -q \
  tests/test_s2_generation_fix.py tests/test_teacher_escalation.py \
  tests/test_teacher_followup.py tests/test_teacher_retry.py \
  tests/test_v2_learning.py tests/test_v2_s1_retrain.py
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
```

**核心相关126 passed，13.25秒；更新源码锁夹具涉及的60项另行通过，3.72秒；合计186项。Ruff check及283文件format通过**。共享Judge、旧hash/全文/数据库评分不回退、原始题官方评分、正常false有效/异常无效、数字/JSON/null/一次完成纠错及Student64K原边界回归通过。这里不把模拟响应当成真实模型结果；本轮未跑全量pytest。

## A：四条历史 Judge 核对

复用原 `judge_check_plan.json`，标签仅事后对照，不发送给Judge。原始记录/奖励不改写。

| 历史行 | 预期 | 首次Judge | 核对 |
| --- | --- | --- | --- |
| 5，报价邮件 | 成功 | 成功 | 创建一个草稿并发送，报价/税额/总价和outbox证据一致；真实null提交不被单独误罚 |
| 7，报价邮件漏草稿 | 失败 | **成功，误判** | 题面明确“Create exactly one draft, send it”，实际仅 `send_email`；Judge只核对发送及内容，漏了草稿过程 |
| 18，转发漏附件 | 失败 | 失败 | 转发邮件attachments为空，后续才下载附件，未将附件补入已发邮件 |
| 21，商品提问 | 成功 | 成功 | 正确商品、新建提问和读回ID一致，没有机械要求Teacher对象ID |

首次为 **3/4一致**。对行7补了一条通用Judge提示：不同合法方法不能省略公开题目明确要求的中间动作、次数或顺序。针对复验**仍误判成功**。输入中的公开要求和动作证据完整，没有程序漏传或解析错置。

随后C出现“理由说答案错，但success=true”的明确不一致。对此再统一调整主提示词：先给理由及证据，最后给与理由一致的success；未加入案例答案、关键词改分器或额外自动复核机制。只对已确认的行7、C的member_2/3各做一次Judge复验，共3次；未重跑Student。行7这次判失败，理由正确定位缺少创建草稿。其他3条历史案例未在最终提示下重判，不能拼接成最终版完整4/4验证。最终提示SHA为 `7fa7f33bef47d4b3fde4f5a25c8df67978d0e920119eead97a1a5560c1ee9f35`。

## B：一个真实生成 context

来源是原始train任务 `0a9d82a_2` 的第5组四次同策略、同初态尝试：询问Simple Note习惯日志中最长“每日屏幕时间不超过1小时”连续天数。原提交为10、10、7、9，四次均正常完成提交且官方失败，没有工具拒绝；从实际公开日志重新计算最长为6天，确认是业务推理失败而非旧解析污染。

输入四条完整历史；已有充分业务观察，额外探索0次。仅调用一次任务抽象，产出2候选，没有扩大批次或额外Student预筛。

| 候选 | 公开任务 | Solver/准入 | 人工依据 |
| --- | --- | --- | --- |
| 0，计数组件 | How many of my Simple Note habit tracking logs record 'limited_screen_time_to_1_hr: yes'? | 真实Solver提交11；共享Judge成功 | 共14条日志，11条yes；搜索、逐条读取及末页检查有实际返回 |
| 1，日期组件 | On which date did my most recent 'limited_screen_time_to_1_hr: no' habit log occur, as per my Simple Note habit tracking logs? | 真实Solver提交2023-05-16；共享Judge成功 | 最新05-17为yes、05-16为no；查询排序和两条读取支持答案 |

两题分别练习完整检索/条件计数和按日期筛选，都是原连续天数题的组件；不强制保留复杂连续区间计算。公开query与completion_criterion一致，无额外隐藏步骤。两次Solver都从来源环境干净初态执行，未继承探索状态；实跑冻结源码下的 `validate_task` 成功读取两份准入定义。最终Judge提示调整后的版本绑定说明见下文。

抽象请求：本地估计63,132输入token，预留4096，合计67,228；API实际输入51,527、输出257。此次**实际输入未超过64K**，不能将其写成真实>64K输入验证；超过64K的完整检查链由本地测试覆盖。全部原始反馈和usage保留。

启动中发现两个控制侧问题：单context金额预留10不足以容纳一次80请求的保守预留；本地worker路径解析到基础Python后丢失虚拟环境。两处均发生在Solver API调用前。已修正预留配置和私有部署路径，保留失败日志，仅继续原2个候选，没有重新出题。Solver子进程失败现记基础设施暂缓。最终2次真实Solver、2次准入Judge；原来0次派发不冒充业务失败或额外完成解。

## C：固定一道新题的四次 S0 推理

按提案顺序选择首个准入任务（计数11），选择在看到任何Student结果之前冻结。使用既有L40S，原S0 adapter SHA `5da6444aa979e2ce0957b3bba82bd239ba0f59fad40024a7ea0a35b7f4534966`；组内同题/干净初态/策略，4个固定种子，两条并发。执行、提交、共享Judge、分组反馈均复用正式函数；只计算优势作有效性检查，不反向传播。

| 成员 | 实际接受的answer | 原始在线Judge | 人工业务核对 | 请求/生成token | episode耗时 |
| --- | --- | --- | --- | --- | --- |
| 0 | 字符串`"11"` | 成功，理由正确 | 成功 | 22 / 2920 | 137.63秒 |
| 1 | 字符串`"11"` | 成功，理由正确 | 成功 | 39 / 3907 | 166.42秒 |
| 2 | 字符串`"9"` | **成功，但理由说正确值11、提交9错误** | 失败 | 32 / 3250 | 137.69秒 |
| 3 | 字符串`"9"` | **成功，但理由说正确值11、提交9错误** | 失败 | 31 / 3228 | 134.77秒 |

人工核对实际为 **2/4**，不能把原始在线4/4当成成功率。四条均恰好一次合法完成提交，完成拒绝0次，没有反复类型拒绝或退化null。member_1/3各有2次普通工具协议拒绝：向file_system工具传入未观察到或不匹配的access token；这是正常模型调用错误，后续继续执行，不是完成提交解析问题。这里记录真实字符串提交，未在提交后改成数字或替模型补答案。

原始组被当时Judge标成4个有效成功，正式分组函数可读、优势为全0。发现评分矛盾后，仅在独立审计副本将member_2/3标成无效未知，实际分组函数返回 `complete=false`、无all_failed/forgetting信号、无可更新优势；**未覆盖在线奖励，未将人工标签当成新在线采样**。整个验收始终0参数更新。

最终reason-first提示的两次计数复验均判失败：member_2理由正确计11；member_3虽然布尔结论正确，仍把逐条列出的11个yes加成10。说明定向复验改善了已发现的布尔矛盾，**Judge算术和证据核对仍不稳定，未达到可直接恢复正式训练的可靠性结论**。没有继续追加模型调用直到全部理由正确。

## 用量、版本与结论

- Teacher共50请求：历史4＋行7首轮复验1＋抽象1＋Solver35＋准入Judge2＋Student Judge4＋最终定向复验3。输入510,769、生成8,715 token；请求耗时累计326.81秒。按既有Go保守单价估算 **$0.091849212**，不是订阅实际扣费；无法取得实际账单。
- Student共124请求，输入1,518,217、生成13,305 token；模型累计398.90秒，环境累计1.56秒；四条episode累计576.51秒，两路并发含启动的作业墙钟约406秒。既有vLLM启动连通性检查另外5请求/10生成token，不计为任务执行。GPU实际账单未取得。
- 已实测：同一真实反馈生成2道可解组件题；Solver从干净初态完成；现有加载/工具/提交/奖励调用链可运行；4条S0实际执行可复核。未见答案类型拒绝污染。
- 已发现：Judge会漏过程要求、会出现布尔与理由矛盾；最终定向修正仍有理由算术错误。因此不能宣称奖励完全正确，更不能宣称模型学习改善。
- 未实测：真实>64K输入、真实可选探索（本例不需要）、1M边界、最终提示下全套准入与4次新Student执行、fresh16、共同旧池准入、正式训练和dev/test。到此停止。

B/C冻结实跑源码在私有 `source.tar.gz` 和远端 `source/`，当时新锁SHA为 `74084666404f5746ee7b09104ec311be915224a359ed308eb799b7cbdb30213b`。最终reason-first提示进入提交源码后，新锁SHA为 `85cdb60bcd95b23f81048e3bbdd3def3a3c78b188601fdb59dd27ac5e02911e1`。旧准入证明绑定旧提示SHA，不能把它静默改成最终版证明；最终版只做了上述3次针对复验，未重复付费准入2题或整个旧池。后续若使用新提示，应明确复用原Solver证据重新准入，不能覆盖旧回执。

## 私有产物与命令

本地：`runs/s2_context_validation_20261117/`。`judge_check/`、`judge_recheck_7/`、`generation/`、`solver_completion_02/`、`manual_goal_audit.json`分别保留原判、复验、唯一抽象、两次真实Solver及只读人工核对；`verdict_order_recheck/`是最终3次复验，`student_manual_audit.json`为独立审计，`cost_summary.json`为实际用量汇总。旧48条仍在原位置。

远端完整4条记录已下载到 `remote_evidence/`；压缩备份5,268,428字节，SHA-256 `5a9d39f55f28467cae92cb3e5c31a82c74895bb6b73703fe32c6e18e43c59aaa`，与远端一致。结束检查：本次进程已退出、GPU显存0MiB。未启动原实验控制器，实例可由用户在控制台关机。

当前薄入口 `scripts/probe_s2_coevolve.py` 只调用已有生成/执行/评分/组反馈函数，无训练调用。以下是本轮使用的调用形式，**不是要求再运行一次**；配置、提示或源码改变后不能复用旧output冒充同协议续跑，后续运行须使用新目录和有效授权截止：

```bash
export PYTHONPATH="$PWD/src:$PWD"
export RECOVERAGENT_S2_EXECUTION=v2_s2_teacher_context_20261117
export RECOVERAGENT_TEACHER_TOKENIZER_DIR="$PWD/runs/s2_context_validation_20261117/teacher_tokenizer"
.venv-v2/bin/python scripts/run_v2_s2.py check-judge \
  --input runs/s2_light_llm_20261117/judge_check_plan.json \
  --deployment runs/s2_context_validation_20261117/deployment_local.json \
  --output runs/s2_context_validation_20261117/judge_check
.venv-v2/bin/python scripts/probe_s2_coevolve.py generate \
  --input runs/s2_context_validation_20261117/event.json \
  --deployment runs/s2_context_validation_20261117/deployment_local.json \
  --output runs/s2_context_validation_20261117/generation
```

这些目录已消费本轮固定名额；原生成目录记录了修正前预留配置，与最终配置绑定不同，不应用上述命令重新派发。2个候选的启动故障补完记录在 `solver_completion_02/`，不要重发抽象来覆盖旧输出。

远端仅本轮目录 `/root/gpufree-data/recoveragent_s2_correctness_20261117_01/`；从新源码目录运行：

```bash
export RECOVERAGENT_S2_EXECUTION=v2_s2_teacher_context_20261117
export RECOVERAGENT_TEACHER_TOKENIZER_DIR=/root/gpufree-data/recoveragent_s2_correctness_20261117_01/teacher_tokenizer
export PYTHONPATH="$PWD/src:$PWD"
/root/gpufree-data/recoveragent_s1_retrain_20261114_01/worker_env/bin/python \
  scripts/probe_s2_coevolve.py student \
  --input /root/gpufree-data/recoveragent_s2_correctness_20261117_01/selected_generation.json \
  --deployment /root/gpufree-data/recoveragent_s2_correctness_20261117_01/deployment.json \
  --output /root/gpufree-data/recoveragent_s2_correctness_20261117_01/student4
```

源码、协议和配置完全一致时，同output续跑复用已完成回执，已派发中断slot保留unknown，不重复挑结果。本轮B/C的复核应使用冻结实跑源码；当前新提示不能与旧准入/状态混用。没有合格题时明确跳过C。用户本轮授权不延伸到正式S2；后续仍停在正确性验收。
