# P3-A 冻结提示后的场景扩样验收（2026-10-21）

本轮从 `cc53bea6bf13307c77abc68566a55ae51ef6e138` 继续。**采样、真实 Teacher 收集、官方筛选和数据格式验收 PASS**；Student 批量接口实现/软件/真实环境协议探针 PASS，**真实4B模型运行未验证**。正式训练规模准备仍 PARTIAL，P3-B/SFT/GRPO/远程训练均 NOT_STARTED。

## 预先固定的采样与协议

冻结 execution_v3、dependency_examples_v2 与 A/B 五个源文件，未修改提示。官方 train 90任务/30家族，仅用公开 ID→generator_id 映射，seed=20261021，选12任务/6家族，各2变体，排除旧诊断家族82e2fac。未按隐藏难度/结果选择，未重跑或替换失败；新清单中仍可包含其他 Spotify 家族。本轮全部用 B dynamic_discovery，A 不运行，不是新的配对实验。

配置 `configs/p3a_expanded.json`；固定清单 `configs/train_expansion_20261021.json`；协议锁 `configs/prompt_protocol_lock.json`。采集启动前记录源文件哈希，结束后全部对应文件不变；P0–P2原17项也不变。12个任务独立进程、原始状态、串行执行。

每条40模型请求/16384生成token/600秒/USD1，总480请求/196608输出/7200秒/USD12，单回复最多4096，工具调用数量不限。费用授权沿用用户“费用不限”，本轮自定限额未触达，自动重试0。模型 `deepseek-flash`，文档版本 DeepSeek-V4.1-Flash-1017，全部实际 fingerprint `e72d3c7f663d3e1383b00b3884cb4501`；供应商 alias 不是不可变权重 revision。

## 全部结果与成本

12次尝试、12次有效官方评价、0基础设施错误，官方成功6次：**6/12=50%**。唯一成功任务6、成功场景家族4；6条全部通过格式/来源/回执/无进展循环等现有筛选。其余6条均为 official_not_successful，全部保留。12条终止均为 completion_claim；没有截断、预算耗尽、格式拒绝或公开工具 Execution failed，不能把“自称完成”当作成功。未进一步依据隐藏答案调提示。小样本的50%不代表总体性能。

| scenario_id | 不同任务 | 官方成功 | 合格示范 |
| --- | ---: | ---: | ---: |
| 29caf6f | 2 | 0 | 0 |
| 22cc237 | 2 | 1 | 1 |
| b0a8eae | 2 | 0 | 0 |
| 287e338 | 2 | 1 | 1 |
| 34d9492 | 2 | 2 | 2 |
| 2a163ab | 2 | 2 | 2 |

| 任务 | 官方成功 | 模型请求/回合 | 工具调用 | 输入token | 输出token | USD费率估算 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 29caf6f_1 | 否 | 10 | 18 | 76350 | 1634 | 0.007141128 |
| 22cc237_1 | 否 | 14 | 27 | 163639 | 3184 | 0.021640308 |
| b0a8eae_3 | 否 | 15 | 27 | 202023 | 3416 | 0.029896500 |
| 287e338_1 | 是 | 8 | 9 | 48310 | 856 | 0.005133768 |
| 34d9492_3 | 是 | 16 | 76 | 180100 | 13197 | 0.025799328 |
| 2a163ab_1 | 是 | 12 | 23 | 114393 | 2523 | 0.013223388 |
| 29caf6f_3 | 否 | 14 | 20 | 156168 | 2274 | 0.017855424 |
| 22cc237_3 | 是 | 16 | 28 | 185433 | 3391 | 0.020712348 |
| b0a8eae_1 | 否 | 16 | 27 | 256688 | 3558 | 0.034574688 |
| 287e338_3 | 否 | 8 | 9 | 50191 | 916 | 0.005356116 |
| 34d9492_2 | 是 | 15 | 88 | 185223 | 15589 | 0.025540260 |
| 2a163ab_2 | 是 | 15 | 30 | 190592 | 4140 | 0.022820160 |

总计 **159次模型请求、382次真实工具调用、1,809,110输入token（1,287,936缓存命中）、54,678输出token**；558.879 worker秒，573.408秒采集墙钟（约9.56分钟）。按已核验高峰费率USD0.30/百万未缓存输入、0.006/百万缓存输入、1.20/百万输出，估算 **USD0.229693416**；billed_cost=null，没有实际逐请求账单，不称为实扣。无未知费用/token预留。

最大单回复批次 20 次调用，最大单任务88次调用；全部执行/返回边界核验。另有 Student 协议探针8次公开工具读取、0付费请求，不计入Teacher的382次或成功率；两次初始化超时探针没有实际工具调用。全部失败轨迹成本已计入上述159次请求。

## Base-SFT 数据质量

新包6条完整Teacher示范/6任务/4家族，全部满足本次32768完整序列和4096 Qwen新目标token预算。固定Qwen3-4B tokenizer，不加载权重：82个Assistant目标、34,559监督token；最长完整序列29,039，最长生成前输入28,922，最长Assistant目标3,161 token。254个成功轨迹工具调用参数逐条回读一致，38个目标含批量调用，23次schema变化受校验。

只有本轮Teacher Assistant新token监督；系统、用户、历史Assistant、工具结果、padding全部屏蔽。每回合使用真实当时schema，未用最终schema覆盖早期输入。raw Clean Dataset不被改写；过长时整轨迹暂缓，不删结果或静默截断。本轮过长/过长目标均0。

逐回合训练输入合计927,022 token，其中上下文892,463（96.27%）；相邻样本完全相同前缀累计646,951 token（69.79%），总输入/各轨迹最后回合长度总和=7.456倍。后两项是具体开销指标，不是语义重复率或可直接消除的比例。

| 家族 | 合格轨迹 | 监督token |
| --- | ---: | ---: |
| 34d9492 | 2 | 25283 |
| 2a163ab | 2 | 5837 |
| 287e338 | 1 | 657 |
| 22cc237 | 1 | 2782 |

文件系统家族34d9492占监督量约73.16%；正式训练前须扩大覆盖并决定按任务/家族的采样权重，当前未应用权重。旧execution_v3数据包4条/2任务/1家族仅只读重验：31目标、9844监督token、全部格式通过。新旧包合计10条、8唯一任务、5家族，**尚未合并为正式训练集**；旧两个任务已用于提示诊断，不能作为独立效果验证集，也不计入本轮50%。更早不同提示/预算的历史包继续单列。

## Student 协议与验证范围

新增独立 StudentSession：固定模型/模板/checkpoint/input IDs，原生多JSON工具块、整批预校验、串行真实调度、完整结果后再生成；B发现的新schema下一次才可用，A固定。不存在同批动态参数替换，依赖真实token/ID的调用必须跨生成回合。格式错/截断整批拒绝，中途异常保留实际结果并停止。JSON/schema无法通用证明任意两个动作语义独立，这一点仍需真实模型任务测试。

软件测试104项通过，Ruff/格式通过；真实Qwen tokenizer覆盖新旧10条/113个Assistant目标。真实AppWorld探针两种模式各2个脚本生成批次，每批2调用，共8次公开读取；实际反馈进入下一次输入，A/B schema转换及完整历史通过。该探针没有模型请求、权重加载、官方任务成功率或Teacher示范产出。远程4B实际推理后端、权重/checkpoint身份、超时、端到端自主任务仍未验收。当前无log probabilities，rl_eligible=false。

两次受限沙箱探针在官方Starlette TestClient/AnyIO初始化等待超时；第二次保存Python栈，随后在与已成功Teacher相同的经批准环境运行通过，没有改依赖源码。日志保留 `.cache/student_batch_probe_{a,b,c}.log` 和 `runs/student_batch_20261021_{a,b,c}/`。

本轮新解析器首次检查未接受Qwen模板末尾的 im_end+换行；依据真实tokenizer输出修复，仅新增Student模块改变，冻结Teacher文件不变。初次日志 `.cache/expanded_protocol_precheck.log`。加入Qwen目标长度检查后的审计完成，但复用公共报告名触发append-only保护；旧报告未覆盖，结果从已完成quality.json发布到 *_v2.json。CLI现已预先拒绝现存输出/报告路径。

## 证据和复现命令

- `reports/scenario_expansion_summary_v2.json`：所有尝试、家族、过滤、逐工具计数和全部回执成本重算（初版summary.json保留）。
- `reports/scenario_expansion_audit.json`：159份响应、官方结果与先存终态后评价的哈希绑定。
- `reports/scenario_expansion_quality_v2.json` / `scenario_expansion_data_balance.json`：真实Qwen质量与成本；`expanded_protocol_precheck_v2.json`为旧成功包只读回归。
- `reports/student_batch_probe.json`：真实工具协议探针；`scenario_expansion_checks.json`：源代码及P0–P2哈希、12份LLM视图索引。
- `reports/scenario_expansion_tests_release.txt` / `scenario_expansion_ruff.txt`：最终软件验证。
- 原始请求/响应/轨迹/终态/评价：`runs/scenario_expansion_20261021_a/`；本地LLM完整视图 `outputs/scenario_expansion_20261021_a/<task_id>.md`。这些受限内容不上传公共GitHub。
- Clean Dataset manifest SHA-256：`ca7e0d3e74567818f21d0a903b49e8a6291829e985fc5818df5cbe82f9d84707`；最终tokenized JSONL SHA-256：`217e8fc7f9a73bee37bcb96849bdb01780c01e613725d01cd496b687e9e18a3b`。

实际执行的主要命令（路径已存在，重跑必须使用新的输出/报告名称）：

```bash
export PYTHONPATH=src
export APPWORLD_CACHE="$PWD/.cache/appworld" IPYTHONDIR="$PWD/.cache/ipython"
.venv/bin/python -m recoveragent.p3a collect --config .cache/scenario_expansion_20261021_a.json --output runs/scenario_expansion_20261021_a --credentials-file .env
.venv/bin/python -m recoveragent.demonstrations verify runs/scenario_expansion_20261021_a/clean_dataset --appworld-root data/appworld --write-receipt
.venv/bin/python -m recoveragent.collection_report runs/scenario_expansion_20261021_a --output reports/scenario_expansion_summary_v2.json
.venv/bin/python scripts/audit_teacher_pilot.py runs/scenario_expansion_20261021_a --output reports/scenario_expansion_audit.json
export HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1
.venv-inference/bin/python -m recoveragent.sft_quality runs/scenario_expansion_20261021_a/clean_dataset --expected-manifest-sha256 ca7e0d3e74567818f21d0a903b49e8a6291829e985fc5818df5cbe82f9d84707 --output data/scenario_expansion_sft_20261021_b --report reports/scenario_expansion_quality.json
# 上一行质量审计完成，最后写现存report被拒绝；已把输出目录quality.json原样另存为scenario_expansion_quality_v2.json。新CLI现在提前拒绝已有路径。
.venv-inference/bin/python scripts/probe_student_batch.py --task-id 29caf6f_1 --namespace student_batch_20261021_c --output runs/student_batch_20261021_c --report reports/student_batch_probe.json
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
```

Teacher日志 `.cache/scenario_expansion_20261021_a.log`；最终quality日志 `.cache/scenario_expansion_quality_b.log`；恢复发布的结果文件保留于 `data/scenario_expansion_sft_20261021_b/quality.json`，与公共v2一致。更早质量版本与失败日志保留，不重写历史。

## 进入 P3-B 尚缺

1. 固定正式示范规模、增加成功家族覆盖，决定家族/任务训练权重；按scenario划分内部训练/验证，禁止同源变体跨划分泄漏。当前6条/4家族不足以支持泛化结论，没有虚构一个已达成的正式数据门槛。
2. 在授权远程RTX PRO 6000 96GB环境核验GPU/驱动/依赖、同版本AppWorld、数据安全传输及SHA-256复验。当前无远程连接或迁移结果。
3. 接入并测试固定4B权重的真实Student批量生成后端、长上下文预算/显存与端到端自主任务；然后在明确训练授权下做一次Base-SFT更新、保存/重载及分组验证。当前没有任何参数更新或训练效果。
4. 后续继续强制 Base-SFT → Student新失败 → Teacher恢复示范 → Recovery-SFT → GRPO，不因现有Teacher成功率跳过阶段。
