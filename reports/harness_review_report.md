# Harness 工程审查、修复与冻结

日期：2026-10-23。基线：`49d3e5b9263572b27b77f15fbaabb13d05c065b5`，已从 `origin/codex/p0-p2-recovery` 拉取确认。范围为 P3-A Harness；没有启动 SFT、GRPO 或新 Teacher 采集。

结论：确认并修复四类执行边界问题，保留原 AnswerGuard、Prompt、官方 Adapter/评价器和历史数据。当前修订以 `public_protocol_correction_v1` + `runtime_revision=review_20261023` 标识，入口为 `configs/p3a_harness_review.json`，源码/参数锁为 `configs/harness_review_protocol_lock.json`。这是已有 v1 的审查修订，未新增 Prompt 版本或业务代理组件；旧 v1 配置与锁原样保留，精确复现使用基线提交。

## A. 确认存在的问题

| 问题与位置 | 触发条件、实际影响 | 修复及证据 |
| --- | --- | --- |
| 意外纯文本结束：`p3a.run_teacher_episode`、`student.StudentSession.step` | 模型返回完整普通文本、没有 tool_calls，但 AppWorld 任务仍 active。旧路径直接终止为 `assistant_stop_without_tool`，没有有限纠错机会。Teacher 仍会运行官方评价，**不是直接把纯文本判为成功**。 | 共用 `check_batch(require_action=True)` 产生 `unexpected_stop`，共用 `rejection_event` 反馈继续执行或 `complete_task(status='fail')`；占用原两次通用机会，数值 pending 不获得额外机会。原响应不改，反馈是声明来源的 user 消息。已完成 world 不生成新请求。对应两端、合法失败、已完成、预算和原回执测试通过。 |
| 初始化失败漏熔断：`harness.ServiceCircuitBreaker.observe` | 旧代码只计 provider_error / worker_interrupted，worker 捕获初始化异常后正常退出并写出普通 infrastructure_error 时计数被清零。扩样允许继续错误时可能遍历大量任务。 | 明确分类普通 infrastructure_error 为 worker/environment；连续两次即停，保存未执行清单。集成控制器测试四任务只启动两个；Provider 401/402/403 各只启动一个。 |
| 中断批次证据不足：Teacher dispatch 循环及 Student 异常分支 | 第一调用已有返回，第二调用可能写入后抛异常。Teacher 的 dispatched_count 原来只在整批完成时赋值；两端没有可靠导出异常时数据库。官方 `AppWorld.close()` 会清缓存并删除临时数据库，不能把 close 当作快照。 | 每次执行前持久化 call_intent；每个真实返回即时计数/落盘；异常时两端共用 `capture_interruption` 导出实际 DB、runtime、部分历史、当时工具、原回合事件、未知结果调用 ID 和哈希。没有补造结果或回滚副作用。明确 `resume_eligible=false`，保存失败单独记录，不遮蔽原异常。真实写入探针及两端软件测试通过。 |
| 评价进程异常分类不完整：`p3a.run_teacher_episode` | `evaluate_isolated` 返回 evaluation_error 能单列，但其启动/超时/读取异常原来落入普通 infrastructure_error；可能继续采集，而不是立即停批。 | 用一个 evaluation_started 标志覆盖整个评价阶段，异常仍归 evaluation_runtime，reward=None，立即停止。软件测试使用评价进程 OSError 验证。官方 evaluator 实现不改。 |

附带修复：中断后的 `interrupted_usage` 原来只根据已返回 dispatch 文件计工具数，未考虑已经进入执行但返回未知的调用。现在结合执行前 intent，保留缺失结果数；同时从持久化 turn 恢复通用拒绝和提示次数；若 turn JSON 写入中断，单列 unreadable_turn_records，计数标记为已知下界，不能因此丢失未知费用/token预留。Provider 未知 token/费用预留及禁止自动重发的规则保持原样。

错误分类是控制侧分类，不解析业务返回文本来猜 Provider 状态：

| 分类 | 来源 | 控制行为 |
| --- | --- | --- |
| Provider Error | TeacherTransportError 安全状态回执 | 401/402/403 立即停；其他传输故障进入连续两次熔断；未知计费仍预留。 |
| Worker / Environment Error | 初始化、执行连接/worker 异常、缺失 worker 结果、清理失败 | 连续两次停止；任务没有有效奖励。初始化前的环境预检失败直接中止，根本不启动任务。 |
| Evaluation Runtime Error | 官方运行中的非断言异常或评价子进程故障 | 单列 reward=None，立即停批；**不等同于已证实的环境依赖故障**。 |
| Model / Tool Execution Error | 协议拒绝、实际业务 API 错误、有效官方失败 | 只在原预算内继续/纠错或记录严格失败；工具返回 401 不触发 Provider 熔断。 |

## B. 潜在风险与本轮新证据

1. **评价异常的根因不能只靠异常类型判断。** 两次脚本探针均从原 train 初态调用公开 `show_profile`，随后真实 `complete_task(status='fail')`，在写入完成后由探针注入返回丢失。保存和恢复成功，但官方测试在该未完成业务状态出现 `IndexError / UnboundLocalError`。当前保守地记录 evaluation_error，禁止当成有效 0/1 或喂给 Actor。停止整批可能较保守；将来若需细分，必须依据控制侧官方栈及新的验证证据，不能直接吞异常或修改断言。
2. **硬杀进程或存储故障不能保证最终快照。** 本次验证的是可捕获异常；intent 能表明调用可能执行过，但 SIGKILL、机器掉电、DB 无法导出时只能保留已持久化证据和未知结果。接口明确 save_error / unavailable，不允许从半批历史直接恢复。完整 Recovery Runner 的各个合法边界快照仍属后续阶段。
3. **动态依赖只能保证协议边界，不能证明任意业务调用语义独立。** 生成时的 schema 冻结，所有字面参数在执行前验证，没有结果引用/替换语言；实际返回只在下一次请求可见。同批发现 API 不能调用。同批猜测任意 ID/业务值无法通用判定，本轮不引入 ToolGraph、自动依赖推断或隐藏参数填充。
4. **Token 检查有明确证据范围。** 只约束此前同应用 login 返回的 access_token，保留多个已观察登录 token；其他应用、尚无 login 证据的 token 不做该推断。过期、账号权限等仍由真实 API 决定。若未来引入其他公开凭据来源，必须先证明这种合法来源再调整；本轮没有发现需扩大规则的证据。
5. **真实 Teacher 纠正效果未测。** 2026-10-23 只读 DeepSeek 余额查询 HTTP200，is_available=false、无正余额。生成请求 0；软件合成回执与脚本动作均不能当成 Teacher 成功示范。账户恢复后仅需少量固定任务验证纠错，不必重跑全量采集。

首次真实中断探针的脚本验收错误地要求官方结果一定是 valid/false，因上述非断言异常退出 1；原目录 `.cache/harness_review_20261023_a` 和日志完整保留。只修正探针的预期为“真实官方 valid 或独立 evaluation_error”，没有修改 Harness 奖励/官方实现。第二次独立探针通过，未复用第一次的状态。

## C. 不建议修改的部分

- `answer_guard.py` 无新增缺陷证据，源码保持不变。一次紧接的同值纠正与通用纠错已通过冲突测试，`status='fail'` 合法退出不被数值规则拦截。不要合并成一个更宽松的重试预算。
- 保留现有整批可见性、参数、ID 和 completion-last 检查。不完整批次零执行；真实 AppWorld 业务错误返回仍是观察，不能伪装成协议拒绝。
- 空页提醒只识别相同 function/query/filter/sort/page_limit 等条件，页面索引非递减的明确 JSON `[]`。条件变化打断匹配；相同调用也可能有业务意义，所以重复/401 只提示、不禁止。两条提示后保持正常执行，不再发第三条；不增加业务完成判定。
- 保留 A 固定 schema、B 下一轮动态发现。`tool_modes.py` 和 AppWorld Adapter 未修改，没有自动登录、补工具、改参数或业务补救。
- 保留官方评价只读观测及原数据过滤要求。原 `95` 条的奖励、源轨迹、manifest、源码锁全部未修改；不以降低筛选门槛凑数据。

## D. 简化及恢复学习接口

- Teacher/Student 使用同一个 `ProtocolHarness.rejection_event`，合并错误分类、字段、数值 pending 消耗逻辑；数据回执审计也使用共用 `rejection_details`。
- 启用 Harness 的路径不再做第二遍相同的 history/schema/completion 校验。未声明 Harness 的历史路径继续保留旧行为，避免重解释旧数据。
- Student JSON 重复键检查复用 `json_object`，删除同功能本地副本；Student 批次状态由 batch_open 直接表达，覆盖“已追加整批但尚未执行第一调用”的超时边界。
- 新增的执行 intent 与中断导出只有两个共用函数，无新 Runner 框架或 ToolGraph。没有增加纠错次数、提示次数、业务配置；新增一个 runtime_revision 元数据用于版本识别。
- RULES、execution_v3/v4/v5、AnswerGuard 和评价器正文未改。旧锁不重写。当前运行必须明确选择 review 配置，新摘要保存 runtime_revision；旧数据审计按无 revision 的旧约定重建。

保留的信息包括原始 request/response、每轮 schema/history 哈希、调用 ID/原参数、已返回结果、执行前 intent、部分副作用状态和控制侧评价。中断导出包含 pre_batch_history_end/hash 方便定位，但该前缀**不代表已保存了执行前数据库**；实际 DB 是中断后状态。缺失结果不补造，因此该证据不能直接当作合法恢复起点。

Harness 在同一正常任务中重新生成协议正确的回复，仍属于原预算内执行控制。它不是 Student 业务失败后从合法检查点进行的 Teacher Recovery-SFT；本轮未实现或宣称 Recovery Learning。

## E. 最终验收

| 验收项 | 结论 | 证据与限制 |
| --- | --- | --- |
| Harness 协议正确性 | PASS（本轮覆盖范围） | 全套 201 个测试通过；Ruff check/format 通过。新增 22 个回归用例，结合原 24 个 Harness 用例覆盖停止、服务错误、混合批次、副作用、Token、notice 和 mask。 |
| Teacher/Student 行为一致性 | PASS（软件） | 同一 unexpected_stop 反馈逐字段一致；两端拒绝非法混合批次；两端写入后异常保存实际状态。没有真实 Qwen3-4B 模型执行成绩。 |
| AppWorld 状态与评价正确性 | PASS（执行/隔离），异常分类保留限制 | 两次中断探针每次 2 个真实调用，第二次验收通过；A/B 各 1 个真实发现调用。另取封存清单首条成功轨迹重放 40 个调用，所有观察一致、快照恢复一致、新进程官方成功。新 Teacher 示范 0。 |
| 数据导出与 loss mask | PASS | 正式包重验仍 95 条/75 任务/29 家族，18 独立源包（21 路径含副本）未变。固定 Qwen3-4B tokenizer 对合成意外停止→纠正样例只生成 1 个目标，24 监督 token/2184 序列；前缀、拒绝及 Padding 屏蔽，Student 解析无损。 |
| 真实 Teacher 自纠正效果 | BLOCKED | DeepSeek 余额不可用；没有付费生成，无效果提升结论。 |
| P3-B 是否有新增 Harness 正确性阻塞 | 未发现；版本配对仍强制 | 原 95 条数据可按其封存训练源码准备烟测；当前新 checkout 不能冒充原包旧源码锁。参见 REMOTE_BASE_SFT.md 的训练/worker 版本分工。远程 96GB 环境、真实更新、保存重载及 dev 评价仍未验收。 |

资源：本轮 46 次真实 AppWorld 公开工具调用（两次中断探针共 4、A/B 发现共 2、历史成功重放 40）；1 次只读余额查询；模型生成请求/token 0，新增模型费用 0，训练步数 0。当前沙箱 nvidia-smi 返回系统禁止 NVML；本轮无需 GPU，未据此声称远程或本机 GPU 训练通过。

### 证据与实际命令

- `reports/harness_review_data_20261023.json`：95 条包、18 独立源包及哈希复验。
- `reports/harness_review_source_20261023.json`：P0–P2 17 文件、v3 5 文件、旧锁/依赖与 Prompt 原样保留；新源码锁。
- `reports/harness_review_world_20261023.json`、`harness_review_modes_20261023.json`：脚本真实环境探针。
- `reports/harness_review_success_20261023.json`：原成功 Teacher 的一次历史重放，绑定原 SHA 与新官方评价 SHA。
- `reports/harness_review_mask_20261023.json`、`harness_review_provider_20261023.json`、`harness_review_checks_20261023.txt`。
- 私有原始输出：`.cache/harness_review_20261023_a/`、`_b/`、`.cache/harness_review_success_20261023/`、`.cache/harness_review_mask_20261023/`；不发布原始任务/工具返回、数据库或官方隐藏报告。

```bash
PYTHONPATH=src:. .venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
PYTHONPATH=src timeout 180 .venv-harness/bin/python scripts/review_harness_probe.py --namespace harness_review_20261023_b --output .cache/harness_review_20261023_b --report reports/harness_review_world_20261023.json
PYTHONPATH=src timeout 180 .venv-harness/bin/python scripts/probe_harness.py world --namespace harness_review_modes_20261023 --output reports/harness_review_modes_20261023.json
PYTHONPATH=src timeout 180 .venv-harness/bin/python scripts/revalidate_teacher_environment.py --source runs/v4_ab_20261021_a/teacher_00013f09020d/trajectory.json --namespace harness_review_success_20261023 --output .cache/harness_review_success_20261023
HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 PYTHONPATH=src .venv-inference/bin/python scripts/probe_harness.py mask --synthetic-run .cache/harness_review_mask_20261023/run/teacher_synthetic --output reports/harness_review_mask_20261023.json
```

数据审核实际调用 `formal_dataset.verify(..., 原发布manifest_sha256)`、`verify_dataset` 和归档 SHA 检查；首次校验历史清单时比较了 21 条路径与 18 个独立哈希，断言失败，改用 manifest 哈希集合后通过。没有修改任何原包。复验不再次执行全部 95 个环境任务。

冻结交接：完成代码审查修订；下一步在账户恢复后验证少量真实 Teacher 纠错，远程就绪后按原训练锁进行短/长样本 BF16+LoRA 烟测。本轮没有把数据编码通过当作训练通过。
