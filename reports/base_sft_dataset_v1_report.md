# Base-SFT Dataset v1：正式 Teacher 数据构建报告

日期：2026-10-21（Asia/Shanghai）；开发分支 `codex/p0-p2-recovery`，起始提交 `5f420909f228945b1643375b9a917b61906a29f4`。

**数据构建/真实验证 PASS；规模目标 PARTIAL；远程环境 BLOCKED；SFT/GRPO NOT_STARTED。** 正式入选 95 条完整成功轨迹，覆盖 75 个任务、29 个场景家族。未加载正式4B权重、未进行Student参数更新，不能据此宣称训练或效果提升通过。

## 1. 范围与来源复核

正式Student固定Qwen/Qwen3-4B；仅官方train。历史数据逐包重验manifest、真实Teacher回执、官方成功证据、原始初态引用、工具/调用ID/参数/反馈、train成员及过滤器。源包不可变；重叠/合并/迁移副本不会重复计轨迹或模型成本。不是简单拼接JSONL。

| 历史重算项目 | 结果 |
| --- | ---: |
| Clean Dataset包数 / 包内条目 | 15 / 78 |
| 独立尝试 / 合格完整轨迹 | 75 / 46 |
| 唯一成功任务 / 家族 | 28 / 15 |
| 32768/4096内完整轨迹 | 45 |
| 全部Assistant目标 / 监督token | 469 / 142,889 |
| 长度合格目标 / 监督token | 449 / 137,144 |
| 初始cap后候选 | 41 |

历史Prompt分布：`{'execution_v4': 14, 'execution_v3': 12, 'execution_v5': 16, 'budget_batched_v2': 2, 'legacy_v1': 2}`；工具模式：`{'dynamic_discovery': 28, 'api_predictor': 18}`。以下条目只是来源包视图，存在重叠，不能相加作为独立数据量。

| 历史来源包 | 包内合格数 | manifest SHA-256 |
| --- | ---: | --- |
| `data/answer_guard_transfer_rehearsal_20261021_a` | 16 | `9efed4033f06bc2705b8e742ac96f6ee9696d06e634d2eb0bf6b997e52684964` |
| `data/p3a_clean_20261020_all` | 1 | `464ea9bef9e794082b266ae6ed53bc5be358eba3e0c74a026325526a70214b30` |
| `data/p3a_empty_handoff_20261020_a` | 0 | `ae594282f2b4a8b6d40232fa84e846c79fc0cdedbb5fa88a235f906878de043a` |
| `data/p3a_transfer_rehearsal_20261020` | 1 | `464ea9bef9e794082b266ae6ed53bc5be358eba3e0c74a026325526a70214b30` |
| `data/unlimited_clean_20261020_a` | 2 | `0a13edb56232ceff8b554f09c2f3885c7e93522423da0c573c3019a3ed994d7f` |
| `data/v4_ab_transfer_rehearsal_20261021_a` | 14 | `336bb99c268fe777e4f61c0e2d25ed23c94271f6a43eafa5ea5c30311b08319a` |
| `runs/a_failure_retry_20261021_a/clean_dataset` | 2 | `2e30bc2e55689e0a0d474dfd76ed23f73492deb1cc0f08c1c5f0428021a2519d` |
| `runs/p3a_teacher_20261020_a/clean_dataset` | 0 | `d979e85fd5032ccfc2bdf269d916bafbcd76455b0e919b1bee96842dadcbcd81` |
| `runs/p3a_teacher_20261020_b/clean_dataset` | 0 | `ff7ba1d9d0b89a73851ade3bef4292f65761ae01b40a7d6de3e3410ad7a9a0d8` |
| `runs/p3a_teacher_20261020_c/clean_dataset` | 1 | `de224e092a28cbad20502e5a3398701f937a544c8180d8ab80bd20d771caf4fe` |
| `runs/scenario_expansion_20261021_a/clean_dataset` | 6 | `ca7e0d3e74567818f21d0a903b49e8a6291829e985fc5818df5cbe82f9d84707` |
| `runs/structured_prompts_20261021_a/clean_dataset` | 4 | `6ce2fff2a2163e2a408a16ec4bd134161a387a191027bee02376ec070904be1e` |
| `runs/tool_modes_20261020_a/clean_dataset` | 1 | `fc1367decb73b4e70d1bbe9cb052ba3c291f721942f33edaa914cdc03dbe4932` |
| `runs/v4_ab_20261021_a/clean_dataset` | 14 | `336bb99c268fe777e4f61c0e2d25ed23c94271f6a43eafa5ea5c30311b08319a` |
| `runs/v5_ab_20261021_a/clean_dataset` | 16 | `9efed4033f06bc2705b8e742ac96f6ee9696d06e634d2eb0bf6b997e52684964` |

## 2. 冻结协议与新增真实采集

主采集B dynamic_discovery、execution_v5、numeric_answer_self_correction_v1；Teacher deepseek-flash，配置声明DeepSeek-V4.1-Flash-1017、temperature=0.2、non-thinking、回复4096。provider别名不是不可变revision；每次真实backend指纹与回执保存在审计中。历史A示范可保留，本轮没有新A/B对比。

采集前固定campaign及源锁，每批20任务（不足则取剩余）、seed=2026102110+批号，最多8批；每任务最多2次新尝试。优先缺成功家族/任务，再补第二条多样性。原始初态、串行独立环境。Actor没有历史失败结论或隐藏答案；控制器依成功覆盖选择下一批，故这是**结果条件化采集，不能作为无偏总体成功率**。失败不替换、不改奖励，全部成本计入。

每轨迹40请求/16384输出/600秒/USD1，每批800请求/327680输出/12000秒/USD20；工具调用不设数量配额，保留131072字节请求上限。没有针对本轮失败修改Prompt、协议或预算。

| 批次 | 尝试 | 有效 | 基础设施错误 | 官方成功 / 合格 | 请求 | 实际工具调用 | 输入 / 输出token | USD费率估算 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| b01 | 20 | 20 | 0 | 13 / 13 | 232 | 650 | 2,418,102 / 69,216 | 0.283937352 |
| b02 | 20 | 18 | 2 | 10 / 10 | 250 | 685 | 2,995,939 / 72,855 | 0.321137364 |
| b03 | 20 | 20 | 0 | 15 / 15 | 262 | 691 | 2,993,459 / 63,554 | 0.270734628 |
| b04 | 20 | 19 | 1 | 10 / 10 | 309 | 787 | 3,934,983 / 88,216 | 0.358311642 |
| b05 | 20 | 13 | 7 | 10 / 10 | 181 | 535 | 2,117,047 / 59,150 | 0.228054804 |
| b06 | 18 | 0 | 18 | 0 / 0 | 26 | 14 | 50,328 / 1,013 | 0.006379152 |

新增共 **118 次尝试、77 个不同任务、30 家族**；其中56个任务不在历史尝试中。官方成功58条，过滤合格58条，涉及56任务/28家族；相对历史新增成功任务47个。

采集停止原因：`provider_http_402_insufficient_balance`；当时仍符合预设次数/cap规则的任务29个。没有为达到120而延长批数、降低质量门槛或复制成功轨迹。

本次因账户余额不足提前停止，最后一批原计划20任务，实际18次、2项未执行，单列未执行清单。余额REST查询成功但无正余额；一次独立16-token上限诊断返回HTTP402，含义依据[DeepSeek官方错误码](https://api-docs.deepseek.com/quick_start/error_codes/)。诊断未执行AppWorld动作、不生成示范。此前传输错误只保存异常类型，不能事后把所有错误都断言为402。

本次条件采集成功/全部尝试 = 58/118 = **49.15%**；成功/有效评价 = 58/90 = 64.44%。28次基础设施错误单列，绝不冒充策略失败奖励。

第二次新尝试共41次，额外成本/请求/工具计数：`{'provider_requests': 355, 'prompt_tokens': 4242714, 'generated_tokens': 99136, 'tool_calls': 970, 'usage_priced_cost': 0.401642142}`。每条的sampling_reason、new_attempt_number及此前成功数见机器审计；coverage ledger绑定先前结果和来源哈希。

筛选原因（可重叠）：`{'invalid_call_format': 13, 'official_not_successful': 60, 'infrastructure_error': 28}`。终止原因（每尝试一类）：`{'invalid_completion': 13, 'completion_claim': 74, 'infrastructure_error': 28, 'budget_exhausted': 3}`。请求大小阻止发生在发送下一请求之前，完整已执行前缀和成本仍保留；有效错误还包括未开放API、回复截断及业务任务未通过。全部失败详情在私有run中。

真实数字格式纠正触发0次，同值格式纠正通过0次，纠正后整任务官方成功0次；分母为0时不得宣称纠正成功率。

## 3. 正式选择、去重与长度

历史+新增唯一合格来源共104条；最终95条/75任务/29家族；整条暂缓5，任务cap reserve 4，精确重复0。高相似配对18对（全长度合格候选中统计，不等于应删除的轨迹数）。

暂缓原因：完整序列超过32768的5条，Assistant目标超过4096的0条（两项可重叠）。它们仍保留官方成功身份，不能写成任务失败；所有原始完整历史在deferred及control来源中。

精确去重忽略调用ID差异但保留完整消息和每回合Schema；高相似判据为业务调用名称/参数序列SequenceMatcher>=0.9，凭据只在指纹计算中忽略。每task最多2条，优先不同簇，再按版本/B/输入成本排序。reserve与deferred保留完整来源和原因，不静默截断、不复制补数。

最终Prompt分布：`{'execution_v4': 13, 'execution_v5': 70, 'execution_v3': 12}`；工具模式：`{'dynamic_discovery': 78, 'api_predictor': 17}`。初始与最终分布可由source_inventory和selection重建。

## 4. 分组与实际训练token

split_seed=2026102120，在全train 90任务/30家族上预先固定24训练家族、6验证家族；只有成功入选者进入实际分区。同任务/场景变体无跨区，官方dev未使用。内部validation部分场景曾参与历史Prompt开发，因此不能称为完全未接触的泛化测试。

| 分区 | 完整轨迹 | 任务 | 成功家族 | Assistant目标 | 监督token | 总输入token | 最长序列 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| train | 82 | 64 | 24 | 925 | 229,776 | 10,008,716 | 31102 |
| validation | 13 | 11 | 5 | 147 | 43,227 | 1,539,591 | 32610 |

全体 **1072个Assistant目标、273,003监督token**；逐回合总输入11,548,307，屏蔽上下文11,275,304（97.636%）。最长完整序列32610、prefix 32272、目标3259。

批量调用目标436/1072（40.67%），无损回读3087个工具调用，Schema变化257次。拒绝回合仅上下文0次。

相邻样本完全相同prefix累计8,039,249 token；逐回合输入/各轨迹最终回合输入合计 = 7.315。这是当前逐回合训练的计算开销定义，不是语义重复率。每样本只监督当前Assistant，其余角色/历史Assistant/拒绝回合/padding为-100。

家族→任务→相似簇→轨迹→回合平衡loss质量，example_weight分区均值为1；先取每样本监督token CE均值再乘权重。长轨迹/重复查询不会只因token数多支配目标，但仍消耗前缀计算。下表同时公开原始token和有效loss质量，零样本家族没有被隐藏。

| scenario_id | 业务Apps | 分区 | 轨迹 | 任务 | Assistant目标 | 监督token | 输入token | 分区内loss质量 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 07b42fd | spotify | train | 3 | 3 | 37 | 2,951 | 276,916 | 4.167% |
| 229360a | spotify | train | 3 | 2 | 35 | 16,232 | 549,523 | 4.167% |
| 22cc237 | simple_note, venmo | train | 4 | 3 | 64 | 11,426 | 760,666 | 4.167% |
| 27e1026 | spotify | train | 2 | 2 | 21 | 5,571 | 297,318 | 4.167% |
| 287e338 | spotify | train | 3 | 2 | 24 | 1,986 | 153,117 | 4.167% |
| 29caf6f | phone, simple_note | train | 5 | 3 | 49 | 6,507 | 432,988 | 4.167% |
| 2a163ab | phone, venmo | train | 3 | 3 | 41 | 10,151 | 464,279 | 4.167% |
| 302c169 | phone | train | 3 | 3 | 27 | 4,022 | 159,798 | 4.167% |
| 34d9492 | file_system | train | 3 | 2 | 46 | 37,035 | 564,983 | 4.167% |
| 3c13f5a | file_system, phone, venmo | train | 2 | 2 | 41 | 4,909 | 498,722 | 4.167% |
| 60d0b5b | venmo | train | 1 | 1 | 9 | 1,112 | 91,638 | 4.167% |
| 6104387 | file_system, spotify | train | 4 | 3 | 58 | 20,804 | 1,055,773 | 4.167% |
| 692c77d | spotify | train | 3 | 3 | 33 | 11,328 | 372,279 | 4.167% |
| 6ea6792 | phone, venmo | validation | 2 | 2 | 31 | 8,691 | 377,723 | 20.000% |
| 76f2c72 | file_system | train | 3 | 3 | 33 | 5,391 | 204,747 | 4.167% |
| 771d8fc | phone | train | 5 | 3 | 44 | 19,739 | 370,824 | 4.167% |
| 7d7fbf6 | file_system | validation | 3 | 3 | 32 | 4,650 | 190,320 | 20.000% |
| 82e2fac | spotify | validation | 4 | 2 | 31 | 9,844 | 242,315 | 20.000% |
| aa8502b | spotify | train | 3 | 3 | 30 | 9,000 | 321,343 | 4.167% |
| afc0fce | phone, venmo | train | 3 | 2 | 49 | 15,857 | 722,108 | 4.167% |
| b0a8eae | simple_note, spotify | train | 3 | 3 | 51 | 7,120 | 665,983 | 4.167% |
| b7a9ee9 | spotify | train | 3 | 3 | 36 | 8,510 | 469,440 | 4.167% |
| c901732 | — | validation | 0 | 0 | 0 | 0 | 0 | 0.000% |
| ccb4494 | spotify | train | 5 | 3 | 39 | 7,610 | 260,849 | 4.167% |
| ce359b5 | spotify | validation | 2 | 2 | 32 | 16,428 | 551,296 | 20.000% |
| cf6abd2 | simple_note | train | 3 | 3 | 29 | 2,940 | 178,868 | 4.167% |
| d0b1f43 | phone, venmo | validation | 2 | 2 | 21 | 3,614 | 177,937 | 20.000% |
| e3d6c94 | spotify | train | 5 | 3 | 62 | 10,711 | 632,049 | 4.167% |
| e7a10f8 | spotify | train | 5 | 3 | 38 | 7,504 | 265,659 | 4.167% |
| e85d92a | spotify | train | 5 | 3 | 29 | 1,360 | 238,846 | 4.167% |

业务App出现轨迹数（多App任务可重叠）：`{'spotify': 53, 'file_system': 15, 'phone': 25, 'simple_note': 15, 'venmo': 17}`。场景家族数依据官方scenario_id，不等于应用数量。

## 5. 全成本与可核验边界

新增采集1260模型请求、3362实际工具调用、3362工具申请；已知输入14,509,858（其中缓存11,255,807）、已知输出354,004，已知token合计 14,863,862。有25个请求缺少完整响应，不能给出精确总token。Teacher控制器累计墙钟约4378.654秒；中断批次使用config文件mtime到外部停止时刻，原monotonic终值不可得。

新增**USD 1.468554942**，按冻结公布费率与实际回执usage估算。包括失败、基础设施错误已完成部分及第二次尝试。未知费用预留7.9872、未知生成预留102400；实际逐请求账单未提供，billed_cost=null，不能将费率估算写成已核实实扣。

本轮已审核包所覆盖的历史+新增独立尝试193次；去除包副本后的累计成本/调用/token：`{'USD': {'provider_requests': 2008, 'tool_calls': 5439, 'prompt_tokens': 22109466, 'cached_prompt_tokens': 16988415, 'generated_tokens': 597212, 'usage_priced_cost': 2.354900190000001, 'unknown_cost_reservation': 7.987199999999996, 'unknown_generation_reservation': 102400}}`。旧A Predictor请求已计入历史费用。AppWorld工具为本地执行，单列调用数量，没有虚构工具单价。本机没有GPU训练耗时，远程资源未接入。

另有1次独立服务诊断模型请求，HTTP402，无usage；其未知预留USD 0.314592000及16输出token单独保留。包括已审核包历史、本轮全部新增和该诊断的总账：`{'provider_requests': 2009, 'tool_calls': 5439, 'prompt_tokens': 22109466, 'cached_prompt_tokens': 16988415, 'generated_tokens': 597212, 'usage_priced_cost': 2.354900190000001, 'unknown_cost_reservation': 8.301791999999995, 'unknown_generation_reservation': 102416}`。这些预留是保守上界，不是已扣款。原Clean manifest的unknown_usage_attempts旧字段未标出缺响应，正式报告改按原始request/response差集统计，不改写原包。

## 6. 完整性、测试和交接

必要软件测试 **155通过**，Ruff与format检查通过（`reports/base_sft_pytest_final.txt` / `base_sft_ruff_final.txt`）。Mock/合成检查只验证逻辑；真实验收来自本轮Teacher+AppWorld与固定Qwen tokenizer。

全源包复验、官方train及公开任务重建、动态工具按已发生发现结果重放、原始请求/响应/成本、Assistant-only/padding mask、Student工具调用解析回读通过。原始消息与真实工具反馈保持对应；control中的评价/来源/状态引用从model_input排除。

正式包再分词与token JSONL逐字节一致、本机独立归档导入通过；文件篡改、错误可信SHA、mask泄漏及控制消息注入均被拒绝。P0–P2、v3/v5源锁、collection锁与原15包manifest不变。详见final verify/retokenize/import/negative及source_checks证据。

私有数据目录 `data/base_sft_v1`；归档 `data/base_sft_v1.tar.gz`，37,773,083字节。manifest SHA：`5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`；归档SHA：`68231f625b876db259c5085d08e70c77604ffbaaf6b0809b807852e2bfc3b9ed`。完整逐文件摘要在私有manifest，可信摘要在已提交的release JSON。原始数据、任务、评价、凭据和权重均不上传GitHub。

远程导入/再分词/依赖/配置/验收说明见`docs/REMOTE_BASE_SFT.md`。准备BF16+LoRA、2步短样本及1步最长样本烟测，参数更新、非零有限梯度、保存/重载tensor SHA和logits检查均有入口；本轮只做CPU微型Qwen3架构loss/梯度等价测试，0 optimizer step，不能算正式SFT。

## 7. 实际命令、错误与下一步

实际执行：

```bash
PYTHONPATH=src .venv/bin/python -m recoveragent.dataset_sources scan --appworld-root data/appworld --output .cache/base_sft_existing_20261021 --report reports/base_sft_existing_inventory.json
PYTHONPATH=src .venv/bin/python scripts/collect_base_sft.py --campaign configs/base_sft_v1_campaign.json --config .cache/v5_ab_20261021_a.json --credentials-file .env
PYTHONPATH=src .venv/bin/python scripts/seal_interrupted_collection.py --campaign configs/base_sft_v1_campaign.json --run runs/base_sft_v1_20261021_b06
HF_HOME=$PWD/.cache/huggingface HF_HUB_OFFLINE=1 PYTHONPATH=src .venv-inference/bin/python -m recoveragent.formal_dataset build --input .cache/base_sft_v1_20261021_result.json --output data/base_sft_v1 --report reports/base_sft_dataset_v1_build.json
PYTHONPATH=src .venv/bin/python -m recoveragent.formal_dataset verify --input data/base_sft_v1 --expected-manifest-sha256 5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c --official-appworld-root data/appworld --report reports/base_sft_dataset_v1_verify.json
PYTHONPATH=src:. .venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
```

日志：`/tmp/recoveragent-base-sft-campaign.log`、`.cache/base_sft_v1_20261021_bNN_{collect,quality,audit}.log`及`.cache/base_sft_dataset_v1_*.log`。真实AppWorld采集沿用已授权的非受限运行环境；公开只提交统计/哈希。初次打包预演缺HF_HOME、随后未设HF_HUB_OFFLINE导致缓存/metadata查询错误，改为本地缓存离线模式后通过；失败预演目录原样保留。旧拒绝响应审计只覆盖JSON解析错误，按冻结预执行逻辑扩展至schema可见性/参数/调用边界，未修改Actor或历史奖励。

尚缺：连接并核实远程RTX PRO 6000 96GB、安装匹配依赖（本机PEFT/Accelerate未安装）、安全传输和远程再分词、固定4B权重与真实后端接入、输入+生成共同窗口限制、真实短/长烟测及checkpoint重载、Raw Base/Base-SFT-only的固定dev评价。内部validation不能替代官方dev。正式长SFT和GRPO本轮没有启动；未达到规模项如实保留，不能降低质量门槛补齐。
