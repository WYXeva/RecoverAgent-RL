# 从 LLM 视角排查 Teacher 轨迹

查看器 `src/recoveragent/inspect_teacher.py` 只读取已有日志，没有模型请求、AppWorld 执行或训练。它直接使用每回合 `requests/NNN.request.json` 中的 `messages` 和 `tools`，不拿最终历史或最终工具列表重建早期输入。

这里能看到实际发送的 API 请求、原始模型响应及真实工具反馈。供应商内部如何序列化 prompt、使用哪些 token IDs，以及未记录的内部思考不可见。本轮 Teacher 是 non-thinking。

## 使用

在项目根目录运行，Python 标准库即可，无需启动 GPU 或 AppWorld：

```bash
export PYTHONPATH="$PWD/src"

# 默认显示全部回合的概览：消息/工具数量、token、finish_reason、调用与返回数量。
.venv/bin/python -m recoveragent.inspect_teacher \
  runs/p3a_teacher_20261020_c/teacher_ad808a15ce5a

# 查看某一轮：完整历史、当时全部工具 schema、工具新增项、响应及后续反馈。
.venv/bin/python -m recoveragent.inspect_teacher \
  runs/p3a_teacher_20261020_c/teacher_ad808a15ce5a --turn 7

# 导出全部回合；输出必须使用新文件名，并位于原轨迹目录之外。
.venv/bin/python -m recoveragent.inspect_teacher \
  runs/p3a_teacher_20261020_c/teacher_ad808a15ce5a \
  --all --output outputs/my_teacher_view.md

# 保留结构化字段，方便进一步筛查。
.venv/bin/python -m recoveragent.inspect_teacher \
  runs/p3a_teacher_20261020_c/teacher_ffe689c06ef7 \
  --turn 6 --format json --output outputs/my_truncated_view.json
```

输入也可以是对应的 `trajectory.json` 文件。便携数据包的 `evidence/teacher_.../` 同样可以读取；缺少 worker 配置时预算显示未知，不套用当前默认值。模型响应尚未落盘时显示 `NO_RESPONSE_RECEIPT`，用量为 null，不编造零输出或工具结果。

本轮实际生成，直接打开：

- `outputs/llm_view_20261020/success_turn1.md`：第一次请求，只有 10 个初始工具。
- `outputs/llm_view_20261020/success_all.md`：成功轨迹全部 8 回合。
- `outputs/llm_view_20261020/truncated_turn6.md`：另一条轨迹第 6 回合的截断响应。

这些视图包含任务派生内容，仅保留在已忽略的本地 `outputs/`，不上传公开仓库。常见密码、访问令牌、API Key 会在结构化 JSON、字符串形式 JSON 及重复文本中一致隐藏；工具 schema 的参数名/类型保持可读。该展示性脱敏不是任意自由文本的通用秘密扫描器。原始请求不修改，SHA-256 对应原始文件，不能拿脱敏视图重新执行或当训练数据。

## 如何阅读

每轮分成几个明确部分：

1. 请求参数及工具变化：请求输出上限、当前可见函数名、相对上一请求新增/移除的工具。
2. 请求发出时的 messages：system、user、此前 Assistant 与此前真实 tool 返回。
3. 请求发出时的 tools：该轮实际传给模型的完整函数说明和参数 schema。
4. 模型原始响应：保留 `finish_reason`、完整 `tool_calls`、非法 JSON 参数或截断内容；不会自动“修好”后展示。
5. 响应之后的工具反馈：当前响应触发的实际返回，它们不属于本轮模型输入。只有之后再次请求模型时才可能成为上下文。
6. 控制侧诊断：最终 reward、终止原因、格式错误及哈希不一致等，明确标记为非 LLM 输入。查看器不读取 `evaluation.json` 或隐藏测试。

对失效批次，模型响应中可能列出 57 次调用，但实际返回为 0；查看器不会把“模型要求调用”误记为“已执行调用”。如果请求与轨迹摘要哈希不一致，会提示差异并展示实际请求文件，不静默替换成摘要内容。

## 当前 token 限制与失败含义

数值来自本项目当前配置和实际采样，不是对模型上下文容量的泛化说明。

| 限制 | 当前值 | 统计范围与处理 |
| --- | --- | --- |
| 单次响应输出 | 4096 token（v0.5） | 每次请求的 `max_tokens`；还受剩余轨迹/全局输出额度约束；旧 A/B/C 实际为 2048 |
| 单条轨迹累计输出 | 16384 token | 累计供应商 `completion_tokens`，包含文本和函数调用输出；达到后停止继续请求 |
| 每轮采集累计输出 | 32768 token | 包含失败和预测开销；本次双模式四次比较均分为每次 8192，旧三轮试点记录保持原样 |
| 请求体大小 | 131072 字节 | 序列化后的完整 API 请求，不是 token 数 |
| 工具调用数量 | v0.6 无数量配额（null） | 旧记录的 40 次/批次 64 次保留；仍校验完整调用和合法 schema |
| 其他预算 | 回合、模型请求数、时间和费用 | 也会停止采样，具体读取该 worker 配置 |

每轮实际输出上限为：

```text
min(单次响应上限,
    min(单轨迹上限, worker 开始时剩余全局额度) - 本轨迹已生成 token)
```

输入历史、工具返回与工具 schema 不计入“16384 输出 token”，但会占用请求输入、影响费用及请求体大小。多轮请求反复携带历史，所以成功轨迹累计输入 token 可以大于 16384；这不是超出输出预算。

必须区分：

- **累计预算耗尽**：记录 `budget_exhausted`，保存已有终态再运行官方验证，reward 由验证结果决定。这个终止标签目前也用于回合/工具/请求/时间限制，不能只凭标签断定是 token 耗尽。用量和 worker 配置可辅助定位。
- **单次响应截断**：供应商返回 `finish_reason=length`，当前协议整轮拒绝，不执行其中的任何调用，终止为 `invalid_completion`，再评价已有状态。即使已有状态偶然成功，带格式错误的轨迹仍不能进入 Clean Dataset。
- **达到数值不等于截断**：恰好生成请求上限的 token 且返回正常 `tool_calls`/`stop`，不会单凭等于上限判错；还要校验调用和历史。
- **基础设施或供应商异常**：例如请求体超限、网络/进程中断，或供应商实际用量越过约定上限，按当前代码单列基础设施错误，reward 为 null，保留已知用量和未知预留。

本次 C 轮失败是**第 6 次响应达到 2048 token 且 `finish_reason=length`**；该轨迹累计仅 2619 输出 token，远未达到 16384。这是单响应截断导致的数据不合格，不能推断模型在更宽预算下仍无法完成任务。

## 可见工具由谁提供

接口名称、说明和参数 schema 来自固定版本 AppWorld 的 `world.task.api_docs.function_calling()`。项目 `AppWorldAdapter` 负责哪些 schema 当前传给模型，`TeacherPolicy` 将它们写入每次请求的 `tools` 字段。

初始状态只传 `api_docs` 与 `supervisor` 的公开工具，本次实际为 10 个。任务消息同时提供应用简介，模型可以知道 Spotify、Gmail 等应用存在，但没有一次获得全部应用的可调用函数定义。

以上是 `dynamic_discovery` 模式。v0.5 新增 `api_predictor`：执行前由官方预测组件读取任务和完整公开 API 简介目录，随后将选中的完整 schema 一次性固定给 Actor。查看器用独立 Predictor 区域显示选择结果，`--all` 包含实际 `000.request/response`；`--turn N` 仍只编号 Actor 回合。两种模式协议、预算和比较命令见 [TOOL_MODES.md](TOOL_MODES.md)。

LLM 自己选择要探索的应用/API：

```text
初始 10 个发现/任务工具
→ LLM 请求 api_docs__show_api_descriptions / search_api_docs
→ LLM 请求 api_docs__show_api_doc(app_name, api_name)
→ Adapter 加入该接口的官方 schema
→ 下一次模型请求的 tools 包含该函数
→ LLM 选择是否调用、何时调用及参数
```

当前实现只在成功执行 `show_api_doc` 后动态注册所选函数。看到接口名字或搜索结果，不等于这个函数已经在可调用列表中；以该轮请求的 `tools` 为准。一个 batch 内新发现的函数也不能在同批后面的调用中立即使用，因为整批必须先通过发出请求时的 schema 校验。

v0.6 的 Actor v2 提示明确允许独立请求组成完整批次，无工具调用数量配额；多个歌曲详情查询可以在一条回复里返回多个 `tool_calls`。这与“并发执行”无关，Runner 仍串行执行每个调用。A 的官方原提示与依赖增强变体的实际文本均可在 `--all` 的 Predictor 区域查看，不靠汇总标签猜测模型看到了什么。

成功轨迹可见工具数量为 `10 → 10 → 12 → 14 → 14 → 14 → 14 → 14`。控制侧快照、数据库检查、官方验证器和隐藏答案不注册为 Actor 工具。

## 验证证据

`tests/test_inspect_teacher.py` 覆盖历史/工具时刻边界、原始截断响应、凭据脱敏、缺失响应、记录不一致提示及只读输出。对现有六条真实 Teacher 尝试的全部 28 次请求做了离线核对，原始文件哈希不变，证据为 `reports/llm_viewer_validation.json`。该核对没有产生新的 AppWorld/模型实验结果。

## v0.9 A公开依赖纠错视图

新A的`predictor`视图保留首次选择、`corrections`中的至多一次公开依赖纠错输入/响应，以及`final_selected_names`。纠错不是Actor回合，费用已计入provider_requests；无响应的请求也展示并标记response_missing。20份真实新视图位于本地`outputs/v4_ab_20261021_a/<task_id>_<tool_mode>.md`，路径和哈希见`reports/v4_ab_handoff.json`；原始正文不提交GitHub。
