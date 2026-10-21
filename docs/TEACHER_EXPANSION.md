# 冻结协议下的 Teacher 场景扩样

基线为 `cc53bea6bf13307c77abc68566a55ae51ef6e138`。`configs/prompt_protocol_lock.json` 固定五个现有文件的 SHA-256：Actor 提示、Predictor 提示、工具选择、Teacher transport、批量历史模块。扩样启动前检查哈希及 execution_v3 / dependency_examples_v2、4096 单回复和空工具数量配额；不修改旧原版/增强版，不再用 `82e2fac` 家族调参。AppWorld adapter 与 P0–P2 也保持原样。

## 固定任务清单

`sampling.official_pool()` 仅调用固定 AppWorld 的 `load_task_ids("train")` 和 `task_id_to_generator_id()`。官方 `Task.generator_id` 在本项目记录为 scenario_id，属于任务家族，不等同于应用名称。源码依据：固定提交 `42b5bcf3cd334fee33f0c37c02070a9f5807add5` 的 `src/appworld/task.py`。**不传 difficulty**，其上游实现会读取 ground_truth metadata；也不加载任务解答或验证器结果。

`scenario_balanced` 将 ID 排序，用独立 `random.Random(seed)` 打乱家族和家族内部任务，轮转取样；支持 `explicit` 固定 task_ids，仍检查 train 成员资格、唯一性和排除家族。清单保存完整设置、公开池哈希、任务顺序及 scenario 映射。启动时从官方成员表重新生成并校验，不接受替换后的清单。

本轮 seed=20261021、12 个任务、6 个家族，每家族 2 个变体；只排除已反复诊断的 `82e2fac`。候选池为官方 90 个 train 任务/30 个家族，排除后按上述规则选择，不根据结果增补、替换或重跑。清单为 `configs/train_expansion_20261021.json`，canonical digest `95f838daaaaf8b52686dae71b5a39c2fbb8524cb4acb4beb4ca3b4b0bc25c16e`。

```bash
PYTHONPATH=src .venv/bin/python -m recoveragent.sampling \
  --config configs/p3a_expanded.json --output /tmp/new_train_plan.json
```

上面是可重复生成命令，输出必须是新路径。`scenario_expansion_v1` 独立扩展原两任务 pilot 的上限，本轮每任务 40 请求/16384 输出 token/600 秒/USD1，总计 480 请求/196608 输出/7200 秒/USD12，单回复最多4096，工具次数不限。费用授权沿用用户“费用不限”，公开配置默认关闭付费调用；本地已授权配置不含密钥，实际命令见阶段报告。

默认 B `dynamic_discovery`，覆盖多场景 Teacher 正常示范；A 仍冻结保留，但此次没有执行 Predictor 请求，也不是新的 A/B 比较。A 默认是基于官方组件的增强提示，不能叫官方原提示。每个任务独立进程、干净初态、同一 Teacher 和相等额度。新增 `continue_after_infrastructure_error` 可保留错误并执行下一个不同任务，自动付费重试仍为0；未知 token/费用继续占用总预算。不得将基础设施故障补成策略零奖励。

后续用户要求的失败专项复试使用独立 `p3a_a_failure_retry.json` / `train_a_failure_retry_20261021.json`。`sampling.diagnostic_selection` 必须标记 `prior_official_failures`、来源报告和SHA-256；启动核对清单恰好包含来源中所有有效失败一次。该manifest的 `hidden_labels_or_results_used=true` 表示使用了控制侧历史评价结果选择任务，不是Actor得到隐藏标签；其scope显式写为条件诊断。常规随机/家族采样仍保持原manifest内容和哈希。不要把这类数据选择解释为无偏采样或总体A/B优劣证据。

## 复核和数据迁移

`collection_report` 从每次真实请求/响应、工具结果、官方评价及数据包重新计算覆盖与成本，失败轨迹也检查完整回执。报告区分全部尝试分母与有效评价分母，不用成功样本数替代总尝试数。公开报告仅含元数据/数量/哈希；任务正文、数据库、原始轨迹和本地 LLM 视图均不提交 GitHub。

新包为 `runs/scenario_expansion_20261021_a/clean_dataset/`，SHA-256 manifest：
`ca7e0d3e74567818f21d0a903b49e8a6291829e985fc5818df5cbe82f9d84707`。
按 `docs/P3A_HANDOFF.md` 通过授权通道迁移整个包，远端使用同版本 AppWorld 重新 `demonstrations verify --expected-manifest-sha256 ... --write-receipt`，然后用固定 Qwen3-4B tokenizer 重新构建 mask。本轮没有 SSH 传输或远程运行。`.verified.json` 为本机验证回执，不能代替远端复验。

`sft_quality` 除官方完整性回执外，核对当时 schema、精确前缀 token 边界、系统/用户/历史 Assistant/工具/padding 全部 loss=-100、本轮 Teacher Assistant token 才监督，以及 Student parser 对每个工具参数的无损回读。超过32768完整序列或4096 Qwen目标 token 的轨迹整条暂缓，不切掉后半任务冒充完整成功示范。供应商 token 与 Qwen token 分别计数。

逐回合只监督新 Assistant，避免旧 Assistant 被重复计入 loss；上下文仍反复参与前向计算。本次输入总量/最终回合总量的比值用于量化开销，不能称为语义去重率。未来减少成本需保留动态 schema 和 token 边界，不能直接套整条最终 schema 或盲目 sequence packing。
