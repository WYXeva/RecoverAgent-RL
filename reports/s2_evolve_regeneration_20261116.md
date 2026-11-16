# S2 Evolve 生成入口修复与首批重跑

2026-11-16 23:48（UTC+8）启动，结果更新至11月17日00:04。本文区分软件修复、真实生成和后续训练；原首批零候选记录见[诊断报告](s2_evolve_zero_generation_20261116.md)。

## 修复与执行来源

执行代码为 `d8d311d3d6a2127e1c307a610b0b01240efeb25b`，独立执行配置为 `v2_s2_generation_20261116`，不修改冻结S2实验配置、旧源码目录或旧源码锁。

- 真实 `p3a.validate_config` 接受S2 runtime，继续复用严格的配置与源码核验；真实Teacher启动前预检。
- 整场预算预留移到旁侧 `.budget` 目录，避免提前创建Teacher执行器要求独占创建的输出目录；用量读取指向真实轨迹回执。
- 超窗摘要的每条消息显式标注全局 `message_index`，提示要求直接引用该编号；越界引用仍拒绝，不猜测或修正Teacher输出。
- 新 `regenerate` 命令读取原失败窗口已经冻结的四个上下文，写入独立尝试目录。原 `feedback_windows` 不改；重跑记录保存在 `feedback_regenerations`，已完成同名尝试不重复执行。只允许原先零候选的失败窗口重跑，不择优重跑已有候选。
- 按用户“不必管账单，重新生产任务”的指示，新执行版关闭累计金额拦截。仍记录实际用量和未核销预留，并保留有限请求/token/时间预算与原GPU绝对截止。没有把旧未知预留清零，也没有购买或续租资源。

新锁覆盖186份依赖，SHA-256为 `cec6aa6035da4336b3c116a95d1c875e2d21ffe758e3a4f11753bb0976bd9a41`。部署归档411文件，SHA-256为 `7c4def2b1fdf20cda7f39f7688faf27594288a2f1a752f1c2118ac398f682182`。最初打包漏掉 `pyproject.toml`，在模型/Teacher调用前被源码检查拒绝；补齐后全锁及三份原失败Teacher配置在远端通过，失败打包记录保留。

## 软件验证

- 针对性与相关回归：`python -m pytest -q tests/test_s2_generation_fix.py tests/test_s2_evolution.py tests/test_s2_experiment.py tests/test_s2_parallel.py tests/test_s2_storage.py`，33 passed。
- 完整 `pytest -q`：1145 passed，2 skipped；3条上游Pydantic弃用警告。
- `ruff check .`、`ruff format --check .`：通过，253文件格式通过。
- 覆盖真实Teacher配置入口、输出目录创建契约、显式金额开关与其他预算、全局索引及非法引用拒绝、原窗口不可变与重跑幂等、新题进入后续采样、历史锁继续拒绝新源码。

这些是软件验证，不能代替真实探索、Solver验收或学习效果。

## 真实执行

复用远端 `/root/gpufree-data/recoveragent_s2_20261116_01/`，新执行源 `source_generation_001/`；输出仍为 `runs/static_evolve_002/evolve/`，首批重跑单列在 `generation/window_008_retry_user_001/`。

切换只暂停调度父进程，保留正在计算的第4次参数更新，待其保存并通过重载后移交控制器；原16组轨迹、actor/optimizer、队列和初始9条生成题保留。新控制器先重跑首批，再由原 `resume --mode evolve` 复用已完成而尚未提交的更新回执；截至本次检查该接续尚未发生，不能把预期复用写成已提交。Static仍暂停。

23:48启动首批4个原上下文，最多8个候选。第一个上下文已真实完成，结果如下：

| 已完成范围 | 数量 / 结果 |
| --- | --- |
| 上下文 | 1/4；其余3个继续执行 |
| 独立干净初态探索 | 2次，分别24/23个Teacher请求 |
| 候选 | 2个 |
| 独立Solver | 1次，24个Teacher请求 |
| 接纳 | 1个，已加入任务池，池大小9→10 |
| 拒绝 | 1个，`feedback_mismatch`，未调用其Solver |
| 当前新增题实际Student采样 / 入更新 | 0 / 0，后续调度尚未开始 |

新增题为 `coevolve_3d1566196b96e0e055bafc91`。来源 `0a9d82a_2` 的四次Student均失败，公开轨迹显示已取到习惯日志，但连续日期聚合与最终计数不稳定。新题更换习惯字段，仍要求按日期确定最长连续记录并核对计数，保留核心能力要求；不是原题文字改写。独立Solver后，semantic judge及生成任务程序验收通过：正确执行为true，空操作、错误答案、遗漏完成提交均为false，四个验收分支初态哈希一致。这是生成任务自身的验收，不是新增官方AppWorld原始任务成绩。

另一候选仅统计某习惯的总次数，反馈贴合检查认为没有保留最长连续日期聚合这一核心能力，按原标准拒绝，没有为了扩大任务池放行。

首个上下文实际78次Teacher请求、732776输入token、22657生成token，调用累计718.36秒，总墙钟844.16秒（14.07分钟）；技术估价USD0.082230672，不是平台账单，新增未知预留0。此统计只含已完成第一个上下文，不能作为整批总成本。

后续上下文已开始真实执行，批次未完成；当前尚无新增任务实际参与GRPO或提升dev的证据。第4次已有更新实际耗时1765.72秒（29.43分钟），重载通过；Evolve原16组为12成功、52失败、0未知。原首批失败batch的SHA仍为 `86478618d4ef9c0773c65e5cfc11788dae6efbedd1a741bfb55ff523ae764339`，与先前备份一致。

首个完成上下文及当时state共654文件已回传并逐文件SHA核验，私有归档 `runs/v2_s2_20261116_01/retrieval/generation_restart_20261116/completed_contexts_80f8e7d3df0d.tar.gz`，SHA为 `80f8e7d3df0d5d7894f36e74916b30ff016019336f06978f6138e3b1fbef7cb0`。移交与第4次训练回执另有12文件核验备份，归档SHA `3d8adbaf884d62ae7ee35d715af1f32f9061498607aa72e07ac168a87497e133`。

原始失败记录、重跑轨迹、预算回执和移交证据留在私有目录；公开仓库只提交代码、测试、配置和摘要。截止仍为2026-11-17 13:31:44（UTC+8），不因修复和重跑重新计时。启动与续跑参数见[S2运行说明](../docs/S2_STATIC_EVOLVE.md)。
