# 技术决策

当前需求为 `AGENTS.md` / `PROJECT_PLAN.md` v0.3；D001–D006 是 2026-10-19 P0–P2 的历史工程记录，不因本次方案改变而修改。

## D001 · 2026-10-19 · 固定源码与独立环境

AppWorld 固定到计划已核对的 commit `42b5bcf3cd334fee33f0c37c02070a9f5807add5`，实际包版本为 `0.2.0.dev0`。[官方 pyproject](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/pyproject.toml) 明确支持 Python 3.14，因此沿用本机 3.14.4。

直接安装该源码归档时，最新 Pydantic 2.14.1 与现有补丁冲突，导入失败：`match_type() takes 2 positional arguments but 3 were given`。固定到 2.12.3 后可导入。ZIP 中 `.source/*.bundle` 是 LFS 指针，必须下载同 commit 的真实对象并核对指针内的 SHA-256 和大小，再使用官方 `appworld install`。解包缓存设置 `APPWORLD_CACHE` 到项目 `.cache/appworld`。没有修改依赖源码。

AppWorld 与推理分别使用 `.venv`、`.venv-inference`；实际依赖另存 freeze。基础数据、解包内容和权重均不提交。

## D002 · 2026-10-19 · 恢复完整性与时钟兼容

`save_state(state_id)` 导出目录在 world 输出目录之外。manifest 同时绑定逻辑记录指纹、文件哈希、合法历史、完整工具 schema、Python RNG、交互计数、任务时钟、基础数据库和原始任务文件哈希。每个 worker 创建唯一输出目录，再复制快照到其检查点目录并调用 `load_state`。用数据库记录的规范化内容验证状态，文件哈希仅作完整性校验。

真实探针发现，[上游 `load_state`](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/src/appworld/environment.py) 调用 `close_all()` 后重建 requester 时钟，却保留 world 的旧 freezer；随后 `close()` 出现 `fake_names` 属性错误和 freezer 栈空错误。适配层在 load 后调用该版本已有的 `_set_datetime()` 重建 world 时钟。该内部方法依赖是明确的兼容点，升级上游时必须重跑真实状态探针。失败日志保留于 `.cache/state-probe-b.log`，不通过吞掉异常掩盖问题。

仅允许无跨轮 Python 变量的直接函数调用，禁止 Actor 改时钟；环境 RNG 在各恢复分支相同，只有模型采样 seed 改变。真实墙钟预算使用 `clock_gettime(CLOCK_MONOTONIC)`，避免被 AppWorld 的 frozen time 冻结。

真实跨进程验证另发现官方 evaluator 会生成任务目录中的 `__pycache__`。基础输入指纹排除 Python 字节码缓存，仍校验原始任务文件、基础数据库和版本文件；快照自身的完整性检查不排除任何文件。完整四进程写入/恢复探针最终通过，证据为 `reports/p0_state_evidence.json`，原始记录位于 `runs/p0_state_20261019_d`。

## D003 · 2026-10-19 · 工具与评价隔离

执行 world 使用 `load_ground_truth=False`。Actor 只得到任务公开信息、官方公开工具、自己的历史及中性的继续提示。初始开放 `api_docs` 与 `supervisor`；Actor 调用 `show_api_doc` 后，才把选中的公开函数 schema 加入后续上下文。此工具发现协议、每回合一个调用和 `enable_thinking=False` 对四个恢复分支保持一致。

参数先解析 JSON、按官方 schema 验证，再用受白名单限制的函数名和 Python 数据字面量执行。多调用响应整轮拒绝，不执行其子集。所有登录与业务步骤由 Student 决定。

终态快照及官方输出保存后关闭 world，再由控制侧调用官方 `evaluate_task`；它重新加载原始任务作为参照。不能使用要求 world 加载 ground truth 的 `world.evaluate()`。官方断言、奖励和其他分支结果不会加入 Actor 消息。

## D004 · 2026-10-19 · 模型、预算与结果解释

本机实际 GPU 为 RTX 5050 8GB。按 v0.2 已允许的资源回退，首轮候选采用 `Qwen/Qwen3-1.7B`，固定 revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`。使用本地 Transformers、BF16、禁用 thinking，不调用付费服务，不训练。

首个有效自然失败出现后立即尝试同起点四分支。预算沿用计划：正常 40 回合/40 调用/8192 token，恢复 15/15/4096；总 150000 生成 token、90 分钟、10 个 train 任务上限、基础设施最多重试一次。大规模权重下载与环境安装属于准备阶段，另行记录；采样预算从实际实验开始计时。

保存实际生成 token IDs 和输入 token IDs，尚无行为 log probabilities，明确 `rl_eligible=false`；这些 P0–P2 数据不能直接当在线 GRPO 训练批次。四个同奖励分支可以通过基础设施验收；P3 的 Teacher SFT、GRPO 条件以及效果提升均独立验收。

中断导致生成量不可确认时，单独记录 `generation_reservation_tokens` 并从总预算扣除，绝不将预留量记为真实生成量。进程超时不补零奖励；失败尝试和重试均保留日志。网络下载中断日志位于 `.cache/download-student.log`，采用一次限时断点续传。

## D006 · 2026-10-19 · ReTerm 参考范围

已核对 [ReTerm 官方说明](https://github.com/twothsnd/ReTerm)：保留基座失败状态与历史、由更强 Teacher 继续执行、只监督 Teacher 恢复后缀，以及同起点分支验证、有限恢复队列，是本项目参考的方法。当前工程只完成 AppWorld 所需的 P0–P2 基础设施，没有复制其分布式训练栈，也不把这些机制宣称为原创或完整 ReTerm 复现。

## D005 · 2026-10-19 · Git 工作目录

原始工作目录 `.git` 为空且只读。保留其现状，使用 `/tmp/recoveragent-dev-20261019` 的真实克隆进行版本管理，remote 为 `https://github.com/WYXeva/RecoverAgent-RL.git`。开发分支 `codex/p0-p2-recovery` 从远端 `14be212003e2af85457377eaf4e154208ea978c7` 创建；保留所有既有提交，不修改 main、不强制推送。源码在用户工作区实现后，显式同步已审查的小型文件到该克隆。

## D007 · 2026-10-20 · 两阶段 Teacher SFT、Multi-Checkpoint 与增量恢复

- 用户明确要求先用固定更强 Teacher 的正常成功轨迹训练 Student **基础执行能力**；只有完成 Base-SFT 后 Student 才自主执行、暴露新错误，由 Teacher 从多个真实检查点接管修复，再训练 Recovery-SFT，随后 Student 在线恢复 GRPO。
- 改造阶段顺序：P3-A Teacher Clean Data；P3-B Student Base-SFT；P3-C 新 Student Failure Mining；P4-A Multi-Checkpoint Teacher Recovery；P4-B Recovery-SFT；P4-C GRPO；P5 Teacher 辅助增量 SFT+GRPO；P6 官方最终评价。
- Base-SFT 只监督 Teacher 正常成功全轨迹 Assistant token；Recovery-SFT 仅监督 Student 失败状态后 Teacher 恢复后缀的 Assistant token。系统、用户、工具输出、隐藏评价都不能成为 SFT target。
- 多检查点 V1 为规则选择，主研究对象为 Post-error 与 Late-failure；Pre-error 仅作错误预防消融。V2 U_SFT=p_T(1-p_S)、U_RL=4p_S(1-p_S) 为待验证启发式。必须控制 Teacher 全尝试成本、每任务数据权重及 SFT/RL token。
- P0–P2 老实验仍有效但只说明基础设施；本次仅更新 GitHub 文档，未运行新实验、没有 Teacher 数据、两阶段 SFT 或 GRPO 的通过证据。无授权不得调用付费 Teacher。
