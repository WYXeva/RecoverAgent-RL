# 取消工具调用数量配额与提示诊断 · 2026-10-20

状态 **PASS：实现、真实环境验证及小样本诊断完成**。新 P3-A 默认不限制整轨迹/单回复工具调用数量，仍限制 4096 单回复、累计生成 token、模型请求、时间和费用。没有训练或加载模型权重，P0–P2 的 17 个原文件哈希不变。

## 当时为什么一次请求 20 个工具

原失败 `runs/tool_modes_20261020_a/teacher_522c2e363712` 的可见任务要求查询 Spotify 歌单中获赞最多的歌曲。真实 playlist 反馈包含 8 个歌单、60 条歌曲记录，去重后 57 首；歌曲详情 API 一次接收一个 song_id 并返回 like_count。模型主动选择逐首查询和分批执行。

| 边界 | 真实发生的事情 | 已执行调用 |
| --- | --- | ---: |
| 第 1–6 次回复 | 查询公开目录/凭据/登录、取得歌单和歌曲详情文档 | 8 |
| 第 7 次回复 | 一批 20 次 show_song，全部执行 | 28 |
| 第 8 次回复 | 又一批 20 次 show_song；仅 696 输出 token，正常 tool_calls 结束 | 仍为 28 |

两批各 20 个不同 song_id，批间无重复，所有 ID 均已在之前的歌单反馈中出现；不依赖同批其他调用结果。旧 prompt 明确允许 independent calls 组成 batch，但没有公开 40 次总额度，只用了含糊的 bounded batch。控制器判断 28+20>40，因此整批拒绝。按原逐首查询路线需要约 8+57+1=66 次，旧配额低于这条路线需要的调用数。

20 是该次模型自行选择的分批大小；记录只能确认它在主动批量查询，不能知道为什么精确选择 20。不能将其归咎于 tokenizer、坏 JSON 或无效循环。此次新采样两条成功轨迹都自行选择 30+27 查询 57 首歌曲，进一步说明批次大小不是代码固定为 20。

合理处理是接受这些已知参数、互相独立的调用，串行执行并收齐全部结果，再请求下一轮。不能擅自只取前 12 个，也无须强制拆成 20 次模型回复。保留输出长度与完整参数要求；如果未来真的 finish_reason=length，仍拒绝不完整响应，不伪造缺失结果。

## 实现与上游证据

- `limits.max_tool_calls=null`、`teacher.max_tool_calls_per_turn=null` 表示不设数量配额；正整数仍用于读取/重现旧配置，不影响旧证据。
- 固定 [AppWorld environment.py](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/src/appworld/environment.py) 构造参数只接受整数，并直接以 `num_interactions >= max_interactions` 比较。不能传 None；P3-A 通过官方构造参数使用 `sys.maxsize=9223372036854775807` 作兼容哨兵，避免 Adapter 旧默认 100 次成为隐藏限制。这不是数学意义的无限数，但在保留的资源预算下不会生效。未修改依赖源码/官方验证器或 P0–P2 adapter。
- `budget_batched_v2` 允许独立批量，要求已知参数、等待依赖结果、完整 JSON 适配输出预算、避免不必要重复查询，并明示实际 token/请求/时间预算及无调用数量配额。`legacy_v1` 保留。
- `official_zero_shot` 保持原样官方 Predictor 提示；新增 `dependency_aware_v1` 追加通用提醒：执行者没有预先取得的凭据/access token，需要预测依赖 API（获取输入、认证、标识、分页、完成）。没有直接插入任何工具或任务答案。增强版必须独立标记，不能冒充官方原版。
- 筛选/便携数据保留 Actor/Predictor 提示版本及真实请求；Predictor 提示不进入 SFT Assistant 目标。

## 真实重放与超过旧限制的验证

`scripts/probe_unlimited_tools.py` 在独立 world 从原始初态重放旧 Teacher 已记录调用：

- 前 28 条真实反馈与旧记录逐条完全一致，状态/历史/schema 边界哈希匹配。
- 原先拒绝的下一批 20 个调用全部合法执行，累计达到 48，完整历史校验通过。
- 第二个独立 world 连续执行 101 次公开 show_active_task，真实 `num_interactions=101`，无失败、逻辑状态未变。
- 共 149 次真实工具动作、0 模型请求、36.14 秒。报告 `reports/unlimited_replay.json`，原始本地证据 `runs/unlimited_replay_20261020_a/`。

这是缓存调用重放和执行限制探测，**不是新 Teacher 示范或新的自主恢复成绩**；不导出到 Clean Dataset。旧源轨迹未改写，没有读取隐藏 evaluator。

## Prompt 对照：同一任务的三次新正常执行

针对已分析的 train 任务 `82e2fac_1`，预先固定 B/A官方/A增强顺序，各从原始初态运行一次。三个独立进程，初态哈希相同；全部 21 次响应来自 `deepseek-flash`、fingerprint `e72d3c7f663d3e1383b00b3884cb4501`，non-thinking、temperature=0.2。三组共同 Actor v2；A 两组 Actor system/user 提示相同，Predictor 输入唯一文本改动是追加依赖提醒，选择出的工具 schema 不同。

每组上限：20 总模型请求、8192 生成 token（含预测）、450 秒、USD 1.50；每回复 4096 token，无工具数量配额。三组合计最多 60 请求/24576 输出 token/1350 秒/USD 4.50，沿用用户已授权费用，无自动重试。

| 条件 | 官方严格结果 | 模型请求（含预测） | 实际工具调用 | 输出 token | 高峰费率估算 USD |
| --- | --- | ---: | ---: | ---: | ---: |
| B 动态发现 | 成功 | 8 | 67 | 2830 | 0.011859636 |
| A 官方原提示 | 失败 | 6 | 5 | 580 | 0.001589784 |
| A 依赖增强提示 | 成功 | 7 | 61 | 2327 | 0.006804408 |

B `teacher_14608f1133b8` 查完 57 首并提交结果，通过官方全部 2 项检查。A 官方 `teacher_a70a23bf306d` 只选 5 个 schema，未选登录和凭据 API，尝试访问失败后提交未完成结果，官方失败。A 增强 `teacher_f48e51cdaecc` 选中 9 个 schema，**模型自己选择**了 spotify.login 与 supervisor.show_account_passwords，经过真实登录、获取歌单、57 次歌曲查询及完成调用，官方全部 2 项检查通过。

这支持当前案例的依赖漏选诊断，不证明总体 prompt 改进有效。每条件仅一次，API 采样有随机性，且选的是已分析 train 错误；不是独立泛化评测。B 与旧版本同时改变配额和 Actor 提示，不能把整个成功变化归于某一句新提示；确定性重放只证明原 20 次批量在取消配额后可执行。单次成功也不构成正式 Qwen3-4B 成绩。

## 数据、成本与测试

总真实 Teacher 费用统计包含失败和两个 Predictor：21 模型请求、128735 输入 token（85888 缓存命中）、5737 输出 token、133 工具调用，Worker 合计 99.99 秒。按当天 [DeepSeek 公开高峰费率](https://api-docs.deepseek.com/quick_start/pricing/)估算 USD 0.020253828，非账单；私有前后余额回执观察差额 CNY 0.00，逐请求实扣未知，不表示免费。另有上述 149 次无模型的控制侧验证动作，应与 Teacher 调用分列，总环境工具执行 282 次。

软件测试 **75 passed**，Ruff/格式检查通过；新增用例覆盖 null/旧整数配额、非法配置拒绝、101 调用单批完整执行与数据校验、上游整数哨兵传递、公开预算提示和原版/增强版隔离。软件 fixtures 不作为真实环境验收。另对全部 21 个真实请求进行离线回执/费用/提示重建检查，两个 A 只有预期 Predictor 提示差异，无隐藏标签输入。

导出 `data/unlimited_clean_20261020_a/`：保留 3 条尝试证据、接受 2 条正常成功 Teacher 示范（同一任务），Dataset ID `ffa7369474394df339f6365f7d0449355a07752f6661c2ebb2f8f0279adf1d61`；manifest SHA-256 `0a13edb56232ceff8b554f09c2f3885c7e93522423da0c573c3019a3ed994d7f`，独立核验通过。固定真实 Qwen3-4B tokenizer 得到 14 个 Assistant 目标、3988 监督 token，未加载权重或更新参数。

公共证据：`unlimited_prompt_comparison.json`、`unlimited_prompt_audit.json`、`unlimited_replay.json`、`unlimited_sft_format.json`、`unlimited_execution_source_manifest.json`、`unlimited_checks.json`。完整本地请求视图：`outputs/unlimited_prompt_20261020_a/{b_dynamic,a_official,a_dependency}.md`；原始任务/凭据/数据包均不提交 Git。

实际命令：

```bash
APPWORLD_CACHE="$PWD/.cache/appworld" IPYTHONDIR="$PWD/.cache/ipython" \
PYTHONPATH=src .venv/bin/python scripts/probe_unlimited_tools.py \
  runs/tool_modes_20261020_a/teacher_522c2e363712 \
  --output runs/unlimited_replay_20261020_a

APPWORLD_CACHE="$PWD/.cache/appworld" IPYTHONDIR="$PWD/.cache/ipython" \
PYTHONPATH=src .venv/bin/python scripts/compare_predictor_prompts.py \
  --config .cache/unlimited_prompt_20261020_a.json \
  --output runs/unlimited_prompt_20261020_a --credentials-file .env

APPWORLD_CACHE="$PWD/.cache/appworld" PYTHONPATH=src .venv/bin/python \
  scripts/audit_prompt_probe.py runs/unlimited_prompt_20261020_a \
  --source runs/tool_modes_20261020_a/teacher_522c2e363712 \
  --dataset data/unlimited_clean_20261020_a --output reports/unlimited_prompt_audit.json

APPWORLD_CACHE="$PWD/.cache/appworld" PYTHONPATH=src .venv/bin/python \
  -m recoveragent.demonstrations export runs/unlimited_prompt_20261020_a/b_dynamic \
  --include-run runs/unlimited_prompt_20261020_a/a_official \
  --include-run runs/unlimited_prompt_20261020_a/a_dependency \
  --output data/unlimited_clean_20261020_a
APPWORLD_CACHE="$PWD/.cache/appworld" PYTHONPATH=src .venv/bin/python \
  -m recoveragent.demonstrations verify data/unlimited_clean_20261020_a --write-receipt

HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 PYTHONPATH=src \
.venv-inference/bin/python -m recoveragent.base_sft data/unlimited_clean_20261020_a \
  --output data/unlimited_base_sft_format_20261020_a \
  --expected-manifest-sha256 0a13edb56232ceff8b554f09c2f3885c7e93522423da0c573c3019a3ed994d7f
.venv/bin/python -m pytest -q
```

重跑需使用全新输出路径；审计命令要求数据包已导出。当前仅完成本机诊断，不启动 SFT/GRPO。下一步在预先固定的更多 train 场景上重复原版/增强版对照，保留所有负结果；不要把本例调过的 prompt 当作未经调参的官方基准。
