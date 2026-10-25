# Harness v1：公开协议纠错与环境验收

用户在失败讨论后明确要求工程化实现。基线为 `473693c3b768940679a2367d149fd54293e27496`；新增 `public_protocol_correction_v1`，不反写旧实验。实现及验证结果见 `../reports/harness_v1_report.md`。

## 2026-10-25 重构修订（当前运行入口）

新版本 `shared_execution_20261025`；Teacher 使用 `configs/p3a_harness_refactor.json` / `configs/harness_refactor_protocol_lock.json`，StudentSession 与 ProtocolHarness 默认使用同 revision。旧 revision 仍用于原数据重放；旧运行须匹配历史源码，不能改旧锁放行。此配置默认无付费授权。

复用上一轮已有的精确 Schema 反馈，修复反馈详情无法通过示范复验的问题；receipt 校验从原始响应与当轮 schema 重算详情，不能只信保存的反馈。Teacher/Student 共用 `record_tool_result` 保存调用返回及计数；响应身份验证通过才清除 token/费用预留，后端契约异常是执行故障。两次通用纠错、AnswerGuard、Prompt、A/B 和 notice 限制不变。

分类：协议 `protocol` → 有限纠正；真实工具错误 `business` → 保留观察，不自动重发；`provider`/`worker_environment` → 原服务熔断；`evaluation_runtime` → reward 未知；`task_failure` → 完整可用官方结果为0。局部错误与最终轨迹失败分别记录。评价包装层沿用上一轮完整性检查，本轮官方与项目 evaluation.py 均未改。新发现、测试和限制见 [重构报告](../reports/harness_refactor_report.md)。以下旧日期内容是当时契约，不覆盖本修订。

## 2026-10-23 审查修订（历史运行入口）

审查基线 `49d3e5b`，完整发现与证据见 [Harness审查报告](../reports/harness_review_report.md)。Prompt/RULES/AnswerGuard不变；行为修订用 `runtime_revision=review_20261023` 明确标识。当前入口 `configs/p3a_harness_review.json` 与 `configs/harness_review_protocol_lock.json`；以下原v1配置及锁仍作为历史保留，不能改写后冒称旧运行。

- 活跃任务返回无工具普通文本，使用原通用预算反馈 `unexpected_stop`，要求继续公开工具执行或正式 `complete_task(status='fail')`；不猜测自然语言是否等于结束。已经完成的world不继续请求模型；合法失败完成不拦截。原响应仍只在回执中保存，拒绝回合不监督。
- 连续两次普通 infrastructure_error（包括初始化失败）也会熔断；评价子进程启动/超时/读取错误与官方非断言错误均单列 evaluation_runtime、立即停止。业务工具401不会成为Provider401。
- 每个真实调用前写入call_intent，实际结果逐个落盘；中途失败导出实际DB/runtime/部分历史/当时schema和未知调用ID。`interruption/manifest.json`固定 `resume_eligible=false`，不伪装成完整恢复检查点。硬杀或保存失败只能保留已有证据，显式标识save_error/unavailable。
- 通用拒绝事件、分类与Student重复键校验共享；保留原数值纠正和两次notice限制，不增加业务语义修补。

原95条数据及所有旧锁、Prompt、官方奖励保持原样；当前源码审计兼容旧数据，但运行原包训练代码仍需匹配原封存源码。Harness纠错不是Recovery-SFT。

## 执行流程

```text
原始公开任务 + execution_v5 + 显式 Harness v1 规则
       ↓
模型完整回复（原始 request/response 先落盘）
       ↓
整批格式、历史 ID、当时 Schema、参数与已观察凭据检查
       ├─ 通过 → 原样执行 → 真实工具结果 → 下一回合
       └─ 拒绝 → 零工具执行 → 显式 harness 反馈 → 模型重新生成
       ↓
保存终态与 model hashes → 关闭 world → 新进程官方评价
       ↓
来源、回执、动态 Schema、拒绝记录、mask 与完整性复验
```

Teacher 与 Student 使用同一个 `harness.py` 检查器。`p3a.py` 在原生 Teacher 返回后接入；`StudentSession(protocol_harness=True)` 在 Qwen 批量解析后接入。Student 的 system 必须包含 `harness.RULES`，已有数值规则另保留 `answer_guard.RULES`。未声明新协议的历史配置不自动启用纠错。

## 可以纠正什么

| 机制 | 实际行为 | 不执行的隐式修补 |
| --- | --- | --- |
| 未开放 API / 错参数 / 重复 ID | 整批拒绝，反馈错误类别及可安全表示的函数名 | 不自动注册、补调用、改参数 |
| 截断回复 | 整批零执行，要求缩小批次重新生成 | 不补 JSON，不执行可解析前缀 |
| 错误 access_token | 仅在此前已获得同应用 login token 时检查原样一致性 | 不替换 token，不自动登录，不在错误消息复制凭据 |
| 无进展提醒 | 相邻相同调用/结果、相同查询条件的连续空页、同函数连续公开 401 | 不直接判任务成功，不替模型选集合或分页终点 |
| 数值格式 | 沿用 v5 的一次同数值纠正 | 不更改金额，不查询标准答案 |
| 服务故障 | 401/402/403 立即停止后续任务；连续两次传输/worker故障停止 | 不自动无限重试，不把未知费用记零 |

每轨迹最多 **2 次通用协议纠正机会**，第三次不合法回复终止；最多 2 条无进展提醒。模型回复与纠正均消耗原请求、token、时间和费用预算。数值纠正仍只允许紧接着的一次回复，不能借通用纠错延长。Provider 错误和 AppWorld 工具返回的 401 分开处理。

首版不自动重发传输失败请求。客户端无法确认是否计费或是否已有部分响应，保留未知生成/费用预留后交由任务级熔断处理。停止原因写进 batch summary，campaign 控制器不得开启下一批；未执行清单显式保存。

A 保持一次性固定工具集；B 只能由模型下一轮通过公开发现工具获得 Schema。同批刚发现的工具不能立即使用。相同 API 名的目录信息不等于当时已提供其完整可调用 Schema。

无进展检测是提示性启发式：任何中间工具调用都会打断“相邻空页”匹配，查询/filter/sort 必须相同，结果只识别明确的 JSON 空列表。它不能证明已读全所有记录，不自动终止分页。复杂集合、排序、目标选择及写入范围仍由模型负责。本版未新增业务计算工具。

## 消息、监督与审计

不合法输出保存在原始模型回执中。为避免向后续请求塞入非法函数消息、重复 ID 或半个 JSON，**不把这类输出伪装成合法 Assistant/tool 消息**；追加 `role=user` 的规范 JSON 反馈，`source=recoveragent_harness`、`appworld_executed=false`，system 已声明其来源。不会将其冒称用户原任务或 AppWorld 返回。

对应 turn 记录 `protocol_rejection`、`rejected_function`、`supervise=false`、实际零执行数和反馈，`assistant_index` 指向该轮生成前的历史边界（该位置在拒绝后是 harness user 消息）。原始 request/response 保持完整、哈希绑定。合法的数值拒绝沿用 v5 Assistant + 显式 tool 反馈，不改变历史协议。

`validate_receipts` 从原始返回重新解析并复算拒绝类别，`validate_record` 重建反馈、计数、历史边界及提示性 notice；不能只信 `supervise=false`。数据导出仍需真实 Teacher、官方 train、干净初态、官方成功和原有筛选通过。协议纠正后的正常任务可以形成新版本 Base-SFT 示范；不是 Recovery-SFT 示范。

每个训练目标只监督当前真实 Teacher Assistant，所有前缀角色和 padding 均屏蔽；拒绝回合不生成训练目标。动态 Schema 以该回合实际可见列表为准。整条轨迹若超过 32768 或单目标超过 4096，进入 deferred，不截断。

`inspect_teacher` 显式显示拒绝反馈和无进展提醒。控制侧官方评价、错误内部栈、数据库状态不进入 Actor 输入。

## 预算与版本锁

入口配置 `configs/p3a_harness_v1.json` 默认 B，保留固定 v5 的 10 个 train 任务作为未来诊断清单，不按新结果换任务，也不声称无偏泛化。运行时仍需使用已有授权的私有价格/授权配置，公共模板不包含有效凭据或自动付费授权。

每轨迹 40 请求 / 16384 输出 token / 600 秒 / USD1，单回复 4096，工具次数不设配额；10 个任务批次上限 400 请求 / 163840 输出 / 6000 秒 / USD10。A Predictor 计入同一预算。工具批次中触达时间限制会标为中断，不能当成可恢复完整边界。

新配置将本地 JSON 请求保护上限从 131072 提至 524288 字节，避免把字节数误当成 tokenizer 上下文限制；这不增加模型请求/生成/费用预算，也不保证 32k 可训练。Teacher 仍完整记录历史；长轨迹由正式格式检查整体 deferred。Student 请求实际按 `input_tokens + max_new_tokens <= 32768` 分配剩余窗口，无静默删历史。

`harness_v1_protocol_lock.json` 固定代码、规则、依赖与生成/episode 参数。旧 v3/v4/v5 提示正文和旧锁文件保留；共享驱动已更新，旧 v5 精确复现需 checkout 基线提交，不能修改旧锁使新代码冒充旧版。

## AppWorld 环境与独立评价

原 SQLAlchemy2.1.5 对 SQLite `file:` URI 的字符串渲染改变，使固定 AppWorld 反解析得到错误内存路径。新独立环境固定 SQLAlchemy2.0.52 + SQLModel0.0.48 + greenlet3.2.4，其余沿用原 AppWorld 锁。旧 `.venv` 与 `requirements/appworld.lock.txt` 不变，未编辑上游源码。

新锁：`requirements/appworld-harness.lock.txt`。本地 `.venv-harness` 使用独立 SQLAlchemy/greenlet，并通过 `.pth` 只读引用原环境其余包；本地 `pip check` 与真实环境检查分别验收。新机器安装完整新锁，不复制含本机路径的 `.pth`。

```bash
# 新机器安装说明；本轮未执行远程安装。
python3.14 -m venv .venv-harness
.venv-harness/bin/python -m pip install -r requirements/appworld-harness.lock.txt
.venv-harness/bin/python -m pip check
PYTHONPATH=src .venv-harness/bin/python -c 'from recoveragent.evaluation import environment_check; print(environment_check())'
```

新采集在 provider 请求前检查内存 URI；完整状态保存后在新进程运行未修改的 `appworld.evaluator.evaluate_task`。采用 Python 只读 profile 观察原 TestTracker 的异常类型：普通 AssertionError 保留官方任务失败；非 AssertionError 或顶层异常标为 `evaluation_error`，不当成有效 0/1 标签。不会改写 TestTracker、跳过断言或改变官方成功条件。非断言异常未必都能归咎基础设施，因此先隔离为评价运行错误待诊断，不武断归因模型。

`scripts/revalidate_teacher_environment.py` 串行执行原 Teacher 动作、比对每个公开反馈、保存并恢复新终态，再独立官方评价。任何反馈差异即停止该条；不调用 Teacher、不生成新增示范、不补造旧 model hashes、不改旧奖励。新证据与原包 manifest/轨迹 SHA 关联。

## 验收边界

软件测试、真实 AppWorld 状态/调用、真实 Qwen tokenizer、真实 Teacher 纠错效果分别报告。前面三项通过也不能替代第四项。当前账户是否可用以 `reports/harness_provider_preflight_20261022.json` 为准；无可用余额时只完成不付费验证，SFT/GRPO 不启动。
