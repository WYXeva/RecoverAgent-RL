# Full732 Stage 3A：A244续采与串行交付报告

生成时间：2026-10-29T10:36:20+08:00。分支`codex/p0-p2-recovery`，开发基线`65ec3548a575687fd9520dbde06d56e3645a4e8f`。

**实际覆盖244/244，主链官方成功110，合格候选102，未执行0。** 有效官方失败40，原始未知94。此处未知没有转换成失败；没有训练或报告训练效果。完整逐任务记录见[JSON](full732_a244_collection_20261028.json)，错误汇总见[CSV](full732_a244_errors_20261028.csv)。此前报告、数据及源码锁全部保留。

## 范围和运行变更

- 冻结Full732的A244/244场景，原train30、dev19、test_normal56、test_challenge139，A/B/C清单、seed和执行顺序不变。只做独立私有自定义研究，不是官方排行榜成绩；未运行B/C，也未读取或复用旧95条Teacher。
- Teacher请求`deepseek-v4.1-flash`，用户确认响应别名`deepseek-v4-1-flash`；上游不可变权重revision未获服务方证明。Teacher允许thinking，真实reasoning只留审计和Teacher历史续传，Student消息不含此字段。
- execution_v6原文字节、dynamic_discovery、Schema/可见性/ID检查、两次Harness纠错和AnswerGuard保持不变。每任务80请求/16384生成token/600秒，单回复4096，工具次数不限。首任务原4次接入诊断仍纳入预算与成本；没有用新配置重复已领取任务。
- 先继承本轮原5项，3个独立进程并行至30项；新控制器以2并发继续，用户要求串行后仅停止派发，在途任务正常完成到35项。随后单worker执行未尝试209项。不能宣称全轮一直串行，也没有证明并发造成所有错误。
- 单worker第4项返回ali/deepseek-v4.1-flash，旧名称检查停止于39项。用户明确授权响应名称只审计，新增user_asserted_relay_aliases_v1/serial_relay锁继续余205项；固定请求和endpoint，所有返回名原样存储，不按名称字符串差异停止，仍要求合法响应/ID/用量。原39项不重收，旧结果不回填。
- 用户要求所有任务的错误归档后续处理；单项传输/协议/业务/评价/超长及完整回执的超限不阻止其他任务，不自动重试。401/402/403、状态污染及连续worker环境故障仍停止新派发；响应名称不再作为当前relay的停止条件。未知费用完整预留，未伪造0成本。
- 两次任务传输错误曾按完成顺序触发旧并行熔断，但停止后仍有15份完整provider响应。原stop保持；v2调度版本单独冻结。用户串行停止的KeyboardInterrupt属于调度控制，不计作Teacher失败。

## 真实结果与分母

| 指标 | 实际值 |
|---|---:|
| 计划任务/场景 | 244 / 244 |
| 已尝试唯一任务/场景 | 244 / 244 |
| 原主链官方成功 | 110 |
| 原主链有效官方失败 | 40 |
| 原主链未知 | 94 |
| 官方成功且过滤/独立恢复通过（含超长） | 110 |
| 当前可训练完整轨迹/任务/场景 | 102 / 102 / 102 |
| 超长暂缓 | 8 |
| 未执行 | 0 |
| 合格Assistant监督样本 | 1588 |
| 合格Assistant监督token | 244187 |
| 最长合格完整序列 | 32693 |

主链已确认成功占已尝试分母为110/244（45.08%）；未知占分母但不被自动定为失败。这是已确认成功比例，不是排除未知后的无偏总体成功率。本轮NOT_RUN=0，244项首轮尝试与证据归档已完成；这不等于244条成功示范。成功采样没有复制任务或按成绩换题。

停机后对官方自动保存状态的独立诊断另计：{"valid_success": 0, "valid_failure": 84, "unknown": 10, "original_results_unchanged": true}。这不回填原trajectory/reward，不把不完整或缺少项目terminal的轨迹当作合格恢复检查点。成功候选必须具有原官方成功、真实合法完整轨迹、独立终态评分一致及格式/长度验收。

### 按实际调度来源

| campaign | 已尝试 | 成功 | 有效失败 | 未知 | 合格 |
|---|---:|---:|---:|---:|---:|
| full732_a244_live_native_20261028 | 5 | 4 | 0 | 1 | 3 |
| full732_a244_parallel_20261028 | 25 | 7 | 6 | 12 | 6 |
| full732_a244_parallel_v2_20261028 | 5 | 4 | 1 | 0 | 3 |
| full732_a244_serial_20261028 | 4 | 2 | 0 | 2 | 2 |
| full732_a244_serial_relay_20261028 | 12 | 3 | 0 | 9 | 3 |
| full732_a244_serial_diagnostics_20261028 | 193 | 90 | 33 | 70 | 85 |

### 按原split覆盖

| 原split | 尝试 | 官方成功 | 合格 |
|---|---:|---:|---:|
| train | 30 | 18 | 18 |
| dev | 19 | 7 | 6 |
| test_normal | 56 | 32 | 30 |
| test_challenge | 139 | 53 | 48 |

## 错误登记与边界

同一任务可能具有多种错误；成功纠正的协议或业务API错误不等于整任务失败。所有原始输出、实际调用/返回、未知意图和控制侧评分均保留。错误统一收集，本轮没有按错误自动重试付费采样。

| 错误类别 | 涉及任务数 |
|---|---:|
| business_api_error | 51 |
| evaluation_runtime | 10 |
| execution_budget_exhausted | 11 |
| official_task_failure | 40 |
| protocol_correction | 102 |
| provider_http_error | 21 |
| provider_model_identity | 1 |
| provider_token_bound | 49 |
| provider_usage_unknown | 35 |
| training_format_deferred | 8 |
| worker_environment | 3 |

共有176项带错误记录，其中42项最后官方成功。首个串行29a7b7e_2仍出现Provider超限：请求247、回执963/finish_reason=tool_calls，未执行该超额回复。证明此类问题不只在并发时出现，不能一律归因并发。真实回执用量已计入审计；原严格预算结果不被反写为成功。

补充HTTP诊断：第42–49项共8次HTTP400与历史Assistant缺少reasoning字段相关；旧错误体未保存，不能认定因果。独立缺失字段/显式空字段两例合成请求均200，共2请求、706输入、31输出token，估价0.000173736，已纳总预算；不计Teacher示范。51项后新serial_diagnostics仅增加错误体有限SHA和关键词布尔值，不保存原文或凭据，不补写推理。实际后续错误类别见逐任务记录。

94个主链未知的互斥主因：Provider输出超限49、回执/用量未知35、评价异常7、worker环境异常3。多标签表中的evaluation_runtime=10还包含部分旁路诊断异常，不能直接相加到94。HTTP共21项：400为12项、502为9项；新增安全诊断4项命中工具消息关键词，不能据此声称根因已经修复。

## Thinking清理与数据资格

- 原始provider回执、reasoning、工具执行和评价保存在私有源目录；`successful_clean.jsonl`只保留结构化正文/工具调用和真实工具返回。
- 042a9fc_1已无reasoning_content字段及显式think块，non-thinking实际最长35797，9个回合超过32768；最长目标288。它依然deferred，不能通过再次宣称删除thinking放行。其余轨迹使用同一真实Qwen tokenizer逐条验收，整轨迹不静默截断。
- Qwen/Qwen3-4B revision `1cfa9a7208912126459214e8b04321603b3df60c`，non-thinking原模板。只监督当回合真实Teacher Assistant；历史Assistant/system/user/tool/纠错反馈/padding屏蔽，拒绝输出只审计。每回合实际可见Schema、调用ID/参数与Student解析器核验保持。
- 最终独立审计110份成功恢复回执：源worker与restore PID均不同，每份10项检查全部为True，包括行/追踪、model_hashes、文件、原始官方完整结果、评分不改变状态等；审计路径`runs/full732_a244_parallel_20261028_setup/final_dataset_integrity.json`。
- 完整Clean成功含deferred；只有`trainable_candidates.jsonl`内102条通过当前32768上下文/4096目标检查。数据编码与mask通过不代表完成SFT。

私有数据目录：`data/full732_a244_fresh_20261028_round1`。manifest SHA-256：

```text
53dd9f3ae950346cd2c4428648085475d3f3f066543e2c39f41e2532746ae4bf
```

文件SHA及每任务source/review/trajectory/原证据manifest引用见JSON。原始任务、隐藏评价内容、凭据、推理回执和数据正文不提交公开GitHub。

## 用量和费用

原始回执返回名称及次数：`{"deepseek-v4.1-flash": 3759, "deepseek-v4-1-flash": 58, "ali/deepseek-v4.1-flash": 81, "None": 6, "cb/deepseek-v4.1-flash": 4, "alicn/deepseek-v4.1-flash": 1, "deepseek-v4.1-flash-1017": 4, "deepseek-ai/deepseek-v4.1-flash": 8}`。名称不同按用户渠道声明处理，独立权重同一性未验证；None表示6份已保存响应缺少model字段，不是新增的模型名称，也未被伪装为合法响应。

| 指标 | 原始响应/执行记录 |
|---|---:|
| 本轮任务模型请求（含继承） | 3949 |
| 另计接入/合成协议诊断请求 | 6 |
| 合计请求 | 3955 |
| 任务实际工具结果 | 6349 |
| 加接入诊断工具 | 6354 |
| 已知response输入token（含全部诊断） | 57845122 |
| 已知response输出token（含全部诊断） | 1992837 |
| 任务费率估价（UI标CNY，不含诊断） | 9.960050820 |
| 仍未知费用的保守预留 | 11.178314400 |

缺失回执的token不伪造为已知0，上表已知token是下界。任务模型请求包含可能已计费但回执未知的调用；不自动重发。公开model-plaza费率乘用户0.3倍率：输入/缓存/输出每百万0.3/0.006/1.2，UI标CNY；账单API标USD。保留币种差异，不自行汇率换算，估价不冒充实际人民币扣费。原4次诊断可核实账单合计0.0049605/API USD。

本地3955次已保留请求/预留与账单3928次计数不同；回执缺失、未知扣费及旧名称拦截的用量不进行猜测性回填。

最终只读聚合账单摘要：`{"url": "https://znbcode.com/v1/usage", "status": 200, "sha256": "a66681f703520318552fc5fd49004822e9ecc994a68e5d62e0a15f054c73bb42", "unit": "USD", "total": {"requests": 3928, "input_tokens": 24586662, "output_tokens": 2008775, "cache_read_tokens": 33276380, "total_tokens": 59871817, "actual_cost": 9.98618688}}`。无法从聚合接口准确归因到单个任务的扣费不伪称精确；账单与response token差异保留。完整账单原件只留本地。

完整记录业务执行耗时的任务数244，任务耗时合计41575.682秒（不含缺失项、独立评分/tokenizer及接入诊断；并行任务求和不是墙钟总时长）。最后193项串行调度（含独立评分及tokenizer）墙钟10小时07分27秒，进程最大RSS约1.42GiB；来源`serial_diagnostics_time.txt`。其他调度墙钟/CPU计时另见setup的日志及time.txt，不伪造缺失耗时。

## 工程和验收

- 新`parallel_teacher.py`复用fresh_teacher/P3-A/Adapter/评分/快照/tokenizer，线程只调度隔离进程。claim先写、SHA继承已有review、禁止自动重跑，完整在途预算预留。串行只改workers=1及继承清单，实际模型名检查按用户声明独立版本化，原执行预算/工具与评分机制不变。
- 新只读`export_fresh_parallel.py`拒绝正在运行的campaign，逐项核验源manifest与官方结果，检查合法Clean消息和候选一致，统一输出成功、暂缓和错误登记，不简单拼历史JSONL。
- 必要软件验证：**501 pytest PASS**；Ruff check PASS，140文件format检查PASS。含thinking不泄漏、超限真实usage/未知预留、401/402/403熔断、局部失败继续、并行隔离、串行不重叠、断点不重付费、源SHA篡改拒绝、导出运行锁和未知结果分类。
- 真实Teacher采集/工具/评分与成功终态独立进程复验属于真实环境证据；模拟调度测试不计新增Teacher数据。Qwen仅调用tokenizer，无模型权重推理/GPU/训练。
- 原execution_v6 SHA `352d106b7b052cc1a2b5c249e19c6a512e989355fc60f6495034f32db24a4400`；原Full732划分SHA `0466cec29d9ad770a1cfeacb4c93945e5fcbcad68b9e980c5264d447418121a2`。历史源码锁/旧报告/权重/95条数据未修改。
- 本轮最后的串行调度无全局故障停止，正常跑到244/244。此前35/39/51项版本切换及旧global_stop原件均保留。本报告不会将未运行项、未知评价或格式暂缓伪称为成功。后续优先集中审查错误/Provider上限与未知计费，不自动第二轮重采或降低质量门槛；训练需另行授权。

## 复现命令和证据

```bash
PYTHONPATH=src CUDA_VISIBLE_DEVICES='' HF_HUB_OFFLINE=1 \
  HF_HOME="$PWD/.cache/huggingface" OMP_NUM_THREADS=1 \
  .venv-harness/bin/python -m recoveragent.parallel_teacher \
  --config configs/full732_a244_teacher_serial_diagnostics_20261028.json \
  --output runs/full732_a244_serial_diagnostics_20261028 --credentials-file .env
PYTHONPATH=src .venv/bin/python scripts/export_fresh_parallel.py \
  --config configs/full732_a244_teacher_serial_diagnostics_20261028.json \
  --campaign runs/full732_a244_serial_diagnostics_20261028 \
  --destination data/full732_a244_fresh_20261028_round1 \
  --public-json reports/full732_a244_collection_20261028.json
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/ruff check --no-cache .
.venv/bin/ruff format --check .
```

同名数据包已存在时导出拒绝覆盖；旧global_stop目录不被自动恢复。私有setup下保存各次日志、源码副本manifest、501测试日志、thinking长度审计和串行请求记录；原3并发、2并发及首5项目录按source引用保留。

## 逐任务结果

| task_id | 原split | status | 官方成功 | 合格 | 请求 | 工具 | 错误类别 |
|---|---|---|---|---|---:|---:|---|
| 07b42fd_2 | train | SUCCEEDED | True | True | 10 | 20 | — |
| 024c982_3 | test_normal | SUCCEEDED | True | True | 6 | 10 | — |
| 042a9fc_1 | test_normal | DEFERRED | True | False | 29 | 34 | protocol_correction, training_format_deferred |
| 07bb666_1 | test_challenge | SUCCEEDED | True | True | 12 | 25 | — |
| 099174d_3 | test_challenge | UNKNOWN | None | False | 30 | 46 | business_api_error, protocol_correction, provider_token_bound |
| 09ac073_1 | test_challenge | UNKNOWN | None | False | 9 | 17 | provider_token_bound |
| 09b0ee6_2 | test_normal | FAILED | False | False | 34 | 36 | execution_budget_exhausted, official_task_failure, protocol_correction |
| 0a9d82a_3 | test_normal | SUCCEEDED | True | True | 11 | 35 | — |
| 0d01c76_3 | test_normal | UNKNOWN | None | False | 15 | 47 | protocol_correction, provider_token_bound |
| 0d22252_2 | test_challenge | FAILED | False | False | 12 | 17 | official_task_failure, protocol_correction |
| 0d8a4ee_3 | dev | DEFERRED | True | False | 12 | 37 | training_format_deferred |
| 0de03ad_1 | test_challenge | UNKNOWN | None | False | 17 | 25 | provider_http_error, provider_usage_unknown |
| 0de03ea_3 | test_normal | FAILED | False | False | 8 | 13 | official_task_failure |
| 1150ed6_2 | test_normal | SUCCEEDED | True | True | 12 | 14 | protocol_correction |
| 13547f5_2 | test_normal | SUCCEEDED | True | True | 8 | 10 | — |
| 143527e_2 | test_challenge | SUCCEEDED | True | True | 14 | 21 | — |
| 166f4ff_1 | test_normal | UNKNOWN | None | False | 7 | 8 | evaluation_runtime |
| 16be9ce_2 | test_challenge | UNKNOWN | None | False | 8 | 20 | evaluation_runtime |
| 18670a5_3 | test_challenge | SUCCEEDED | True | True | 14 | 30 | — |
| 1a79e37_1 | test_challenge | UNKNOWN | None | False | 35 | 38 | business_api_error, protocol_correction, provider_token_bound |
| 1b2bae9_3 | test_challenge | FAILED | False | False | 31 | 40 | business_api_error, official_task_failure, protocol_correction |
| 1b988ac_1 | test_challenge | UNKNOWN | None | False | 9 | 21 | provider_token_bound |
| 1c4bd27_3 | test_challenge | SUCCEEDED | True | True | 12 | 22 | protocol_correction |
| 20c1328_2 | test_challenge | FAILED | False | False | 9 | 20 | official_task_failure, protocol_correction |
| 21abae1_3 | test_normal | UNKNOWN | None | False | 6 | 7 | evaluation_runtime, provider_token_bound |
| 229360a_3 | train | UNKNOWN | None | False | 11 | 25 | provider_usage_unknown |
| 22cc237_1 | train | FAILED | False | False | 9 | 10 | official_task_failure, protocol_correction |
| 23cf851_2 | dev | UNKNOWN | None | False | 10 | 10 | protocol_correction, provider_usage_unknown |
| 23d431c_3 | test_challenge | UNKNOWN | None | False | 18 | 30 | business_api_error, provider_token_bound |
| 245cb43_1 | test_challenge | UNKNOWN | None | False | 12 | 22 | provider_usage_unknown |
| 258796c_1 | test_challenge | FAILED | False | False | 6 | 13 | official_task_failure |
| 270f1ff_1 | test_normal | SUCCEEDED | True | True | 12 | 23 | — |
| 277d81d_2 | test_challenge | DEFERRED | True | False | 11 | 45 | business_api_error, training_format_deferred |
| 27e1026_2 | train | SUCCEEDED | True | True | 11 | 37 | — |
| 287e338_1 | train | SUCCEEDED | True | True | 6 | 8 | — |
| 29a7b7e_2 | test_normal | UNKNOWN | None | False | 23 | 23 | protocol_correction, provider_token_bound |
| 29caf6f_3 | train | SUCCEEDED | True | True | 14 | 22 | — |
| 2a163ab_3 | train | SUCCEEDED | True | True | 18 | 35 | — |
| 2c544f9_1 | test_normal | UNKNOWN | None | False | 25 | 29 | business_api_error, protocol_correction, provider_model_identity, provider_usage_unknown |
| 2d9f728_2 | test_normal | SUCCEEDED | True | True | 30 | 30 | — |
| 2e9b91e_3 | test_challenge | SUCCEEDED | True | True | 17 | 18 | — |
| 2ff4dfb_2 | test_challenge | UNKNOWN | None | False | 2 | 2 | provider_http_error, provider_usage_unknown |
| 302c169_3 | train | UNKNOWN | None | False | 3 | 4 | provider_http_error, provider_usage_unknown |
| 30e8586_2 | test_challenge | UNKNOWN | None | False | 2 | 3 | provider_http_error, provider_usage_unknown |
| 31dc501_1 | test_normal | UNKNOWN | None | False | 3 | 3 | provider_http_error, provider_usage_unknown |
| 321ec38_2 | test_challenge | UNKNOWN | None | False | 2 | 2 | provider_http_error, provider_usage_unknown |
| 325d6ec_2 | test_normal | UNKNOWN | None | False | 3 | 5 | provider_http_error, provider_usage_unknown |
| 32616b5_2 | test_normal | UNKNOWN | None | False | 3 | 2 | protocol_correction, provider_http_error, provider_usage_unknown |
| 33e202d_3 | test_challenge | UNKNOWN | None | False | 2 | 2 | evaluation_runtime, provider_http_error, provider_usage_unknown |
| 34d9492_1 | train | UNKNOWN | None | False | 19 | 29 | protocol_correction, provider_token_bound |
| 3650990_1 | test_challenge | SUCCEEDED | True | True | 9 | 9 | — |
| 365e0a3_2 | test_challenge | UNKNOWN | None | False | 4 | 4 | provider_http_error, provider_usage_unknown |
| 36705ae_3 | test_challenge | UNKNOWN | None | False | 2 | 2 | provider_http_error, provider_usage_unknown |
| 37a8675_3 | dev | UNKNOWN | None | False | 3 | 5 | provider_http_error, provider_usage_unknown |
| 383a053_2 | test_challenge | UNKNOWN | None | False | 2 | 3 | provider_http_error, provider_usage_unknown |
| 383cbac_2 | dev | FAILED | False | False | 9 | 8 | official_task_failure, protocol_correction |
| 396c5a2_1 | dev | UNKNOWN | None | False | 3 | 6 | provider_http_error, provider_usage_unknown |
| 3aa1a22_3 | test_normal | SUCCEEDED | True | True | 21 | 29 | — |
| 3ab5b8b_1 | dev | UNKNOWN | None | False | 9 | 18 | provider_token_bound |
| 3b8fb7a_3 | test_normal | SUCCEEDED | True | True | 20 | 38 | — |
| 3c13f5a_1 | train | FAILED | False | False | 16 | 21 | official_task_failure, protocol_correction |
| 3d9a636_1 | test_normal | UNKNOWN | None | False | 10 | 15 | provider_token_bound |
| 3f3c139_3 | test_challenge | SUCCEEDED | True | True | 11 | 29 | — |
| 3fcc458_2 | test_challenge | UNKNOWN | None | False | 45 | 77 | protocol_correction, provider_token_bound |
| 4242c97_2 | test_challenge | FAILED | False | False | 21 | 29 | business_api_error, official_task_failure, protocol_correction |
| 425a494_2 | test_normal | SUCCEEDED | True | True | 11 | 31 | — |
| 432dc7a_3 | test_challenge | UNKNOWN | None | False | 8 | 10 | protocol_correction, provider_http_error, provider_usage_unknown |
| 4441ee9_1 | test_challenge | UNKNOWN | None | False | 1 | 0 | provider_usage_unknown |
| 476b213_2 | test_challenge | SUCCEEDED | True | True | 6 | 8 | — |
| 4815c06_3 | test_challenge | UNKNOWN | None | False | 24 | 37 | business_api_error, provider_token_bound |
| 4ac4a8d_3 | test_challenge | FAILED | False | False | 29 | 35 | official_task_failure, protocol_correction |
| 4d12842_3 | test_challenge | SUCCEEDED | True | True | 12 | 32 | — |
| 4ec8de5_1 | dev | UNKNOWN | None | False | 9 | 38 | provider_token_bound |
| 4fab96f_1 | dev | SUCCEEDED | True | True | 18 | 33 | business_api_error, protocol_correction |
| 50e1ac9_2 | dev | UNKNOWN | None | False | 29 | 47 | provider_token_bound |
| 522e5e5_2 | test_normal | UNKNOWN | None | False | 7 | 9 | provider_http_error, provider_usage_unknown |
| 5238afc_1 | test_challenge | FAILED | False | False | 33 | 33 | business_api_error, execution_budget_exhausted, official_task_failure, protocol_correction |
| 530b157_3 | dev | SUCCEEDED | True | True | 16 | 26 | protocol_correction |
| 552869a_1 | test_normal | FAILED | False | False | 10 | 10 | official_task_failure |
| 57c3486_1 | dev | UNKNOWN | None | False | 12 | 23 | provider_usage_unknown |
| 5800354_3 | test_challenge | DEFERRED | True | False | 41 | 43 | training_format_deferred |
| 59bcfc8_3 | test_challenge | UNKNOWN | None | False | 16 | 15 | protocol_correction, provider_http_error, provider_usage_unknown |
| 59fae45_2 | test_normal | SUCCEEDED | True | True | 13 | 73 | protocol_correction |
| 5a83b05_3 | test_normal | SUCCEEDED | True | True | 19 | 51 | protocol_correction |
| 5e27cd7_3 | test_challenge | SUCCEEDED | True | True | 13 | 18 | business_api_error, protocol_correction |
| 60d0b5b_3 | train | FAILED | False | False | 11 | 11 | official_task_failure |
| 6104387_2 | train | UNKNOWN | None | False | 10 | 41 | provider_token_bound |
| 6171bbc_2 | dev | UNKNOWN | None | False | 12 | 80 | provider_usage_unknown |
| 634f342_3 | test_normal | SUCCEEDED | True | True | 19 | 86 | — |
| 6474048_2 | test_challenge | UNKNOWN | None | False | 7 | 6 | provider_http_error, provider_usage_unknown |
| 652485c_1 | test_normal | UNKNOWN | None | False | 12 | 15 | provider_usage_unknown |
| 6588a51_3 | test_challenge | SUCCEEDED | True | True | 13 | 12 | protocol_correction |
| 66b7899_1 | test_challenge | FAILED | False | False | 10 | 13 | official_task_failure, protocol_correction |
| 68ee2c9_1 | dev | UNKNOWN | None | False | 18 | 16 | protocol_correction, provider_token_bound |
| 690d51b_2 | test_challenge | FAILED | False | False | 28 | 26 | business_api_error, execution_budget_exhausted, official_task_failure, protocol_correction |
| 692c77d_2 | train | SUCCEEDED | True | True | 10 | 36 | — |
| 69ba40f_1 | test_challenge | SUCCEEDED | True | True | 29 | 29 | protocol_correction |
| 6a5e690_2 | test_challenge | UNKNOWN | None | False | 6 | 5 | provider_usage_unknown |
| 6b6ca61_2 | test_normal | FAILED | False | False | 33 | 51 | business_api_error, execution_budget_exhausted, official_task_failure, protocol_correction |
| 6bdbc26_1 | dev | SUCCEEDED | True | True | 10 | 9 | — |
| 6c2c621_2 | dev | FAILED | False | False | 13 | 62 | execution_budget_exhausted, official_task_failure, protocol_correction |
| 6d59d90_1 | test_challenge | FAILED | False | False | 7 | 7 | official_task_failure, protocol_correction |
| 6ea6792_1 | train | SUCCEEDED | True | True | 16 | 37 | protocol_correction |
| 6f4b9a5_1 | test_normal | DEFERRED | True | False | 17 | 25 | training_format_deferred |
| 7238049_2 | test_challenge | SUCCEEDED | True | True | 32 | 32 | business_api_error, protocol_correction |
| 7264edc_2 | test_challenge | SUCCEEDED | True | True | 12 | 20 | — |
| 7434096_2 | test_challenge | SUCCEEDED | True | True | 14 | 21 | business_api_error |
| 7574325_1 | test_challenge | SUCCEEDED | True | True | 13 | 14 | — |
| 76f2c72_1 | train | UNKNOWN | None | False | 35 | 34 | business_api_error, evaluation_runtime, protocol_correction |
| 771d8fc_2 | train | FAILED | False | False | 32 | 34 | official_task_failure |
| 77bcb81_2 | test_challenge | UNKNOWN | None | False | 24 | 40 | business_api_error, provider_token_bound |
| 7847649_2 | test_normal | SUCCEEDED | True | True | 8 | 9 | — |
| 7b97e6d_3 | test_challenge | UNKNOWN | None | False | 23 | 29 | business_api_error, protocol_correction, provider_token_bound |
| 7bdaf23_3 | test_challenge | SUCCEEDED | True | True | 13 | 22 | protocol_correction |
| 7d26579_3 | test_challenge | SUCCEEDED | True | True | 11 | 13 | — |
| 7d7fbf6_1 | train | SUCCEEDED | True | True | 10 | 16 | — |
| 7e1be84_3 | test_challenge | FAILED | False | False | 30 | 33 | business_api_error, official_task_failure, protocol_correction |
| 80acbaf_3 | test_challenge | FAILED | False | False | 19 | 24 | business_api_error, official_task_failure, protocol_correction |
| 81be677_2 | test_challenge | SUCCEEDED | True | True | 8 | 10 | — |
| 82d2e01_3 | test_challenge | SUCCEEDED | True | True | 20 | 30 | — |
| 82e2fac_2 | train | SUCCEEDED | True | True | 29 | 29 | — |
| 83a7951_3 | test_normal | UNKNOWN | None | False | 18 | 29 | protocol_correction, provider_token_bound |
| 8749218_3 | test_normal | SUCCEEDED | True | True | 34 | 45 | protocol_correction |
| 8a13317_2 | test_challenge | SUCCEEDED | True | True | 24 | 26 | protocol_correction |
| 8ce6779_1 | test_normal | SUCCEEDED | True | True | 19 | 41 | — |
| 8d42650_1 | test_challenge | UNKNOWN | None | False | 21 | 46 | protocol_correction, provider_token_bound |
| 8f79e35_2 | test_challenge | SUCCEEDED | True | True | 35 | 36 | business_api_error |
| 9016950_2 | test_normal | SUCCEEDED | True | True | 29 | 31 | — |
| 906f2c7_3 | test_challenge | SUCCEEDED | True | True | 18 | 22 | protocol_correction |
| 90adc3f_3 | test_normal | SUCCEEDED | True | True | 17 | 20 | — |
| 9126bf0_1 | test_challenge | SUCCEEDED | True | True | 18 | 23 | — |
| 92fe421_2 | test_challenge | UNKNOWN | None | False | 16 | 29 | provider_token_bound |
| 953b296_3 | test_challenge | UNKNOWN | None | False | 5 | 4 | provider_usage_unknown |
| 96bf160_2 | test_challenge | SUCCEEDED | True | True | 16 | 19 | — |
| 986aa4e_3 | test_normal | UNKNOWN | None | False | 1 | 0 | provider_http_error, provider_usage_unknown |
| 9871968_3 | test_challenge | UNKNOWN | None | False | 29 | 34 | business_api_error, protocol_correction, provider_token_bound |
| 988af8e_1 | test_challenge | UNKNOWN | None | False | 30 | 33 | protocol_correction, worker_environment |
| 98d2608_3 | test_challenge | SUCCEEDED | True | True | 15 | 23 | protocol_correction |
| 998908e_1 | test_challenge | UNKNOWN | None | False | 29 | 30 | evaluation_runtime, protocol_correction |
| 9a16324_2 | test_challenge | UNKNOWN | None | False | 26 | 60 | protocol_correction, provider_token_bound |
| 9aae7da_1 | test_challenge | SUCCEEDED | True | True | 7 | 9 | — |
| 9b2dc64_1 | test_challenge | UNKNOWN | None | False | 25 | 35 | business_api_error, provider_token_bound |
| 9bf2c8a_2 | test_challenge | FAILED | False | False | 8 | 5 | official_task_failure, protocol_correction |
| 9dabbc9_3 | test_normal | UNKNOWN | None | False | 15 | 16 | protocol_correction, provider_token_bound |
| 9ef034e_2 | test_challenge | UNKNOWN | None | False | 30 | 34 | business_api_error, protocol_correction, provider_token_bound |
| 9ef798c_1 | test_normal | UNKNOWN | None | False | 10 | 15 | business_api_error, protocol_correction, provider_usage_unknown |
| a132254_1 | test_challenge | SUCCEEDED | True | True | 11 | 12 | — |
| a1d3dfd_2 | test_challenge | UNKNOWN | None | False | 8 | 7 | provider_usage_unknown |
| a30375d_1 | test_normal | SUCCEEDED | True | True | 7 | 11 | — |
| a3ba388_2 | test_challenge | SUCCEEDED | True | True | 16 | 26 | business_api_error |
| a53a8fd_3 | test_challenge | UNKNOWN | None | False | 9 | 15 | protocol_correction, provider_token_bound |
| a5b0084_3 | test_challenge | FAILED | False | False | 19 | 27 | official_task_failure |
| a676f2a_3 | test_challenge | UNKNOWN | None | False | 8 | 16 | provider_token_bound |
| a7179fa_3 | test_challenge | SUCCEEDED | True | True | 25 | 30 | — |
| a8f302f_3 | test_challenge | FAILED | False | False | 16 | 39 | official_task_failure, protocol_correction |
| a97e35d_3 | test_challenge | UNKNOWN | None | False | 22 | 42 | business_api_error, provider_token_bound |
| aa8502b_3 | train | SUCCEEDED | True | True | 16 | 28 | — |
| aaa25aa_1 | test_challenge | SUCCEEDED | True | True | 31 | 37 | business_api_error, protocol_correction |
| ac62177_2 | test_challenge | SUCCEEDED | True | True | 19 | 47 | business_api_error |
| adb1060_3 | test_challenge | SUCCEEDED | True | True | 23 | 23 | — |
| af84964_1 | test_challenge | UNKNOWN | None | False | 17 | 29 | protocol_correction, provider_token_bound |
| afc0fce_2 | train | FAILED | False | False | 15 | 34 | execution_budget_exhausted, official_task_failure, protocol_correction |
| afc4005_1 | test_normal | SUCCEEDED | True | True | 8 | 9 | — |
| b05f69a_1 | test_challenge | UNKNOWN | None | False | 19 | 21 | provider_token_bound |
| b08d5ef_3 | test_challenge | UNKNOWN | None | False | 24 | 42 | business_api_error, provider_token_bound |
| b0934aa_3 | test_challenge | UNKNOWN | None | False | 27 | 43 | business_api_error, provider_token_bound |
| b0a8eae_1 | train | SUCCEEDED | True | True | 16 | 29 | — |
| b119b1f_3 | dev | SUCCEEDED | True | True | 14 | 15 | protocol_correction |
| b3bdcc1_3 | test_challenge | FAILED | False | False | 21 | 27 | business_api_error, official_task_failure, protocol_correction |
| b4707e8_1 | test_challenge | DEFERRED | True | False | 14 | 22 | protocol_correction, training_format_deferred |
| b68eb83_1 | test_challenge | UNKNOWN | None | False | 16 | 21 | business_api_error, provider_http_error, provider_usage_unknown |
| b6d1104_1 | test_normal | SUCCEEDED | True | True | 18 | 21 | — |
| b6d1f70_3 | test_challenge | FAILED | False | False | 25 | 31 | business_api_error, execution_budget_exhausted, official_task_failure, protocol_correction |
| b71318e_1 | test_challenge | SUCCEEDED | True | True | 24 | 46 | — |
| b7a9ee9_2 | train | SUCCEEDED | True | True | 21 | 82 | business_api_error |
| b9c5c9a_1 | test_normal | SUCCEEDED | True | True | 24 | 30 | — |
| ba46d91_3 | test_challenge | SUCCEEDED | True | True | 8 | 7 | — |
| baeb104_3 | test_challenge | SUCCEEDED | True | True | 16 | 31 | protocol_correction |
| bcb9696_1 | test_challenge | SUCCEEDED | True | True | 12 | 21 | protocol_correction |
| bde252e_1 | test_normal | FAILED | False | False | 13 | 19 | official_task_failure, protocol_correction |
| c1091c7_1 | test_challenge | DEFERRED | True | False | 15 | 34 | protocol_correction, training_format_deferred |
| c77c005_1 | test_normal | SUCCEEDED | True | True | 16 | 38 | protocol_correction |
| c8f5f44_3 | test_challenge | UNKNOWN | None | False | 21 | 40 | business_api_error, provider_token_bound |
| c901732_2 | train | SUCCEEDED | True | True | 15 | 23 | protocol_correction |
| c9458ed_2 | test_challenge | UNKNOWN | None | False | 17 | 28 | business_api_error, provider_http_error, provider_usage_unknown |
| cb4ee7a_3 | test_challenge | SUCCEEDED | True | True | 21 | 28 | protocol_correction |
| ccb4494_1 | train | SUCCEEDED | True | True | 12 | 32 | — |
| ccf4b82_3 | test_normal | SUCCEEDED | True | True | 19 | 37 | protocol_correction |
| cdaaea5_3 | test_challenge | UNKNOWN | None | False | 28 | 53 | business_api_error, protocol_correction, provider_token_bound |
| cdf61bd_3 | test_challenge | SUCCEEDED | True | True | 12 | 15 | — |
| ce359b5_3 | train | UNKNOWN | None | False | 14 | 65 | provider_usage_unknown |
| ce73d68_2 | test_challenge | FAILED | False | False | 24 | 33 | official_task_failure |
| cef9191_2 | test_normal | SUCCEEDED | True | True | 6 | 8 | — |
| cf6abd2_2 | train | UNKNOWN | None | False | 2 | 1 | worker_environment |
| d037dc8_1 | test_challenge | UNKNOWN | None | False | 15 | 20 | protocol_correction, provider_token_bound |
| d0b1f43_3 | train | SUCCEEDED | True | True | 13 | 20 | — |
| d18139b_3 | test_normal | UNKNOWN | None | False | 26 | 32 | business_api_error, protocol_correction, provider_token_bound |
| d194965_1 | test_normal | UNKNOWN | None | False | 3 | 3 | worker_environment |
| d37c235_3 | test_challenge | FAILED | False | False | 15 | 36 | execution_budget_exhausted, official_task_failure, protocol_correction |
| d4e9306_3 | dev | SUCCEEDED | True | True | 11 | 38 | — |
| d6ac34d_2 | test_normal | SUCCEEDED | True | True | 10 | 14 | — |
| d6d8cb6_1 | test_challenge | SUCCEEDED | True | True | 23 | 31 | protocol_correction |
| d8e490b_1 | test_challenge | SUCCEEDED | True | True | 20 | 24 | protocol_correction |
| d9987f6_1 | test_challenge | FAILED | False | False | 14 | 14 | official_task_failure, protocol_correction |
| dac78d9_1 | test_normal | SUCCEEDED | True | True | 8 | 8 | — |
| dbc0276_3 | test_challenge | SUCCEEDED | True | True | 12 | 19 | — |
| dc5c5c6_2 | test_challenge | UNKNOWN | None | False | 29 | 39 | provider_token_bound |
| dcb2559_3 | test_challenge | SUCCEEDED | True | True | 11 | 18 | — |
| dcbd34b_3 | test_challenge | SUCCEEDED | True | True | 19 | 20 | protocol_correction |
| df61dc5_1 | dev | SUCCEEDED | True | True | 10 | 30 | — |
| e0fe09c_3 | test_challenge | FAILED | False | False | 11 | 30 | execution_budget_exhausted, official_task_failure, protocol_correction |
| e201314_2 | test_challenge | FAILED | False | False | 21 | 33 | business_api_error, official_task_failure, protocol_correction |
| e3d6c94_1 | train | SUCCEEDED | True | True | 14 | 40 | — |
| e52623a_2 | test_challenge | UNKNOWN | None | False | 21 | 37 | protocol_correction, provider_token_bound |
| e70b117_2 | test_challenge | SUCCEEDED | True | True | 17 | 34 | protocol_correction |
| e775c78_3 | test_challenge | FAILED | False | False | 12 | 12 | official_task_failure, protocol_correction |
| e7a10f8_3 | train | SUCCEEDED | True | True | 37 | 45 | — |
| e7f15ba_3 | test_challenge | UNKNOWN | None | False | 23 | 36 | business_api_error, protocol_correction, provider_token_bound |
| e85d92a_1 | train | SUCCEEDED | True | True | 6 | 11 | — |
| eb5ad85_2 | test_challenge | UNKNOWN | None | False | 5 | 9 | evaluation_runtime |
| ec437da_1 | test_challenge | UNKNOWN | None | False | 30 | 37 | business_api_error, evaluation_runtime, protocol_correction, provider_token_bound |
| ec655df_1 | test_challenge | UNKNOWN | None | False | 23 | 34 | business_api_error, protocol_correction, provider_token_bound |
| efc3cea_1 | test_challenge | FAILED | False | False | 33 | 36 | business_api_error, official_task_failure, protocol_correction |
| f099b4c_2 | test_challenge | UNKNOWN | None | False | 13 | 28 | evaluation_runtime |
| f323bae_3 | test_normal | SUCCEEDED | True | True | 21 | 46 | — |
| f37e804_3 | test_challenge | UNKNOWN | None | False | 23 | 36 | protocol_correction, provider_token_bound |
| f3a6713_3 | test_challenge | FAILED | False | False | 23 | 38 | business_api_error, execution_budget_exhausted, official_task_failure, protocol_correction |
| f3f60f0_1 | test_normal | SUCCEEDED | True | True | 15 | 69 | — |
| f691597_1 | test_challenge | SUCCEEDED | True | True | 9 | 9 | — |
| f6936d4_1 | test_challenge | UNKNOWN | None | False | 20 | 29 | business_api_error, protocol_correction, provider_token_bound |
| f6be291_2 | test_challenge | FAILED | False | False | 34 | 48 | business_api_error, execution_budget_exhausted, official_task_failure, protocol_correction |
| f6f2e67_2 | test_challenge | FAILED | False | False | 14 | 28 | official_task_failure, protocol_correction |
| f861c32_3 | test_normal | UNKNOWN | None | False | 34 | 40 | business_api_error, protocol_correction, provider_token_bound |
| f86d850_2 | test_challenge | UNKNOWN | None | False | 25 | 39 | business_api_error, evaluation_runtime, protocol_correction |
| f99d726_2 | test_challenge | UNKNOWN | None | False | 20 | 35 | business_api_error, provider_token_bound |
| fa327a6_3 | test_challenge | UNKNOWN | None | False | 9 | 23 | provider_token_bound |
| fac291d_3 | dev | UNKNOWN | None | False | 7 | 15 | provider_token_bound |
| fb05fed_2 | test_challenge | DEFERRED | True | False | 16 | 29 | protocol_correction, training_format_deferred |
| fd1f8fa_2 | test_normal | SUCCEEDED | True | True | 11 | 27 | — |
| fdc4b74_2 | test_challenge | SUCCEEDED | True | True | 28 | 27 | protocol_correction |
| fddb6b6_3 | test_challenge | UNKNOWN | None | False | 7 | 8 | provider_usage_unknown |
| ff58e36_1 | test_normal | SUCCEEDED | True | True | 19 | 32 | — |
| ffe6d5e_2 | test_normal | FAILED | False | False | 18 | 23 | business_api_error, official_task_failure, protocol_correction |
| ffea2b5_1 | test_challenge | SUCCEEDED | True | True | 24 | 33 | — |
