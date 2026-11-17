# S2：真实 Student 生成题验收

2026-11-17，基于 `7d99d0d`。本轮结论：**执行与提交正常，Judge 正确性尚未通过；不启动正式 Evolve。**

## 范围与结果

按用户要求，先从已准入生成池固定第一题，让已验收 Qwen3.5-9B S0 从相同干净初态独立执行四次，再调用正式 Student 奖励路径的共享 Judge。使用现有 L40S、vLLM 0.19.0、64K、不同种子 `20261117..20261120`，两条并发。没有重做 Solver，没有生成新题、更新参数或运行 dev/test；没有按结果换题或补跑。

题意：只批准指定联系人发来的待处理 Venmo 请求，报告批准数与最终余额。来源 `ccf4b82_1`，生成任务 `coevolve_1ede081c3acc8ad19924cec6`。实际应批准两笔，金额分别为 37 和 21；起始余额 1590，最终余额 1532。这个核对结果仅在控制侧事后使用，没有发给 Student 或 Judge。

| 次数（member） | 实际审批 | 实际提交 | Judge | 证据复核 |
| --- | --- | --- | --- | --- |
| 1（0） | 两笔均批准 | `Approved: 2; Balance: $1532.00` | 成功 | 正确 |
| 2（1） | 只批准 37 的一笔 | `Approved: 1; Balance: $1553.00` | 失败 | 正确：漏批 21 的一笔 |
| 3（2） | 两笔均批准 | `Approved: 2; Balance: $1532.00` | 失败 | **误罚：以未翻页为由拒绝** |
| 4（3） | 只批准 37 的一笔 | `Approved: 1; Balance: $1553.00` | 失败 | 正确：漏批 21 的一笔 |

业务结果是 **2 成功 / 2 失败**，原 Judge 回执是 **1 成功 / 3 失败 / 0 接口异常**，结论一致 3/4。四次完成提交均第一次接受，0 Schema/协议拒绝、0 null 退化。

## 误判依据

第 3 次 Judge 理由认为：返回满页 20 条但没有继续翻页，因此无法证明找到了全部待处理请求。它据此把任务判成业务失败。

实际证据：

- 同一初态的已有 Solver 完整读取待处理列表三页：20、18、0 条；指定联系人只有两条待处理请求。
- 四条 Student 的初态 SHA 与该准入初态逐一相同：`f89f5878f31d62bad2ea03eff9c5bd240f80cd0993db99ca5ba4ad12587a3259`。
- 第 1、3 次均执行且成功批准这两笔、读取余额 1532，未批准其他人的请求；最终答案正确。
- 二者保存的完整 Venmo 终态变更文件也完全相同，SHA `63cd864f166adfb60464503dcdad7859c07413d10818bb59e1c9fe7fd894b86d`。该哈希只用于事后诊断，未作为在线奖励规则。
- **第 1 次同样没有继续翻页，却被同一 Judge 接受。**公开任务没有规定必须翻页。由“无法从当前观察证明穷尽”直接推导“业务未完成”，同时额外要求某个操作步骤，是本次错误；不是输出格式、上下文截断或金额算错。

两个真实失败也有直接依据：模型已经看到了第二笔待处理请求，却没有批准。Judge 对这两条均正确指出遗漏，不能归咎于接口或环境。

原始奖励、Judge 理由和轨迹均保留。独立审计将第 3 条标记为评分污染；通过现有整组有效性规则检查，整组退出更新和能力负例反馈。原始组可读出 `boundary`，但其原始优势值**未用于训练**；审计排除后优势为 None、反馈不完整。0 次 optimizer 更新。

## Judge 实际配置与成本

四次实际请求均为 OpenCode Go `deepseek-v4.1-flash`，`thinking=enabled`、`reasoning_effort=high`、`max_tokens=8192`，不发送 temperature。入池与 Student 奖励使用同一个 `coevolve_llm.judge()`，reason-first 提示 SHA 仍为 `7fa7f33bef47d4b3fde4f5a25c8df67978d0e920119eead97a1a5560c1ee9f35`；没有改提示或反复重判。

Teacher 输入窗口仍为 **1,000,000 token**。8192 是单次思考与最终回答的输出上限，不是输入窗口。完整请求由匹配 DeepSeek tokenizer 加明确 framing 余量估算；接口实际用量单列。四次输入均未超窗、未裁剪、输出均 `finish_reason=stop`，且包含非空 reasoning 与 reasoning 用量。误判那次输出 5408 token，没有碰到输出上限。

| 用量 | 实际值 |
| --- | ---: |
| Student 请求 | 51 |
| Student 输入 / 输出 token | 419,035 / 4,147 |
| Student 生成耗时合计 | 125.43 秒 |
| 四条 episode 耗时合计（并发） | 296.64 秒 |
| Judge 请求 | 4 |
| Judge 输入 / 输出 token | 39,594 / 8,335 |
| 其中 reasoning token | 8,015（包含在输出中） |
| Judge 请求耗时合计 | 151.93 秒 |
| 从运行绑定到结果汇总（含 vLLM 启动） | 346 秒，约 5 分 46 秒 |
| Judge 项目保守估价 | USD 0.0218802 |
| 接口 list_cost / allowance | 0.0109401 / 0.00656406 |

接口 `usage.cost=0` 为 Go 订阅记账，不宣称无成本。GPU 实际账单不可得。vLLM 启动另有 5 次合成连通性/长窗请求、10 个输出 token，不计为真实任务轨迹。

此前准备阶段复用原 9 题 Solver 证据做了 9 次新 Judge 准入，9/9 被接受：输入 153,182、输出 12,635 token、调用耗时合计 198.61 秒，保守估价 USD 0.0611166；不把这 9 个布尔结果称为已逐题人工验收。本次选择的第一题已完成上述独立核对。此前固定 10 例重判结果仍保留，本轮未重复调用。

## 实现与软件验证

- 小规模 Student 验收脚本的总输出预留改为 `4 × 当前 Judge max_tokens`，避免仍按 4096 预留、不能覆盖四次 thinking 请求。没有改变 Teacher 输入窗口或任何角色的采样设置。
- 检查发现旧验收薄包装仍设置 `protocol_probe`，会略过正式入池证明与初态检查。本批真实调用复用了相同解析、提交、环境和 Student reward 函数；**上述证明与四条初态一致性是在事后完整核验**。随后修正薄包装：已准入题走普通 rollout，执行前验证准入，不再携带该诊断豁免。这个入口修改只做了 CPU 回归，未再付费重跑四条，不能将其写成本批已经执行的版本。
- 此前一轮运行准备保留：独立 `v2_s2_evolve_round1_20261117` 配置及 234 文件源码锁，最多 40 组并在轮末 dev24 后停，生成仅在第 8/16/24/32 组触发；监督器可从新目录启动。**只完成软件准备，正式运行尚未启动。**

实际运行的软件检查：

```bash
PYTHONPATH=src:. .venv-v2/bin/pytest -q \
  tests/test_s2_judge_thinking.py tests/test_coevolve_light_llm.py \
  tests/test_completion_submission_v2.py tests/test_s2_goal_integration.py
# 61 passed，1.02 秒

PYTHONPATH=src:. .venv-v2/bin/pytest -q \
  tests/test_s2_round_stop.py tests/test_s2_experiment.py \
  tests/test_s2_background.py tests/test_s2_parallel.py tests/test_s2_teacher_context.py
# 42 passed，13.44 秒

.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
# check 通过；286 文件格式通过
```

CPU 测试含模拟 Judge/环境：四次配置预算、准入必需、普通 rollout 接线、断点不重复采样、禁止更新、真实请求与响应解析、正常失败/评分无效、数字/JSON/null、同源分组与一轮停止。它们不能替代上述真实 Judge 可靠性结论。

## 产物与后续

私有本地：`runs/s2_evolve_round1_20261117/`，含 `student_probe_input.json`、`student_acceptance_audit.json`、原始完整回传与终态。远端：`/root/gpufree-data/recoveragent_s2_evolve_round1_20261117_01/student_acceptance_001/`。执行源码归档保留，实际源码锁 SHA `33bc4d897c454fae6962e36c53cdc0131dcb56bb211e5d65af93209c384c3718`，与之后修正的包装入口区分。

两份归档已回传并验证 SHA：

- 执行证据 `c5650c6aafbd76595787ae2c05e61b72eeea08ab86632dc6b8bba90f7963906b`。
- AppWorld 终态 `7e2965b8bec87d68c678226c1a60ad277fed6b7091ad0c7b546f05aecef57a57`。

同类受限验收入口（新目录、有效资源截止；本轮不再执行）：

```bash
PYTHONPATH=src:. RECOVERAGENT_S2_EXECUTION=v2_s2_evolve_round1_20261117 \
RECOVERAGENT_TEACHER_TOKENIZER_DIR=/path/to/pinned/teacher_tokenizer \
python scripts/probe_s2_coevolve.py student \
  --input /path/to/frozen_accepted_task.json \
  --deployment /path/to/authorized_deployment.json \
  --output /path/to/new_acceptance_run
```

本次 GPU 服务已退出，确认显存 0 MiB；没有自动关闭平台实例。下一步应处理 Judge 将证据缺口或自加步骤判成业务失败的问题，再做固定复验。当前不改冻结提示、不追加付费重判、不启动正式 Static/Evolve；尚不能宣称新的奖励链足够可靠用于训练。
