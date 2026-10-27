# Harness-Managed Memory：29k 压缩，本地验收与重实验准备

2026-10-27；分支`codex/p0-p2-recovery`，基线`23a7d41beb35f950196a99ebe49b1a0ed21b8fca`。已拉取，远程无更新，发布克隆工作区初始干净。用户要求暂退回Harness管理Memory，只在接近29k时压缩并重实验；随后明确“先完成本地工作，本地工作完成后告诉我我去开机”。因此本轮**本地工程/真实AppWorld探针PASS，真实Qwen重实验NOT_RUN，等待用户开机**。无SSH连接、模型加载、GPU推理、付费调用或训练。

## 行为与版本

没有Git回滚、删除模型驱动实现或改旧锁。新增独立`egtm-harness-memory-29k-v1` / `egtm_harness_29k_20261027`，默认关闭。复用v1规则事实/操作记录、L2索引、L3原始历史及现有编译器接口：

- Memory完全由Harness依据已经观察的真实工具结果维护，无Student Memory Update，无第二模型或额外模型请求。这里只提供可追溯事实与原始计划引用，不宣称有模型语义总结；任务完成状态仍unknown，observed/inferred/stale边界保持。
- 阈值为**当前实际待发送输入的29,000 token**，包含原任务、当前工具Schema、已有Memory和保留历史；不是29×1024，也不是累计历史token。
- 阈值前保持原输入不注入Memory、不压缩。29,000+4,096超过32,768，故在28,673–28,999区间沿用Student原有剩余窗口限制，输出上限适当低于4096；例如28,999输入仅允许3,769输出。不能因为预留4096而提前压缩。
- 达到阈值后移除较早的**完整调用/结果批次**，保留最近2批、全部原始system/user约束及反馈、当前完整Schema。来源明确的规则事实和必要历史证据替代旧冗长结果；无法安全减少并容纳回复时明确失败。
- 保存最后一次移除边界。后续继续累积实际输入，直到再次达到29k才移除更多批次；不能因为不可变完整历史已经超过29k就每回合继续压缩。规则事实和stale元数据仍随完整工具批次更新。
- L2复用v2.1去重/来源机制，不再完整插入实际输入已含的同一source；原文过长时显式deferred，保留有来源的标量/引用。没有新增可见Memory工具或业务规划器。

单回复上限4096、完整窗口32768、每任务40请求/16384生成token/600秒不变。新增`compression_events`表示实际触发次数，`compressed_requests`表示使用压缩上下文的请求数；两者不能混为一谈。规则维护CPU耗时继续记`memory_update_seconds`，此版本不存在模型Memory更新成本。

新源码锁SHA：`ff37168beefddbda0a8d12ce291a123d103f5d03496e4c880e3d43caddf510a6`。模型驱动v2/v2.1、原v1配置/锁/报告原样保留。

## 修改与兼容

| 文件 | 修改 |
| --- | --- |
| `task_memory.py` | 新Harness版本/固定阈值配置；保存最后压缩边界、当时history_end及来源SHA；加载验证合法边界和非未来历史 |
| `context_compiler.py` | 新版本专用29k触发，已有压缩边界复用；输入/回复窗口检查及原子提交 |
| `student.py` | 统计真正压缩事件及输入节省；该版本不进入模型更新路径 |
| `p3c.py`、`student_evaluation.py` | 独立运行锁选择，重实验允许原预算内已经纠正的模型错误，基础设施/不可恢复协议中断停止 |
| `scripts/probe_egtm.py` | 复用原真实状态探针，可传新配置并验证29k触发；旧默认保留 |
| `scripts/summarize_egtm.py` | 支持`--version harness_29k --stage pilot`；报告是否真实触发Memory，不再要求模型L1接受 |
| 新配置/测试 | 默认关闭配置、固定3任务A/B、P3-C探针配置、运行锁、9项针对性用例 |

未改AppWorld、官方评价、工具语义、Harness纠错预算、AnswerGuard、execution_v5或训练代码。`state.py`无需改动：新Harness Memory使用既有格式3，独立Memory/runtime身份，原格式1/2/3/4仍可读。压缩边界可从快照恢复，完整原始历史和工具证据仍在；它不授权重置Agent预算或实现Teacher接管。快照恢复验证状态一致，不声称已实现完整Agent续跑。

## 本地证据

[检查日志](egtm_harness_29k_checks.txt)：**350 pytest PASS**，Ruff check PASS，119文件format PASS。9项新增用例覆盖28,673/28,999/29,000边界、历史不改、工具配对、固定移除边界/再次触发、保存恢复一致、未来/半批边界拒绝、安全失败、无内部模型请求、关闭前后输入一致及回复上限收缩。开发中补齐了测试夹具的tool_mode、合成模板/公开工具目录；未为通过测试修改生产校验。

[真实AppWorld探针](egtm_harness_29k_world_probe.json)：`runs/egtm_harness_29k_20261027/on_script/`，train `e3d6c94_3`，真实认证/创建/读取/修改笔记、公开错误、长API目录以及部分写入丢失返回。动作与模型输出由脚本指定，**不计真实Student、Teacher示范或官方任务成绩**。

| 指标 | 实测 |
| --- | ---: |
| 真实工具调用意图 / 合成请求 | 29 / 25 |
| 真实模型请求 / Memory模型请求 | 0 / 0 |
| 实际压缩事件 | 1 |
| 使用压缩上下文的已发送请求 | 5 |
| 触发前实际输入 → 压缩后 | 29,977 → 15,894 token |
| 末次未发送输入：全历史 → 实际上下文 | 37,677 → 23,640 token，无新压缩 |
| 未压缩相同行为输入 / 实际输入 | 420,299 / 350,005 token |
| 输入减少 | 70,294 token，16.72% |
| 合成输出token | 1,455 |
| 规则维护 / 编译CPU耗时 | 0.036 / 2.041秒 |
| 检查点 / 独立进程恢复 | 3 / 3通过 |
| 完整探针耗时 | 151.04秒 |

`compiled_requests=6`的探针字段包含一次`next_unsent`构造；实际请求使用量中的`compressed_requests=5`才是已发送分母。所有触发均满足当前输入≥29,000；之前构造的Memory不靠额外模型生成。真实写操作使旧事实stale，部分执行保留已执行副作用和未知返回，拒绝合法恢复快照。

[直接tokenizer回放](egtm_harness_29k_replay.json)：全部25个请求用固定Qwen tokenizer/non-thinking模板直接重渲染，IDs/窗口/合法配对/原始历史hash通过。首轮A/B输入IDs、messages与Schema相同；原始DB初态的逐任务配对门槛继续由未来真实worker记录，不能把脚本输入配对当成自主模型实验。

另对上一轮`287e338_3` Memory-off真实轨迹的10个请求离线重编译：新Harness在该短历史上逐一保持相同输入IDs，未触发压缩。这证明阈值前不注入Memory，**不是重跑任务，也不能复制旧成功率当作新结果**。今后的短任务若完全不触发压缩，需要报告“Memory未被实际使用”，不能据此宣称记忆有效。

[历史完整性](egtm_harness_29k_integrity.json)：353个旧报告/配置与23a7d41逐字节一致；原95轨迹/75任务/29家族、18包/6616文件和两个checkpoint发布SHA重验通过。数据manifest仍为`5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`。真实旧1/2/3/4快照（含上一轮v2.1）和真实模型拒绝Memory终态均可读。旧锁在修改后的源码上应拒绝，精确复现用旧commit，不改hash放行。

## 待开机后的实验

[固定计划](egtm_harness_29k_plan.json)，配置`configs/egtm_harness_29k_pilot_{a,b}.json`：

- A：同一原A100 Qwen3-4B Base-SFT，Memory关闭。
- B：同权重，Harness-Managed Memory，29k触发。
- 固定`287e338_3`、`3c13f5a_1`、`229360a_2`，逐任务A后B，最多6次；不按结果更换任务，所有历史失败保留。
- 同模型revision、adapter tensor SHA、工具模式、execution_v5、seed42、temperature0.8/top_p0.95及40/16384/600预算。没有内部Memory请求，因此不再因Memory生成占用一个请求而移动业务采样seed。
- **新运行停止规则**：基础设施故障、不完整工具执行或协议纠错耗尽停止后续；原有最多2次纠错中已经纠正的模型Schema错误计入报表，不单独视为新Memory基础设施故障。官方失败和未知评价仍分别统计。该规则独立版本化，不反写上轮“任何协议拒绝都停止扩样”的结果。
- 完整A/B PASS仍需全部预定任务完成有效评价、初始DB及首次token IDs一致。实际Memory效用另需发生压缩；未触发不会被标为Memory验证通过。禁止用较短轨迹、未完成任务或脚本压缩率宣称模型收益。

当前机器由用户报告已关机，按用户新指示不连接、不启动。等用户开机后再核验现有机器/模型/数据/新锁，隔离部署并执行。结束后保存证据并关机；上一轮平台缺关机密钥，届时必须如实确认关机结果。无需重训、P4-A或正式12任务扩样。

## 已执行命令

```bash
git -C /tmp/recoveragent-diagnosis-20261022 pull --ff-only origin codex/p0-p2-recovery
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
PYTHONPATH=src HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 \
  .venv-harness/bin/python scripts/probe_egtm_v2.py \
  --config configs/egtm_harness_29k_pilot_a.json --initial-only \
  --output runs/egtm_harness_29k_20261027/off_initial \
  --namespace egtm_harness29k_20261027_off
PYTHONPATH=src HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 \
  .venv-harness/bin/python scripts/probe_egtm.py \
  --config configs/egtm_harness_29k_p3c.json \
  --output runs/egtm_harness_29k_20261027/on_script \
  --namespace egtm_harness29k_20261027_on \
  --report reports/egtm_harness_29k_world_probe.json
PYTHONPATH=src .venv/bin/python /tmp/audit_harness29k_integrity.py
PYTHONPATH=src HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 \
  .venv-inference/bin/python /tmp/audit_harness29k_replay.py
```

私有运行、原始AppWorld结果及日志不公开，仅提交代码/配置/汇总/报告。本轮0真实模型token、0 GPU推理、0新增付费服务；上述350,005输入是脚本协议与tokenizer计数，不能报为真实模型成本。远程模型效果尚未验证。
