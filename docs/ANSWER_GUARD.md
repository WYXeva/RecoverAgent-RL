# 单值答案格式检查与模型自纠正 v1

本协议对应 execution_v5 / numeric_answer_self_correction_v1，由用户确认后启用。它解决可明确识别的单值答案表示问题，不能判断金额算得是否正确。D023曾讨论自动序列化和模型重发两种路径；本次采用模型重发，没有静默改写参数。官方 AppWorld 源码和 evaluator 均未修改。

## Actor 可见契约

单一金额/数量用 JSON number；文本、名称、ID、区间、百分比、多值或明确要求的格式按任务语义保留。实际硬检查只覆盖公开指令中以 `What is the total/average cost/price/amount ...?` 或 `How many ...?` 开始、且没有歧义标记的单问句。实现不读取任务ID、scenario、工具返回、答案或评测标签。

这是有意保守的可观察启发式，不是通用任务类型识别。多个问句、问题中的连接词/列表/范围/百分比/编号/格式要求、引号等退出识别；问题后目录路径的引号不影响数值判断。识别之外的任务完全透传，后续覆盖扩展应另记版本，不根据隐藏标签补规则。

对于识别范围内的 `complete_task(status=success)`，有限 JSON 数值直接透传。明确的数字字符串或带单一 `$ € £ ¥` 符号的十进制字符串会被拦截：

```text
模型原始 complete_task(answer="$368")
→ 不调用 AppWorld
→ tool role 中返回 source=recoveragent_harness,
  error=harness_format_error, appworld_executed=false
→ 下一次模型回复 complete_task(answer=368)
→ 校验同值后原样执行 AppWorld
→ 原始任务官方验证器独立评价
```

该例只说明协议，不能当作 Teacher 成功示范。`"$1,234"`、混合文字、区间和多个金额不解析、不尝试纠正，原样交环境和评价；不会从任意字符串抽出数字。布尔值/空值等不获得纠正机会。原始字符串只用于明确的数值相等检查，harness不会生成替代调用。

只有紧接着的一次模型回复能纠正，必须是单独一个完成调用；换数值、继续查工具、多次完成或再次字符串形式均终止。模型可使用 `status=fail` 明确放弃。格式不合法或输出截断由既有整批校验停止，不额外救援。纠正没有新额度：与正常任务共享40次端到端模型请求、16384累计输出、600秒、单回复4096和原费用预算，工具调用次数无配额。若首个格式错误发生时预算已尽，不再请求模型。

正常批次中的业务调用仍按原样执行；若末尾完成被拒绝，只保证该完成没有环境副作用，不声称整个批次回滚。独立调用/依赖跨回复的规则仍适用。纠正回复不允许夹带其他业务调用。

## 数据、Student与监督

- `messages` 与供应商原始输出逐字对应，不改 Assistant 的数字；harness反馈保持调用ID，并明确来源。
- `usage.tool_requests` = 实际 AppWorld 调用 + harness 拦截。`tool_calls`仅计实际调度，模型请求及所有token照常计费。日志分别在 `dispatches/` 和 `harness_feedback/`。
- 每回合保存 `harness_verdicts`、`appworld_dispatched_count`、`supervise`。任何含拒绝的 Assistant 整回合 `supervise=false`，包括同回合其它本来合法的业务调用；这是保守的数据策略。
- 错误回合及真实反馈留在后续模型上下文，loss=-100；仅纠正回合及其他正常Assistant目标参与loss。系统、任务、工具/harness消息和padding一律不监督。
- 导出/迁移验证从公开指令和原始调用重新执行检查器，核对反馈、监督标志、调度计数和终态。只有通过原任务官方验证及既有格式/循环过滤的真实Teacher轨迹才能进入Clean Dataset；脚本探针和离线模拟不能进入。
- `StudentSession(..., answer_guard=True)`要求相同系统契约和公开初始任务JSON，使用同一个 `AnswerGuard`。这是新会话接口，不宣称支持从旧恢复快照重建纠正器状态，也未验收真实4B后端。P0–P2不改写。

## 版本和实验解释

execution_v3五个冻结源文件与 execution_v4 提示、A认证依赖检查实现保持原字节。共享P3-A驱动更新后，旧v4源锁会在新checkout正确拒绝；精确复现旧v4请使用 `f5ce0596abbddf7cb6d3e8f19f9fd371933363de`。不重新计算旧锁来假装旧运行源码未变。新v5有独立源锁，绑定驱动、检查器、Student和数据/mask路径。

新任务清单seed=2026102105，10个train任务/5个未使用家族，按公开成员/场景映射预固定，排除以前12家族，不读隐藏难度和答案。清单恰好没有当前检查器可分类的单值问句，不为触发修复而换任务。因此真实A/B用于新协议兼容性和采集，不支持“金额纠正成功率提升”结论。旧四条离线回归、真实AppWorld脚本探针、真实Qwen tokenizer的合成mask探针分别报告。旧失败原奖励不变；不得把校验后的模拟数字写回旧轨迹凑成功数据。

真实结果、命令和限制见 `reports/answer_guard_report.md`，状态与证据见 `docs/STATUS.md`。
