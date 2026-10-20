# P3-A 阶段报告 · 2026-10-20

**本轮本机小样本闭环 PASS。** 真实 Teacher 从 AppWorld train 初态完成一个任务，官方验证成功；已导出非空 Clean Demonstration Dataset 并验证 Qwen3-4B Assistant-only 数据格式。含所有协议调试，共 6 次尝试、1 次成功。正式 SFT、Recovery-SFT、GRPO、远程训练均未开始；没有性能提升结论。

从 `codex/p0-p2-recovery` 的 `40836c4c02a3b3785083fe2d5c89eb6c0c2da120` 快进继续，以 v0.4 文档为基线。根工作区 `.git` 为空且只读，版本管理使用 `/tmp/recoveragent-dev-20261019`；origin 指向 `WYXeva/RecoverAgent-RL`。P0–P2 的 17 项源码/测试/配置与原 `code_manifest.json` 完全一致，没有修改依赖仓库源码或覆盖旧实验。

## 真实结果与失败分母

使用官方 train 顺序中的前两个任务 `82e2fac_1`、`82e2fac_2`，每次独立进程从原始初态执行；未向 Actor 提供隐藏标签、官方答案或上一次评价。所有任务动作、登录和 API 发现均由 Teacher 生成。

| 轮次 | 协议 | 尝试/成功 | 请求 | 真实工具调用 | 输出 token | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| A | 单调用，原生响应 | 2 / 0 | 10 | 8 | 2542 | 两次响应均包含批量调用，被整体拒绝 |
| B | 单调用，加 `parallel_tool_calls=false` | 2 / 0 | 4 | 2 | 318 | 仍返回批量，参数未强制单调用 |
| C | 有上限的原生批量，串行执行完整边界 | 2 / 1 | 14 | 37 | 4148 | 一个响应达 2048 token 截断；另一个官方成功 |
| 全部 | 包含全部调试尝试 | **6 / 1** | **28** | **47** | **7008** | **0 基础设施异常** |

总尝试成功率 1/6（16.67%）；最终协议 C 的成功率 1/2。协议和提示经过修改，不能将这些数字当作固定策略基准、TGC/SGC 总体成绩或与 Student 的公平能力比较。一个任务成功，只证明该 Teacher 在这次真实任务中具备可用执行能力。

成功轨迹为 `runs/p3a_teacher_20261020_c/teacher_ad808a15ce5a/`：任务 `82e2fac_2`，8 个 Assistant 回合、29 次实际工具调用、1529 个供应商生成 token、官方严格奖励 1。`terminal/` 在官方评价前保存；`evaluation.json` 使用原任务参照。失败批次不执行部分子调用，截断文本不补全或伪装成合法示范。

原始请求、响应、逐调用反馈、可见历史、终态和独立评价保存在三个 run 的 worker 子目录。`reports/p3a_pilot_audit.json` 重新核算所有回执用量、历史边界、终态文件/manifest 哈希及评价关联；`p3a_pilot_configurations.json` 保存实际配置元数据，采样源码清单为 `p3a_code_manifest.json`、`p3a_second_pilot_code_manifest.json`、`p3a_third_pilot_code_manifest.json`。最终代码另有交付清单，不冒充采样时源码。

## Teacher、授权与成本

用户对付费试点问题回复“费用不限”后才调用现有 DeepSeek 服务；没有新增云 GPU 或训练授权。默认仓库配置仍为 `approved=false`、费率待填；这不表示本次授权缺失。本次三个私有执行配置位于 `data/p3a_authorized_20261020*.json`，均不含 API Key。

实际 Teacher `deepseek-flash`，non-thinking，temperature 0.2。供应商文档对应 DeepSeek-V4.1-Flash-1017，所有 28 次响应的 `system_fingerprint=e72d3c7f663d3e1383b00b3884cb4501` 一致。API alias 不是不可变权重 commit，不能保证未来复用得到同一权重。[官方版本说明](https://api-docs.deepseek.com/)、[Chat Completions 契约](https://api-docs.deepseek.com/api/create-chat-completion/)。

- 输入 140388 token，其中缓存命中 79232；输出 7008 token。来自真实响应 usage，非本地 tokenizer 估算。
- 高峰 USD 单价/百万 token：非缓存输入 0.3、缓存输入 0.006、输出 1.2；按此估算 **USD 0.027231792**。这是公开高峰费率估算，可能高于实际时段费用，不是账单。[官方价格说明](https://api-docs.deepseek.com/quick_start/pricing/)。
- 官方 `/user/balance` 前后两次只读查询，账户余额观察减少 **CNY 0.04**。未排除账户内其他并发使用，没有逐请求账单；不公布绝对余额。证据 `p3a_observed_cost.json`，原始余额记录仅在忽略的 `.cache/`。
- 三轮采样墙钟合计 **170.22 秒**，不含安装、下载、软件开发、数据导出和测试；没有训练计算消耗。本机实际 RTX 5050 8151MiB，未加载新模型权重。仅下载 4B tokenizer/小配置 6 文件，共 15,882,037 字节。
- 累计上限为 2 个唯一任务、80 请求、32768 输出 token、1800 秒；实际未触达。每次新 run 扣除前轮用量；保留 USD 5 的操作性费用上限。C 放宽单任务工具上限至 80，仍串行、每批最多 64，不提高全局模型预算。没有自动付费重试。

## 数据与 mask 验收

非空数据：`data/p3a_clean_20261020_all/`，1 条成功完整多轮轨迹；全部六次尝试及费用仍保存在控制侧 evidence。

- Dataset ID：`868da089c98626a256308edfa3111d6b914d95ebd3d375d74536a1268181e9bd`。
- manifest SHA-256：`464ea9bef9e794082b266ae6ed53bc5be358eba3e0c74a026325526a70214b30`。
- 官方 train 成员、AppWorld 源码/数据版本、原始 Teacher 请求响应、格式/循环筛选、来源、顺序、文件哈希及统计重算通过。筛选程序不向 Actor 暴露控制侧评价。
- 真实非空包复制到 `data/p3a_transfer_rehearsal_20261020/` 后，用已记录的 SHA-256 重新核验通过；大小 1,373,144 字节。仅同机换目录演练，未执行 SSH/rsync 或跨主机快照恢复。
- 原始数据和官方隐藏评价不上传公开仓库。公开摘要见 `p3a_clean_export.json`、`p3a_real_handoff_check.json`。

正式 Student/tokenizer `Qwen/Qwen3-4B` revision `1cfa9a7208912126459214e8b04321603b3df60c`；模板 SHA-256 `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`。真实成功示范转换为 **8 条逐 Assistant 回合样本、1146 监督 token**；输入 token 总计 52135（包含重复的完整前缀，不是 API 输入成本），最大样本 12115 token。29 次函数名/参数往返一致。

每条样本使用实际当时工具 schema 与已见历史，只监督当前新 Assistant 内容和结束格式；此前 Assistant、system/user/tool、固定 non-thinking 提示与 padding 为 -100。批量调用保持单个 Assistant 目标，完整消息没有被人为拆分。字符串/token 前缀保持、不截断及 mask 核验通过。证据 `p3a_real_sft_preparation.json`、`p3a_real_mask_check.json`；本地格式产物 `data/p3a_base_sft_format_20261020/`。这不是参数训练或可用的 SFT checkpoint。

## 旧 Qwen 格式问题

旧 P0–P2 100 回合中 50 次格式拒绝都是没有 tool tags 的普通文本，全部提及 access token；另有 2 次环境执行错误。没有观察到合法 JSON 被 parser 丢弃。4B 真实 tokenizer 的单调用和批量结构化参数、动态 schema 与非 Assistant mask 检查均通过，不能据此宣布 4B 自主执行问题已解决；这需要后续真实 4B/训练结果。[Qwen 官方工具协议](https://qwen.readthedocs.io/en/latest/framework/function_call.html)。

旧源码/协议/结果不改写。新 P3 的 `teacher_history.py` 使用单独的完整 batch 终态证据格式，不声称兼容旧恢复接口。设计依据及边界见 `docs/DECISIONS.md` D009–D013。

## 实际命令与测试

真实 Git 克隆中执行过 `git fetch origin codex/p0-p2-recovery` 与 `git merge --ff-only origin/codex/p0-p2-recovery`，快进至需求基线。以下为工作区实际命令；配置内已保留本次授权和剩余预算，重跑必须使用新的输出 ID。

```bash
export PYTHONPATH="$PWD/src"
export APPWORLD_CACHE="$PWD/.cache/appworld"
export IPYTHONDIR="$PWD/.cache/ipython"
export HF_HOME="$PWD/.cache/huggingface"

# 授权前预检：当时阻断且未调用；保留为历史证据。
.venv/bin/python -m recoveragent.p3a collect --config configs/p3a_teacher.json \
  --credentials-file .env --output runs/p3a_preflight_20261020_a

# 授权后的三个真实 run，stdout/stderr 分别保存到 .cache/p3a-teacher-{a,b,c}.log。
timeout 1900 .venv/bin/python -m recoveragent.p3a collect \
  --config data/p3a_authorized_20261020.json --credentials-file .env --output runs/p3a_teacher_20261020_a
timeout 1800 .venv/bin/python -m recoveragent.p3a collect \
  --config data/p3a_authorized_20261020_b.json --credentials-file .env --output runs/p3a_teacher_20261020_b
timeout 1750 .venv/bin/python -m recoveragent.p3a collect \
  --config data/p3a_authorized_20261020_c.json --credentials-file .env --output runs/p3a_teacher_20261020_c

.venv/bin/python scripts/audit_teacher_pilot.py runs/p3a_teacher_20261020_a \
  runs/p3a_teacher_20261020_b runs/p3a_teacher_20261020_c --output reports/p3a_pilot_audit.json
.venv/bin/python -m recoveragent.demonstrations export runs/p3a_teacher_20261020_c \
  --include-run runs/p3a_teacher_20261020_a --include-run runs/p3a_teacher_20261020_b \
  --output data/p3a_clean_20261020_all
# verify 实际经 Python main 包装传入同样参数运行；SHA 从此前导出记录取得。
.venv/bin/python -m recoveragent.demonstrations verify data/p3a_clean_20261020_all \
  --expected-manifest-sha256 464ea9bef9e794082b266ae6ed53bc5be358eba3e0c74a026325526a70214b30 --write-receipt
HF_HUB_OFFLINE=1 .venv-inference/bin/python -m recoveragent.base_sft data/p3a_clean_20261020_all \
  --expected-manifest-sha256 464ea9bef9e794082b266ae6ed53bc5be358eba3e0c74a026325526a70214b30 \
  --output data/p3a_base_sft_format_20261020
HF_HUB_OFFLINE=1 .venv-inference/bin/python scripts/check_qwen_protocol.py \
  --historical-run runs/p0_p2_20261019_a --output reports/p3a_qwen_batch_protocol.json

.venv/bin/python -m pytest -q
.venv/bin/ruff check src scripts tests
.venv/bin/ruff format --check src scripts tests
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python -m pip check
.venv-inference/bin/python -m pip check
.venv-inference/bin/python scripts/remote_preflight.py --scope local-preparation \
  --output reports/p3a_remote_preflight_local.json
```

52 项软件测试通过；Ruff/格式/compileall/pip check 通过，最终日志 `p3a_final_checks.json`（授权前的 `p3a_software_tests.txt` 是 47 项测试的历史记录）。pytest 的 transport/world 是合成 fixture，绝不算真实成功；真实成功只来自上述 AppWorld run。额外真实 mask 审计和换目录演练使用本地 Python 脚本片段，其断言结果保存为对应 `p3a_real_*` 摘要。

首次 tokenizer 检查因 `.venv-inference` 缺 jsonschema 失败，固定安装 4.25.1 后通过；日志 `.cache/p3a-install-data-prep.log`。后续 A/B 的多调用拒绝、C 的截断均完整保留。中断计费补强在采样结束后经软件测试，未新增模型请求。

## 限制与下一项工作

1 条示范、1 个成功场景不足以开展有代表性的正式训练。下一步先增加 train 场景覆盖，按 task/scenario 分组；在已授权的远程主机上核验 GPU、驱动/CUDA、同版本 AppWorld worker 和数据，再私有传输、重验 SHA-256、使用 4B tokenizer 重新生成 mask。当前没有远程地址或执行证据，本机预检的 BLOCKED 仅表示“本机不是远程 96GB 环境”。

单响应 2048 token 会截断大批量调用，已作为限制记录；下一次低负载采集可在固定总预算内验证更小批次提示或更高单响应上限。不得将失败剔除后只报告成功。正式 Base-SFT、Recovery-SFT、GRPO 及效果比较均留待对应阶段，本轮停止。
