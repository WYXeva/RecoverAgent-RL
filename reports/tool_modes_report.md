# 双工具模式真实小试 · 2026-10-20

状态 **PASS：双模式实现、同初态配对和真实官方评价通过**。效果结论有限：DeepSeek Teacher 在两个同族 train 任务上，动态发现 B 成功 1/2，官方零示例 Predictor A 成功 0/2。没有正式 Qwen3-4B 推理成绩，也没有 SFT、GRPO、模型权重加载或 GPU 训练。

## 固定协议

需求 v0.5；上游 AppWorld commit `42b5bcf3cd334fee33f0c37c02070a9f5807add5`、数据版本 `0.2.0`。保留 P0–P2 的 17 个原代码/配置/测试哈希。

Teacher `deepseek-flash`、文档版本 `DeepSeek-V4.1-Flash-1017`、non-thinking、temperature=0.2；全部 21 次响应 fingerprint `e72d3c7f663d3e1383b00b3884cb4501` 一致。Alias 非不可变权重，不把 fingerprint 当权重 revision。Actor 与 Predictor 用同一 Teacher。

两个任务是预先指定的官方 train 顺序前两项 `82e2fac_1`、`82e2fac_2`，同一场景 `82e2fac`；执行顺序 B1/A1/A2/B2。每次独立进程和 world，初始状态哈希在每对中一致。每次 20 总模型请求、8192 累计生成 token、40 工具调用、450 秒；**每条回复 max_tokens=4096**，A 的预测也计入额度。4 次累计最多 80 请求、32768 生成 token、1800 秒；实际 120.92 秒。

A 直接载入已核验哈希的官方 APIPredictor 源码/提示。只用 `predicted` 模式和公开输入；`demo_task_ids=[]`，关闭 cache_control，避免官方示例加载 hidden required_apis 以及空示例列表索引异常。完整差异和官方来源见 [工具协议](../docs/TOOL_MODES.md)。这是 Predictor 组件对比，**不是包含官方 few-shot/Actor 提示的完整基线复现**。

## 真实结果（保留全部尝试）

| 统计项 | A：api_predictor | B：dynamic_discovery |
| --- | ---: | ---: |
| 官方严格成功 / 总尝试 | 0 / 2 | 1 / 2 |
| 基础设施错误 | 0 | 0 |
| 总模型请求（含预测） | 5 | 16 |
| 其中 Predictor 请求 | 2 | 0 |
| 总真实工具调用 | 3 | 57 |
| 其中文档发现调用 | 0 | 10 |
| 输入 token | 19747 | 102543 |
| 缓存命中输入 token | 8704 | 55168 |
| 输出 token | 257 | 4057 |
| 峰值公开费率估算 USD | 0.003673524 | 0.019411908 |
| Worker 墙钟秒（含初始化/评价） | 47.56 | 68.32 |

A 额外预测部分：2 次请求、14588 输入 token、66 输出 token、估算 USD 0.00238584。不能把 A 较低开销解释为“成功完成任务更高效”：本轮 A 两次都提前失败。总计 **21 次模型请求、60 次工具调用、4314 输出 token、122290 输入 token**。

| 任务 / 模式 | 轨迹 | 官方结果 | 观察到的原因 |
| --- | --- | --- | --- |
| 82e2fac_1 / B | teacher_522c2e363712 | 失败 | 已执行 28 次，剩余工具额度 12，下一回复申请 20 次，整批拒绝；finish_reason=tool_calls，非 token 截断 |
| 82e2fac_1 / A | teacher_608963737f80 | 失败 | 选中 7 个 schema（含自动加入的完成工具），漏选登录和凭据工具；Actor 直接提交未完成结果 |
| 82e2fac_2 / A | teacher_bc344b80fb2d | 失败 | 选中 4 个 schema，漏选登录和凭据工具；一次业务调用未获得可用访问令牌，随后提交未完成结果 |
| 82e2fac_2 / B | teacher_5601c6f4d156 | 成功 | 8 个执行回合、29 次真实工具调用，通过官方全部 2 项检查 |

漏选依据是实际 Predictor 响应/可见 schema 和 Actor 后续请求/反馈，不利用隐藏 API 标签修补选择结果。A 的工具列表全程固定；B 从 10 个增加到 14 个。三条失败不进入示范。四条轨迹的环境正常结束、保存终态并按原始任务参照官方评价；未把失败误报为基础设施异常。

## 成本与限制

沿用用户“费用不限”的 DeepSeek 授权，仍保留 USD 5 操作性上限；未开新服务或购买算力。按照当天核对的 [DeepSeek 公开高峰费率](https://api-docs.deepseek.com/quick_start/pricing/)，总 token 费率估算为 **USD 0.023085432**。它不是账单。采样前后私有余额回执观察差额 **CNY 0.00**；供应商未提供逐请求实扣归因，本次不能据此声称免费，也不能把 USD 估算写成人民币实扣。公开报告不包含绝对余额或凭据。

两任务同族、单次随机采样、无置信区间，不能证明 B 在 AppWorld 总体优于 A；缓存命中、顺序和 non-thinking 设置影响成本/时延。正式 Student 尚未运行两模式。若后续给 A 固定补入认证工具，应视为新的预注册变体，不能覆盖本轮原始 A 结果；更广任务族及多次采样仍待执行。

## 数据、测试和证据

- 本地原始 run：`runs/tool_modes_20261020_a/`；所有任务/模型输入、响应、dispatch、终态、官方评价与未通过轨迹保留，未上传公开 Git。
- 公共汇总：`reports/tool_modes_comparison.json`，完整配对、预算、使用量审计 `reports/tool_modes_audit.json`。审计从 21 份实际响应重算费用/调用/哈希；重新加载**公开 Task（load_ground_truth=False）**和官方 Predictor，输入消息、解析选择、选中 schema 与实际请求完全一致。
- 成功数据包：`runs/tool_modes_20261020_a/clean_dataset/`，1 条成功示范、4 条尝试证据。Dataset ID `ea3c3add27540544aff962d6d90203e571cc53908e7751cc70a05c75fcfc8e3f`；manifest SHA-256 `fc1367decb73b4e70d1bbe9cb052ba3c291f721942f33edaa914cdc03dbe4932`。官方 train 成员、文件哈希、来源、筛选及总成本重验通过。
- 真实固定 Qwen3-4B tokenizer 格式检查：8 个 Assistant 目标样本、1474 监督 token、51108 累计上下文 token，所有目标使用当时可见 schema；无权重加载/训练。证据 `reports/tool_modes_sft_format.json`，本地数据 `data/tool_modes_base_sft_format_20261020_a/`。A 的预测消息仅作为控制证据，不混入 SFT 目标。
- 四份完整脱敏视图：`outputs/tool_modes_20261020_a/teacher_<ID>.md`。其中 A 的 `--all` 单独显示 `000` Predictor 输入和原始响应，Actor 第一回合仍为 `001`。无回执警告。
- 软件测试：**65 passed**；Ruff 与格式检查通过。新增 6 个 synthetic 测试覆盖选择/执行隔离、预算共享、截断记录、固定 schema、源码哈希/模式限制和完整失败分母；它们不替代上述四条真实 AppWorld 验收。
- 新源码在采样开始时冻结到 run 的 `execution_source_manifest.json`；旧 P0–P2 17 项哈希未变。未修改官方源码或验证器。

实际运行命令：

```bash
.venv/bin/python -m pip install 'Jinja2==3.1.6'
APPWORLD_CACHE="$PWD/.cache/appworld" IPYTHONDIR="$PWD/.cache/ipython" \
PYTHONPATH=src .venv/bin/python -m recoveragent.p3a compare \
  --config .cache/tool_modes_20261020_a.json \
  --output runs/tool_modes_20261020_a --credentials-file .env

APPWORLD_CACHE="$PWD/.cache/appworld" PYTHONPATH=src .venv/bin/python \
  scripts/audit_tool_modes.py runs/tool_modes_20261020_a \
  --output reports/tool_modes_audit.json
APPWORLD_CACHE="$PWD/.cache/appworld" PYTHONPATH=src .venv/bin/python \
  -m recoveragent.demonstrations verify \
  runs/tool_modes_20261020_a/clean_dataset --write-receipt

HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 PYTHONPATH=src \
.venv-inference/bin/python -m recoveragent.base_sft \
  runs/tool_modes_20261020_a/clean_dataset \
  --output data/tool_modes_base_sft_format_20261020_a \
  --expected-manifest-sha256 fc1367decb73b4e70d1bbe9cb052ba3c291f721942f33edaa914cdc03dbe4932

.venv/bin/python -m pytest -q
.venv/bin/ruff check src/recoveragent tests scripts/audit_tool_modes.py
.venv/bin/ruff format --check src/recoveragent tests scripts/audit_tool_modes.py
```

已有输出不可覆盖，复跑时使用新 run/审计输出路径。默认公开配置保持付费关闭。下一步是按预先固定的更多 train 场景和重复次数扩大比较；是否补认证工具需要独立标记实验，不应在已见结果上悄悄改变官方原样选择。
