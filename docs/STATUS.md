# 项目状态

- 最后更新：2026-10-20 15:08（Asia/Shanghai）
- 当前需求：`PROJECT_PLAN.md` / `AGENTS.md` v0.6；正式 Student `Qwen/Qwen3-4B`。
- 本轮状态：**P3-A 本机小样本闭环 PASS**：真实 Teacher 正常任务执行、官方筛选、非空数据导出和 4B Assistant-only mask 均有证据。当前示范数量少且来自同族任务，不代表正式训练规模或总体效果。P3-B 及以后 `NOT_STARTED`。
- P0–P2 17 项原源码/配置/测试哈希不变，旧自然失败及四分支结果保留。
- GitHub：<https://github.com/WYXeva/RecoverAgent-RL>；开发分支 `codex/p0-p2-recovery`，基线 `40836c4c02a3b3785083fe2d5c89eb6c0c2da120`。真实克隆 `/tmp/recoveragent-dev-20261019`，主工作区 `.git` 仍为空且只读。没有更改公开可见性、覆盖提交或强制推送。

## 取消调用次数配额与 prompt 诊断（2026-10-20，v0.6）

- **实现/真实环境验证 PASS**：用户明确取消工具调用次数限制。新默认 `max_tool_calls=null`、`max_tool_calls_per_turn=null`，不存在原 40/64 数量配额；通过官方整数构造参数的 sys.maxsize 哨兵避免旧底层 100 次限制。4096 单回复、累计 token/请求/时间/费用预算及完整调用校验保留，P0–P2 不改写。
- 原 B1 任务的真实公开反馈含 8 歌单、60 条记录、57 首独立歌曲。模型先查 20 首再查 20 首，两批无重复、ID 均来自历史观察；第二批仅 696 输出 token。旧 prompt 允许批量却没有告知 40 次总额度。按原逐首路线预计需 66 次，不能把截停归结为模型不会调用工具。
- 原历史重放：28 条观察逐条一致，原拒绝的 20 次全部真实执行（累计 48）；另一个 world 连续 101 次公开读调用通过。149 次环境动作、0 模型请求，36.14 秒。明确不作为新 Teacher 示范。证据 `reports/unlimited_replay.json`。
- 新鲜原始初态、相同 Teacher/预算/Actor v2、三个独立进程：B 动态发现 **67 调用成功**；A 官方原提示 **5 调用失败**（仍漏登录/凭据）；A `dependency_aware_v1` **61 调用成功**，模型自行选择认证工具。每条件一次；只支持该案例诊断，不支持总体/Student 优势。官方原提示仍保留为默认 A，增强版单独标记。
- 实际新 Teacher 开销：21 请求、128735 输入（85888 缓存命中）、5737 输出 token、133 工具调用、99.99 秒。高峰费率估算 USD 0.020253828，账户观察差额 CNY 0.00、逐请求实扣未知；无训练。每组预算20请求/8192输出/450秒/USD1.50，三组总请求/token/时间预算均未触达。
- 新合并包 `data/unlimited_clean_20261020_a/` 接受 2/3 条，1 个唯一任务；manifest SHA-256 `0a13edb56232ceff8b554f09c2f3885c7e93522423da0c573c3019a3ed994d7f`。真实 4B tokenizer 得到 14 个 Assistant 目标、3988 监督 token；预测提示不入目标，未加载权重。原失败/重放证据不混入成功样本。
- 软件 **75 passed**，Ruff/格式通过；真实 21 回执离线审计重算、两种 A Predictor 公开输入重建、A Actor 提示相同、初态/模型/预算一致、P0–P2 17 哈希未变。证据 `reports/unlimited_prompt_report.md`、`unlimited_prompt_comparison.json`、`unlimited_prompt_audit.json`、`unlimited_sft_format.json`。
- 三份本地完整视图 `outputs/unlimited_prompt_20261020_a/{b_dynamic,a_official,a_dependency}.md`。实际命令见报告。下一步为更多预先固定 train 场景的重复提示对照；保持原版与增强版独立、所有失败完整计入，不启动训练。

## 双工具模式比较与 4096 token 上限（2026-10-20）

- **实现和真实配对验证 PASS**：`api_predictor` 复用固定上游官方源码和提示，只用公开任务/API 简介、零 gold 示例；执行开始后工具固定。`dynamic_discovery` 保留动态注册。所有新默认 Teacher/Predictor 回复最多 4096 token，预算不足时下调；旧记录的 2048 不改写。协议及命令见 `docs/TOOL_MODES.md`。
- 两个官方 train 任务、四条全新 Teacher 轨迹：B1/A1/A2/B2 串行独立 world/进程，同任务初态与 Teacher backend 一致；每次相同 20 总请求/8192 输出 token/40 工具调用/450 秒预算，A 预测开销包含在内。结果 **B 动态发现 1/2，A 官方零示例 Predictor 0/2**，0 基础设施错误；这不是正式 Student 或完整官方 benchmark 成绩，不支持总体优劣结论。
- A 两次预测都漏选登录/凭据工具；B1 在已执行 28 次后申请 20 次工具调用、超过剩余 12 次额度，整批拒绝，非 token 截断。B2 `teacher_5601c6f4d156` 经 8 回合/29 次调用获官方成功。没有自动补认证工具或根据失败结果改写本次协议。
- 真实开销：21 模型请求（含 2 次 Predictor）、60 工具调用、4314 生成 token、122290 输入 token、120.92 秒。公开高峰费率估算 USD 0.023085432；前后余额回执观察差额 CNY 0.00，逐请求实扣未知，不能写成免费。无 GPU 训练、无新付费服务。原始数据在 `runs/tool_modes_20261020_a/`。
- 新 Clean Dataset 接受 1/4 条，SHA-256/train/来源/成本复核通过；固定 4B tokenizer 得到 8 个 Assistant 目标、1474 监督 token，未加载权重或更新参数。Dataset manifest SHA-256 `fc1367decb73b4e70d1bbe9cb052ba3c291f721942f33edaa914cdc03dbe4932`。未宣称远程迁移/训练已执行。
- 65 项软件测试与 Ruff/格式检查通过；离线审计全部 21 份响应，按官方公开输入重建两次 Predictor 消息、解析结果及 schema 一致。P0–P2 原 17 项哈希不变。证据 `reports/tool_modes_report.md`、`tool_modes_comparison.json`、`tool_modes_audit.json`、`tool_modes_sft_format.json`；测试命令见报告。
- 查看器 `--all` 已能分开展示 Predictor 和 Actor：本地四份视图 `outputs/tool_modes_20261020_a/teacher_<ID>.md`，无哈希警告。原始/派生任务数据不公开。下一步是预先固定更多 train 场景与重复采样；若补入认证工具，作为另一个明确标记的变体保留本次负结果。SFT/GRPO 仍 `NOT_STARTED`。

## LLM 请求视图与排错工具（2026-10-20）

- 状态 `PASS`：新增 `recoveragent.inspect_teacher`，离线读取每轮真实 API request/response，展示完整 messages、当时工具 schema 及工具增减、原始响应、后续反馈、token 用量、控制侧诊断和原始回执哈希。
- 严格区分请求前输入、响应后观察与控制侧奖励；不读取官方 evaluator 文件，不从最终历史/最终 schema 回填早期请求，截断响应不会被修补。展示时一致隐藏已识别的密码/访问令牌/API Key；原始记录只读，输出不覆盖已有文件。
- 实际产物：`outputs/llm_view_20261020/success_turn1.md`、`success_all.md`、`truncated_turn6.md`，仅本地保留。用法和当前预算/动态工具发现规则见 `docs/LLM_VIEW.md`。
- 验证：新增 7 项测试，全套 **59 passed**，Ruff/格式检查通过。离线核对现有 6 条真实 Teacher 轨迹的 28 次请求，历史/工具数量、响应终止原因一致、回执无哈希警告，原始文件未修改；证据 `reports/llm_viewer_validation.json`、`reports/llm_viewer_checks.json`。
- 该查看器开发轮的模型请求、AppWorld 新执行、GPU 训练及费用均为 0；当时单回复上限 2048、每轨迹累计上限 16384。后续 v0.5 上限及新采样见上节；历史 C 轮失败为单响应截断，不能据此推断 Teacher 在更宽预算下必然失败。
- 下一步可先用 `--turn N` 定位上下文、工具可见性或截断问题，再决定后续采集参数；本轮没有扩大采样或进入训练。

## P3-A 开发与真实验证（2026-10-20）

- 用户在异步费用授权问题回复“费用不限”后才启动 DeepSeek 请求。本轮保留 2 个唯一 train 任务、累计 80 次请求/32768 生成 token/1800 秒的小试上限，未购买算力或启动训练。默认配置仍关闭付费调用。
- Teacher 为 `deepseek-flash`，供应商文档版本 DeepSeek-V4.1-Flash-1017；实际响应 fingerprint `e72d3c7f663d3e1383b00b3884cb4501` 全程一致。该 API alias 不是不可变权重 revision，也没有与 4B Student 做正式能力比较。
- 原生 Teacher adapter、正常任务 Runner 和批量历史模块已实现。所有业务步骤由 Teacher 决定，只看到公开任务/工具/已发生历史；终态先保存，再关闭 world 并调用官方 `evaluate_task`，隐藏评价不进入消息。
- 三轮均从官方前两个 train 任务 `82e2fac_1` / `82e2fac_2` 的原始初态执行。A：2 次失败（单调用协议拒绝批量）；B：2 次失败（禁用并行参数仍返回批量）；C：1 次截断失败、1 次官方成功。共 **6 次尝试、6 次有效评价、0 基础设施异常、1 次成功**，全尝试成功率 **1/6**；最终协议 C 为 **1/2**。协议不同，不能当作固定策略基准成绩。
- 成功轨迹 `runs/p3a_teacher_20261020_c/teacher_ad808a15ce5a/`：8 个 Teacher 回合、29 次真实工具调用、官方严格奖励 1。其余失败、回执、终态和筛选原因全部保留，不进入成功示范。
- P3-A 允许最多 64 个原生调用组成一批，整批先校验、逐个执行、收齐全部结果再保存边界；截断或无效批次整体拒绝。新终态格式只用于 P3-A 评价证据，不冒充 P0/P2 恢复快照。设计见 D012。
- `data/p3a_clean_20261020_all/`：合并所有 A/B/C 尝试成本，筛得 1 条完整正常 Teacher 示范。Dataset ID `868da089c98626a256308edfa3111d6b914d95ebd3d375d74536a1268181e9bd`；manifest SHA-256 `464ea9bef9e794082b266ae6ed53bc5be358eba3e0c74a026325526a70214b30`。官方 train 成员核验、哈希/来源/顺序重算、同机换目录交接检查通过。原始数据不上传公开 GitHub。
- 正式 4B tokenizer revision `1cfa9a7208912126459214e8b04321603b3df60c`。真实示范转换为 **8 样本、1146 监督 token**，最大样本 12115 token；29 次工具参数往返、前缀保持、非目标上下文及 padding mask 通过。仅 tokenizer/CPU 准备，没有模型权重、optimizer 或 SFT 更新。
- 旧 Qwen 格式问题重算：100 回合中 50 次格式拒绝均为无 tool tags 的普通文本，全部提及 access token；另有 2 次工具执行错误。没有证据说明 JSON parser 丢失了合法调用，也没有证据说明 4B 的错误已被训练解决。
- 软件测试 **52 passed**（含合成 transport/world，仅验证软件）；Ruff、格式、compileall 和两个 venv 的 pip check 通过。真实 API/AppWorld 验收与软件测试分开报告。
- 资源：28 次模型请求、140388 输入 token（含 79232 缓存命中）、7008 输出 token、47 次工具调用；三轮累计采样墙钟 170.22 秒。按公开高峰 USD 费率估算 0.027231792 美元；官方账户前后余额减少 **人民币 0.04 元**，仅为账户级观察差额，未排除其他并发使用，逐请求账单未取得。
- 初始授权前预检和空包 (`p3a_preflight_20261020_a`、`p3a_empty_handoff_20261020_a`) 仅保留为历史记录；不是当前阻塞或成功数据。首次 tokenizer 检查缺 jsonschema，固定安装 4.25.1 后通过。
- 完整命令/限制/证据索引：`reports/p3a_report.md`。交接说明：`docs/P3A_HANDOFF.md`。主要新增代码：`teacher.py`、`teacher_history.py`、`p3a.py`、`demonstrations.py`、`base_sft.py`。
- 远程 RTX PRO 6000 96GB、SSH 传输、远程 AppWorld worker 均未实际验证。下一项工作是扩大 train 场景覆盖、按场景分组准备数据及完成远程预检/迁移；本轮到此停止，不进入正式 Base-SFT、Recovery-SFT 或 GRPO。

## 此前 v0.4 模型与硬件路线记录（2026-10-20，仅文档）

- **唯一正式 Student**：`Qwen/Qwen3-4B`。此前本机 RTX 5050 8GB 上的 `Qwen3-1.7B` 是 P0–P2 基础设施历史，不作为正式 4B SFT/GRPO 的模型或算法结论。
- **本机 P3-A**：执行 AppWorld Teacher 正常任务、真实官方验证/过滤、导出含 SHA-256 manifest 的未分词多轮轨迹；Teacher 是否可用与费用仍需检查。
- **远程 P3-B 以后**：目标单张 RTX PRO 6000 Blackwell 96GB（尚待 SSH、实际 GPU 型号、显存、CUDA/驱动和权限验证），执行 Qwen3-4B Base-SFT、4B Student 自主任务与新失败、多检查点 Teacher 恢复、Recovery-SFT/GRPO 和迭代。需远程可执行的同版本 AppWorld worker，不只复制模型训练数据。
- **交接前置**：保存 Teacher/model/任务/划分/AppWorld 源码数据版本、schema、完整消息、官方成功证据摘要、过滤分母、文件 SHA-256；远程重新使用 Qwen3-4B tokenizer 构建 Assistant-only loss mask，不能直接使用 1.7B token IDs。数据通过经授权安全通道转移，不上传受限轨迹/权重/凭据到公共 GitHub。
- 本次仅文档更新；没有新增 Teacher 成功示范、远程训练、96GB 硬件预检或 4B Student 结果；不得将计划冒充完成。

## v0.3 原训练顺序记录（2026-10-20）

- **已确定的训练顺序**：P3-A Teacher 从 AppWorld 干净初态完成**正常成功轨迹** → P3-B Student **Base-SFT**（全部 Teacher 正常轨迹 Assistant token）→ P3-C Base-SFT Student 独立执行产生**新自然失败** → P4-A 从多个合法检查点由 Teacher 真实修复 → P4-B Student **Recovery-SFT**（仅 Teacher 恢复后缀 Assistant token）→ P4-C Student 恢复 GRPO → P5 每轮新失败 + 新 Teacher 示范 + 增量 SFT/GRPO → P6 最终评价。
- **主要增量**：Multi-Checkpoint Recovery Learning；Post-error/Late-failure 主实验、Pre-error 独立预防消融。V1 规则选点先行，U_SFT=p_T(1-p_S)、U_RL=4p_S(1-p_S) 属于 V2 待验证启发式。
- **下一轮范围**：先核对已知 Qwen 工具调用格式错误及 P3-A Teacher 正常任务成功示范的接口/低负载试点；没有合法可用 Teacher 与费用授权时只做不付费组件/测试。完成经验证的正常 Teacher 轨迹且资源允许才进入 P3-B，绝不按旧方案先做基座失败的 Recovery-SFT 或直接 GRPO。
- **本次修改**：PROJECT_PLAN.md、AGENTS.md、docs/STATUS.md、docs/DECISIONS.md、README.md；未修改实际运行代码、权重、轨迹或旧报告，没有运行训练。

## P0–P2 历史执行记录（2026-10-19）

- 当时依据 v0.2 完成基础设施；后续执行顺序以 v0.3 新用户要求为准。
- 独立开发分支 `codex/p0-p2-recovery`，真实 Git 克隆 `/tmp/recoveragent-dev-20261019`，origin 已核对为用户指定仓库。
- 实现：公开 API 白名单、JSON/schema 参数校验、模型单调用协议、独立推理服务、正常与恢复 Runner、外置不可变快照、完整历史边界、train-only Failure Buffer、四进程串行调度与预算。
- 软件测试：`.venv/bin/python -m pytest -q` → 26 passed（软件逻辑，不替代 AppWorld），证据 `reports/software_tests.txt`。
- AppWorld 实际安装：`0.2.0.dev0`，commit `42b5bcf3cd334fee33f0c37c02070a9f5807add5`，Python 3.14.4。官方数据已下载到 `data/appworld`。
- 真实状态探针：`runs/p0_state_20261019_d` 完整通过。四个独立 PID 的写入、导入、重复恢复和官方评价可核查；公开摘要 `reports/p0_state_evidence.json`。这是基础设施探针，不计入自然失败。
- 真实 Student 自然失败：`runs/p0_p2_20261019_a/normal_dbf9101f8c55/trajectory.json`，train 来源，40 个生成回合、22 次真实工具调用、1471 生成 token、266862 输入 token；终止原因 `budget_exhausted`，官方严格奖励 0。没有人工注入故障或 Runner 补做业务步骤。
- Failure Buffer：同一运行的 `failure_buffer/`；匹配快照为正常轨迹的 `terminal/`。Buffer 重读、train 来源与状态/历史绑定均通过。
- 四分支恢复：4 个独立 PID 串行执行，每条 15 个生成回合、7 次真实工具调用；实际首轮消息、工具 schema、状态与运行时指纹一致，源快照在结束后仍完整。官方严格奖励均为 0，没有发现成功，也没有奖励差异；不能据此判断状态不可恢复。证据 `reports/p2_protocol_checks.json`。
- 最终阶段报告与成本重算：`reports/p0_p2_report.md`、`reports/p0_p2_evidence.json`；原始 worker 日志、token IDs、轨迹、快照和官方评价保存在 `runs/p0_p2_20261019_a/`，不上传原始基准内容。
- 错误证据：`.cache/appworld-unpack*.log`、`.cache/state-probe-b.log`、`.cache/state-probe-c.log`；诊断与兼容决策见 `docs/DECISIONS.md`。
- 当前阻塞与限制：P0–P2 无未解决阻塞。只有 1 个 train 起点；该自然失败的数据库记录与初始状态相同，尚未覆盖 Student 误写业务数据后的补救。P0 的真实写入探针为独立人为测试。没有 dev/test 总体评价或跨主机验收。无行为 log probabilities，当前轨迹不能直接用于在线 GRPO；Teacher SFT 尚未进行。
- 资源：RTX 5050 8GB、约 15GiB 系统 RAM，固定 Qwen3-1.7B BF16、单环境 worker。模型实验共 100 个生成回合、50 次工具调用、919188 输入 token、4404 生成 token、1647.63 秒（27.46 分钟）；基础设施错误和重试均为 0。未触达 150000 生成 token / 90 分钟上限。安装、下载及状态探针另见环境报告。付费服务、租用算力和训练步数均为 0，推理服务已停止。
- 实际实验命令：`PYTHONPATH=src APPWORLD_CACHE="$PWD/.cache/appworld" IPYTHONDIR="$PWD/.cache/ipython" timeout 5400 .venv/bin/python -m recoveragent.cli run --config configs/p0_p2.json --output runs/p0_p2_20261019_a`，日志 `.cache/p0-p2-a.log`。
- 复现与验证命令：真实状态探针、推理服务、主实验、摘要重算、pytest 与 Ruff 的完整实际命令见 `reports/p0_p2_report.md`；环境安装和兼容性日志见 `reports/environment_report.md`。
- 当时的下一步计划已由 v0.3 替代；现在 **先 Teacher 正常成功轨迹与 Student Base-SFT**，再从 Base-SFT Student 新自然失败生成 Teacher 恢复示范与 Recovery-SFT，最后才是 GRPO。

## 上次文档发布记录：已完成

- 阅读 `AGENTS.md`、`PROJECT_PLAN.md`，确认初始项目只有两份设计文档，没有代码、依赖、模型或实验结果。
- 通过已连接的 GitHub 账号核对身份为 `WYXeva`。
- 添加 `README.md`，明确项目路线和未实施状态。
- 添加 `.gitignore`，排除凭据、环境、原始数据、快照、轨迹和权重。
- 保留既有计划与工作约定原文。
- 在可写的临时目录准备独立 Git 发布副本：`/tmp/recoveragent-publish-20261019-i0v5ybuw`，分支为 `main`，仅包含上述两份原始文档及新增的 README、忽略规则、状态记录，共五个文件。
- 五个文件均为小型 UTF-8 文本；常见 token/私钥模式检查未发现匹配，预期敏感路径忽略检查与新增文件空白检查通过。模式检查不代表完整秘密审计。
- 复查远端初始提交 `a9c5b1ba769bab3e17e4a8affe5b446e0f202b65`，确认仅有一行项目标题的 README；在其上追加文档提交，没有强制覆盖历史。
- 通过 GitHub 连接器完成文档发布提交 `c13b316d0939ea92b367f0f91c99cfb6210b7334`，包含 `.gitignore`、`README.md`、`AGENTS.md`、`PROJECT_PLAN.md`；本状态文件随后单独提交。
- 回读发布树 `c20bfc95d80b2cc8f1326a2a87fff80987c40e13`，上述四个文件的 Git blob SHA 与本地逐字节计算结果全部一致。

## 上次文档发布：检查证据与当时阻塞

实际执行的检查：

| 命令或接口 | 实际结果 |
| --- | --- |
| `git status --short` | 当前目录不是有效 Git 仓库；现有 `.git` 为空且只读。 |
| `gh auth status` | 报告认证检查失败；因后续发现网络不通，不能据此断言 token 已失效。 |
| `gh api user --jq '{login: .login, id: .id, name: .name}'` | 无法连接 `api.github.com`。 |
| `curl -I --max-time 15 https://api.github.com` | 无法解析 `api.github.com`，退出码 6。 |
| 使用提升权限的 `curl` 检查 GitHub | 自动审批拒绝执行：当前会话禁止 sandbox 权限提升。 |
| GitHub 连接器 `get_profile` | 成功核对账号为 `WYXeva`。 |

以上是首次尝试的阻塞记录。继续执行时，终端仍无法连接 GitHub，项目根目录 `.git` 仍只读；但远端仓库已存在，GitHub 连接器对该仓库具有写入权限，因此通过连接器完成发布，不依赖终端 `git push`，也没有绕过终端权限限制。

本轮实际执行的远端操作（仓库均为 `WYXeva/RecoverAgent-RL`）：

| 操作 | 实际结果 |
| --- | --- |
| GitHub `get_repo`、`fetch`、`fetch_file` | 核对账号、公开可见性、`main` 分支与原有 README。 |
| `create_tree(base_tree_sha=e8a5957d53d32bcda1aa71592e7242e03049270e)` | 生成文档树 `c20bfc95d80b2cc8f1326a2a87fff80987c40e13`。 |
| `create_commit(parent_sha=a9c5b1ba769bab3e17e4a8affe5b446e0f202b65)` | 生成文档提交 `c13b316d0939ea92b367f0f91c99cfb6210b7334`。 |
| `update_ref(branch_name=main, expected_sha=a9c5b1ba769bab3e17e4a8affe5b446e0f202b65, force=false)` | 成功推进 `main`，保留初始提交。 |
| `fetch` 回读文档树；Python `hashlib.sha1` 计算本地 Git blob SHA | 四个项目文档全部匹配。 |

GitHub 发布已完成；本地原始工作目录的 Git 初始化及与远端同步仍受当前 `.git` 写入限制影响。临时发布副本中的 `4d84f74` 是首次准备提交，不是本次远端发布提交，不能用它覆盖当前远端历史。

## 上次文档发布：资源与验证范围

- 仅进行文件、Git 与 GitHub 连接检查；没有安装项目依赖或启动实验。
- 模型生成 token、训练步数、GPU 用量及付费模型调用均为 0。
- 没有真实 AppWorld、Teacher SFT、GRPO 或效果提升结果。

## 上次文档发布：当时下一步

1. 后续需要本地 Git 工作流时，在允许 Git 写入且可联网的终端中接入当前远端历史；无需再次创建 GitHub 仓库。
2. 用户授权开始实现后，按计划推进 P0–P2；本轮发布任务不启动训练。
