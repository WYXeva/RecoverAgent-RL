# Solver参考与三态Judge：固定证据复验

基于 `codex/p0-p2-recovery` 的 `c6bd35c937967826b102fb941db8467310c5ecac`。本轮完成共享Judge输入、奖励传播与协议绑定修复；只读复用旧环境执行，真实调用8次Judge。没有新Solver/Student执行、GPU服务、参数更新、dev/test或正式Evolve。原轨迹、原判与旧锁保留。

## 实现

- `coevolve_llm.pending()` 从准入记录 `validation.pending.judge_input` 直接提取实际工具调用、返回和最终提交。Student评分要求同生成题ID、来源ID、公开题面SHA和初态；原Solver SHA、投影证据SHA及准入SHA进入回执/缓存。缺失或不匹配即无效，不能凭相同source_task_id套用其他题的参考。Solver准入固定 `reference=null`。
- 唯一核心提示在 `coevolve_prompts.JUDGE`：参考可补足初始对象和完整范围，实际目标动作/结果须由本条轨迹支持。没有公开要求的重复分页不是必需步骤；公开明确的草稿等过程仍必需。不同合法方法、文字和新建对象ID可接受。生成、探索、Solver和Student提示/推理设置未改。
- `finish()` / `s2_reward.finalize()` 保留三态：true=有效1，false=有效0，null=无效、scoring_gap。Judge故障与参考错误也无效；现有四条整组有效性过滤继续排除污染组，不作为真实失败回流。原始train/dev/test仍官方评分。
- `v2_episode`传入运行开始保存的初态指纹，Actor只接收公开题目与正常工具。新执行版 `v2_s2_judge_reference_20261117`、奖励版 `coevolve_llm_reward_v2`、输入版 `solver_reference_input_v1`、结果版 `judge_tristate_v1`，绑定实验/state/evaluation。旧准入、奖励或缓存不能静默接续。
- 旧9题仅在本地只读核验Solver原件并准备为 `ready_for_one_judge`，未对整个池重新付费准入。新锁覆盖237个执行依赖；旧锁继续拒绝新源码。同协议缓存续跑已验证，不追加请求。

## 固定真实复验

使用原来四条Venmo Student证据；用户追加邮件后，再读取原来邮件的一条正确轨迹与一条明确漏草稿轨迹。两题各先对其完整Solver原件做一次新准入，合计2次准入＋6次Student Judge。每条只判一次，无付费重试。人工标签只用于事后对照，未进入请求；新提交中也没有按案例编写评分条件。

邮件所缺Solver/Student原件从远端只读下载：Solver项目SHA为 `852ee886e84a5b46965f82841637fe72c5b9076864e8ac89fd6aa1597cdb989e`，与旧任务引用一致；公开题面、来源和初态一致。旧Student回执没有accepted_submission，使用绑定原history/result文件SHA的既有终态审计提取实际提交，未读取审计预期答案作为输入。

| 证据 | 事实标签 | 新Judge | 理由/实际动作核对 |
|---|---|---|---|
| Venmo Solver | 成功 | true | 完整分页确定目标两笔；本轨迹批准两笔并读回正确余额 |
| Venmo member_0 | 成功 | true | 自身批准全部两笔、余额及报告正确，引用8/15/16/17/18 |
| Venmo member_1 | 失败 | false | 参考及自身观察均确认两笔，只批准一笔；引用7/10/11/12 |
| Venmo member_2 | 成功 | true | 自身批准两笔、读回余额正确，引用5/8/9/10；未重复翻页不再被罚 |
| Venmo member_3 | 失败 | false | 明确遗漏另一笔，报告数量/余额不符；引用5/8/9/10 |
| 报价邮件 Solver | 成功 | true | 查价、创建一份草稿、从草稿发送、查询outbox，引用16/17/20/21/22 |
| 报价邮件 member_0 | 成功 | true | 正确报价与税额，创建一份草稿并发送/确认outbox，引用3/4/12/14/15 |
| 报价邮件 member_2 | 失败 | false | 直接send_email，明确漏做公开要求的草稿步骤，引用15/16/18 |

Venmo实际批准数及余额分别为：两笔/1532.00、一笔/1553.00、两笔/1532.00、一笔/1553.00。新Judge没有把Solver完成过的审批当成Student完成；两条漏批仍失败。原第三条因未翻页的误罚已消失，原1成功/3失败记录未改写。

邮件两条Student最终均为status=success、answer=null；题目要求的是实际邮件动作，没有额外要求最终回报，因此正确轨迹未因合法空答案被误罚。报价两件商品15.00与130.00，合计145.00、10%税14.50、总159.50，与实际邮件内容一致。负例有正确邮件正文但没有创建草稿，仍判失败。逐条检查理由事实、计数、动作编号与success一致；本批六条Student均符合标签，不等于所有新任务均可可靠评分。

## 实际请求与成本

8次请求均为 `deepseek-v4.1-flash`、`thinking={"type":"enabled"}`、`reasoning_effort=high`、`max_tokens=8192`，没有temperature。8次均返回非空reasoning_content、reasoning用量和正常stop；JSON只从最终content解析。2次准入reference为空，6次Student包含各自准确绑定的Solver证据。

Judge提示SHA：`434c89505ba4e61c089a80f79afdfb66a000d4186672264435c6cb4b20e4eb7f`。Teacher窗口沿用Go已保存模型元数据的1,000,000；使用固定匹配DeepSeek tokenizer对完整请求JSON估算，另加1,024 framing allowance并预留8,192输出，非提供者精确计数。所有参考内容参与估算与输入摘要。Student仍64K，本轮未调用Student。

| 用量 | 实际值 |
|---|---:|
| Judge请求 | 8 |
| 输入token | 245,379 |
| 输出token（包含reasoning） | 5,350 |
| 其中reasoning token | 4,617 |
| API调用墙钟之和 | 156.08秒 |
| 固定复验整体墙钟 | 157.25秒 |
| 项目保守估价 | $0.0800337 |
| 接口opencode.list_cost合计 | 0.04001685 |
| 接口opencode.allowance合计 | 0.02401011 |
| 接口usage.cost合计 | 0（Go订阅，不表示服务免费） |

固定复验预算8请求/65,536输出/8,388,608输入/960秒/保守估价10。保留一轮正式配置中的80次Student奖励Judge上限、9次初始池准入上限和独立生成预算，但本轮未启动这些作业。输入预算是费用预留，与1,000,000上下文窗口分别处理。

## 本地验证

以下相关180项pytest通过（18.75秒），不包含真实模型调用：

```bash
PYTHONPATH=src:. .venv-v2/bin/pytest -q \
  tests/test_s2_judge_reference.py tests/test_s2_judge_thinking.py \
  tests/test_coevolve_light_llm.py tests/test_s2_goal_integration.py \
  tests/test_s2_teacher_context.py tests/test_completion_submission_v2.py \
  tests/test_s2_experiment.py tests/test_s2_round_stop.py tests/test_s2_parallel.py \
  tests/test_s2_background.py tests/test_s2_generation_fix.py \
  tests/test_teacher_escalation.py tests/test_teacher_followup.py tests/test_teacher_retry.py \
  tests/test_v2_learning.py tests/test_v2_s1_retrain.py
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
git diff --check
```

Ruff check通过，288文件format通过，diff check通过。覆盖真实请求构造链（仅网络模拟）、同题参考/准入空参考、缺失/null/错题/错初态/错SHA、Actor边界、三态传播、实际BackendRuntime奖励收尾及缓存、整组排除、旧锁/旧池拒绝，以及数字/JSON/null提交、官方评分、64K/Teacher独立上下文等相关回归。

本批真实Judge没有返回success:null；该分支和故障处理只由CPU测试确认。真实固定调用后另补缺失准入记录为null的本地防护，最终源码与实际调用源码分别留存；防护不改变上述合法输入/提示/判断，8份旧结果已在最终源码下离线重新验证，未再次调用模型。

## 证据与入口

私有 `runs/s2_judge_reference_20261117/` 保存计划、只读原件、请求/响应/reasoning、预算回执、结果、标签对照和 `review_audit.json`。计划SHA为 `a0276a5a04c96ae51ac1c506208261596efe77dd126e28e3fc94080bdcbe8d44`。

实际调用源码及锁另存 `executed_source.tar.gz` / `executed_source_lock.json`；锁的项目SHA为 `1c872d0a279d7ec325f704247dd350c2ea784e1fdee97e10d66087a7642f8add`。最终提交锁项目SHA为 `da722b79972be236f85749fb924156c165117ee0a859faeed25bef6bfab3ed7e`。真实调用的精确源码下已确认同目录续跑复用8份缓存、用量不增长；更换源码不能继续旧输出目录。

薄入口复用正式 `admit/pending/judge/finalize`，不创建AppWorld环境。下面是**如需另行复现**的命令，会在新目录产生新的有限Judge调用；本轮已完成，未额外执行：

```bash
export RECOVERAGENT_S2_EXECUTION=v2_s2_judge_reference_20261117
export PYTHONPATH="$PWD/src:$PWD"
.venv-v2/bin/python scripts/recheck_s2_reference.py \
  --input runs/s2_judge_reference_20261117/plan.json \
  --output runs/s2_judge_reference_20261117/recheck_NEW
```

需要现有Teacher配置、匹配tokenizer缓存和私有计划引用的原件，不需要GPU。远端此次仅下载文件，已告知用户可关机。后续正式实验需新协议共同初始池、新目录与当批截止授权；当前一轮模型/训练安排不变。本轮到此结束，未把旧轨迹改写为新在线采样，也没有新的学习收益结论。
