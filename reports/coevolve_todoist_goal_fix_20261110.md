# CoEvolve Todoist目标评分修复

2026-11-10。软件实现完成；没有启动新的真实任务、模型、Teacher或训练。

## 修复的问题

对首轮私有轨迹和终态进行只读比较，`group_00_member_1`正确创建三项指定待办并回答3，但新增了题目未限制的description。旧评分要求业务数据库与参考解完全相同，错误地给出负奖励；该奖励进入了第一次GRPO更新。历史记录、2/24→1/24→1/24开发结果和S0/S1/S2权重保持原样。

## 本次修改

新增`src/recoveragent/todoist_goal.py`，评分revision为`todoist_project_tasks_v1`。仅覆盖一个已发生问题的任务家族：创建新Todoist项目、创建明确清单中的任务、列表读回并报告数量。没有重构信号提取、任务调度或GRPO。

生成器允许提出以下结构化目标；公开题面由同一目标渲染，防止评分字段与题面各自漂移。原提案query保留于proposal.json，生成任务使用渲染后的新指令和新身份，不改写旧任务。

```json
{
  "revision": "todoist_project_tasks_v1",
  "project_name": "Example project",
  "tasks": [
    {"title": "Prepare materials", "due_date": "2023-05-22", "priority": "high"}
  ]
}
```

评分核对公开用户的项目归属、实际新建任务数量、标题、日期、优先级和实际读回。任务指定description时按指定值检查，未指定时允许额外description；不要求生成的记录ID或任务顺序等于Teacher参考解。已有Todoist项目和任务保持不变。返回的数量必须正确，不能只声明完成。读回使用固定公开API的`project_id/no_section_tasks/sections/task_id`响应结构，检查的是实际工具历史。

Teacher验证和Student评分共用这一实现；Solver的语义检查仍保留，目标程序检查也必须通过。参考业务哈希继续保存为审计信息，但不参与此类新目标的成功判断。

既有四项执行对照之外，加入漏建任务、错误日期、错误优先级、漏读回四种业务负例；目标允许时，增加“额外description应通过”的正例，共8或9项。全部复用现有隔离重放入口，无额外模型请求。

新运行配置`configs/v2_round1_coevolve_goals_20261110.json`及同名源码锁已接入现有入口；历史配置和锁未改写。旧的计数答案修复接续入口继续拒绝新goal配置，不静默将旧奖励用于新评分训练。新配置并不重新授权已结束的24小时作业。

## 验证

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/test_todoist_goal.py tests/test_coevolve.py tests/test_coevolve_quality.py tests/test_coevolve_resume.py` | 75 passed |
| `PYTHONPATH=.:src .venv/bin/python -m pytest -q` | 897 passed、1 skipped、3条上游弃用警告 |
| `.venv/bin/ruff check src scripts tests` | 通过 |
| `.venv/bin/ruff format --check src scripts tests` | 195文件通过 |

测试包含额外description回归、明确description仍检查、缺失/错误字段、错误归属、缺失/错误数量、创建后的读回、不同ID及顺序、生成器到Solver/入池接线、隔离重放分支和旧评分接续拒绝。使用合成Adapter、固定公开API形状和已有记录的只读核对，不是真实AppWorld新任务运行。

开发验证时工作区副本没有Git元数据；`ruff ... .`因此将原本忽略的`.cache`及私有`runs`历史脚本也纳入扫描并报错。本轮明确检查项目维护的`src/scripts/tests`，没有修改或格式化缓存、第三方及历史产物。提交前已从远端开发分支恢复Git元数据，确认远端仍为`19b82a4`，已有工作和未跟踪文件均保留。

## 范围与下一步

这一修复只对显式携带新goal契约的Todoist任务生效。其他自由生成题型仍使用原参考状态评分，相关误拒风险未一并解决；它们没有被自动升级。也未声称生成任务与能力缺口的整体贴合度改善。

新增评分尚未通过真实AppWorld执行验收，没有模型效果证据。下一次真实运行前应先用同一新目标核验正解、额外description和业务负例，再采集新的当前策略轨迹。旧S2已经受到旧奖励影响，本次代码修复不会修复已训练权重；本轮没有重训或重评分。
