# S2 轻量生成与共享 LLM Judge：本地接线验收

日期：2026-11-17。基于已审查 `d07fdcd`，分支 `codex/p0-p2-recovery`。本轮完成本地实现、回归与运行准备；**0次新Solver/Judge调用、0条fresh rollout、0次正式模型更新，没有连接或启动远端**。历史原件、奖励和模型保留，不声称学习增益。

## 实际生效的链路

正式 `scripts/run_v2_s2.py` 的 Static/Evolve、后台生成、任务加载、Student reward 和续跑均接入新执行版 `v2_s2_light_llm_20261117`，显式评分身份 `coevolve_llm_reward_v1`，独立224文件源码锁。原模型/S0、64k/vLLM、train100、每组四次、五轮预算及dev24/test111不变。

- `GroupEvolution.context/generate` → `coevolve_light.py`：使用四条有效同组历史和结果，直接出题；只有缺少成功业务观察才增加一次针对性探索。无强制两次探索、三步/多API/三条成功引用；允许简单任务或空列表。
- `coevolve_tasks.candidate/validate_task` → `coevolve_llm.py`：三字段候选，程序附加来源/反馈/探索SHA；同源明显重复不新增，但旧题仍可训练。简化任务无额外新颖性证明。
- `coevolve_llm.admit`：一次Solver或适用的保存证据 → 一次共享Judge。Solver只看到公开任务、工具和一般执行指引，不看到提案解法、判据、数据库条件或参考答案。
- `v2_episode` → `coevolve_tasks.evaluate` → `s2_backend.finalize_reward` → `s2_reward.score`：生成题实际轨迹使用同一个 `coevolve_llm.judge/finish`；原始train/dev/test仍 `evaluate_isolated` 官方评分。
- Judge正常true/false对应1/0；接口/结构/环境故障无效。保留完整原始答案、拒绝和实际提交记录，无效整组不算优势/all_failed/forgetting；其他有效组仍可更新。同协议续跑可用，不重复已处理采样或Judge；旧协议state/actor/评价回执不混用。

退出新路径的旧限制：六字段独立分析、新颖性/反馈贴合/可学习性评分、课程硬门槛、数据库 `success_conditions`/字段元数据/repair、Teacher终态业务hash或答案全文比较、positive/noop/omit/wrong-answer重放、多轮拒绝复核。相关源码与历史配置留供复现，没有删除严格工具Schema、答案解析或完成提交纠错。

初态SHA仍验证“同题从相同干净起点执行”，与已取消的“终态业务hash必须等于Teacher”不同。

## 提示词与证据

四份完整主提示均位于 [coevolve_prompts.py](../src/recoveragent/coevolve_prompts.py)：

| 常量 | 实际入口 | 作用 |
| --- | --- | --- |
| `EXPLORATION` | `coevolve_tasks.exploration_instruction` → Teacher执行器 | 按四尝试证据补信息，至多一次干净初态探索 |
| `GENERATION` | `coevolve_light.generate` → `GroupEvolution.raw_text` | 0/4组件题、混合结果相近变体、4/4适当加条件；只输出三字段tasks JSON |
| `SOLVER` | `coevolve_tasks.validation_hint` → Teacher执行器 | 只解公开题，允许自行选择正确方法 |
| `JUDGE` | `coevolve_llm.judge`，准入与Student共用 | 公开目标＋实际调用/返回＋最终提交；允许等价做法，考虑后续撤销，不能凭完成声明 |

生成/Judge请求不再拼第三方XML、最低步数、课程评审或数据库表达式模板。探索/Solver继续使用既有基础工具调用协议和共享公开数字格式。

Judge输出严格为 `{success: boolean, reason: string, evidence_steps: array}`，动作编号须指向实际证据；正常false仍为有效失败。调用目的只区分日志，业务标准相同。保存输入SHA、提示SHA、原响应、结果与预算；中断claim不自动再次付费。无新的自动摘要层：四份原记录完整保存，当前轻量输入超64k时明确暂缓，该情形和Judge真实性仍需后续实跑观察。

## 旧池、历史核对与 fresh16 准备

私有目录 `runs/s2_light_llm_20261117/`：

- 从已有归档找到旧9题全部Solver轨迹，校验归档成员/原引用SHA、公开指令、来源及初态。`initial_candidates/` 为9个待Judge候选和100份反馈；未把旧准入当新评分通过。后续 `admit-pool` 对每题仅一次新Judge，缺证据/未通过保留原因，原始题补位；两分支共同读取同一结果。
- `judge_check_plan.json` 固定历史行5/21（确认成功）、7/18（真实失败）；使用实际有序业务调用/返回及真实终态提交，非重建环境轨迹。人工核对标签与Judge输入分离。已准备，**尚未调用Judge**。
- `fresh16_READY/frozen.json` 已锁定既定S0与四题×四次：原始单值数量官方评分；购物车JSON、商品提问、报价邮件使用共享LLM奖励。只作诊断、不自动入池。S0 adapter SHA仍为 `5da6444aa979e2ce0957b3bba82bd239ba0f59fad40024a7ea0a35b7f4534966`。
- 新run/state/evaluation/reward和初始池manifest记录执行、完成提交、奖励协议身份；所有新路径使用独立输出目录。历史48条记录与旧评分未改写。

离线冻结已实际执行：

```bash
PYTHONPATH=src:. .venv-v2/bin/python scripts/probe_s2_reward_chain.py prepare \
  --plan runs/s2_light_llm_20261117/fresh16_plan.json \
  --output runs/s2_light_llm_20261117/fresh16_READY
```

输出 `Prepared 16 slots; no model/environment execution`。完整旧池准备、历史核对、fresh16运行/续跑、共同池准入、正式S2双进程启动/续跑/评估命令见 [S2运行说明](../docs/S2_STATIC_EVOLVE.md)。付费命令未执行；仍缺用户后续资源安排/新截止，以及届时可用Teacher、GPU与远端模型/环境路径核对。

## 实际软件验证

新增 `tests/test_coevolve_light_llm.py`：**20项通过**，覆盖三字段/单动作/空列表、可选探索、真实发送文本无旧模板、共享Judge、错误结构无效、正常false为0、无旧评分回落、旧证据准入/缺证据暂存、明显重复、有限预算、历史标签不入Judge、原始官方评分。

CPU集成用明确标注的模拟响应串起“候选→准入→Student奖励→组反馈/GRPO筛选→模拟更新回执→同协议续跑”，并检查旧协议拒绝；不把它写成真实Solver、真实Judge或9B更新。

```bash
.venv-v2/bin/python -m pytest -q tests/test_coevolve_light_llm.py
.venv-v2/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
git diff --check
```

受影响的生成/奖励/提交/调度/后台/续跑9个测试文件：**130 passed，18.96秒**。命令为上述新增文件加 `test_completion_submission_v2.py`、`test_public_goal_reward.py`、`test_s2_goal_integration.py`、`test_s2_experiment.py`、`test_s2_evolution.py`、`test_s2_background.py`、`test_s2_parallel.py`、`test_s2_generation_fix.py`。

完整pytest：**1301 passed，56.36秒，13条依赖弃用警告**。包含既有CPU微型随机模型和本地API定义回归，不是正式训练或真实任务执行。Ruff check、280文件format、diff check通过。数字、JSON字符串、合法null、一次完成纠错、采样跨轮复用/补位和更新后权重切换等原测试保留通过。

## 后续预算与未验证内容

正式两分支生成题奖励Judge共用800次有限额度；新增候选准入最多192次；旧池最多9次，保守合计 **1001次**。给定初始9题、Static不扩池，实际训练生成轨迹理论最多180+400，故Judge理论合计最多 **781次**。历史核对4次、fresh16最多16次另计（固定3生成题×4次实际最多12次）。每项另有token、时间及估价上限，见新配置；本轮实际调用/费用均0。

生成侧总请求上限23,328，包括最多96次可选探索、192次Solver、96次任务抽象和192次准入Judge；没有额外新颖性、六字段分析或复审开销。Student和Teacher继续保存实际token、耗时及可获得价格数据；调用预算不代表执行授权，也不保证原GPU时限内跑完全程。

尚未验证：新Judge对真实正负例的准确性、新轻量生成的真实质量、fresh16提交/业务/奖励一致性、正式两分支更新及dev提升。下一步依次为小量历史Judge核对、fresh16、共同池一次准入、同协议S0/Static/Evolve。到这里结束本轮，不自行恢复远端。
