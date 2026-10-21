# 完成参数、精确输出、聚合与工具依赖：候选修改方案

状态：**分析与可审查方案，尚未启用或验证效果**。当前 execution_v3 / dependency_examples_v2 及 A/B 五文件锁不修改。本轮A复试沿用冻结协议，不能把下面的候选条款、失败轨迹或隐藏评价传给模型。该方案只来自公开API契约与通用任务执行要求，不包含任务ID、隐藏答案或特定测试字符串。

## 1. 动作完成与回答问题必须分开

固定AppWorld公开 `supervisor.complete_task` 的answer参数说明：只在任务要求答案时提供；动作型任务保留默认None。schema中answer非必填，默认null，但显式类型只允许number/integer/string；**调用时省略answer键**最直接，不应改为空串或显式传null。官方源码 `src/appworld/apps/supervisor/apis.py` 第49行起，固定AppWorld提交 `42b5bcf3cd334fee33f0c37c02070a9f5807add5`。

现有execution_v3已提醒“a concise answer for a question, no invented answer for an action-only task”。不足之处是“no invented answer”不等于“必须省略answer键”：模型可能认为真实完成摘要不算编造，仍填入。公开schema一直可见，冻结提示也有提醒，因此不能认定是工具缺失或完全没有给规则。可确认的是行为违反了公开参数契约；上述解释是可能的提示歧义，模型内部原因不可直接观察。

候选新版本可把完成段落改成以下明确规则（不能改写旧版本标签）：

```text
Before completing, identify the deliverable requested by the public instruction:
- STATE CHANGE ONLY (send, create, update, play, etc., with no requested answer):
  call supervisor.complete_task with {"status":"success"}, or {} if allowed.
  OMIT the answer key entirely. Do not put a progress report, confirmation,
  operation summary, empty string, or null into answer. A brief confirmation may
  appear in ordinary Assistant text, but is not the tool's answer parameter.
- ANSWER REQUESTED (including imperative forms such as "Name ..." or "Count ..."):
  provide only the requested concise value in answer, after checking evidence.
  Do not decide task type solely from the presence of a question mark.
- MIXED: perform the requested change and provide an answer only if the user
  explicitly also requested one. Never infer a requirement to summarize actions.
Use the currently visible schema and check this argument contract immediately
before completion. This is a public API rule, not a query to hidden validation.
```

人工通用正例：用户“开始播放我指定的播放列表”，真实播放与公开自查结束后 `complete_task({"status":"success"})`；用户“给出列表中条目数量”，先从公开返回计算，若实际数量为N，则 `complete_task({"answer":N,"status":"success"})`。N必须来自真实观察；这些是协议例子，不是Teacher轨迹或训练样本。

不采用Runner静默删answer：控制器无法仅凭是否含问号可靠判断任务类型，也不能根据官方隐藏期望改写动作。否则会把控制器纠错伪装成Teacher能力，并损坏模型生成/执行/示范的一致性。可以新增仅依据公开信息的审计指标，但不能据它篡改官方奖励或把原失败补成成功。

## 2. 需要精确格式时，正文与叙述分开

动作的“已发送”回执只证明执行成功，不证明正文符合要求。候选规则应要求在写入前核对目标、集合和序列化后的准确正文，避免额外前缀。

```text
When the user specifies an exact output format, the content written into the
target app must contain only that requested payload. For a comma-separated list,
serialize the verified items as item1, item2, ... without an introduction,
explanation, greeting, or completion summary unless requested. Keep narration
outside the tool argument that carries the user's payload. Verify the stored
payload, not only the API's acknowledgement.
```

本轮有一个控制侧负例：额外英文开场白被官方“禁止包含其他标题”的子串检查命中。该检查不区分单词边界，对自然语言前缀敏感，这是验证器的局限；任务本身要求逗号分隔标题，因此移除多余叙述也符合公开要求。不要针对某个隐藏短词写禁词表，也不修改官方验证器。

## 3. 聚合按完整嵌套字段统计并交叉检查

有一条失败已读完28条推荐及空末页，错误发生在聚合，不是漏翻页。每首歌可包含多个artist对象；所有作者都需要计入。仅统计第一位或凭印象比较，会把合作作者漏掉。不能从一次输出证明模型具体采用了哪种错误算法，但公开重算可定位错误阶段。

```text
For counts, maxima, and rankings, first define the counted record and grouping
key from the public schema. Maintain a page-by-page tally using stable IDs.
Expand every relevant entry of nested lists; do not silently keep only the first.
Deduplicate repeated records across pages without discarding legitimate group
members. Reconcile the tally total against the observed record/group pairs, then
compare the largest counts and any tie rule. Recheck the leading candidates
against the original observations before submitting the answer.
```

可在控制侧用确定性代码复核公开观察，定位误差；本轮已实现这样的离线计数审计，但不把重算答案送回Actor。若后续给Actor新增通用计算工具，必须显式注册、记录输入输出和调用成本，并作为新的工具协议评测；不能让Runner暗中算好答案。纯提示方案不能保证算术永不出错，须在新train/dev任务上检验。

## 4. A增加公开依赖校验；Actor遇到真正缺工具时停止猜测

本轮付款任务A只选择14个API，包含simple_note.search_notes/show_note，漏选simple_note.login；20种API上限未触达，不是容量不足。原B该任务成功完成了付款请求，仅错在最后answer；A则没能读取笔记，23次公开工具执行报错，反复尝试跨应用/猜测凭据后产生4096-token截断。A的工具固定约束使执行中无法补回遗漏，但原提示本来要求报告缺失并停止猜测；这也是Actor未遵从已有规则的证据。

Predictor只看到公开功能简介，返回API名字列表，现有检查能保证名字合法、数量不超限，却不保证组合能完成认证依赖。两个正例与“补齐依赖”提示不能构成确定性保证。候选改法：

1. 保留冻结A作为对照，新建明确命名的 `api_predictor_validated_v1`，不冒称原始官方协议。
2. 选择后、一次性开放前，读取所选函数的公开schema，识别必填access_token等输入。按所选App的公开认证接口检查是否存在取得这些输入的路径。不是每个App都必须登录，也不能假定只有login一种合法路径。
3. 若缺依赖，向**Predictor**返回“所选某函数需要某公开输入，目前未选择获取路径”的结构化校验错误；最多一次重新选工具，计入同一请求/token/费用预算。仍由模型选择最终列表，不由Runner静默补API或执行登录。最终列表一次性固定后才启动Actor。
4. 校验仍未通过则记录无效预测并终止，不让Actor在不可达工具组合中长时间试错。此门槛只能验证已定义的公开依赖，不能证明整个任务一定可解。

Actor候选条款：

```text
If a required input can only be obtained through a tool that is unavailable in
the fixed tool list, do not substitute another app's token, passwords, user IDs,
or guessed/constructed credentials. Report the missing public dependency and
stop with failure according to the available completion schema. Repeating an
authentication request requires new evidence or a newly obtained legitimate
credential; changing an invented token is not progress.
```

还可单独评估公开认证错误的停机规则：同一依赖缺失且没有新证据时提前停止，减少重复工具/输出成本。该规则不能根据隐藏评价触发，必须另记协议版本并测试误停风险。当前只保留现有预算截停，不在冻结复试中加入新终止器。

## 后续验收方式

若启用候选版本，保留execution_v3与本次A结果，新建版本和运行ID。先检查所有示例符合公开schema，再用预先固定、按场景分组的任务和相等预算评测；同时记录官方成功率、动作任务answer误填率、精确正文偏差和可重算聚合错误。不只在这6个已分析失败上复试，也不把一次改善当作泛化收益。SFT数据只保留真实执行且官方通过的原始轨迹，不直接修改失败轨迹末尾使其“合格”。
