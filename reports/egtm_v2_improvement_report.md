# EGTM v2 完善与真实 Qwen 小样本报告

日期：2026-10-27。分支 `codex/p0-p2-recovery`，基线 `a2dbcb298a6fa4c74c2d858385e6c3b70ee4bd90`。发布克隆 `/tmp/recoveragent-diagnosis-20261022` 已执行 `git pull --ff-only`，当时无新提交；主工作区 `.git` 仍为空且只读。保留此前全部工作及证据。

**工程测试和真实 AppWorld 脚本探针 PASS；真实 Qwen 小样本 PARTIAL / 未通过扩样门槛；正式 A/B NOT_RUN。** 已真实加载原 A100 Qwen3-4B Base-SFT，执行第一个固定任务的 A/B 两次：官方均失败，B 的唯一 L1 更新被严格校验拒绝。按预先记录的停止规则不再运行后两个任务，没有重采样。没有训练、Teacher Recovery、GRPO、租用或开机新实例。自动关机因平台未配置密钥失败，用户随后明确确认“已关机”；不是自动关机成功，也未独立核验账单。

## 1. 确认问题与必要修改

| 位置 | 起点问题 | 本轮修改 |
| --- | --- | --- |
| `context_compiler.py:compile_context` / `_compile_visible` | 阈值以下直接返回原历史，已生成L1不进入业务决策输入 | 新v2.1路径始终注入有效模型L1；展示与压缩独立；真实tokenizer验证窗口 |
| `memory_store.py:EvidenceIndex.retrieve` / `project` | 相同已在输入的证据仍可能重复装入；不能按路径读取字段 | 当前source去重；同原文旧source只留引用；精确字段/path投影附原值、JSON path、来源及SHA |
| `task_memory.py:update_messages` / `update_reason`，`student.py:build_request` | 只看业务输入可能晚于更大的内部更新请求；公开事件最小间隔可能拖延压力触发 | 实测增量更新输入大小，必要时提前触发；同边界失败不重试，事件节流继续保留 |
| `scripts/summarize_egtm.py:paired_report` | 两组汇总不足以证明初态、首次IDs和完整分母一致 | 逐任务精确配对、完整清单、有效评价与真实L1载荷检查，缺项INCOMPLETE |
| `scripts/probe_egtm.py:TokenizerPipe.rpc` | 排序JSON改变schema嵌套字段顺序，管道内自检掩盖与直接后端的差异 | RPC只保留原键序；用真实tokenizer直接重渲染全部回执再次验收 |

主要改动限原三个Memory模块、Student接口、版本选择、探针与汇总；没有新增生产框架、Agent工具、向量数据库、业务规划器或多套重试预算。`state.py`、官方AppWorld/评价器、Harness/AnswerGuard、执行prompt、模型backend及SFT实现均未更改。

新版本 `egtm-model-working-memory-v2.1`，新运行锁 `egtm_visible_memory_20261027`，SHA：

`ddc3bb9c4bcb3a66bb422ccba583993eb595e74cb6300e89cacd6c0c55b25c1f`

旧v1/v2路径和锁保留。变更没有回写旧hash；原版本精确复现仍需相应旧commit。

## 2. L1/L2/L3与执行契约

L1仍由同一Student的内部请求产生，Harness仅校验和展示。已接受L1以`internal_model_memory_data`呈现，明确标为数据、不是原始用户的新指令。短历史完整保留并附L1；长历史只压缩L1覆盖的较早完整批次，保留最近两批、原始任务/system/user约束、当前完整Schema和未覆盖交互。没有成功L1时不展示规则事实视图冒充模型总结。无法容纳L1、必要证据和未覆盖历史时明确失败，不静默隐藏或截断。

L2优先`need_evidence`，索引受task_id及历史截止位置限制。当前模型输入已有原始source时不再检索全文；相同原文但不同source保留不同来源和时间，仅引用已在上下文的原文。精确path只复制真实JSON值；field查询返回实际同名路径，**entity_id只筛证据行，不推断其在数组中的对象归属**，对象定位需给path。投影带source ID、原始观察/内容SHA、路径、值及值SHA；无法解析或重复JSON键时保留原文引用/必要原文，不生成null替代。stale来源继续标记，不改旧回执。

L3原始请求、响应、token IDs、业务历史、工具执行和终态全部保留。Memory更新失败保持旧L1及未覆盖历史；未增加重试。内部请求使用同policy/non-thinking模板，计入原40请求、16384累计生成、600秒；业务4096、Memory2048、完整窗口32768。协议纠错和数值纠错优先级不变。Memory关闭时原请求、工具行为和预算逻辑保持一致。

v4仍校验真实更新回执、历史/工具来源、可见性、hash和时间边界，不重新生成L1；旧1/2/3/4快照仍可读。部分执行、未知副作用和不完整批次不变成合法恢复样本。

## 3. 阶段1：本地软件与真实环境

[完整检查](egtm_v2_improvement_checks.txt)：**341 pytest PASS**，比基线319增加22项；Ruff check PASS，118文件format PASS。覆盖持续L1可见、同回合真实输入载荷、精确投影/原值/哈希、重复证据与未来隔离、opaque JSON与stale、提前触发/节流、失败原子保留、原任务与工具配对、旧协议和v4回执恢复、完整A/B分母、未知/infra分类、初态/IDs不一致拒绝、默认关闭、tokenizer键序。

最终真实AppWorld脚本探针：`runs/egtm_v2_improvement_final_20261027/`，train `e3d6c94_3`。使用真实公开API认证、创建/修改笔记、长目录返回、业务错误以及部分写入丢失回执。**动作和L1回执由脚本提供，不是模型策略成绩，不评价原任务成功率。** [探针结果](egtm_v2_improvement_world_probe.json)、[28请求直接分词回放](egtm_v2_improvement_replay.json)。

| 指标 | 最终实测 |
| --- | ---: |
| 真实模型调用 | 0 |
| 合成生成回合 / 真实工具意图 | 28 / 29 |
| L1接受 / 故意非法JSON拒绝 | 3 / 1 |
| 后续实际L1可见请求 / 字段投影请求 | 18 / 10 |
| 压缩业务请求 | 10 |
| 相同行为原始业务输入 | 414,589 token |
| 实际业务输入 | 233,751 token |
| Memory输入 / 输出 | 50,079 / 481 token |
| 总输入 / 输出 | 283,830 / 1,914 token |
| 业务输入减少 / 扣除Memory输入后净减少 | 180,838 / 130,759 token（31.54%） |
| 最长原始输入 → 实际输入 | 37,570 → 12,710 token |
| 检查点 / 独立进程恢复 | 3 / 3通过 |
| 完整探针耗时 | 149.60秒 |

A/B初始DB、首次输入IDs、messages及tools均相同。三次恢复核对DB、合法历史、tools、runtime、L1/索引及回执；部分真实写入保留已执行副作用和未知返回，拒绝合法快照。脚本的长目录具有刻意重复，不代表真实任务净收益。

开发探针`runs/egtm_v2_improvement_20261027/`原样保留：直接重渲染发现24/28请求与排序RPC不符，旧传输重渲染则28/28符合。证据见该目录`token_order_diagnosis.json`和`source_archive/`。修复后另建最终运行，未覆盖开发证据。该缺陷影响脚本token计数可信度，不能把旧脚本管道内自检当作直接backend验证；真实模型backend原本直接渲染，不使用该RPC。两次探针合计58个真实工具意图、0模型推理。公开旧报告不改写。

## 4. 阶段2：授权现有A100真实小样本

用户明确授权“现有运行中机器，沿用ssh.txt”“授权小样本，完成后关机”。实际设备A100-SXM4-80GB，驱动580.126.09；Python3.12.3，torch2.10.0+cu128、transformers4.57.6、PEFT0.18.0。复用原AppWorld/SFT两个环境，没有重新安装或训练。SSH曾在认证前断开；只重连控制操作，没有重发已计费或已执行的模型请求。

核验固定revision `1cfa9a7208912126459214e8b04321603b3df60c`，non-thinking模板SHA `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`，原A100 adapter tensor SHA `34b87e77f8f49e3fef772d9c7f1074d89f07dd32bda38d81d6675120bb5e0ccd`。7个checkpoint发布文件、源码锁、tokenizer及AppWorld兼容检查通过；真实BF16模型加载并重算adapter参数hash通过。[远程核验与归档](egtm_v2_improvement_remote.json)。

固定任务按原计划前三个家族顺序选择，运行前记录为A后B逐任务配对；完成当前对后若出现协议或Memory校验错误停止后续任务，infra立即停止，不替换任务、不现场调prompt重试。两组每次同40/16384/600预算；执行prompt execution_v5、dynamic_discovery、模型、采样配置完全相同，仅memory.enabled不同。

| 任务 | A：Memory关闭 | B：Memory开启 |
| --- | --- | --- |
| 287e338_3 | 官方有效评价，失败 | 官方有效评价，失败 |
| 3c13f5a_1 | NOT_RUN，停止扩样 | NOT_RUN，停止扩样 |
| 229360a_2 | NOT_RUN，停止扩样 | NOT_RUN，停止扩样 |

[逐任务配对报告](egtm_v2_improvement_pilot.json)：两组各尝试1/计划3，官方成功各0/1；总执行2/计划6，有效评价2，infra0，未知评价0。不是0/3成功率，也不是完整A/B PASS。已执行任务初始DB、首次IDs/messages/schema逐一相同；前5个业务请求的输入、输出IDs和seed也完全相同。

| 已执行任务成本 | A | B |
| --- | ---: | ---: |
| 全部模型请求 | 10 | 8（7业务+1Memory） |
| 输入token | 79,757 | 48,212 |
| 输出token | 641 | 915 |
| 实际工具调用/返回 | 12/12 | 7/7 |
| 协议拒绝 | 1 | 1 |
| 业务API报错/未知返回 | 0/0 | 0/0 |
| Memory输入/输出 | 0/0 | 7,843/461 |
| Memory接受率 | 不适用 | 0/1 |
| 后续有效L1可见 / 压缩次数 | 0/0 | 0/0 |
| 固定B行为扣除更新后的净输入节省 | 不适用 | **−7,843** |
| 完整worker耗时 | 104.70秒 | 117.70秒 |

合计18模型请求、127,969输入、1,556输出、19实际工具调用，生成耗时118.18秒；其中Memory32.06秒。两worker总222.40秒，控制器225.13秒；实际峰值allocated 11,684,067,840 bytes（10.88 GiB）。GPU实例账单/单价未获取，**实际货币成本未知**，不把已有机器当免费，也不从token捏造费用。

B的总输入更少是因为它更早提交答案，轨迹不同；**不存在Memory压缩收益**。现有seed为基础seed+总模型请求计数，Memory内部请求会消耗一个seed位置；更新被拒绝后后续业务抽样seed已变化。因此无法把后续行为差异归因于L1内容，也不能将较少调用解释为效率提升。

## 5. 真实模型失败诊断

[18请求回放、真实L1及来源审计](egtm_v2_improvement_model_audit.json)：所有请求在本地固定tokenizer上直接重渲染通过，输入/输出回执一致；B终态Memory可由真实回执重放核验，L1仍为None，没有接受错误内容。

- 两组首轮均对`api_docs__show_api_descriptions`遗漏必填`app_name`，Harness返回明确`schema_error.missing_fields=[app_name]`，未执行非法批次；下一回复补齐并继续。这是可纠正的真实模型协议错误，不是工具服务故障。
- B第006请求为真实Student Memory Update，EOS完整、JSON可解析，仅461输出token，**不是token截断**。但progress引用5个ID超出4；3条important_facts各引用2项超出精确单来源约束；把`spotify__login`和`api_docs__show_api_doc`函数名当成evidence_id；两个事实路径不存在（含数组数字字符串及schema/实际响应混淆）。唯一可匹配的字段来自真实登录返回，但整份更新必须拒绝，不能只取正确部分。
- 语义上保留了原目标，但constraints过于简略，pending为空，没有记录仍需完成的推荐统计；need_evidence请求的是调用参数路径，索引保存的是工具返回，无法提供该值。completed为空，未声明官方成功；progress保持unverified。这里只审核拒绝回执，不将其当有效L1或正向示范。
- 两组均只获取推荐列表第0页、page_limit20，返回恰为20条，未请求后续页或确认覆盖完整。A另查三个艺术家信息，B直接提交答案。**可见证据存在分页/统计覆盖缺口**；官方失败是独立控制侧证据。没有读取隐藏正确答案来修正行为，也不将这个观察冒称唯一失败原因。

目前真实模型未满足结构化Memory契约，不能确认L1实际辅助决策、约束准确率、检索有用性或压缩提升。后续应先在新的独立版本中研究更短明确的引用/path指导或schema约束生成，对引用/语义分别验收；不放宽证据校验、不无限重试，不用Harness补写业务计划。此次没有根据该任务结果继续调参或重跑。正式A/B仍需小样本通过及另行GPU授权。

## 6. 历史兼容、交付和关机

[历史完整性](egtm_v2_improvement_integrity.json)：95轨迹、75任务、29家族，18来源包/6616文件全部重验；manifest仍为`5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`。两个历史checkpoint各7发布文件未变；337个旧报告/配置与基线逐字节相同，真实旧快照格式1/2/3/4继续可读。没有改Teacher数据、奖励、训练mask或旧实验成绩。

私有远程运行在原持久盘下独立`egtm_v2_improvement_20261027/`，原worker_source/training_source/data/checkpoint未覆盖。仅回传808,403 bytes本轮证据包，没有重新回传权重。归档SHA `b950d35c9fb54136a50ab4682e5f35b7eeee91e2fc7c23b27e98932b6b4215ce`，158文件全部核验。落地`runs/egtm_v2_improvement_remote_20261027/`；原请求/工具结果/DB/官方tracker不上传公开GitHub，只公开汇总、代码、配置和摘要。

停止本轮backend后显存0 MiB；第一次关机请求距实验结束451.58秒，期间有SSH认证前断连、归档/回传/核验和平台方法确认，应计入实例使用时间，不算模型生成时间。[关机证据](egtm_v2_improvement_shutdown.json)：平台`/usr/local/sbin/shutdown`返回255“未设置关机密钥”，及时通知用户；用户确认手动关机，之后无新SSH操作。依据[平台说明](https://www.gpufree.cn/docs/guide/instance/)，关机与释放删除不同；未释放实例或删除数据，也没有独立验证账单停止。

## 7. 实际命令与最终验收

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
PYTHONPATH=src HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 \
  .venv-harness/bin/python scripts/probe_egtm_v2.py \
  --config configs/egtm_v2_improvement_eval_a.json --initial-only \
  --output runs/egtm_v2_improvement_final_20261027/off_initial \
  --namespace egtm_improvement_final_20261027_off
PYTHONPATH=src HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 \
  .venv-harness/bin/python scripts/probe_egtm_v2.py \
  --config configs/egtm_v2_improvement_p3c.json \
  --output runs/egtm_v2_improvement_final_20261027/on_script \
  --namespace egtm_improvement_final_20261027_on
# 远程在隔离源码根，HF_HOME指原缓存，PYTHONPATH=src：
# .venv-sft环境：student_backend --adapter <原formal> --port8766
# .venv-appworld环境：以下命令由保存的pilot_controller.py依A/B顺序执行
python -m recoveragent.student_evaluation worker \
  --config configs/egtm_v2_improvement_pilot_a.json --case-index 0 \
  --arm base_sft --output <新A目录>/base_sft_base_sft_unseen_task_287e338_3
python -m recoveragent.student_evaluation worker \
  --config configs/egtm_v2_improvement_pilot_b.json --case-index 0 \
  --arm base_sft --output <新B目录>/base_sft_base_sft_unseen_task_287e338_3
# 本地根据回执重新计算，真实执行：
PYTHONPATH=src .venv/bin/python scripts/summarize_egtm.py \
  --version v2_improvement --stage pilot \
  --a runs/egtm_v2_improvement_remote_20261027/pilot/A \
  --b runs/egtm_v2_improvement_remote_20261027/pilot/B \
  --output reports/egtm_v2_improvement_pilot.json
```

本地独立诊断命令`/tmp/audit_egtm_improvement_integrity.py`、`/tmp/audit_egtm_improvement_receipts.py`和`/tmp/audit_egtm_real_pilot.py`只读调用现有验证函数；日志私有保存，不调用模型。正式12任务命令**未运行**。

| 验收 | 状态与界限 |
| --- | --- |
| 工程/单元/Ruff | PASS，341测试 |
| 真实AppWorld脚本、3独立恢复、28请求直接回放 | PASS，无真实模型效果含义 |
| 原数据/权重/旧锁与快照兼容 | PASS |
| 真实Qwen权重加载、18请求与成本记录 | PASS |
| 真实Qwen小样本Memory流程可用 | 未通过：0/1更新接受、未发生有效L1使用 |
| 3任务完整A/B | INCOMPLETE，按协议停止，执行2/6 |
| 正式A/B及效果提升 | NOT_RUN，不支持收益结论 |
| 关机 | 自动失败；用户确认手动关机，未独立查账 |

下一步为独立版本的Memory输出契约改进与新授权小样本验证；仍不进入训练、P4-A或正式扩样。
