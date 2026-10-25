# Harness 小范围重构与验证报告

2026-10-25；基线 `0d59b0099aae67626d56fa32c12746e99bcb4948`；开发分支 `codex/p0-p2-recovery`。开始前独立 Git 克隆及主工作区均无未提交差异，fast-forward pull 返回 Already up to date。

**工程验收 PASS；真实 Teacher/Student 纠错效果未测。** 本轮不训练，不运行 Recovery 或 GRPO，不调用付费模型。原 95 条示范、Base-SFT checkpoint、官方奖励与全部旧报告/锁保持不变。

## 1. 审查发现与修复

先阅读 AGENTS、PROJECT_PLAN、STATUS、HARNESS_V1、原 Harness review 和 P3-B diagnosis；在修改前列明问题并增加三个回归测试，基线均失败。不能将旧诊断中的所有问题都说成这次新发现：精确 Schema 反馈与评价完整性分类已在上一轮实现，本轮复用。

| 确认的问题 | 基线代码位置 | 修复与证据 |
| --- | --- | --- |
| 精确 Schema 反馈能运行，却不能通过示范重放审计 | `harness.py:424` 重建 feedback 未传 schema_error；`demonstrations.py:136` 只核对错误码/函数名 | 重放保存的反馈详情，并在原始 receipt 校验中从原响应和当轮 Schema 独立重算，拒绝伪造详情。覆盖成功导出/复验及篡改负例。当前 `harness.py:480`、`demonstrations.py:150`。 |
| 工具返回、计数和持久化重复；Student 未启用 AnswerGuard 时没有真实返回计数 | `p3a.py:507–537`、`student.py:363–395` | 两端共用 `harness.record_tool_result`。无论数值 guard 是否开启，都保存真实返回数；收到返回后再解除 in-flight 标记，落盘失败前保留已知结果。软件与真实部分写入探针通过。 |
| 后端身份/响应边界异常会提前清零未知 token/费用；Student 将此类故障标作模型错误 | `student.py:277–286,428–440`；`p3a.py:395–418` | 校验身份、输入、实际 token 边界之后才消除预留；异常归执行故障，不消耗协议纠错。原响应仍保存。Teacher 的现场处理和 killed-worker 费用重算共用 `verified_response_usage`，避免重算又将错误后端按配置价格计费。 |
| 错误层次混杂 | `harness.py:303–316`，Student 协议拒绝标 `model_tool_execution` | 协议事件、真实工具错误、执行故障、评价未知、官方任务失败分别标记；最终失败要求 `status=valid` 且 reward=0。Teacher/Student 复用分类函数，保留已有熔断策略，不新增重试状态机。 |
| 精确反馈只在显式 schema revision 下启用，默认 Student 仍是旧通用反馈 | `student.py` 构造参数与 `ProtocolHarness` 默认 revision | 新默认运行版本 `shared_execution_20261025` 启用已有精确反馈；新 Teacher 配置使用同版本。旧记录仍按其显式 revision 重放。不得将新默认行为称为旧实验。 |

精确反馈例：缺少 `app_name` 时为 `schema_error={"path":[],"rule":"required","missing_fields":["app_name"]}`。嵌套类型错误返回路径，例如 `["payload","items",0,"count"]` 和 `expected_type="integer"`；未知字段列出 schema 可证明的字段名。**不回显参数值，不补值，不选 API，不自动登录。**

## 2. 错误分类与处理策略

| 类别 | 控制侧标记 | 处理 |
| --- | --- | --- |
| A 协议错误 | `protocol`；原 `protocol_rejection` 码保留 | 整批零执行；显式反馈；原每任务两次通用纠正，第三次拒绝终止。数值 AnswerGuard 保持原独立一次紧接同值纠正，不扩预算。 |
| B 工具业务错误 | `business` | 识别 AppWorld 公开错误返回封装，保留真实内容与调用 ID；让模型据此决定下一步，不当作协议拒绝，不自动重发，不触发 Provider 401 熔断。 |
| C 执行故障 | `provider` / `worker_environment` | 保留未知调用/用量及真实副作用；Provider 401/402/403 立即停止，连续两次传输/worker 故障沿用现有熔断。响应身份/边界错误也不能算模型自然失败。 |
| D 评价异常 | `evaluation_runtime`，评价详情保留 environment_error / unknown | 无可用完整官方结果时 success/reward=null，停止采集。 |
| E 任务失败 | `task_failure` | 官方结果可用、完整执行但未通过；严格官方 0 奖励不变，不能与执行故障混淆。 |

事件级 business/protocol 与轨迹最终 task_failure 是不同层级；一次局部错误不等于整任务失败。业务错误识别只观察官方错误封装，不扫描普通 JSON 文本中的“401”或错误词来猜服务故障，也不声称能判断所有 API 返回的业务语义。

### evaluation.py 审查结论

本轮检查固定官方 `TestTracker.__init__`、`num_tests`、`success`、`__exit__` 与项目包装层。官方 num_tests 在有 test_data 时是预定义测试数，项目检查 passes+failures、success 和观察异常数量有实际依据。上一轮 `official_result_integrity_20261025` 已保留完整官方字典及 official_success，区分直接任务评价代码中的状态访问失败、环境异常和未知来源，故**本轮没有再次修改 evaluation.py，也没有修改官方验证器**。

真实初态/脚本终态 `e3d6c94_3` 的完整官方 false 按 task_failure 保留；不把缺少目标对象的官方测试异常一律丢出分母。未识别来源的运行异常继续为 unknown。直接任务代码内的 KeyError/IndexError 等并不证明具体哪次业务动作错误，仍需控制侧证据诊断，不能用此分类向 Actor 泄漏答案。

## 3. 简化与执行边界

- 共用已有 `check_batch`、`strict_arguments`、`rejection_event`，没有再写第二套 Schema 检查器。Teacher 原生 API envelope 与 Qwen 文本标签解析器仍各自负责传输格式；二者之后走相同可见性、参数、ID、批次检查，不强行套同一父类。
- 合并两端逐工具 observation 构造、history/event 追加、计数、in-flight 解除、文件目录及文件名逻辑为 **一个** `record_tool_result`。预算、实际调用、AnswerGuard 判定仍留在现有 Runner。
- Teacher 在线核验与 interrupted_usage 重算复用 `verified_response_usage`；Student 的 token receipt 身份/输入验证依旧属于自己的后端接口。
- 删除 Student 手写协议/执行错误分类分支，复用 rejection_event / failure_category。没有新增计数器、重试额度、ToolGraph、规划器、每工具补救规则或运行时框架。
- 保留 Adapter、state.py、teacher.py、teacher_history.py、AnswerGuard、A/B 获取机制、无进展提醒、execution_v5 Prompt、base_sft.py 原文。没有必要重写这些已验证部分。

计数口径明确：`appworld_attempted_count` 是发起调用的意图数；`appworld_dispatched_count` 是收到真实 AppWorld 返回的次数；`dispatched_count` 包含真实返回及明确标记的 guard 反馈。没有返回的调用可能已经写入，不能用返回数冒充实际执行数。已有 intent 和 interruption 机制保留；不完整批次 `resume_eligible=false`，不补造 Tool 消息。

## 4. 数据、版本与验证

新运行配置：`configs/p3a_harness_refactor.json`；新锁：`configs/harness_refactor_protocol_lock.json`。新配置的付费授权仍为 false，不是本轮采集授权。Student 默认 Harness revision 与该配置一致。历史兼容意味着原数据可审计、旧锁/源码可在对应 commit 重现，**不意味着可以在新源码下绕过原 P3-B/P3-C 锁**。当前旧冻结采集入口应拒绝修改后的源文件；以后使用新 Harness 的 P3-C 运行须另建匹配版本，不能修改旧哈希。

验证结果：

- **252 项软件测试全部通过**，较基线新增 14 个测试用例；`Ruff check src tests scripts` 通过；`Ruff format --check src tests scripts` 对全部 **99 个文件**通过。
- 覆盖：缺 app_name、嵌套缺字段/未知字段/类型路径、不可见工具、非法混合批次零执行、业务 401 保留、连续执行故障熔断、部分执行副作用/未知结果、Teacher/Student 相同反馈、两次纠错限制、原始响应/详情防篡改及导出。
- 真实 AppWorld **A/B 各一个脚本探针**：每模式 4 次公开 API 调用；401 实际返回没有触发协议纠错/服务熔断；非法批次零执行；第二个写调用执行后丢失返回，实际 DB 变化与中断快照一致，已知返回=1、未知调用=1、不可恢复。A 使用脚本预选的固定 schema，B 实际动态发现，**没有运行模型 API Predictor 或比较 Agent 成功率**。
- 开发中首轮探针保留，冻结后最终探针另建目录。两轮合计 **16 次真实工具调用**；所有模型请求/生成 token/计费/训练步数为 **0**。脚本是测试注入，不是自然失败或 Teacher 示范。
- 固定 Qwen3-4B tokenizer 对 **合成**“缺 app_name→反馈→纠正”轨迹实际编码：1 个 Assistant 样本，24 监督 token，最长序列 2238；拒绝回合、Harness User 反馈、历史与 padding 均不监督，Student parser 回读一致。没有运行模型权重或声称 SFT 训练通过。
- 原正式包仍 **95 条 / 75 个任务 / 29 家族**，18 个来源包及文件 SHA、动态 Schema 复验通过；可信 manifest 仍为 `5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`。7 个 Base-SFT 发布文件与原 release SHA 一致。
- 264 个历史锁/报告及保留核心文件逐字节与基线一致。详细列表和 SHA 在 `harness_refactor_integrity_20261025.json`。原 95 条 reward、原始模型输出和历史实验未覆盖。

## 5. 证据与实际命令

公开证据：`reports/harness_refactor_{world,world_final,mask,integrity,validation}_20261025.json` 和 `reports/harness_refactor_checks_20261025.txt`。完整私有世界/回执：`runs/harness_refactor_20261025_a/`、`runs/harness_refactor_20261025_b/`；合成 mask fixture：`.cache/harness_refactor_mask_20261025/`；日志 `.cache/harness_refactor_*`。不上传原始任务、数据库、隐藏测试内容、凭据或权重。

```bash
PYTHONPATH=src:. .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts

PYTHONPATH=src timeout -k 5 300 .venv-harness/bin/python scripts/probe_harness_refactor.py \
  --namespace harness_refactor_20261025_b --output runs/harness_refactor_20261025_b \
  --report reports/harness_refactor_world_final_20261025.json

PYTHONPATH=src HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 \
  .venv-inference/bin/python scripts/probe_harness.py mask \
  --synthetic-run .cache/harness_refactor_mask_20261025/run/teacher_synthetic \
  --output reports/harness_refactor_mask_20261025.json
```

还实际执行 `formal_dataset.verify`、`p3c.verify_checkpoint`、`protocol_lock.verify_lock`，并使用测试中的 schema_teacher fixture 构造软件审计样本。开发中两个用例失败来自测试 fixture 的非规范 JSON 序列化与重复调用 ID，修正 fixture 后通过；没有放松生产验证规则。

## 6. 剩余限制

工程验收与真实模型效果分开：**尚未测 DeepSeek 或 Qwen3-4B 在新反馈下能否更常纠正、是否提升任务成功率**。没有新增真实 Teacher 正向示范。真实业务语义错误（例如误删歌曲、错选统计指标）仍由模型承担，Harness 不补救业务。

已返回的错误封装只说明真实工具报错，不能穷尽其内部根因；未知评价继续保守排除训练标签。进程硬杀/磁盘完全不可写时，状态导出仍只能 best effort；已有真实结果、调用意图和未知用量尽可能保留，不虚构检查点。

P3-C 的远程可用性和自然失败采集不是本轮工作，未访问或启动远程 GPU。此前 P3-C 真实模型采集 BLOCKED 的状态不因此变成 PASS。本轮没有 Base-SFT 重训、Teacher Recovery、Recovery-SFT 或 GRPO。
