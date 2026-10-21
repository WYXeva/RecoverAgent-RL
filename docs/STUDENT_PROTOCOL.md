# Qwen3-4B Student 批量函数调用接口

`recoveragent.student.StudentSession` 是新的独立接口，旧 `policy.py` / `runner.py` / `inference.py` 及 P0–P2 轨迹不修改。正式模型固定 `Qwen/Qwen3-4B`、revision `1cfa9a7208912126459214e8b04321603b3df60c`，non-thinking，模板 SHA-256 与 `base_sft.py` 一致。当前完成格式、调度和证据接口；尚未接入并运行真实4B权重，不是 Student 成绩或训练完成。

## 输入、生成和执行

控制器用 `AppWorldAdapter(..., max_interactions=sys.maxsize)` 创建独立环境，使用 `actor_system(config)` 的冻结 execution_v3 与 `adapter.public_task()` 构造初始 messages。B 使用 `adapter.initial_tools()`；A 先运行已有公开 Predictor 协议、记录预测成本，并传入一次选定的 schema。StudentSession 不自动补选工具。传入的每个 schema 必须与公开 catalogue 一致，不接受 admin 工具或改写参数。

```python
session = StudentSession(
    adapter, tokenizer, messages, tools,
    tool_mode="dynamic_discovery", output="runs/NEW_STUDENT/session",
    checkpoint_id="raw_base:" + REVISION,
)
session.run(generate)  # backend callable；不是本轮启动训练/模型的命令
```

`generate(request)` 收到 `qwen3-batch-generation-v1`：固定模型/模板/思考模式/checkpoint 身份、当前 messages/tools、实际 input_token_ids、采样参数、EOS、最大新 token。后端必须直接使用这些 input IDs，执行有超时的真实推理；返回相同 policy 和 input IDs、**实际生成**的 token_ids、真实 finish_reason。不能用 Teacher 文本编码冒充 Student rollout。远程后端仍需接入并验收权重加载、超时及 checkpoint 身份；接口中的身份声明本身不是权重来源证明。

一条 Assistant 可含多个 Qwen `<tool_call>{"name": ..., "arguments": {...}}</tool_call>` 块。解析器用 JSONDecoder 读完整对象，允许合法字符串里出现结束标签；拒绝重复键、NaN、未闭合块、尾部垃圾及 length 截断。真实模板的 `<|im_end|>\n` 和推理服务停在 `<|im_end|>` 两种终止形式均受测试。调用 ID 由会话唯一生成，格式相同的 Assistant 与 Teacher Base-SFT 目标逐条回读校验。

整批先针对**生成前**的 tools 做名称/参数/ID/完成动作位置校验，之后串行执行真实工具并逐项保存结果。独立文档查询、已知 ID 的多对象读取可以合批。动态依赖的例子：先 login，下一次生成读取真实 token 才调用需要 token 的函数；先查列表，下一次才用实际返回 ID；先 show_api_doc，下一次才调用新注册函数。没有同批结果引用或占位符替换功能，不能由 Runner 猜参数或补做业务。

这保证时间边界和工具可见性，**不是通用语义依赖检测器**：模型若硬编码一个猜测 ID、或对同一对象提出相互影响的两个写操作，光靠 JSON Schema 无法证明它们独立。行为约束沿用冻结执行提示，真实4B任务运行仍需测量此类错误；不能宣称已自动解决所有依赖问题。

全部结果收齐才进入下一轮。批次中途异常保存已有结果/调度日志并停止，该未完成历史不能作为恢复检查点或完整示范。A 下一轮 schema 固定；B 仅添加已成功查询文档的公开函数。每请求默认4096、累计16384、40请求、600秒；没有独立工具次数配额。上下文超限不自动截断。真正服务的上下文容量和生成超时仍需远程验收。

## 证据与验证边界

v0.10可选 `answer_guard=True`，与 execution_v5 的系统契约和公开任务JSON一起传入，复用Teacher的单值格式检查器。被拒绝的完成不发送给AppWorld，收到明确harness消息；下一次模型回复是唯一同值纠正机会，预算不增加。无此开关的旧协议行为不变。拒绝回合在Base-SFT中只作masked上下文。详细启用条件、透传范围和统计见 `docs/ANSWER_GUARD.md`。

本地 session 保存 requests、responses、逐工具 dispatches、完整回合 messages/schema/hash 和已知/未知 token 预留。工具返回作为下一次输入；不添加 evaluator/隐藏标签。当前没有行为 log probabilities，也不与 P0–P2 checkpoint 格式混用，`rl_eligible=false`，不能直接用于在线 GRPO。

`tests/test_student_batch.py` 使用软件替身，验证批量合法性、同批新工具拒绝、下一次使用真实模拟结果、A 固定工具、输入/身份/截断拒绝及中途异常。真实 Qwen tokenizer 在新6条和旧4条成功数据上检查所有 Assistant 目标；`scripts/probe_student_batch.py` 另用真实 AppWorld 工具和真实 tokenizer、**脚本生成的调用**验证调度/消息/工具切换。探针无模型请求，不计入 Teacher 示范或 Student 成功率，也不执行任务验证器。详细结果以阶段报告为准。
