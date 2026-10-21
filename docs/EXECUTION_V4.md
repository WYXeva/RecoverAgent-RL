# execution_v4 与新 A/B 采集协议

用户在失败诊断后授权“按你的方案修改，重新收集教师数据使用A/B”。本版本保持 `configs/prompt_protocol_lock.json` 的五个源文件和旧结果不变。新提示在 `execution_v4.py`，新认证依赖选择在 `validated_selection.py`；采集前代码锁为 `configs/execution_v4_protocol_lock.json`。不修改官方 AppWorld、P0–P2 或 Student 模型。

## Actor 共享规则

保留理解→证据→执行→纠错→验证→完成及独立调用批量、动态依赖等待真实返回的规则。新规则明确：纯动作任务省略 complete_task.answer；要求答案（包括祈使句）才提供简洁答案；精确正文不混入问候/解释；计数先界定记录和稳定ID、展开完整嵌套成员、分页去重、核对合计和领先候选；认证失败不能猜token或替代为其他App凭据。

两个完成调用例子是人工通用协议示例，不是成功轨迹或任务答案。`{status:success}` 和带数值answer的版本已对本轮10任务的真实公开schema校验。控制器不会删answer、改正文或暗算统计答案。提示不能保证模型遵从或算术正确；真实结果与代码实现分开报告。

## A：api_predictor_validated_v1

仍用固定官方 Predictor 组件及 dependency_examples_v2 两个公开工具选择示例，初始选择只读取公开任务和API目录。工具模式字段保持 `api_predictor`，新增 `selection_protocol` 标识项目变体，不能称为官方原版。步骤：

1. 提取公开参数/成功返回schema中的必填字段及access_token/password产出。对需要access_token的App，检查是否选入不依赖已有token的同App产出接口；跨App token和依赖旧token的refresh不构成初始路径。
2. 如果所有选中token路径都要求password，且目录中存在无必填输入的同App/supervisor密码来源，检查是否选入该来源。不假定所有App需登录，也不假定只有login能产出token。没有可识别产出路径的App保持未验证，不凭猜测造出依赖错误。这是有限认证检查，未证明任意参数、业务语义、跨工具组合均可达。
3. 缺依赖则将公开结构化错误发送给Predictor，最多一次纠错，要求重选完整列表。纠错可建议公开产出API名，但最终工具仍由模型选择，控制器不补入。若仍缺依赖或预算不足，记录invalid_prediction并停止Actor，保留分母/成本。
4. 最终列表一次性固定后才执行Actor。两次选择请求/响应分别存 `000` / `000-correction`，回执验证、费用汇总、LLM视图和数据包均保留它们；不把Predictor对话作为Actor上下文或SFT目标。

官方依据：AppWorld固定提交 `42b5bcf3cd334fee33f0c37c02070a9f5807add5`，公开api_docs包含parameters和response_schemas；例如simple_note.search_notes的access_token必填，login返回access_token，supervisor.show_account_passwords公开返回密码。本轮只是读取这些文档，不读取hidden required_apis或验证标签。

B仍是 `dynamic_discovery`：请求show_api_doc后，工具在下一模型回合开放。它与A共享execution_v4，使用独立环境与相等端到端预算。新A的公开检查是额外方法组件；当前实验比较整个新A与B，不能单独归因于Prompt或选择器。

## 固定采样与预算

清单 `configs/train_v4_ab_20261021.json`：seed=2026102104，10个任务、5个此前未采样的家族，每族2个变体；从公开train ID→generator_id均衡采样，排除此前7个诊断家族。不使用隐藏难度/答案/成功结果选任务。清单canonical SHA `0f0383f2634331b26334c96d4e0f512b9e00c3c6523d291600ad3b466acd77c0`。

每任务A/B各一次，按任务交替B→A和A→B，共20个独立进程尝试。每次40请求（包含全部Predictor请求）、16384累计输出、600秒、操作性USD1；单回复4096 token，工具调用数量不限。总体800请求/327680输出/12000秒/USD20，无自动付费重试。DeepSeek费用授权沿用用户“费用不限”，公开配置仍关闭付费，实际本地授权配置忽略且不含密钥。Actor业务动作都是真实模型决定/环境执行，最终由官方严格验证器评价。

## 数据与远程交接

全部尝试、失败、模型和工具成本进入报告；官方成功轨迹还须通过合法完整多轮、当时可见schema、原始回执、来源及循环筛选。使用固定Qwen3-4B tokenizer验证逐回合Assistant-only mask、动态工具和Student批量parser；上下文/工具返回/历史/填充不监督。超过32768完整序列或4096 Qwen目标长度的轨迹整条暂缓，不裁剪成“合格”示范。

新数据包与tokenizer结果保持独立，旧包不覆盖。远端遵循 `P3A_HANDOFF.md`，经授权通道传输完整包并用可信manifest SHA重新verify；本机verify和拷贝演练不冒称远程迁移。原始任务、隐藏评价、快照、LLM视图及凭据不公开。具体结果、命令、哈希及P3-B剩余项见 `reports/v4_ab_report.md`。
