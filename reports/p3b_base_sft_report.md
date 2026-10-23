# P3-B Qwen3-4B Base-SFT 实际执行报告

2026-10-23，分支 `codex/p0-p2-recovery`，同步基线 `2145a930f94f54bbe2170b677c6f985b48dc9fd8`。

**真实训练与 checkpoint 交付 PASS；任务效果验收 PARTIAL。** 已完成短/长烟测、正式 1 epoch、116 个 optimizer steps、真实参数更新、保存和重新加载，以及固定 6 任务的 Raw/Base-SFT AppWorld 对照。Base-SFT 仅在一个训练集任务成功，尚无内部验证/dev 成功或泛化提升证据。两模型各有两个官方评价运行异常，不能作为有效 0 分。已核验并回传全部 checkpoint、轨迹和失败证据，执行平台关机命令后 SSH 不可达；未核实平台最终账单。

## 1. 数据、模型及源码

原 `data/base_sft_v1` 保持不可变：95 条完整 Teacher 轨迹、75 个任务、29 个 scenario 家族。18 个来源包重新校验，官方 train 成员、公开任务、361 套 Schema、动态可见性和来源一致；不读取隐藏答案来构造模型输入。

| 分区 | 轨迹 | 任务 | 家族 | Assistant 样本 | 监督 token | 全输入 token | 屏蔽 token | 最长完整序列 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| train | 82 | 64 | 24 | 925 | 229,776 | 10,008,716 | 9,778,940 | 31,102 |
| 内部 validation | 13 | 11 | 5 | 147 | 43,227 | 1,539,591 | 1,496,364 | 32,610 |

只监督当前 Teacher Assistant；System/User/Tool、历史 Assistant、拒绝回合与 padding 屏蔽。按家族/任务/相似簇/轨迹/回合平衡 loss，不用全 batch token 均值替换。两分区 tokenizer 输出在远程与原包**逐字节一致**；完整样本未截断，原 deferred/reserve 未进入训练。官方 dev 未进入参数训练。

- 数据 manifest：`5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`。
- 数据归档：`68231f625b876db259c5085d08e70c77604ffbaaf6b0809b807852e2bfc3b9ed`，37,773,083 字节。
- 正式模型唯一为 `Qwen/Qwen3-4B`，revision `1cfa9a7208912126459214e8b04321603b3df60c`，non-thinking。
- chat template：`a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`。
- 训练封存源码 `473693c3b768940679a2367d149fd54293e27496` 的原 50 文件全部匹配旧锁。仅另加 BF16 runtime 及补充锁，未改旧 `sft_recipe.py`、旧配置或数据锁。
- 运行补充锁：`configs/p3b_bf16_runtime_lock.json`（代码提交 `6c36e35`）；实际评价锁：`configs/p3b_evaluation_runtime_v2_lock.json`（代码提交 `975b506`）。旧评价锁及失败运行保留。
- 原配置中的 manifest 占位值由既有 `--expected-manifest-sha256` 参数绑定，最终 `formal/run_config.json` 保存上述完整可信 SHA；没有编辑原锁内配置来放行。

证据：[本地源码与配置审计](p3b_local_protocol_verification_20261023.json)、[远程重新分词](p3b_remote_retokenization_verification_20261023.json)、[结束时数据复验](p3b_final_data_integrity_20261023.json)、[实际源码版本](p3b_execution_sources_20261023.json)。各预检报告中的 `training_started=false` 只描述该预检过程，不覆盖后续真实训练事实。

## 2. 实测远程环境

SSH 实际连接成功。单张 NVIDIA RTX PRO 6000 Blackwell Server Edition，nvidia-smi 97,887 MiB，驱动 595.71.05；Python 3.12.3、PyTorch 2.10.0+cu128、CUDA 12.8、Transformers 4.57.6、PEFT 0.18.0、Accelerate 1.12.0。实际 BF16 CUDA 运算通过。容器 CPU 配额 22 核，内存 110 GiB，数据盘 100 GiB；安装和完整 checkpoint 存储未耗尽磁盘。

训练与 AppWorld 使用独立虚拟环境。AppWorld 固定官方 commit `42b5bcf3cd334fee33f0c37c02070a9f5807add5`，SQLAlchemy 2.0.52/SQLModel 0.0.48。远程真实写入、命名状态保存/恢复、四进程隔离脚本探针通过；它是基础设施证据，不是新增 Teacher/Student 成绩。完整 pip freeze 保存在私有 `final_snapshot/reports/`。

HF 直连失败、本机 Xet 无进展、AutoDL 学术加速出现低速/503；最终经 [Qwen 官方 ModelScope 镜像](https://modelscope.cn/models/Qwen/Qwen3-4B)传输。三个真实 safetensors 分片及 tokenizer/config 全部匹配独立获取的 HF 固定 revision 元数据，之后离线加载；未仅凭镜像 master 名称认定版本一致。见 [模型完整性](p3b_model_integrity_20261023.json)、[硬件](p3b_remote_hardware_20261023.json)、[环境预检](p3b_remote_sft_preflight_20261023.json)、[状态探针](p3b_remote_state_probe_20261023.json)。

## 3. 真实训练与重载

使用用户指定三份配置，正式训练从固定基座重新初始化 LoRA，不承接 smoke 权重。BF16、LoRA r=16/alpha=32/dropout=0.05，7 类 attention/MLP 投影，batch=1、累积梯度=8、学习率 1e-4、1 epoch、gradient checkpointing、SDPA；没有改成 1.7B、packing 或静默截断。

| run_003 阶段 | optimizer steps | train loss | eval loss | 阶段墙钟秒 | CUDA allocated 峰值 GiB | 重载 logits 最大差 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 短样本 smoke | 2 | 1.220343 | 1.149565 | 23.697 | 9.122 | 0 |
| 最长完整样本 smoke | 1 | 0.781450 | 0.515541 | 39.742 | 22.404 | 0 |
| 正式 Base-SFT | 116 | 0.227391 | 0.164290 | 2701.740 | 22.770 | 0 |

正式训练覆盖全部 925 个训练样本；最终验证覆盖 147 个样本。116 次 optimizer 前检查均有有限、非零梯度，每次 504 个梯度 tensor。训练报告 train_runtime=2585.3694 秒（含周期验证），最终独立验证 95.0423 秒；阶段墙钟还包括加载、保存及重载。第 50/100 步验证 loss 为 0.1791645/0.1644949。nvidia-smi 采样峰值 40,029 MiB，包含缓存/运行时分配，不能与 CUDA allocated 峰值混用。未发生 OOM。

正式 LoRA tensor 哈希从 `029b841888928835aab325ecfa0f304f271829422a58776f5a0daa6dcd1f9f49` 变为 `aefac6f068361cb5c0fc76c6a26f8b45231953b37d141f1ec9352a29917cf9c0`；重新加载固定基座与 adapter 后相同，显式 BF16 上下文的同输入 logits 最大误差为 0。独立推理后端再次核验 adapter tensor 哈希后才开始真实评价。

最终私有 checkpoint：`runs/p3b_remote_20261023/checkpoints/run_003/formal/`。保留最终 adapter/tokenizer/config，以及 `checkpoint-100`、`checkpoint-116` 的 optimizer/scheduler/RNG/trainer state。第 50 步曾保存，按原 `save_total_limit=2` 正常轮换，未声称仍保留其完整 optimizer。**精确断点续训尚未测试**，不能用 adapter 重载代替这一验收。

证据：[正式训练全部梯度与文件 SHA](p3b_formal_training_20261023.json)、[短 smoke](p3b_short_smoke_20261023.json)、[长 smoke](p3b_length_smoke_20261023.json)、[阶段门槛及实际命令](p3b_training_pipeline_20261023.json)、[checkpoint 交接摘要](p3b_checkpoint_release_20261023.json)。训练函数内 `p3b_complete=false` 表示该函数不判定 Agent 效果；保留原元数据，最终效果另见下文。

## 4. 真实 AppWorld 配对评价

`configs/p3b_paired_evaluation_20261023.json` 在生成结果前按 seed=20261023 固定各分区 2 个公开 task_id。两模型均从原始任务状态出发，6 对初始逻辑状态哈希和第一次模型输入 token 完全相同。

使用 **B dynamic_discovery**、冻结 execution_v5 与共享 Harness；non-thinking，temperature=0.8、top_p=0.95，逐请求固定采样 seed；每任务 40 请求、16,384 生成 token、600 秒，单回复最多 4,096，输入加输出总窗口 32,768。工具调用次数不限，整批校验后串行真实执行，真实反馈进入下一轮。模型看不到官方评价或隐藏答案，评价在终态保存后独立进程执行。

| 分区 / 任务 | Raw | Base-SFT | 观察到的终止原因（Raw / SFT） |
| --- | --- | --- | --- |
| train / e3d6c94_3 | unknown | unknown | completion_claim / 协议纠错预算耗尽；官方 IndexError、UnboundLocalError |
| train / e85d92a_3 | 失败 | **成功** | 上下文预算耗尽 / completion_claim |
| 内部 validation / 7d7fbf6_2 | 失败 | 失败 | completion_claim / 协议纠错预算耗尽 |
| 内部 validation / ce359b5_1 | 失败 | 失败 | completion_claim / 上下文预算耗尽 |
| 独立 dev / 68ee2c9_3 | unknown | unknown | completion_claim / 协议纠错预算耗尽；官方 KeyError |
| 独立 dev / 6bdbc26_1 | 失败 | 失败 | 协议纠错预算耗尽 / completion_claim |

每模型均 6 次尝试、4 个有效官方结果、2 个 unknown，0 个未尝试。有效结果成功率 Raw **0/4**，Base-SFT **1/4**；总尝试中已确认成功分别 **0/6、1/6**，后者不是将 unknown 当失败得到的完整官方成功率。分区有效结果：train 0/1 对 1/1；内部验证 0/2 对 0/2；dev 0/1 对 0/1。

非断言异常按既有评价规则标 `evaluation_error`、reward=null，未修改官方断言或用 0 代替。相同两个任务在两端都出现异常，但仅凭异常类型不能证明全是环境故障；也可能是未完成业务状态触发官方测试的边界。需后续单独控制侧诊断，不能让隐藏异常内容指导当前 Actor 重试。此次不换任务、不追试择优。

| 固定六任务开销/错误 | Raw | Base-SFT |
| --- | ---: | ---: |
| 模型请求/生成回合 | 65 | 86 |
| 实际工具调用 | 60 | 116 |
| 输入 token（含重复历史） | 831,468 | 891,671 |
| 生成 token | 2,233 | 14,277 |
| malformed/truncated 回复 | 0/65（0%） | 1/86（1.16%，截断） |
| 参数 Schema 拒绝回复 | 0 | 12 |
| 不可见工具拒绝回复 | 5 | 1 |
| 全部协议拒绝回复 | 5/65（7.69%） | 14/86（16.28%） |
| 任务墙钟总秒 | 313.252 | 765.213 |
| 模型生成总秒 | 78.910 | 526.360 |

“格式错误率”在此拆成语法/截断、参数 Schema 和可见性，不用一个含糊数字掩盖差别。Base-SFT 更长的执行不等于更强的能力：此次协议拒绝总比例更高，任务成功仅发生于训练集。小样本、单 seed、内部 validation 有历史 Prompt 开发接触，均不足以支持泛化提升或统计显著优势。

完整可重算摘要：[p3b_results_20261023.json](p3b_results_20261023.json)。私有逐轮输入、可见 tools、原始 token 回执、执行 intent、真实反馈、终态 DB/历史/运行时和独立官方评价在 `runs/p3b_remote_20261023/evaluations_v2/`；未作为新 Teacher 数据或 Recovery-SFT 后缀使用。

## 5. 已处理错误与完整成本

1. **run_001，0 更新**：编排器解析 venv Python 软链接后误选系统解释器，预检拒绝。改为保留绝对解释器路径，加回归测试。
2. **run_002，真实 2 更新**：短 smoke 已更新并保存，但重载 logits 验收失败。官方 Accelerate 包装与独立推理的 BF16 autocast 不同；实际 saved adapter 诊断，默认比较最大差 0.421875，统一显式 BF16 后为 0。新增版本化 runtime，原 0.01 阈值、50 文件锁不改。run_003 重做两个 smoke 再正式训练。见 D036、[实际诊断](p3b_reload_diagnostic_20261023.json)。
3. **真实时间预算**：官方 AppWorld 冻结 time.monotonic，实际 0.1 秒探针前后差为 0；Linux CLOCK_MONOTONIC 正常。StudentSession 仅加可选时钟注入，评价显式使用真实时钟，任务日期仍冻结。独立 v1 锁保留，见 D037。
4. **首次评价两条 worker 异常**：Freezegun 关闭 world 时扫描 Transformers 延迟模块，触发无 torch 的 NameError，重复关闭又掩盖主错。采用官方 configure API 排除控制侧 tokenizer 模块、在 world 创建前加载 tokenizer，并保留主错误与清理错误。真实工具/冻结日期/token IDs/close 探针通过后，在新 `evaluations_v2/` 重跑原固定清单；不改 Prompt、任务或奖励。旧两条真实生成/副作用保留，见 D038。
5. **关机包装器**：Python 直接 exec 平台 shutdown 报 Exec format error；改用平台文档的 shell `shutdown` 后远端关闭连接，SSH 复查 exit=255、无认证提示。未删除实例。

全部实际 optimizer steps 为 **121 = 失败 smoke 2 + 成功短 smoke 2 + 长 smoke 1 + 正式 116**。run_001/002 墙钟 2.294/23.032 秒，run_003 2765.185 秒。没有将失败 smoke 宣称 PASS。

首轮无有效官方评价的两条 worker 尝试另耗 39 请求、438,912 输入/2,175 输出 token、37 工具调用。因此本轮全部 **14 次模型任务尝试**共 **190 请求、2,162,051 输入 token、18,685 输出 token、213 工具调用**，缺响应生成预留为 0。真实 Teacher 请求为 0；未启动 Recovery-SFT/GRPO。

上述墙钟不含全部安装、下载、诊断、备份和空闲占机时间；GPU 实际账单单价/结算未取得，**货币成本为未知**，不能把阶段墙钟折算成虚构账单。完整资源摘要：[p3b_resource_usage_20261023.json](p3b_resource_usage_20261023.json)。

## 6. 回传、关机与复现命令

私有备份全部在关机前通过 SSH/SCP 回传并验证。首个大包 SCP 超时后用 SFTP 断点续传；正式包分片传输，每片及整包 SHA 均验证。checkpoint 清单的 43/28/43 个文件以及最终评价包 1489 个文件均独立核对。基座权重可按固定官方 revision 重取，无需公开上传。

| 本机私有归档 | 字节 | SHA-256 |
| --- | ---: | --- |
| runs/p3b_remote_20261023_phase1.tar.gz | 1,440,872,774 | `1f6b14f49ccc69114e7a732c0b4e68bf3d4f2c4192cc3d7c6c339340041d6f45` |
| runs/p3b_remote_20261023_formal.tar.gz | 867,459,412 | `70706c6547267f2a8827536f3344c858d10af3ef14a811a44bba41bb7ffafc55` |
| runs/p3b_remote_20261023_evaluation.tar.gz | 9,756,015 | `e0230b101f7ea57a6b8c830b21d8ed6cf3dfba66960d652f0c175e18537b8abb` |

归档解包于 `runs/p3b_remote_20261023/`，最后一轮日志单独置于 `final_snapshot/`，没有覆盖早期失败证据。公开只提交代码、配置、哈希与汇总报告；凭据文件已忽略，数据、隐藏评价、权重不上传。

约 21:25（Asia/Shanghai）按用户要求执行 [AutoDL 官方 shutdown](https://www.autodl.com/docs/save_money/)，随后连接关闭且 SSH 不可达。没有重新启动或租新机器。此为关机命令与连接状态证据，不冒称平台账单已结算：[p3b_shutdown_20261023.json](p3b_shutdown_20261023.json)。

实际训练逐阶段命令和路径已完整保存在 [pipeline JSON](p3b_training_pipeline_20261023.json)。远程主目录为 `/root/autodl-tmp/recoveragent_p3b_20261023`；编排器使用该目录 `.venv-sft/bin/python`，`--source training_source`、`--runtime-module recoveragent.sft_bf16_runtime`、`--output checkpoints/run_003`、固定 HF cache、远程重新分词报告及完整 manifest SHA。运行目录必须新建，不能覆盖本轮输出。

实际评价由私有 `final_snapshot/scripts/evaluate_paired_v2.py` 串行加载两个模型：

```bash
# 远程 worker_source 下，HF_HOME/PYTHONPATH 已指向锁定的本地缓存/源码
../.venv-sft/bin/python -m recoveragent.student_backend
../.venv-appworld/bin/python -m recoveragent.student_evaluation collect --config configs/p3b_paired_evaluation_20261023.json --arm raw_base --output ../evaluations_v2/raw_base
# 关闭 Raw 后端后，再加载已验收正式 adapter
../.venv-sft/bin/python -m recoveragent.student_backend --adapter ../checkpoints/run_003/formal
../.venv-appworld/bin/python -m recoveragent.student_evaluation collect --config configs/p3b_paired_evaluation_20261023.json --arm base_sft --output ../evaluations_v2/base_sft
```

本机最终执行：

```bash
PYTHONPATH=src .venv-harness/bin/python -m recoveragent.formal_dataset verify --input data/base_sft_v1 --expected-manifest-sha256 5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c --official-appworld-root data/appworld --report reports/p3b_final_data_integrity_20261023.json
PYTHONPATH=src:. .venv/bin/python scripts/summarize_p3b.py --input runs/p3b_remote_20261023 --output reports/p3b_results_20261023.json
PYTHONPATH=src:. .venv/bin/python -m pytest -q
.venv/bin/ruff check src scripts tests
.venv/bin/ruff format --check src scripts tests
```

## 7. 验收与后续边界

| 验收项 | 状态 | 实际依据 |
| --- | --- | --- |
| 原95条、可信 SHA、划分、Assistant-only mask | PASS | 本机/远程/结束复验，逐字节重新分词 |
| 真实96GB环境、官方工具与状态 | PASS | 实测依赖/权重、写入保存恢复及进程隔离 |
| 短/最长烟测、正式更新、保存/重载 | PASS | 2/1/116步，有限梯度，tensor变化，重载误差0 |
| Raw/Base-SFT真实执行与成本保留 | PASS | 固定6对、相同初态/首输入、全部回执 |
| 完整官方结果与泛化提升 | PARTIAL / 未得到支持 | 每端2个unknown；内部验证/dev没有成功 |
| 私有交接与关机 | PASS（操作证据） | 三包 SHA、逐文件复验、shutdown后SSH不可达；账单未核验 |
| 软件检查 | PASS | 210测试、Ruff check/format；不等同真实模型成绩 |

下一步先诊断两个固定任务的官方运行异常并冻结有效开发评价口径；在已保存 Base-SFT checkpoint 上规划 P3-C 新 train 自主失败与完整边界快照，之后才进入多检查点 Teacher 恢复。当前评价失败含内部验证/dev，**不直接回灌训练**。尚未验证精确 optimizer 续训、更大 dev 性能或任何 Recovery-SFT/GRPO 效果。本轮没有自动进入这些阶段。
