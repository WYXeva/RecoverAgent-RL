# EGTM v2 Model-Driven Working Memory 验收报告

日期：2026-10-27。分支 `codex/p0-p2-recovery`，起点 `091b8f18cfee384e9fb8c3406638ab6a71c78d4b`；开始时工作区与该提交一致，`git pull --ff-only` 确认已是远程最新。主目录没有可用 Git 元数据，沿用 `/tmp/recoveragent-diagnosis-20261022` 发布克隆。

**工程、软件测试和真实 AppWorld 脚本探针 PASS；真实 Qwen Memory 效果 NOT_RUN。** 本轮未加载模型权重、未调用真实模型/付费 API、未连接或启动远程 GPU，未训练或执行 Teacher Recovery。正式模型仍为原 A100 训练的 Qwen/Qwen3-4B Base-SFT。

## 1. 原问题与最小改动

起点 v1 已有 JSONL Evidence Index、不可变原始历史、独立编译器与 v3 快照。主要缺口是 `task_memory.py:working_view()` 以规则事实/操作生成 L1；`student.py:build_request/step` 只有业务生成；`state.py` 的 v3 仅靠工具历史重建规则状态。没有重新实现这些基础设施，也没有新增 Memory Agent、向量库、业务规划器或 Agent 可见工具。

| 文件 | 本轮职责变化 |
| --- | --- |
| `task_memory.py` | 保留 v1 路径；增加有界 L1 Schema、更新输入、来源/原值校验、原子提交与原始回执重放核验 |
| `context_compiler.py` | 优先 L1 精确检索；只删成功 L1 覆盖的旧完整批次，未覆盖新历史必须保留 |
| `student.py` | 同一 generate/backend 的独立内部请求，共享请求/Token/时间预算；完整记录输入和真实响应 |
| `student_backend.py` | 仅内部请求额外返回真实生成 IDs 的原样解码文本，现场再验证 text/IDs 相符 |
| `state.py`、`p3c.py` | 可选格式4，绑定 L1/更新回执/L2/Agent runtime；内部更新之后也保存合法边界 |
| `student_evaluation.py` | 选择独立 v2 源码锁并保持固定模型协议 |
| `scripts/summarize_egtm.py` | 支持 `--version v2`，从回执重算内部请求成本，净收益扣除 Memory 输入 |
| 新测试/脚本/配置 | `test_egtm_v2*.py`、`probe_egtm_v2.py`、`configs/egtm_v2*.json` |

`memory_store.py`、AppWorld Adapter、官方评价、AnswerGuard、Harness、execution_v5 提示、SFT mask 实现都没有改动。没有新增生产模块。v1 自动事实/操作元数据继续服务证据有效性与旧快照；v2 给模型展示的 L1 仅来自已接受模型 JSON，不用规则补出业务进度。

## 2. 三层职责与校验

- **L1：Student 生成。** 内部请求使用同一个 policy、checkpoint、tokenizer、non-thinking 模板、generate 回调和 backend。结构包含 goal、constraints、progress、completed、pending、important_facts、uncertainties、errors、need_evidence。当前工程测试喂入的是合成回执，不能据此说真实 Qwen 已经成功生成或维护这些内容。
- **L2：Harness 管理。** 仍只接收完整合法工具批次的真实返回。精确 source_id / tool_name / field / typed entity_id 查询有任务与历史位置边界；L1 的 need_evidence 优先，原确定性检索兜底。指定不存在或未观察 source_id 会拒绝更新；开放字段/实体查询无命中时返回空，不虚构结果。
- **L3：Harness 保管完整原记录。** 请求、响应 token IDs/text、工具调用/结果/执行意图与合法历史都保留。内部请求带 `purpose=memory_update`，不插入业务 messages、不执行 AppWorld 工具、不充当任务完成声明或 Teacher 示范。

progress/completed/pending/errors/uncertainties 只能标 `unverified`，即使带引用也不升级为官方事实。important_facts 必须引用真实已执行结果中的原始 JSON scalar 路径，类型和值精确相等；日期、路径、ID 和数值不转换。错误返回不能作为成功字段事实。写操作按原有保守失效机制影响旧证据；原 L1 回执不改写，当前失效覆盖层使旧 observed 标签不能被当作当前有效。

JSON 解析拒绝重复键、非有限数值、未知字段、错误类型、缺字段、过长内容和未完成输出；检查 EOS/finish、来源可见性、原值与 stale 状态。失败更新保留旧 L1，整条拒绝回执继续入审计。没有“补全 JSON”、伪造工具返回或隐藏评价输入。语义真实性和任务约束总结是否充分仍需真实模型评价，不能由 Schema 检查保证。

## 3. 增量、触发与预算

更新输入为旧 L1 + 自上次**成功**更新以来的新合法历史 + 历史证据 + 原始任务。失败更新不推进覆盖位置。新历史已有的工具结果不会再次作为检索结果附加；旧证据优先满足 L1 请求，没有请求时只附一个确定性兜底结果，减少重复前缀。

v2 默认关闭。固定 B 配置采用实际下一次输入阈值 **20,000 token**、最小 **3 个新完整批次**间隔、最近 **2 批**交互保留。最新完整批次的公开错误或可能写操作也可触发；“可能写入”只是公开执行事件，并不推断阶段完成。失败尝试也进入节流记录，不在原地重试。协议拒绝和数值纠正的下一回合不插入 Memory 请求。

业务输出仍最多4096；Memory Update 默认最多2048，L1最多6000字符，各数组/字段另有上限。所有请求合计最多40次、生成最多16384 token、任务600秒，输入加允许输出不超过32768。生成前、编译后均检查时间；超时或未知传输结果不自动重发。远端单个 generate 的中断仍依赖现有 backend/HTTP 超时机制，不能声称可实时抢占 GPU。

内部请求使用与业务请求相同的计数/响应身份检查；另记 memory_update_requests/input_tokens/output_tokens/seconds、memory_validation_failures、compressed_requests、saved_input_tokens。证据索引耗时单列 evidence_index_seconds，编译及请求构造耗时计入 context_compile_seconds；全部实际耗时均处于任务 wall budget 内。没有付费请求，所以本轮实际模型账单为零，不把合成 token 数当作真实模型消费。

## 4. 安全压缩与检查点

Context Compiler 不生成业务摘要：它保留原始 System/User 任务与约束、全部当前工具 Schema、模型 L1、所需 L2 证据和最近完整交互。只删除已被有效 L1 覆盖的旧完整调用/返回组；失败更新之后未覆盖的历史不能删除。必须检索的已存在证据容不下时，回退完整输入或明确 context_budget_exhausted；不静默截断。L1 没有可用更新时，不能用 v1 规则视图冒充 v2 记忆进行压缩。

新内存版本 `egtm-model-working-memory-v2`，运行版本 `egtm_model_memory_20261027`，检查点格式 **4**。文件包括原 DB/runtime/context、Agent runtime、memory/state.json、index.jsonl，以及 updates/*.json 中完整请求与原始响应。manifest 和 state 引用覆盖 SHA；加载按时间边界交错重放 L2 与更新回执，核验原任务、旧 L1、新历史、可见 Schema、策略、使用量、输出完成状态及证据原值，再接受回执中的 L1，**不重新请求模型**。

实时后端与 Session 校验 token/text 同一性；离线无 tokenizer 的恢复以可信原始回执哈希及来源核验为基础，不声称能仅靠哈希证明模型身份真实性。探针还用固定真实 tokenizer 对每个实际请求重新渲染、核对 IDs 和窗口。

原格式1/2/3保留原验证路径，旧快照无字段改写。Pre-error 快照不含后续 Memory，Post-error/Late 只包含保存时已发生的更新。部分批次和未知副作用仍不能生成合法恢复快照；未知生成预留也禁止进入 v4。内部更新是单独的执行回合，P3-C 下一业务动作的 pre 边界包含正确 L1 和预算。

本轮验收的是**完整检查点状态一致性**，没有实现 Teacher 接管或完整 Agent 续跑控制器。若将已恢复的 v2 Memory 装入预算清零的新 StudentSession，会明确拒绝，避免借恢复绕过原预算；未来续跑必须恢复完整 Agent runtime。

## 5. 软件与真实环境证据

`reports/egtm_v2_checks.txt`：**319 pytest 通过**，Ruff check 通过，**117 文件 format check** 通过。新增30项软件测试覆盖：严格提交/回退、证据与原值类型、未来隔离、精确检索、覆盖边界压缩、原任务保留、请求/Token/时间预算、无立即重试、传输未知预留、v4与旧协议兼容、回执篡改、pre/post来源、部分写入、P3-C内部边界、关闭后精确一致、成本重算及禁止预算重置。

**真实 AppWorld 脚本探针**：`runs/egtm_v2_probe_20261027_c/`，公开结果 `reports/egtm_v2_world_probe.json`。原始 train 任务 `e3d6c94_3` 上执行公开 API：真实认证、创建/读取/更新笔记、公开业务错误、目录长结果、两个写操作中第二个丢失返回的故障探针。脚本动作和合成 L1 是软件探针，**不是自主 Student 成绩、不是自然失败或 Teacher 示范**。没有评价该脚本是否完成原始任务。

| 指标 | 实测 |
| --- | ---: |
| 脚本生成回合 | 28 = 24业务 + 4内部Memory |
| 真实工具调用意图 | 29（包括最后有真实副作用但返回丢失的调用） |
| L1更新 | 3接受，1故意非法JSON拒绝 |
| 完整检查点 / 独立进程恢复 | 3 / 3通过 |
| 实际业务压缩请求 | 10 |
| 相同行为未压缩业务输入 | 414,188 token |
| 编译后业务输入 | 264,088 token |
| Memory输入 / 输出 | 57,217 / 469 token |
| 全部输入 / 输出 | 321,305 / 1,902 token |
| 业务输入节省 / 扣除Memory输入的净节省 | 150,100 / 92,883 token（净22.43%） |
| 最长原始业务输入 → 该请求实际输入 | 37,553 → 11,251 token |
| Memory回合 / 编译 / 索引耗时 | 0.177 / 2.494 / 0.037秒（无真实模型计算） |
| 完整探针含恢复耗时 | 141.25秒 |
| 私有探针目录 | 约18MB |
| 真实模型请求、训练步数、付费API | 0 / 0 / 0 |

三个恢复分别重新核对 DB 逻辑状态、环境 runtime、合法历史、工具 Schema、Memory/L2/回执和 Agent runtime，源快照不变。部分执行保留首条真实返回、第二条未知返回及实际 DB 修改，未更新 Memory，不能保存合法恢复检查点。

净输入节省只对应这个刻意使用冗长目录的固定脚本，不是自主模型 A/B 收益。Memory输出还额外占469 token；模型可能生成差摘要、错误引用或频繁被拒绝，其真实成本与效果尚未测量。

开发中23项测试首轮发现：更新载荷重复加入已在新历史内的结果，可能使请求膨胀；已改为只检索旧证据。另两个测试夹具的task_id和结果键错误已修正。发布前复核还修正了关闭Memory时的额外时间检查、非JSON/重复键返回与合法null的区分，以及无L1时恢复仍可能重置预算的漏洞。开发探针a/b原样保留（含各自源码锁）；b的最终恢复发生在边界修正期间，仅作开发证据。最终冻结源码用c完整复验，不改写a/b来源哈希。三轮合计87个真实工具意图、84次合成生成（12次Memory），始终0真实模型/付费调用。

## 6. 历史完整性与实验配置

`egtm_v2_integrity.json`：原95条轨迹/75任务/29家族、18来源包、6616文件重新核验通过，manifest仍为：

`5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`

两个 Base-SFT checkpoint 各7发布文件保持原SHA。327个旧报告/配置与起点逐字节相同；旧v1快照、旧v2快照和三份v3快照通过当前代码检查。没有改奖励、训练目标、Teacher数据或原始日志。

独立锁 `configs/egtm_v2_runtime_lock.json` 绑定父提交与当前源码。旧锁不重算：在新源码上应拒绝不匹配的旧运行，精确复现v1使用091b8f1；“旧快照可读”不等于“旧运行协议换了源码仍可冒称复现”。

`egtm_v2_preflight.json` 已离线核对新锁、原固定12个train任务/11家族、checkpoint发布SHA与模型/tokenizer身份。A/B配置为 `egtm_v2_eval_a.json` / `egtm_v2_eval_b.json`，只改memory.enabled，锁定同一原A100 adapter：

`adapter:34b87e77f8f49e3fef772d9c7f1074d89f07dd32bda38d81d6675120bb5e0ccd`

模型revision `1cfa9a7208912126459214e8b04321603b3df60c`；模板SHA `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`；non-thinking。没有普通摘要基线。任务清单已在历史开发中使用，未来也不能称为独立未见benchmark。

## 7. 实际命令与后续

本轮运行：

```bash
git -C /tmp/recoveragent-diagnosis-20261022 pull --ff-only origin codex/p0-p2-recovery
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
PYTHONPATH=src HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 timeout 600 \
  .venv-harness/bin/python scripts/probe_egtm_v2.py \
  --output runs/egtm_v2_probe_20261027_c --namespace egtm_v2_probe_20261027_c
PYTHONPATH=src .venv/bin/python /tmp/audit_egtm_v2_integrity.py
```

真实 AppWorld 需要本地 IPC 权限，探针在授权的放宽沙箱中串行执行；未修改依赖。私有日志 `/tmp/egtm_v2_probe_a.log`、运行目录tokenizer/restore日志与requests/turns全保留。完整性命令直接调用现有 `formal_dataset.verify`、文件SHA及 `state.inspect_snapshot`，没有新评价器。

后续获推理/GPU授权后，在已有机器验证相同4B backend，再以 `student_evaluation` 对两份固定配置各运行 `--arm base_sft`；用 `scripts/summarize_egtm.py --version v2 --a <A目录> --b <B目录> --output <新汇总>` 重算。**这些真实模型命令本轮未运行。** 比较官方有效评价/未知结果、成功率、上下文耗尽、全部输入/输出、内部Memory开销、重复调用与错误副作用；另对L1约束保留和语义事实准确率做控制侧审计。静态Schema原值通过不代表语义总结准确，不能预报性能提升。
