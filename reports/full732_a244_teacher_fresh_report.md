# Full732 Stage 3A — A244 全新 Teacher 采集工程与阻塞报告

2026-10-28。基线 `29216c7516d9604838ca499f04327997001f2e84`，分支 `codex/p0-p2-recovery`。本轮只实施 Stage 3A；没有进入训练或 B/C 任务执行。

**结论：工程测试和单任务无模型环境往返通过，真实 Teacher 采集 BLOCKED。** 冻结 A244 已提取，244 项都没有发送模型请求；没有新 Teacher 示范，不能宣称完成 Stage 3A。逐项来源、状态、费用和来源哈希见 [244 行结果 JSON](full732_a244_teacher_fresh_report.json)。原 95 条 Teacher 示范未读取、分析或复用。

## 1. 真实分母与成本

| 指标 | 实际结果 |
| --- | ---: |
| 计划任务 / 场景 | 244 / 244 |
| 真实 Teacher 已尝试 | 0 |
| 官方成功 / 合格 SFT 候选 | 0 / 0 |
| 真实 Teacher 失败 / 未知评价 | 0 / 0 |
| 采集前阻塞 / 未执行 | 244 / 244 |
| Teacher 请求 / 工具调用 | 0 / 0 |
| Teacher 输入 / 输出 token | 0 / 0 |
| 由本轮已发推理请求产生的费用 | 0（没有推理请求） |
| 已知收费不确定请求 | 0 |
| Teacher 成功率 | N/A，分母为 0，不写作 0% |

阻塞是未执行的子集，两项不能相加为 488。中转站账单未独立核验，费率也尚未知；不能把预算、第三方官网单价或软件模拟 usage 当成实际账单。只有无模型探针执行了 1 次真实公开工具，不计入 Teacher 工具调用。另有只读 HTTP 配置诊断，不属于模型生成。

## 2. `.env` 诊断与实际阻塞

用户确认凭据是购买的中转站 API，并将 `DEEPSEEK_BASE_URL` 改为 `https://znbcode.com/v1`；`DEEPSEEK_MODEL=deepseek-flash`。dotenv 语法有效，没有发现 Key 首尾空格或重复 `Bearer` 前缀。凭据不写入源码、报告或 Git。

旧 Teacher 代码从凭据文件读取 Key，但 endpoint/model 来自 JSON 配置，不会自动采用 dotenv 的同名变量。此前官方 `/user/balance` 的 HTTP 401 是在**官方地址与中转站 Key 不匹配**时观察到的，不能据此断言购买的 Key 无效。新入口固定中转站 endpoint/model，并在 dispatch 前校验 dotenv 与冻结配置一致；不允许凭据文件暗中更换运行来源。

用户提供的 [接入页面](https://znbcode.com/custom/quickstart) 实际 HTTP 200。其公开 `window.__APP_CONFIG__.custom_endpoints` 明确列出默认 OpenAI 兼容地址 `https://znbcode.com/v1`；现有 Runner 追加 `/chat/completions`，因此**当前 URL 写法正确**。读取这页之前，两次 `/v1/models` GET 都为 403，公开价格/状态探测亦为 403。后一次响应为 text/plain、server=cloudflare，没有取得标准 JSON 错误；不足以区分网关限制、接口权限或认证问题，也不证明 chat endpoint 一定不可用。没有发送生成请求验证它。

更关键的是同页公开使用政策明确排除将服务输出用于模型训练、微调，以及批量采集工具调用 Agent 轨迹作为训练数据。A244 → Base-SFT 正是这类用途。当前没有取得针对此研究用途的服务方例外授权，因此不能仅修改 URL、READY 字段或填入价格就开展采集。这是已读取的用途限制，不是根据 403 推测的原因。

- 政策来源：该页面的 `login_agreement_documents[id=usage-policy]`；页面声明 revision `a5b2ad4a23972837`、updated_at `2026-09-04`。
- 本次页面原始 SHA-256：`71857f6c4d0738896a72221a8ecf8f3839c0629ab5c4651f53299137c30a7b12`；原文仅保存在私有证据目录。
- 费率/计价单位未核实；冻结新配置的三项费率均为 null，不能沿用 DeepSeek 官方费率。relay 模型别名的上游版本、能力、指纹与参数兼容性尚未实际核实。
- `provider_preflight` 拒绝已审查的不允许训练数据用途；将 `provider_access_verified` 改为 true 不能移除该阻断。源代码、配置和用途证据一起受新锁约束。

需取得覆盖本用途的服务方明确授权，或选择允许该用途且经核实的服务；然后核验 endpoint、模型、费率及实际工具调用能力，创建新的冻结采集配置。不能静默替换服务或对未知计费请求重发。

## 3. 固定 A244、用途与预算

直接提取原 Full732 manifest 的 `role=base_sft`，没有重新分配、读取历史成绩或缩回 A30。原始来源为 train 30、dev 19、test_normal 56、test_challenge 139，各任务对应一个不同 scenario。B/C 不进入采集。

- 原划分文件 SHA：`0466cec29d9ad770a1cfeacb4c93945e5fcbcad68b9e980c5264d447418121a2`。
- 原 manifest SHA：`086c0e2a1ac1143967d7ea8af06e0cc434aecdb39a51e1fe6c5fd42585ae2cab`，seed 20261028。
- 预定 smoke 为按 ID 排序的第一个 A 侧官方 train：`07b42fd_2`；其后剩余 A 按 ID 排序。每批 10 项，每项第一轮一次，不做付费自动重试，不按成功与否替换任务。
- 用户对本轮总费用回答“无上限”。工程仍保留每任务 USD 1，因此第一轮最多 USD 244；不是实际报价。每任务 40 请求、16384 累计生成 token、600 秒，每回复最多 4096，工具次数不限。总请求上限 9760、输出上限 3997696。只有合法费率及用途门槛满足后才有可执行成本预留。

本轮依据更新后的 Stage 3A 文档，显式区分**独立研究**和官方 benchmark。安装包实际许可证 SHA 为 `3786bdb398d95201c067c3b59a3943659a36783579f70ad0fab99f915b499609`：保护包采用 Apache-2.0 并附公开传播需加密要求；结合 [上游许可说明](https://github.com/StonyBrookNLP/appworld#-license)，本入口限定本地私有研究，不公开受保护材料，不声称官方排行榜或原官方 test 泛化成绩。许可核验不由旧清单里的可编辑 READY/BLOCKED 字段决定。

244 项的原 split 清单 SHA、成员身份、数据版本、所需文件存在性及共享 DB/schema 文件哈希已预检；**不等于 244 项都已实际初始化或评分**。仅 `07b42fd_2` 有本轮真实运行证据，其余 243 项运行就绪性仍待逐项验证。AppWorld 数据许可与 Teacher 服务输出用途许可是两个独立门槛，前者通过不能代替后者。

## 4. 实现及保留边界

- `research_access.py`：核验冻结 A 身份、原 split、数据许可证与版本，拒绝 B/C、来源改标、公开再分发和官方 benchmark 冒称。新记录使用独立 `clean-teacher-custom-full732-v1` 格式。
- `fresh_teacher.py`：复用 P3-A Runner、现有严格过滤及 SFT 编码，提供串行 campaign、独占文件锁、确定 attempt ID、dispatch 前持久化 claim、已完成证据校验和中断保守预留。已 claim 的未知/中断任务不会自动重发；已知 usage 留存，未知部分继续预留。业务失败可继续，基础设施、未知费用或快照评分不一致停止。
- Adapter/P3-A 接线：只有显式 custom context 才使用经许可证核验的入口；原默认 train 限制不变。Agent 只接收公开任务和工具，隐藏评价始终保留在控制侧。
- 成功候选：先复用官方原任务评分，再独立进程恢复终态，核验行状态、model_hashes、SQL 变更追踪、文件和完整原始评分；复用 Stage 2B 诊断，不修改官方 evaluator。不因空 hashes 本身拒绝只读终态。失败、部分执行与未知状态不冒充成功样本。
- v6 过滤兼容：原 AnswerGuard/Harness 示范复验仍要求 System Prompt 包含旧规则文字，这也会错误拒绝 v6 新数据。新 custom 格式核验精确 v6 原文、实际控制器声明和完整原回执重放；**没有关闭实际 guard/schema/可见性/纠错验证**。旧格式保留原检查。
- 编码：用原 Qwen3-4B tokenizer/non-thinking 模板及当前回合 Schema；只监督当前 Assistant。整轨迹超 32768 或单目标超 4096 进入 deferred，不截断；工具名/参数须经 Student parser 无损往返。当前尚无真实候选可编码。

新配置及锁为 `configs/full732_a244_teacher_fresh_v1.json`、`configs/full732_a244_teacher_fresh_lock.json`。本轮尚未付费派发，开发期间修订只涉及这份新草案锁；旧运行锁不重签。`execution_v6.py` 文件和原始 System Prompt 完全未改。

因为共享 Adapter/过滤源码发生变化，历史 Student/Teacher 锁在当前 checkout 应拒绝；历史重现需原 commit。测试里的临时源码锁仅隔离测试当前协议行为，**不是可发布的新 Student 锁或执行授权**。官方评价、state.py、student.py、base_sft.py、旧数据、权重、历史报告和划分未改。遵照不读取旧示范要求，本轮没有对旧 95 条数据做重新内容/哈希审计。

## 5. 验证：三类证据分开

| 验证 | 实际结果 | 限度 |
| --- | --- | --- |
| 完整软件 pytest | 481 PASS，新增 14 项 | 模型回执是合成；不代表 Teacher 能力 |
| Ruff check / format | PASS / 137 文件已格式化 | 仅代码检查 |
| 真 AppWorld 无模型探针 | `07b42fd_2`，1 次公开文档查询，原始与恢复评分均 1/5，完整有效失败；10 项评分保真检查 PASS | 没执行业务解题，不是 Teacher 成功示范 |
| 真 Qwen tokenizer + 合成回执 | 1 个 Assistant 样本，24 监督 token，最长序列 559；mask、padding、调用解析 PASS | 0 模型推理，不计入训练候选 |
| 真实 Teacher smoke / A244 | 未运行，BLOCKED | 不能报告真实能力或成功率 |

真实环境使用已验证的 SQLAlchemy 2.0.52 / SQLModel 0.0.48。保存/恢复是两个独立 world 进程，评分也分别在独立进程进行。只读查询无新增业务写入，空 model_hashes 合法；数据库、SQL 追踪、13 个快照文件及官方 tracker 保持一致。这不替代未来每条成功写入终态的逐条复验，也不把现有全局 scoring PARTIAL 改为 READY。

软件覆盖固定清单和排除 B/C、许可证/来源拒绝、总预算、原子 claim、文件篡改、已知 usage 与未知费用、中断不重复派发、配置/源码锁、合成完整 P3-A worker、v6 guard 复验、endpoint/model 不匹配和服务用途拒绝。既有批次/schema/纠错/mask 测试继续通过。开发时发生的 tokenizer 路径/探针字段读取错误及草案锁失配留存私有日志；修正后以上最终检查通过，未产生付费请求。

主要命令（仓库根目录）：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/ruff check --no-cache .
.venv/bin/ruff format --check .

# 已执行的无模型环境探针；脚本与原配置在私有 runs 目录
PYTHONPATH=src .venv-harness/bin/python runs/full732_a244_fresh_20261028/environment_probe.py
PYTHONPATH=src .venv-harness/bin/python -m recoveragent.fresh_teacher replay \
  --config runs/full732_a244_fresh_20261028/world_probe.config.json \
  --task 07b42fd_2 --source runs/full732_a244_fresh_20261028/world_probe \
  --output runs/full732_a244_fresh_20261028/world_probe/replay
```

以上 probe 输出是不可覆盖的证据目录，不能直接重跑到同一路径。私有根目录 `runs/full732_a244_fresh_20261028/` 保存原始 HTTP 诊断、许可预检、环境/恢复评分、合成分词来源、测试日志与完整性记录。公开 JSON 只包含脱敏清单/计数/SHA；不包含原任务、测试程序、答案、凭据或轨迹原文。

将来满足服务用途、访问、模型身份和价格门槛后，用新冻结配置从原定首题启动 `python -m recoveragent.fresh_teacher collect --config <新配置> --output <新私有campaign目录> --credentials-file .env --max-batches 1`。当前配置必定拒绝付费派发。smoke 通过真实完整链路后才继续相同清单，不能把本轮软件模拟当作已通过该门槛。

## 6. 剩余事项与停止点

1. 解决 Teacher 服务训练数据用途许可，核实兼容 endpoint/model、费率、usage/指纹回执；不能通过改状态字段放行。
2. 重新冻结合法服务配置，执行预定首任务真实 smoke，再覆盖全部 A244；成功逐条跨进程复评并验证编码。
3. 244 项真实采集、成功率、新候选数量及最终数据包当前都未完成。新 Base-SFT、Student 推理、GPU、Recovery-SFT、GRPO 和 C244 验证均未执行。

本轮提交代码与真实阻塞报告后停止，不进入 Stage 3B。
