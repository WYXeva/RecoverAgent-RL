# Harness-Managed Memory 29k：真实Qwen固定三任务A/B

日期：2026-10-27。代码提交`2b6f99823999231ff9cc0637aad2c0b63d1d30cb`，开发分支`codex/p0-p2-recovery`。用户确认开机后，使用现有A100执行既定小样本；未训练、改提示、替换任务、租用或启动新机器。原本地报告和全部历史记录保留。

本次配对验收状态：**INCOMPLETE**；全部任务未完成（3/计划6次执行）；真实Harness压缩未触发。完整运行通过不等同Memory性能提升。

## 逐任务对照

| task_id | 组 | 官方结果 | 请求 | 工具调用 | 输入token | 输出token | 压缩事件 | 使用压缩输入的请求 | 耗时秒 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 287e338_3 | A | task_failure | 10 | 12 | 79,757 | 641 | 0 | 0 | 103.96 |
| 287e338_3 | B | task_failure | 10 | 12 | 79,757 | 641 | 0 | 0 | 102.65 |
| 3c13f5a_1 | A | task_failure | 11 | 11 | 72,708 | 518 | 0 | 0 | 93.29 |
| 3c13f5a_1 | B | NOT_RUN | — | — | — | — | — | — | — |
| 229360a_2 | A | NOT_RUN | — | — | — | — | — | — | — |
| 229360a_2 | B | NOT_RUN | — | — | — | — | — | — | — |

A=Memory关闭；B=Harness管理，实际待发送输入达到29000才压缩，保留上次移除边界。两组配置中的checkpoint、初始任务、execution_v5/dynamic_discovery、seed42、temperature0.8/top_p0.95及每任务40请求/16384生成/600秒均固定；已配对首任务的数据库和首次输入逐项一致，未执行项的初始条件尚未验证；工具次数不限，单回复≤4096、完整窗口32768。Memory不生成额外模型请求。

## 完整分母与成本

- A：计划3任务，实际2，有效评价2，成功0，任务失败2，未知评价0，基础设施错误0。未执行：['229360a_2']。
- B：计划3任务，实际1，有效评价1，成功0，任务失败1，未知评价0，基础设施错误0。未执行：['3c13f5a_1', '229360a_2']。

实际合计：31请求，232,222输入/1,800输出token，35工具调用意图。原始回执生成耗时138.34秒，峰值allocated 10.88GiB。Harness规则维护0.017秒，编译0.279秒；均在任务时间预算内。没有Memory模型调用。

货币成本UNKNOWN：没有平台账单/单价证据，不能把现有实例称为免费，也不能由token伪造GPU费用。模型生成、worker初始化/环境评价、控制器总时长、回传及关机等待分别记录。此次正式A/B未通过完成门槛，不能比较两组总体成功率：A尝试2任务、B仅1任务，分母不同。

## 解释与审计

[完整配对结果](egtm_harness_29k_live_results.json)从原请求/响应重算；[直接分词与Memory回放审计](egtm_harness_29k_live_audit.json)检查真实请求IDs、Schema/工具配对、无内部Memory请求及29k触发。所有输入用同一固定tokenizer/non-thinking模板重渲染。Memory终态根据完整历史重建事实/L2，并核对压缩边界，不重新生成模型结果。

### 为什么停止，为什么没有测到压缩

- 首任务`287e338_3`：A/B都在首轮遗漏`api_docs__show_api_descriptions.app_name`，收到明确缺字段反馈后纠正。随后两组全部10轮输入IDs、输出IDs和工具调用一致，最后都正式提交答案，官方评价均失败。可见行为仅查推荐列表第0页20条（恰返回20条），未取后续页/证明完整覆盖，再查询3个艺术家资料后提交；这是可见统计覆盖不足的证据，不是借隐藏正确答案定位或修补行为。
- 第二任务`3c13f5a_1`只执行A：请求001、002连续遗漏`app_name`，分别消耗原2次纠正机会；之后真实工具执行返回两个422：phone应用不存在`search_users`和`search_friends`。这些是公开业务API错误，未误算为Provider故障或消耗协议纠正次数。第011请求试图查询phone.login，却向`api_docs__show_api_doc`传入包含username/password键的不允许的`parameters`字段，原纠正预算已用尽，非法整批未执行，终态`protocol_correction_exhausted`。
- 三次执行都得到完整有效官方失败评价，**不是基础设施故障或未知评价**。全程31回复均EOS完成，生成token远未到16384，也未耗尽40请求或600秒。问题是API发现、参数契约与任务执行能力，**不是token截断或上下文耗尽**。
- 最长输入仅13,412token，第二任务末轮9,720token；B唯一轨迹未到29k，未注入压缩Memory，压缩事件0、净输入节省0。真实模型只验证了阈值前输入/行为保持一致，尚未验证真实长任务压缩收益或损害。
- 控制器按先前已提交的重实验规则，在不可纠正协议错误时停止，所以没有运行第二任务B及第三任务A/B；不能用A的失败补填B，也不能报0/3或完整A/B PASS。原模型驱动实验的负结果不覆盖。

此次没有为继续跑满清单临时放宽纠错预算、改prompt或挑换任务。若后续希望专门测29k压缩，应在开机前明确新实验协议：区分单任务模型协议失败与服务/执行故障，决定是否允许后者之外继续剩余固定任务，并预先固定可检验长上下文的清单。该调整本轮**未实施、未运行**，不能根据本次失败直接宣称Memory无效。

B的saved_input_tokens仅表示在B自身实际动作历史上，用完整原历史重渲染得到的反事实输入差；不是额外真实模型请求，也不是A与B之间的因果收益。短任务未触发压缩时不能据此宣称Memory有效；两组动作/终止点不同，较少token也可能来自提早失败。固定三任务属于已检查过的开发清单，不是官方总体benchmark。

## 来源、环境与关机

[远程预检与完整性交接](egtm_harness_29k_live_remote.json)。A100-SXM4-80GB，原BF16 Qwen3-4B+LoRA；revision`1cfa9a7208912126459214e8b04321603b3df60c`，adapter tensor SHA`34b87e77f8f49e3fef772d9c7f1074d89f07dd32bda38d81d6675120bb5e0ccd`，模板SHA`a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`，源码锁`ff37168beefddbda0a8d12ce291a123d103f5d03496e4c880e3d43caddf510a6`。原数据manifest`5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`不变。

私有运行目录`runs/egtm_harness29k_live_20261027/`；原模型请求、工具结果、终态DB和官方tracker不公开。仅公开代码、配置、SHA和汇总。原95条Teacher示范、权重及历史源码锁不改。

[关机证据](egtm_harness_29k_live_shutdown.json)：停止本轮backend后0MiB；实验结束147.50秒后首次调用平台原生shutdown，返回255“未设置关机密钥”。已及时通知用户手动关机；用户随后明确回复“已关机”。此为用户报告的手动关机确认，平台状态及账单未独立核验，自动关机失败回执保持原样。证据已回传并校验，确认后不再SSH或运行模型。

控制器用时304.13秒，三个worker合计299.90秒；backend进程存在392.79秒，包含加载/空闲时间，不能全部当成生成时间。回传归档1,334,798字节，SHA`6cdc3f58a1e75a64cd2370c2ee81a5f1a4ba9e358fdb40261a1f6c9bbafe1307`，251文件本地SHA复验通过。SSH一次认证前断开导致上传失败及随后的包不存在预检失败，保留日志后重新上传/核验；均在模型执行前，没有模型请求重发。

364份历史报告/配置逐字节与2b6f998一致。本轮[350测试与Ruff检查](egtm_harness_29k_live_checks.txt)通过；31个真实请求直接分词回放通过，B的Memory终态可重建且compaction为None。未将任何模型失败或官方奖励改成成功。

## 实际命令

```bash
# 远程独立目录，PYTHONPATH=src，HF_HOME指向既有缓存，离线加载
python -m recoveragent.student_backend --adapter <原checkpoints/run_001/formal> --port 8766
# 预先保存并哈希的控制器逐任务A/B，case-index按固定清单，无重采样
python -m recoveragent.student_evaluation worker --config configs/egtm_harness_29k_pilot_a.json --case-index <0..2> --arm base_sft --output <独立目录>
python -m recoveragent.student_evaluation worker --config configs/egtm_harness_29k_pilot_b.json --case-index <0..2> --arm base_sft --output <独立目录>
# 本地只读复算
PYTHONPATH=src .venv/bin/python scripts/summarize_egtm.py --version harness_29k --stage pilot --a runs/egtm_harness29k_live_20261027/pilot/A --b runs/egtm_harness29k_live_20261027/pilot/B --output reports/egtm_harness_29k_live_results.json
```

具体已执行case由上表及原始控制器记录决定，未执行项不冒称运行。此前本地350测试、真实脚本和3次独立恢复通过；本轮继续执行软件/Ruff检查并记录。没有Base-SFT重训、Teacher Recovery、Recovery-SFT或GRPO。
