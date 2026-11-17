# Judge thinking/high：固定10条已有证据复核

日期：2026-11-17；代码基线 `83182a5`，分支 `codex/p0-p2-recovery`。

本轮只修改Judge调用及响应解析，冻结reason-first提示，不生成任务、不执行Solver/Student、不连接GPU、不训练、不运行dev/test。固定10条原证据各调用一次，**10/10结论符合人工标签，逐条理由核对也通过**；6条正确轨迹未被误伤，4条真实失败均拒绝。该结果支持这些已知案例上的评分正确性改善，不等于正式训练效果或普遍评分可靠性。

## 调用与解析

- 新执行配置 `configs/v2_s2_judge_thinking_20261117.json`，232文件独立源码锁。原配置、锁、原轨迹和原奖励保持原样。
- `coevolve_llm.judge()` 显式传 `role="judge"`，经过 `GroupEvolution.text/raw_text → Evolution.text → TeacherPolicy.build_text_request`。准入和Student奖励共用这一配置；用途只区分日志。
- **10份实际请求均为** `model=deepseek-v4.1-flash`、`thinking={"type":"enabled"}`、顶层 `reasoning_effort="high"`、`max_tokens=8192`，没有temperature。
- `normalize_message` 按实际请求的thinking模式接受reasoning；评分只解析 `message.content`。`reasoning_content` 原样留在私有 `response.json`，不混入评分JSON或Student输入。截断、空最终content、非JSON、错误结构等仍是无效评分，不是业务0分。
- 当前Judge提示 `src/recoveragent/coevolve_prompts.py:JUDGE` **未修改**，SHA-256为 `7fa7f33bef47d4b3fde4f5a25c8df67978d0e920119eead97a1a5560c1ee9f35`。没有新增判据、硬编码答案或关键词翻分。
- `call_receipt.json` 保存实际请求参数与提示SHA；Judge绑定、最终评分回执和既有run/evaluation协议身份包含同一调用身份，改变模型、thinking、effort或输出预算不能命中旧缓存。旧准入证明不能冒充新Judge准入，本轮没有迁移任务池。

参数依据：已保存的Go `/models` 元数据声明该模型支持 `low/high/max`；[DeepSeek Chat Completions思考模式文档](https://api-docs.deepseek.com/guides/thinking_mode/)使用顶层reasoning_effort与thinking对象。实际Go端点接受了这10份请求，响应模型均为 `deepseek-v4.1-flash`，全部返回非空reasoning_content和独立reasoning token计数，finish_reason均为stop。**thinking有响应证据；接口未回显内部effort等级，high的证据是实际发送字段及模型能力元数据。**

生成、探索、Solver及Student推理设置未改：抽象文本仍0.2/non-thinking/4096；探索与Solver工具执行仍开启thinking、不发送temperature或reasoning_effort、单次4096；Student仍0.8/top_p=1.0/non-thinking/64K。本轮这些角色没有新增实际调用。

## 预算同步

本轮独立 `judge_recheck_budget`：**10请求、81,920输出token、10,485,760计费输入token预留、1200秒、$10技术费用上限**，上限不是预计扣费。固定清单SHA见 `configs/s2_judge_thinking_check_20261117.json`，标签单独保存在私有审计文件，未传入模型。

每次实际请求、Teacher上下文输出预留、操作token预留和费用计算均使用8192；上下文仍为Teacher自己的1,000,000窗口。没有修改billing_context_tokens、请求体字节上限或其他角色4096输出预算。新配置中已有Judge准入、奖励、核对的总输出额度同步按8192计算；生成侧仅增加最多192次准入Judge的输出预留，探索/抽象/Solver预算不变。正式运行仍暂停。

## 固定10条统一结果

下表只展示最终content中的评分依据，不公开模型思考文本。人工标签只在模型返回后比较；另检查工具动作、返回、计数与最终答案。所有证据来自上一轮真实记录，没有新任务执行。

| 案例 | 人工预期 | 本次Judge | 理由与实际证据核对 |
| --- | --- | --- | --- |
| history_5：完整报价邮件 | 成功 | 成功 | 两件商品15/130，subtotal145、tax14.50、total159.50；第26步创建唯一草稿、第30步从草稿发送，outbox有记录。理由正确，合法null提交未被单独误罚。 |
| history_7：漏草稿 | 失败 | 失败 | 价格与发送内容正确，但第32步直接send_email，完整轨迹没有create_draft；正确指出公开要求中的中间动作缺失。 |
| history_18：转发漏附件 | 失败 | 失败 | outbox为has_attachments=false，已发送邮件及thread的附件列表为空；正文提到文件名不等于真的附上PDF。 |
| history_21：商品提问 | 成功 | 成功 | 最新订单对应商品970，第18步实际创建问题10765，第22步读回；按照本次实际对象判断，没有要求Teacher的另一组ID。 |
| solver_0：习惯日志yes计数 | 成功 | 成功 | 读取14条日志，yes为11，提交11；理由所列ID集合确有11个。 |
| solver_1：最近一次no日期 | 成功 | 成功 | 最新05-17为yes，其次05-16为no；提交2023-05-16，排序与读回证据吻合。 |
| student_0：提交11 | 成功 | 成功 | 实际读到14条，11条yes；判断和计数正确。 |
| student_1：提交11 | 成功 | 成功 | 第22–35步读取14条，11条yes；未因前面的普通工具错误误罚最终完成结果。 |
| student_2：提交9 | 失败 | 失败 | 理由正确列出11个yes的note ID，明确9不正确，success=false。 |
| student_3：提交9 | 失败 | 失败 | 理由正确指出11个yes、3个no；本次未再把11算成10，success=false。 |

计数事实来自每份实际工具观察独立汇总：yes IDs为1521、1523–1528、1531–1534，共11；no IDs为1522、1529、1530，共3。没有根据参考答案向Judge补填这个列表。

本轮未出现上一轮的漏步骤、计数错误或布尔/理由矛盾；合法不同对象ID和null未导致误伤。上一轮最终reason-first提示只对3条误判做过定向复验，没有同提示下完整10例non-thinking对照，因此不宣称严格隔离了thinking的因果贡献。未继续改提示或追加复验，也没有把新判分覆盖到历史在线奖励或用于更新。

## 真实用量与费用

| 案例 | 输入token | 输出token（含思考） | 其中reasoning | 请求秒数 |
| --- | ---: | ---: | ---: | ---: |
| history_5 | 6,844 | 567 | 471 | 10.82 |
| history_7 | 10,987 | 1,038 | 889 | 21.58 |
| history_18 | 7,661 | 734 | 636 | 15.62 |
| history_21 | 7,189 | 574 | 495 | 12.83 |
| solver_0 | 14,784 | 1,053 | 945 | 16.94 |
| solver_1 | 6,007 | 580 | 478 | 10.84 |
| student_0 | 10,375 | 1,072 | 983 | 16.94 |
| student_1 | 23,729 | 1,382 | 1,278 | 20.38 |
| student_2 | 16,400 | 707 | 583 | 12.91 |
| student_3 | 14,211 | 1,164 | 1,046 | 18.92 |
| 合计 | **118,187** | **8,871** | **7,804** | **157.78** |

墙钟158.91秒。输出总数已经包含reasoning，不重复相加；缓存输入768 token。10次请求全返回，0未知用量预留、0重试。按项目现有保守峰值单价估算 `$0.045875508`。接口逐次返回 `billing_source=go`，累计 `list_cost=0.02293775`、`allowance=0.01376265`、`usage.cost=0`；这是Go订阅接口的费用/额度字段，不能据此声称订阅免费，未取得或分摊月费账单。本轮没有GPU作业。

## 软件验证与运行材料

新增10项针对性测试走真实共享Judge、配置、请求构造、上下文检查、预留和响应解析，仅网络处模拟：准入/奖励请求完全一致；reasoning与content分离；7类无效响应不变成0分；同协议缓存可用/改变effort拒绝旧缓存；其他角色不变；只有4096剩余额度时不能派发8192的Judge请求。

相关回归最终 **234 passed in 16.70s**；Ruff check通过、285文件format检查通过，diff check通过。测试命令：

```bash
.venv-v2/bin/python -m pytest -q \
  tests/test_s2_judge_thinking.py tests/test_s2_teacher_context.py \
  tests/test_coevolve_light_llm.py tests/test_completion_submission_v2.py \
  tests/test_public_goal_reward.py tests/test_s2_goal_integration.py \
  tests/test_s2_evolution.py tests/test_teacher_collection.py \
  tests/test_s2_parallel.py tests/test_v2_context.py tests/test_s2_generation_fix.py \
  tests/test_teacher_escalation.py tests/test_teacher_followup.py \
  tests/test_teacher_retry.py tests/test_v2_learning.py tests/test_v2_s1_retrain.py \
  tests/test_teacher_data.py
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
```

首次回归发现旧测试替身缺少新增role关键字；同步替身签名后重跑上述全部234项通过。没有以模拟结果代替真实10次验收，也未进行全量pytest或模型训练测试。

薄入口只读固定证据，调用现有Judge/finalize/用量记录；没有环境执行器或训练调用。本轮已完成，下面命令同目录只读取完成回执，不重新派发；源码/协议改变则拒绝混用旧绑定。

```bash
export PYTHONPATH="$PWD/src"
export RECOVERAGENT_S2_EXECUTION=v2_s2_judge_thinking_20261117
export RECOVERAGENT_TEACHER_TOKENIZER_DIR="$PWD/runs/s2_context_validation_20261117/teacher_tokenizer"
.venv-v2/bin/python -u scripts/recheck_s2_judge.py \
  --input runs/s2_judge_thinking_20261117/plan.json \
  --output runs/s2_judge_thinking_20261117/recheck
```

私有产物：`runs/s2_judge_thinking_20261117/`，含冻结plan、单独labels、原始request/response、call_receipt/context/usage、逐条result、统一summary与人工事实审计 `audit.json`。旧证据保留原位，公开仓库只提交代码、配置、测试及本报告。**固定复核已结束；不启动后续实验。**
