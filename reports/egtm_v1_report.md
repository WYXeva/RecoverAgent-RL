# EGTM Task Memory v1 验收报告

日期：2026-10-27。开发分支 `codex/p0-p2-recovery`，起点 `5a77886fb41b968f600a9fd6d2ff3e9a82100e58`，已从 origin 拉取核对。主工作区没有可用 Git 元数据，使用已有 `/tmp/recoveragent-diagnosis-20261022` 克隆提交；原有未提交文件不覆盖。

**实现/软件 PASS，真实 AppWorld 无模型探针 PASS，真实 Qwen 模型效果 NOT_RUN。** 正式 Student 仍为 Qwen/Qwen3-4B。没有加载模型权重、请求 Teacher、重训 SFT、启动 Recovery/GRPO、访问或启动远程 GPU。原 95 条 Teacher 数据及两版 checkpoint 保持不变。

## 1. 审查结论与实现

原 StudentSession 将完整 messages 直接送入 tokenizer，只有达到窗口后的停止逻辑；原 P3-C 已有完整批次边界、v2 Agent runtime 与独立恢复，Harness 已有整批校验/执行回执。这些能力复用，不新增状态机、业务规划器或 Actor 工具。

| 文件 | 职责/改动 |
| --- | --- |
| `src/recoveragent/task_memory.py` | L1：原任务/约束引用、精确观察事实、操作与错误、推断计划、stale 状态；完整批次增量更新；保存/从来源重建验证 |
| `src/recoveragent/memory_store.py` | L2：任务内 exact source/tool/field/typed entity ID 索引；完整结果 JSONL；显式 task_id/as_of 检索 |
| `src/recoveragent/context_compiler.py` | 独立编译请求、确定性证据选择/去重、实际 tokenizer 计数、合法配对、无静默截断 |
| `student.py` | 默认关闭；完成真实批次后更新；生成前编译；原始 history 与实际 compiled request 分开落盘；维护时间/Token 统计 |
| `state.py`、`p3c.py` | 显式 v3 保存 Memory/索引/来源/运行版本；复用 DB/历史/Schema/时钟/RNG 和独立恢复核验；v1/v2 继续读取 |
| `student_evaluation.py` | 新锁选择、Memory 配置、固定模型 identity 检查及终态 Memory 保存；官方评价路径不改 |
| `scripts/probe_egtm.py` | 实际 AppWorld 写/读/错误/中断/跨进程恢复与固定 tokenizer 探针，脚本输出不当模型结果 |
| `scripts/audit_egtm_contexts.py` | 全部历史 P3-C 请求的只读同前缀重算，不使用评价结果选样或改行为 |
| `scripts/summarize_egtm.py` | 未来 A/B 回执重算：成功/未知、Token、调用、重复查询、业务错误、上下文耗尽、维护成本 |

新增核心模块仅三个。Adapter、官方 evaluator、AnswerGuard、Harness 校验、execution_v5 文本、Qwen chat template、训练/mask 实现均未修改。

## 2. 事实、有效性和边界

- observed 仅表示原工具返回含此原值，附 `tool_call_id`、原消息位置/哈希及 JSON 字段路径；ID、日期、路径、金额不转换。大标量（序列化超过256字符）或无法可靠解析的文本只存完整证据和引用，不编造摘要。
- inferred 为 Assistant 原文计划，绝不升级为 verified fact。执行记录只声明 `returned` / `error_observed`；完成了哪些业务要求、还剩哪些步骤无法由返回证明时保留 unknown，不能把“调用返回”认作“任务完成”。
- 方法性质只从已观察的 `api_docs__show_api_doc` 返回提取。公开文档 GET/HEAD 作为读取；其他/未知方法保守视为可能写入，跨应用失效旧业务事实，即使返回错误也不能假定没有副作用。重新读取只替代同一完整查询参数的旧结果；不同分页、过滤、排序不会共享空页结论。
- 检索同时限制任务和完整批次截止位置，精确 ID 区分数字和字符串，支持ID数组；不能提取标量的完整原文也有独立的过期来源标记。只从原任务及最近已发生的 Assistant 调用产生确定性检索词，不读官方评价或数据库内部状态。
- 非法批次不索引；AnswerGuard 返回不当真实工具观察；部分执行不更新 Memory。原调用意图、已返回结果、未返回调用和真实 DB 副作用仍由旧执行审计保存，不能捕获成完整恢复样本。
- L3 保留原 messages、原请求/模型回执、工具结果、执行证据；Memory 只是派生状态。开启时每次请求额外保存 `NNN.history.json`，实际 `NNN.request.json` 包含编译后 messages/token IDs、原历史/工具/Memory 哈希及省略/检索映射。

## 3. Context Compiler

默认配置 `configs/egtm_v1.json` 为关闭；启用配置在约24576输入 Token 时开始压缩，保留最近2个完整批次、最多48条可见事实投影和3条去重检索结果。短历史直接使用原输入，避免无意义增加 Token。

原 system/user 全文（包括任务限制及 Harness 反馈）、当前真实工具 Schema、最近完整调用/返回均保留；较早调用与返回成对移出**请求视图**。Memory 作为明确标识的 user 数据消息加入，不是新 Assistant 监督目标，也不是伪造工具返回。视图明确表示事实不完整、inferred 未证实、stale 须重验。完整证据放不下时记录 deferred 引用；不宣称检索已覆盖所有语义需求。

实际请求使用原 Qwen3-4B revision `1cfa9a7208912126459214e8b04321603b3df60c`，non-thinking，template SHA `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`。保证输入加允许生成量不超过32768，单回复最多4096；不能安全压缩就明确 `context_budget_exhausted`，不截断原始消息或 token。后端 `validate_request` 对编译消息重新分词、逐个核对实际 input IDs。

## 4. 测试和真实探针

**289 pytest PASS，Ruff check PASS，114 Python 文件 format 检查 PASS**。新增17项测试覆盖精确来源/推断、写/错误后的失效、同查询更新、分页/过滤隔离、typed ID/未来检索、无法解析返回、非法/部分批次、实际压缩/完整配对、约束保留、Memory 篡改重建、恢复运行版本、关闭后的精确请求一致性及同模型 A/B 配置。原测试覆盖 v1/v2、有限纠正、mask 等；旧锁测试改为验证其拒绝新源码，而非重写旧哈希。

最终真实探针：`runs/egtm_probe_20261027_f/`，报告 `egtm_v1_world_probe.json`。单个官方 train world 的脚本测试，不是自主 Student/Teacher 轨迹，也未判定该任务是否成功。

- 25次脚本回复、29次真实公开工具调用意图；其中1次写入后故意丢失返回，已保存真实副作用/未知返回，Memory保持上一个完整边界，禁止保存不完整快照。1次非法批次零执行，1次真实业务错误，原纠正预算/提醒机制保持。
- 真实创建、读取、修改、再读取笔记；已验证写后旧事实 stale，新读取原值来自工具。pre-error/post-error/late 三份 v3 快照，在三个独立进程恢复 DB、合法历史、当时 Schema、环境 runtime、Memory和Agent runtime，来源不变。
- 精确编译通过；所有请求输入+允许回复≤32768。最终长历史请求 **37660→17977 Token（减少52.27%）**，保留4096回复空间。原始完整 history 未删改。
- 25次脚本请求中7次实际压缩；另有1次仅构建、未发送的 next request，不计生成请求/Token总量。脚本请求输入总计 **419888→322320**（减少97568，23.24%），不是收费模型调用。
- 约139.31秒含工具/三次独立恢复；Memory更新约0.036秒，编译约1.881秒（总编译时间，含原输入分词、跨本地 tokenizer 进程通信及一次未发送构建，不是纯增量CPU时间）。late Memory两文件1528933字节；该大小包含可独立重建的历史 Schema/来源，不是模型输入大小。

最终源码锁 SHA：`83fb78507ddafa13c7fbd41b5c216951b7b80a353c2893c0666d7487a0fa2d7c`。probe实际执行前验证它；新v3要求 `agent_runtime.format=egtm_task_memory_20261027`。

开发过程如实保留：a 在沙箱内 world 初始化停住，确认本轮进程后终止；b 的 tokenizer IPC 未将 BatchEncoding 转普通字典，0工具调用即失败，修复的是探针通信。c 已通过但发现检索重复装入同样证据，d 去重后通过；e明确v3 runtime绑定后通过；最后补齐数组ID和不可解析原始证据的失效边界，冻结并执行f。c/d/e 的公开汇总分别为 `egtm_v1_world_probe_development.json` / `egtm_v1_world_probe_pre_freeze.json` / `egtm_v1_world_probe_pre_validity.json`，仅为发布前候选版本的开发证据，不能用来放行正式新运行；正式v1以最终f所绑定的提交锁为准。没有覆盖失败日志或旧输出。全轮四次完成的探针共 **116真实工具调用意图、100次脚本回复、0实际模型请求/付费API/训练步**。不将脚本注入错误计作自然失败。

## 5. 已有真实轨迹的同前缀 Token 重算

对原A100 P3-C全部12任务的218个请求，按顺序只读重建Memory，每次先验证原请求token IDs与固定tokenizer一致，再编译该时刻上下文；没有重跑工具、修改历史响应或加载官方评价。

| 指标 | 数值 |
| --- | ---: |
| 轨迹 / 原请求 | 12 / 218 |
| 压缩请求 / 无法编译 | 9 / 0 |
| 原输入总 Token | 2471377 |
| 同历史编译后总 Token | 2400074 |
| 节省 | 71303（2.89%） |
| 触发压缩的任务 | 229360a_2（5请求）、2a163ab_1（4请求） |
| 重算耗时 | 约10.45秒 |

这比脚本人造长历史的比例小得多：默认阈值只影响长前缀，多数原请求不改。例：`2a163ab_1`第20请求32101→20166。**不能推断换上下文后的响应、自然失败率、重复查询或错误副作用会改善**；真正模型 A/B 仍未运行。该重算没有把原来被隔离的基础设施/预算中断纳入自然失败训练池。

## 6. 兼容性与历史完整性

`egtm_v1_integrity.json`重验原数据6616文件、95轨迹/75任务/29家族/1072监督回合，manifest SHA仍为 `5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`。旧/新 Base-SFT 各7发布文件SHA不变，312个历史报告及配置与起点逐字节相同。真实旧v1、v2快照分别通过当前inspect；未改旧快照或奖励。

默认关闭不新增输入消息、usage字段、模型工具或动作；历史精确复现依然使用对应旧commit/锁。新 v3 保存 `memory/state.json` 和 `memory/index.jsonl`，全部进入快照文件清单，Agent runtime绑定identity。恢复从同期原始历史重新推导facts/index后对比；只改Memory的自报哈希不足以通过。pre-error不能获得post-error事实。

本轮没有修改训练数据/mask代码，也没有新SFT示范。将来若训练Memory rollout，必须使用当回合保存的**实际编译输入**建立loss/logprob上下文，不能用完整历史替代；当前接口仅保留证据，不提前实现该训练阶段。

## 7. A/B配置与未运行命令

仅两组：`configs/egtm_v1_eval_a.json`（Memory关闭）和`egtm_v1_eval_b.json`（开启）。两文件除enabled外完全一致；同A100 Base-SFT tensor `34b87e77f8f49e3fef772d9c7f1074d89f07dd32bda38d81d6675120bb5e0ccd`、execution_v5/B动态工具、seed42/temperature0.8/top_p0.95、40请求/16384生成/600秒、4096回复/32768上下文、工具次数不限。

本机已实际运行 `validate_config`、`verify_checkpoint`、`verify_source`，重验公开任务清单及已保存adapter哈希，见 `egtm_v1_preflight.json`；没有联系模型backend或检查/启动远程GPU。清单复用固定12个train任务/11家族（8个Base-SFT未见task+4个已见task），都已用于开发检查，非新盲测；没有普通摘要基线。只有已经获授权并运行的同一模型后端就绪后，才可执行以下**本轮未执行**命令，各组使用独立新目录：

```bash
PYTHONPATH=src python -m recoveragent.student_evaluation collect --config configs/egtm_v1_eval_a.json --arm base_sft --output runs/egtm_eval_A_NEW
PYTHONPATH=src python -m recoveragent.student_evaluation collect --config configs/egtm_v1_eval_b.json --arm base_sft --output runs/egtm_eval_B_NEW
PYTHONPATH=src python scripts/summarize_egtm.py --a runs/egtm_eval_A_NEW --b runs/egtm_eval_B_NEW --output reports/egtm_model_comparison_NEW.json
```

如需同期保存多检查点，已有P3-C入口可读取 `configs/egtm_v1_p3c.json`，同样未运行真实模型。新锁与默认开关文件均版本管理；启动既有backend时单独核验真实权重/环境，不自动租机或开机。A/B不包含P3-C每回合DB快照开销，以免原约20秒/快照掩盖输入成本；真实Memory维护开销仍计入原600秒。

报告成功率、有效/未知分母、实际输入/输出Token、请求/工具次数、上下文耗尽、精确重复调用、业务错误、中断未知副作用和Memory维护耗时/存储。重复调用不等于错误；DB变化不等于错误副作用，后者保留unknown并须单独控制侧审计。当前未证明语义压缩无信息损失、确定性检索对任意任务都充分，跨主机v3恢复也未实测。

## 8. 实际执行命令与下一步

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
PYTHONPATH=src HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 timeout 600 .venv-harness/bin/python scripts/probe_egtm.py --output runs/egtm_probe_20261027_f --namespace egtm_probe_20261027_f --report reports/egtm_v1_world_probe.json
PYTHONPATH=src HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 .venv-inference/bin/python scripts/audit_egtm_contexts.py --input runs/a100_remote_20261026 --output reports/egtm_v1_prefix_audit.json
```

公开证据：`egtm_v1_checks.txt`、`egtm_v1_world_probe.json`、`egtm_v1_prefix_audit.json`、`egtm_v1_integrity.json`、`egtm_v1_preflight.json`；私有原始数据/快照/请求位于 `runs/egtm_probe_20261027_{a,b,c,d,e,f}`，日志 `.cache/egtm_probe_20261027_*.log`、`.cache/egtm_prefix_audit.log`。代码、配置和汇总可公开，凭据、原始任务结果和快照未上传。

下一步是获GPU授权后执行固定同模型A/B，核对初态/模型/预算一致及全部成本，判断输入压缩是否转化为实际任务收益。**当前工程验收通过；真实模型效果待验证。**
