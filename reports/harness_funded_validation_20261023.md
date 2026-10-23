# DeepSeek 余额恢复后的真实验证

2026-10-23；运行代码固定在 `8ea72d8b1078f493f4eeaa1c530cf9ad2a3527e7`。没有修改运行源码、Prompt、参数锁、官方工具或评价器。

**余额和真实 Teacher 执行链路 PASS。两个固定任务均官方成功，导出两条合格完整示范。Harness 纠错效果为 NOT_OBSERVED：本次没有自然触发错误，不能把成功执行称为纠错效果提升。**

## 范围与预先约定

使用冻结 `execution_v5`、B `dynamic_discovery`、`public_protocol_correction_v1` / `review_20261023` 和 DeepSeek `deepseek-flash`（文档版本 DeepSeek-V4.1-Flash-1017）。真实回执中的模型/指纹记录在机器报告中。

选择原已固定 `configs/train_v5_ab_20261021.json` 清单的前两个任务，事先写入新清单和 seed=2026102301。它们属于已经使用过的两个场景家族，目的是小样本链路复验，不是未见任务泛化评价或新的 A/B 比较。每任务一次，不替换失败任务，也不为了看到纠错而持续追加任务；无错误注入。

预算：每任务40请求/16384生成token/600秒/USD1；合计80请求/32768生成token/1200秒/USD2，单回复4096，工具数量不设配额。沿用已有DeepSeek授权，本轮没有新服务或训练。

## 真实结果

| task_id | 官方结果 | 模型请求/回合 | 工具调用 | 生成token | 轨迹耗时 | 过滤 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 771d8fc_2 | 成功 | 8 | 34 | 4494 | 47.15秒 | 合格 |
| e7a10f8_3 | 成功 | 7 | 15 | 1833 | 36.05秒 | 合格 |
| 合计 | 2/2 | 15 | 49 | 6327 | 整批85.48秒 | 2/2 |

- 输入token 128599，其中缓存命中104832；输入+输出合计134926（Provider口径）。
- 通用协议拒绝0、数值纠正0、无进展提醒0、业务工具执行错误返回0；基础设施错误0、评价运行异常0、未知用量/费用预留0。
- 两条都是从原始train初态开始的真实Teacher正常任务示范，真实调用公开AppWorld工具，以新进程官方评价判成功；没有脚本冒充模型或Teacher接管旧失败。
- 原95条正式数据、manifest与archive SHA保持不变；新增2条保存在独立Clean Dataset，尚未并入或重建正式Base-SFT v1，不把正式数量改成97。

## 数据和SFT格式复验

新包：`runs/harness_funded_20261023_a/clean_dataset/`。

Manifest SHA-256：`e9372bf7e7cd914eeb3f9f8cb97d2682606ae55b0557aa3978305a0906b3d4db`。

官方train成员/来源版本、原始Provider request/response、完整历史、当时工具Schema、参数/调用ID、官方成功、过滤条件和全部文件SHA复验通过。初次调用sft_quality时缺少独立的 `clean_dataset.verified.json` 回执，因此按预期拒绝；执行官方成员复验CLI并写出回执后再检查通过。没有放宽质量门槛或改源包。

固定Qwen3-4B tokenizer/chat template复验：

| 指标 | 数值 |
| --- | ---: |
| 32768上下文内完整轨迹 | 2 |
| Assistant训练目标 | 15 |
| 监督token | 5730 |
| 屏蔽的上下文token | 125765 |
| 所有回合输入token | 131495 |
| 最长完整序列 | 14211 |
| 最长Assistant目标 | 2776 |
| 批量调用目标 | 8 |
| 动态Schema变化 | 2 |
| 相邻样本相同前缀token累计 | 94295 |
| 超长deferred | 0 |

Assistant-only、历史角色和Padding mask、Student函数解析回读均通过。Qwen目标token与Provider生成token使用不同tokenizer/模板，不能当成同一种计数。编码通过不是训练通过；训练步数0。

## 可核实费用

前后两次只读余额接口均HTTP200、is_available=true且有正余额；私有原始余额回执只保存本机，公开报告不暴露余额金额。

按照2026-10-23复核的[DeepSeek官方价格](https://api-docs.deepseek.com/quick_start/pricing/)，冻结配置继续使用峰时USD单价作为保守估算：输入未缓存0.3/百万、缓存0.006/百万、输出1.2/百万。上述真实usage计算为 **USD 0.015351492**，明显低于USD2预算。

验证窗口内账户余额实测减少 **CNY 0.03**。这是账户级、有显示精度限制的余额差额，不是逐请求发票；不同币种和峰谷定价不能直接混用。因此原运行中的 `billed_cost` 仍为null，没有把估算改写成精确实扣账单。

## 状态与证据

| 项目 | 状态 |
| --- | --- |
| DeepSeek余额/模型可调用 | PASS |
| 冻结Harness真实执行、状态保存及官方评价 | PASS（2任务小样本） |
| Clean Dataset来源/完整性/数据过滤 | PASS |
| Qwen3-4B格式、mask及解析器 | PASS（编码，无权重更新） |
| 真实模型在Harness反馈后自纠正 | NOT_OBSERVED_NO_TRIGGER；余额阻塞已解除 |
| 正式SFT / GRPO | NOT_STARTED |

证据：`reports/harness_funded_20261023_{before_provider,after_provider,plan,result,integrity,quality}.json`。真实运行/完整对话/终态/隐藏官方评价保存在 `runs/harness_funded_20261023_a/`；token文件在 `.cache/harness_funded_20261023_tokens/`，均不上传公开GitHub。Ruff check与85文件format检查通过；源码锁与8ea72d8一致，既有201项软件回归基线未改。

实际命令：

```bash
PYTHONPATH=src timeout 1320 .venv-harness/bin/python -m recoveragent.p3a collect --config .cache/p3a_harness_funded_20261023.json --output runs/harness_funded_20261023_a --credentials-file .env
PYTHONPATH=src .venv-harness/bin/python -m recoveragent.demonstrations verify runs/harness_funded_20261023_a/clean_dataset --expected-manifest-sha256 e9372bf7e7cd914eeb3f9f8cb97d2682606ae55b0557aa3978305a0906b3d4db --write-receipt
HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 PYTHONPATH=src .venv-inference/bin/python -m recoveragent.sft_quality runs/harness_funded_20261023_a/clean_dataset --expected-manifest-sha256 e9372bf7e7cd914eeb3f9f8cb97d2682606ae55b0557aa3978305a0906b3d4db --output .cache/harness_funded_20261023_tokens --report reports/harness_funded_20261023_quality.json
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
```

下一步可在后续已计划采样中观察自然触发的纠错；本次验证到两条固定任务即停止。远程GPU、4B真实更新/重载及dev评价依然尚未验收。
