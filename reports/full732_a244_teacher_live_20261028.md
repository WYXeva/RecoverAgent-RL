# Full732 Stage 3A：真实中转站 Teacher 采集续报

2026-10-28；分支 `codex/p0-p2-recovery`，基线 `3b950e8f3d564dfcdde47938899d553dec6eae8b`。已 fetch/pull，基线无新增远程提交。只执行 Stage 3A。

**部分完成，尚未完成 A244。** 模型名称兼容和真实正常任务采集已通过：5 个任务实际启动，4 个官方成功且终态跨进程重评一致，3 条通过完整 SFT 格式检查，1 条超长 deferred。第 5 个任务遇到服务端输出超过请求上限，主链中断为 UNKNOWN；独立无模型诊断随后获得其自动保存状态的有效官方失败。后续239项未派发，不记作模型失败。

此前 `full732_a244_teacher_fresh_report.md/json` 与 `full732_provider_gate_revision.md` 是历史阶段报告，保持原样。本报告及[完整244项清单](full732_a244_teacher_live_20261028.json)记录新增结果。没有读取、检查或复用历史95条Teacher示范。

## 1. 已确认的运行协议

- 冻结 A244/244场景、顺序、seed20261028与原split不变；train30、dev19、test_normal56、test_challenge139。属于独立私有Full732研究，**不是官方benchmark/排行榜成绩**，B/C未运行。
- 用户确认请求模型 `deepseek-v4.1-flash` 以及真实响应别名 `deepseek-v4-1-flash`。新协议仅显式允许这两个名称；请求仍使用 `/v1/models` 列出的名称。真实response ID/model/可选fingerprint保留；服务未提供可核实的不可变上游权重revision，不冒称已经锁定该权重。
- 用户允许Teacher推理、Student保持non-thinking。原始reasoning_content保留在私有provider回执，并按真实历史位置回传Teacher；结构化SFT消息/目标不包含该字段。不得将此数据称为non-thinking Teacher生成。
- System Prompt继续为原字节execution_v6；dynamic_discovery、整批可见性/Schema/ID校验、Harness两次纠错和AnswerGuard均保留。没有追加提示或替模型完成业务操作。
- 每任务最多80次请求、16384生成token、600秒；单回复最多4096，工具数量不限。首任务4次接入诊断/484输出token扣入预算，新正常执行最多剩76次/15900 token。每次干净执行的时间与接入诊断时间分别保存。
- 用户不设独立金额上限；为复用有限数值会计，26/任务、6344/全轮是从已冻结最大请求/输入/输出推导的保守会计预留，并非实际费用或新的独立费用限制。

配置：`configs/full732_a244_teacher_relay_native_20261028.json` 及对应 `_lock.json`。此前严格接入、推理兼容两版配置/锁与派发前源码副本均封存；未重新签署旧运行锁。每版实际源码包位于私有 `runs/full732_a244_launch_20261028/frozen*_source/`，SHA见JSON。当前源码用于native版本；复现前两次接入需其封存源码。

## 2. 逐任务真实结果

| task_id | 原始split | 正常执行请求/工具 | 主链官方成功 | 恢复重评分 | SFT候选 |
| --- | --- | ---: | --- | --- | --- |
| 07b42fd_2 | train | 10 / 20 | 是 | PASS | 是 |
| 024c982_3 | test_normal | 6 / 10 | 是 | PASS | 是 |
| 042a9fc_1 | test_normal | 29 / 34 | 是 | PASS | deferred：完整上下文超过32768 |
| 07bb666_1 | test_challenge | 12 / 25 | 是 | PASS | 是 |
| 099174d_3 | test_challenge | 30 / 46 | UNKNOWN，中断 | 未进行合格终态恢复验收 | 否；另有停机后自动保存状态诊断评分2/7 |

以上split以JSON冻结清单为准。第一题另有2次接入诊断，共4请求/5工具；它们不是额外成功轨迹，也不从费用分母中删除。全部7次执行尝试覆盖5个唯一任务/场景。

- 主链有效官方评分4/5，成功4，未知1；在已有效评分者中为4/4，不能排除未知后将其当作总体100%成功率。
- 停止后的独立诊断使第5题保存状态的官方结果已知为失败，但其原采集status/reward仍为UNKNOWN/null；不将发生Provider/执行故障的样本冒充普通自然失败。
- A244覆盖5/244；4个已确认成功仅是部分成果，不能报告完整A244 TGC。剩余239个NOT_RUN全部保留在清单。
- 当前3条合格候选/3任务/3场景，28个Assistant监督样本、3540监督token，最长合格序列12583 token。按原split为train1、test_normal1、test_challenge1；只适用本次独立自定义研究，不混入原官方train数据集或历史95条包。

四个成功终态全部验证数据库行/SQL追踪、model_hashes及文件一致、官方完整结果一致、评分不改变DB、独立进程与可信源回执等10项。没有仅因行哈希一致而认定恢复完整，也没有修改官方评价器或用Teacher声明替代评分。

## 3. 中断根因与证据边界

实际首轮接入先遇到可选fingerprint缺失和thinking=disabled仍返回reasoning，再遇到服务响应模型名称采用连字符。两次原回执未改；用户明确确认推理模式与别名后，使用新协议重新从原首题干净状态运行。没有重发收费状态未知的请求。

本轮最后的中断**与模型名称无关**：

1. 099174d_3第30次请求前，已记15877输出，剩507，真实payload为`max_tokens=507`。
2. 原始response报告`completion_tokens=512`、`finish_reason=length`。实际回执合计16389，比任务生成上限多5。该回复未执行工具。
3. `p3a.verified_response_usage`在更新累计usage与正常终态捕获之前抛出`Provider exceeded configured billing bound`。外层通用异常处理将它标为`worker_environment`；这是分类不精确，根因应为Provider输出契约违反，不能称为AppWorld自身故障。
4. 不改变旧结果以隐藏异常；额外离线复验确认，即使单独检查其格式，该回复也因length截断不可成为合法Assistant工具回合。**一次512返回不足以证明服务有固定512最小值，本轮没有添加猜测性参数取整规则。**

原运行未产生项目`terminal/`及主链评价文件，但上游`AppWorld.execute`每次完整执行后会保存状态和日志。真实残留17个文件已复制保全。无模型审计将46个实际dispatch与官方环境日志逐条核对，调用代码/返回完全一致，历史合法，截断回复未进入合法历史。随后只用原官方评价模块独立读取该保存状态：完整7项、2项通过、有效失败；原17文件SHA全部不变。此诊断不回填旧trajectory/reward，不宣称获得新的合格恢复checkpoint或成功示范。

私有证据：

- `runs/full732_a244_launch_20261028/token_bound_diagnosis.json`
- `post_stop_history_check.json`、`post_stop_scoring/evaluation.json`、`post_stop_scoring_integrity.json`
- `stop_environment_copy/`、原第30次request/response及reservation；SHA均见公开JSON。

按冻结campaign故障规则停止后未继续下一任务、未重试本任务。剩余问题是Provider不严格遵守小额剩余生成上限，以及Runner在此异常路径未记录精确类别/完整项目终态。后续应独立版本化修复，真实用量需保留、不能放宽旧预算冒充通过；修复后才能继续其余239项，不能覆盖已完成记录或自动重收前四题。

## 4. 实际用量和费用

| 指标 | 包含全部接入诊断与中断请求 |
| --- | ---: |
| 真实模型请求 | 91 |
| 真实工具调用 | 140 |
| response.prompt_tokens合计 | 1,861,852 |
| 其中缓存输入 | 1,310,080 |
| response.completion_tokens合计 | 34,908 |
| Key账单requests | 91 |
| Key账单输入（未缓存）/缓存输入 | 551,772 / 1,310,080 |
| Key账单输出token | 35,068 |
| Key账单actual_cost | 0.21547368，接口unit字段为USD |
| 按response字段和公开费率估价 | 0.21528168，公开UI单位CNY |

价格来源为[公开model-plaza接口](https://znbcode.com/api/v1/model-plaza)，乘用户确认的0.3倍率后，输入/缓存输入/输出每百万0.3/0.006/1.2；原始价格和账单回执均保存。Key的`/v1/usage`与公开价格UI的币种标签不一致，**没有自行换汇，不将预算或估价当作实际人民币扣费**。

账单前为0请求，后为91；没有本轮以外新增模型请求。账单输出比response.completion_tokens合计多160。另有14份response的total_tokens不等于prompt_tokens+completion_tokens，差值合计826。保留所有原字段；这些差异的服务端语义未核实，不伪造精确解释。正常工具和Qwen监督token来自各自真实记录，与这些Teacher账单字段分别统计。

原UNKNOWN结果保留0.3151812未知费用预留；后验取得完整响应和聚合账单后，附录单独确认此次调用已计费，不反写原结果或悄悄释放预留来继续采集。按response字段该请求估价0.001960632；无法从聚合账单单独断言每任务精确实际扣费。没有余额不足或401/402/403导致本次停止。

## 5. 数据、软件与兼容性验收

私有候选包：`data/full732_a244_fresh_20261028_partial/`，仅3条已合格数据，明确`complete_a244=false`。`candidates.jsonl`保留真实结构化消息、每回合Schema及来源；控制侧原评价和原始provider推理留在私有源回执，不插入训练messages。manifest绑定源trajectory、evidence manifest、独立恢复评分、配置/锁和每文件SHA。

可信manifest SHA-256：

```text
e8c1ec60e1b645075878b2fc85fc5a19791bac7f30bb74a987ff7eb760178938
```

Qwen/Qwen3-4B revision `1cfa9a7208912126459214e8b04321603b3df60c`，原non-thinking tokenizer/template。真实候选逐一通过Assistant-only loss mask、4096目标/32768完整上下文、Student工具名/参数无损解析；长轨迹整条deferred，无静默截断。这是数据验收，不是训练PASS。

- 完整pytest **485 PASS**；Ruff check PASS，format check **137文件PASS**。
- 新回归覆盖显式User-Agent、旧严格身份与新可选fingerprint/response ID协议、允许别名及拒绝其他模型、真实reasoning按历史位置回传、伪造reasoning拒绝、SFT不含reasoning、接入请求/token扣入原80/16384预算。
- 首4成功任务是**真实Teacher+真实AppWorld+真实官方评分及独立恢复**；软件测试的合成模型回执明确不算Teacher轨迹。第5题停机诊断是无模型真实状态评价，不是新模型成功。
- 原execution_v6文件/正文、Full732划分、官方评价模块、旧数据/权重及历史锁没有修改。共享Teacher/过滤源码有版本化变更，旧运行须用原commit/冻结源码，不重签旧锁。

执行命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/ruff check --no-cache .
.venv/bin/ruff format --check .
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv-harness/bin/python \
  -m recoveragent.fresh_teacher collect \
  --config configs/full732_a244_teacher_relay_native_20261028.json \
  --output runs/full732_a244_live_native_20261028 \
  --credentials-file .env --max-batches 1
```

采集日志：`runs/full732_a244_launch_20261028/native_batch_01.log`；逐任务progress与完整原件在对应live目录。只有当前3条partial候选可供后续数据准备；A244采集未通过全量验收。没有启动GPU、Student推理、Base-SFT、Recovery-SFT、GRPO或C244评价。
