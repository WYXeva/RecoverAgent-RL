# RecoverAgent-RL：全任务池审计与三阶段统一协议 v1

本轮只执行 Phase 0–1及后续接口/配置/软件验证。**不执行Teacher采集、模型推理、远程GPU或任何训练。** 最终机器此前已由用户手动关机，本轮没有SSH。

## 已核验的任务池与用途限制

| 官方split | 实际Task | 实际Scenario | 每场景变体 |
| --- | ---: | ---: | ---: |
| train | 90 | 30 | 3 |
| dev | 57 | 19 | 3 |
| test_normal | 168 | 56 | 3 |
| test_challenge | 417 | 139 | 3 |
| 合计 | **732** | **244** | **3** |

[全池审计](full_task_pool_audit.json)来自本地官方 `load_task_ids` 和 `task_id_to_generator_id`，未传difficulty筛选。固定AppWorld源码`42b5bcf3cd334fee33f0c37c02070a9f5807add5`、data版本`0.2.0`、原split文件/基础DB/API文档SHA均记录。没有重复成员、跨split场景、缺失变体或必需文件缺失。任务目录还有官方`_base_`，它不是额外benchmark task，不计入732。

全部732任务均存在specs、DB差量和评价相关文件；**存在文件不等于已验证运行，也不代表可用于训练**。test有评价程序和必要评价数据，但没有完整公开生成/参考解法；不能以“没有完整ground truth”误说官方test不能评分。本轮只对test检查文件存在，不读取内容或执行逐题评价。

官方[Agent Development Restrictions](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/README.md#no_entry_sign-agent-development-restrictions)规定：train可用于教学/训练，dev用于调参或分析，test仅做最终汇总测试。这里遵守的是官方基准用途约定，不能把Apache许可概括成一律禁止训练；受保护内容另外受加密公开再分发条件约束。报告、代码和清单只公开ID、协议和统计/哈希，不公开任务指令、数据库、官方测试内容或Teacher原始轨迹。

因此，完整732的自定义244/244/244方案保留为 **BLOCKED候选**。其A/B各214题（19dev+195test）不允许按本协议进入参数训练；候选C中的195test也不能作为可逐题反复分析的开发验证。不能因本机能访问文件就消除此限制。

## 确定性划分与唯一主C

- 候选：[full pool manifest](../configs/unified_task_split_full_pool_candidate.json)，A/B/C各244题/244场景，仅元数据。
- 操作方案：[train manifest](../configs/unified_task_split.json)，A/B/C各30题/30场景，总90个不同task。Recovery-SFT与GRPO共用B，不另占30题。
- seed固定`20261028`，每场景按`SHA256(canonical([version,seed,scenario_id,task_id]))`排序后依次赋A/B/C。不使用模型成绩、Teacher成功、Student失败、难度或历史暴露；输入排序不影响结果。
- 操作方案canonical manifest SHA：`15760679a4cf338b281d3b893b980a539dea3a1c81e2fc676f4408926e2d24bf`。完整候选SHA：`086c0e2a1ac1143967d7ea8af06e0cc434aecdb39a51e1fe6c5fd42585ae2cab`。文件SHA另列交接manifest，区分规范化对象哈希和文件字节哈希。
- 只设一份主验证集：train方案的C30。全池C244不是第二份正在执行的验证集。运行异常不会触发换题、重排或复制；缺少三有效变体的场景应整体登记异常。
- [覆盖报告](task_type_coverage.md)披露应用和任务类型及无法可靠标注的分页/认证/跨应用依赖。不声称SGC或未见Scenario泛化。

## 历史暴露与新M1

原正式95轨迹/75任务/29场景，实际优化器训练82轨迹/64任务；另外13轨迹/11任务用于训练validation loss。按可信manifest `5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`重新核验全部包文件，未改旧数据、mask、奖励或权重。

[暴露审计](stage_data_leakage_audit.json)逐题绑定来源和SHA。C30中21题有旧参数训练曝光、1题有旧验证loss曝光、7题出现在已审计Student执行/开发报告中，30题均有Teacher尝试记录。类别重叠，不能相加。审计覆盖现存正式包和指定历史实际报告；缺少记录不等于从未接触。完整候选的暴露另见[全池审计](full_candidate_exposure_audit.json)，不参与划分排序。

旧A100/96GB checkpoint只能作历史基线，不能冒充新划分无参数泄漏的M1。新M1须从固定raw Qwen3-4B独立训练，M2从新M1继续，M3从新M2继续。即使新权重隔离C，已有开发暴露仍须披露。

[旧示范到新角色映射](unified_base_sft_source_inventory.json)：A侧32条/25任务，B侧37条/28任务，C侧26条/22任务。A侧仅是后续重新审核/合法过滤的候选，未生成新训练包；B正常示范不冒充恢复后缀；C数据不进入任何新参数训练。A余下5任务无已选成功轨迹，不以重复或失败数据填数。

## 新接口与训练顺序

新增`unified_split.py`处理元数据、权限、确定性分配、来源准入和暴露/词面覆盖；新增`shared_stage.py`处理checkpoint谱系、干净初态共享评估调度和三阶段配对汇总。复用原dataset的可信manifest、StudentSession、EvaluationAdapter和官方评价分类；不改AppWorld或旧执行源码/锁。

`validate_training_sources`为新流程的数据准入接口：A只给Base-SFT，B给Recovery-SFT/GRPO，C与非train拒绝；每个来源绑定划分SHA。它只是成员/来源门槛，不替代原轨迹过滤、Assistant-only mask、真实恢复或在线logprob检查。历史工具仍可复现旧实验，不宣称本接口追溯改变所有旧Runner。

`configs/unified_training_stages.json`记录三个训练契约：

1. Phase2：A干净初态正常Teacher严格成功、完整合法、来源齐全；复用BF16+LoRA/动态工具发现/Assistant-only。先短长烟测，再真实更新、保存重载，发布独立M1。C不得用作训练期间validation loss或选checkpoint；如需要优化器内验证，只能预先在A内另划。
2. Phase3：同C从干净初态评价M1。
3. Phase4：B由新M1自主执行，合法自然失败及post-error/late-failure优先；Teacher恢复原任务严格成功后，仅新Assistant恢复后缀监督，旧前缀/tool/padding全mask；M2继承M1。pre-error只作预防消融。
4. Phase5：同C评价M2。
5. Phase6：B在线GRPO，M3继承M2，group_size4、共同数据库/前缀/Schema/预算、独立进程/采样、真实当前策略tokens/logprobs/mask；未知奖励隔离，零方差跳过优势更新并报告。此轮没有实现完整Teacher Recovery、Recovery-SFT构建器或GRPO trainer。
6. Phase7：同C评价M3。每阶段数据、评分可用性、预算和用户授权均为前置条件。

## 共享端到端评估

入口`scripts/evaluate_shared_stage.py`：prepare仅校验并生成计划；run才显式启动已有Student worker；compare从已有真实回执重算。release须绑定新split、训练源、验收文件哈希、policy身份和正确父checkpoint；旧adapter不得作M1。配置还绑定真实评分预检；存在未解决边界时prepare/run拒绝进入后续阶段，compare仍可报告未运行矩阵。该软件检查不替代未来训练的实际参数更新/梯度/保存重载验收。

[冻结协议](../configs/shared_stage_validation.json)：固定Qwen3-4B revision、non-thinking模板、execution_v5、dynamic_discovery、现有Harness；每任务40请求/16384生成/600秒、单回复4096、窗口32768、seed42、temperature0.8/top_p0.95、工具次数不设配额。Memory三个阶段统一关闭，保持单一权重训练阶段变量；任何未来更改必须另建协议，不能混入本对比。

每题独立子进程/原始任务初态，仅提供原任务及公开工具；不接收恢复检查点，也不跨stage复用messages。对所有C逐项记录初态DB/首次token IDs、消息和Schema哈希，完整原请求/工具结果/官方tracker保留私有。模型本身的任务失败只结束该题；服务故障保留未知、停止并列未执行，绝不自动重发。

完整TGC仅在该stage全部C有可靠结果时计算；未知、基础设施、未执行分别列出，不能当0或以有效子集冒充总分母。跨阶段提升仅在三组完整且初态/首次输入一致时给出。逐task三列矩阵包含未执行单元，另按公开词面类别及场景汇总；Schema错误率分母为模型请求，业务错误与协议拒绝分开，Token/工具/时间/预算耗尽单列。没有估计置信区间；以后如估计，按scenario抽样。

## 本轮验证与剩余门槛

真实探针完成**90/90**初态加载、公开只读调用及数据库不变性检查，合计90调用、worker耗时2256.25秒（约37.60分钟）。**84**题得到完整可用的初态评价；`76f2c72_1/2/3`、`d0b1f43_1/2/3`共**6**题在无完成答案初态出现ValueError，保守分类unknown。A/B/C各2题涉及该边界。没有任何模型请求，不能将84/90当作模型成功率；也不证明全部业务任务可解。386项初轮检查后补充输入哈希门槛回归，最终**389 pytest、Ruff check与124文件format通过**。

元数据和分配审计完成、Phase1清单冻结；合法train方案环境可运行，但**评分门槛PARTIAL**，完整三阶段实验尚不能放行。新协议绑定此预检SHA，prepare/run在门槛未解决时明确拒绝，compare仍生成诚实的未运行矩阵。没有按异常删场景或另建第二主C。

真实初态、只读工具、评价完整性结果见 `unified_task_runtime_probe.json`；原始官方tracker只保留私有。本轮不使用这些初态评价结果作为模型成绩，也不把它们写入训练样本或用来挑选更容易的C。

当前发现数值类任务在尚无完成答案的初态可能触发ValueError，被现有分类器保守记为unknown；没有改成0，没有改官方验证器或删除任务。文件完整、环境可运行与任意终态可可靠评分是不同门槛。新实验应在训练授权前明确剩余评价边界，不可由此宣称整个三阶段闭环已通过。

软件用合成fixture验证划分、C隔离、历史adapter拒绝、谱系、计分完整性和配对；真实环境探针使用本地AppWorld但不调用模型。测试/完整性证据见 `unified_stage_checks.txt`、`unified_stage_integrity.json`。新M1/M2/M3均未生成，三阶段效果尚无结果。

## 重现与后续执行

```bash
# 元数据、历史包和两套候选重新生成到新目录，不覆盖证据
PYTHONPATH=src .venv-harness/bin/python scripts/audit_unified_pool.py prepare --output <new_audit_dir>
# 本轮实际第一轮初态探针；44题后因两个未知评价停止，保留原件
PYTHONPATH=src .venv-harness/bin/python scripts/audit_unified_pool.py probe --plan runs/unified_split_20261027/audit_001/unified_task_split.json --output runs/unified_split_20261027/probes_001
# 区分真正服务故障与未知评价后，只继续未执行的46题，不重跑前44题
PYTHONPATH=src .venv-harness/bin/python scripts/audit_unified_pool.py probe --plan configs/unified_task_split.json --previous runs/unified_split_20261027/probes_001 --output runs/unified_split_20261027/probes_002
PYTHONPATH=src:. .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
# 仅输出全NOT_RUN三阶段矩阵，不执行模型
PYTHONPATH=src:. .venv/bin/python scripts/evaluate_shared_stage.py compare --output <new_comparison.json>
# 未来训练和授权全部通过后才可prepare/run；本轮未执行
PYTHONPATH=src:. .venv-harness/bin/python scripts/evaluate_shared_stage.py prepare --stage M1 --release <new_accepted_release.json> --release-sha256 <trusted_sha> --output <new_run_plan.json>
```

真实采集和训练仍缺新A数据包验收/新checkpoint、现有GPU及授权、B自然失败和Teacher恢复接口/预算、Recovery-SFT与GRPO实现和验收。完整732自定义方案另外缺官方允许的用途范围；不能以修改本项目配置自行解除。

交接文件字节SHA见[发布清单](unified_stage_release.json)；真实门槛和拒绝验证见[readiness](unified_stage_readiness.json)。[三阶段矩阵](shared_stage_validation_not_run.json)当前全部NOT_RUN、TGC及阶段提升均null，不能当成已完成模型实验。完整性审计核验489旧文件、旧数据包与两份adapter；实际新划分任务输入哈希也已重新核对。
