# 项目状态

- 最后更新：2026-10-19 23:30（Asia/Shanghai）
- 当前研究阶段：P0–P2 实现与真实环境验收完成；P3–P5 未开始。
- 本轮任务：真实 AppWorld 状态恢复、Student 自然失败与四分支恢复；不训练。
- 本轮状态：`PASS`（P0 / P1 / P2 基础设施）。1 条真实自然失败和同起点的 4 条恢复全部保存并经官方评价，恢复奖励 `[0, 0, 0, 0]`；26 项软件测试通过。训练管线未验收，未支持效果提升。下方保留上次文档发布记录。
- 远端仓库：<https://github.com/WYXeva/RecoverAgent-RL>，默认分支 `main`。
- 实际可见性：公开。继续执行时发现该仓库已经存在且设为公开，本轮保留其可见性与初始提交；此前拟建私有仓库的方案未执行。

## 本轮推进（2026-10-19）

- 需求仍为 v0.2；未改变 Teacher SFT 强制前置。
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
- 下一步：本轮停止于 P2，代码和公开证据由上述开发分支交付。只有另行授权 P3 后，才配置更强 Teacher 生成经验证的真实恢复示范，并完成 Student 恢复后缀 SFT；真实 SFT 重载与开发评价通过后才考虑 GRPO。

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
