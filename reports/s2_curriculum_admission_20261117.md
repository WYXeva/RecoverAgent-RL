# S2 简化任务准入与多值答案契约

2026-11-17，按用户指示修复新任务的多值输出接口，允许简化子任务，取消“偏离薄弱点”硬门槛，并独立重新生成一次。实现源码 `602225ff0a765dfcb7dc9ade083639bb179da860`，新执行版本 `v2_s2_admission_20261117`。旧候选与评分不改写。

## 改动与验证

- 新提案可声明公开的 `answer_fields`，在任务指令中明确字段名和类型。购物车基价/税额/总价的已知请求对应 `base_cost`、`tax`、`total_cost` 三个JSON数字；不再因为多值答案格式未定义而在Solver前暂缓。其他类型不从隐藏答案猜测。
- Teacher Solver和Student生成任务使用同一个 `public_object_v1` 比较器，模型提交JSON对象编码的answer字符串。字段、类型、数值仍须正确，标识符保留字符串；不会自动补答案、删单位、删货币符号或改评分。原proposal和添加公开约定后的proposal分别保留。
- 新颖性允许减少对象、条件、步骤，以及“只找邮件、不转发”这样的组件任务。语义完全相同、纯措辞或格式变化仍不算新题。反馈仍作为出题参考，但不调用 `feedback_fit`；独立Solver、探索动作引用、semantic judge与程序验收保持。
- 原四轨迹反馈继续使用，超窗才逐轨迹可追溯摘要。重做窗口8原选定的4个context，每个两次独立探索、合计至多2候选，本次最多8候选；新尝试ID `admission_v2_001`。旧批次8候选/2成功/6拒绝或暂缓保留。

验证来自软件测试：相关92项通过；完整 `.venv-v2/bin/python -m pytest -q` 为1174 passed、13条上游弃用警告；项目Ruff 0.8.0 `check .` 与 `format --check .` 通过（258文件）。覆盖公开三字段、错误键/类型/值、历史文本不变、简化题准入、不再调用贴合审查、纯改写仍拒绝、重生成身份与幂等。

远端新源码193文件锁通过，SHA `96b07283c50a8940f23bcbf07cb4ef2d3f1ff959683a8fbbe7616afb70a4f950`；418文件部署归档SHA `d2dd543ca71f35d4f15eb29eba23880dea9af17d10ec282190cabda4c45a8050`。远端CPU重现购物车公开契约和共享比较器通过；这一检查没有调用模型，不能代替真实新任务验收。

## 实际运行

新源码位于远端 `recoveragent_s2_20261116_01/source_admission_001/`，学习输出沿用 `runs/static_evolve_003/`；只在完整任务组且更新已提交的边界切换。模型、optimizer、已完成组、采样队列和原绝对截止11月17日13:31:44保持。Teacher与Student继续后台并行，新题只在后续组边界导入。

切换前已完成20组、4次更新，正在计算第5次更新。上一批两条新题已分别用于第18、20组，奖励均0/4，因此它们尚未提供非零组内优势。这是实际采样证据，不是有效学习或效果提升证据。

01:26:57，新版Teacher实际开始本批生成。01:29:53已完成第1个组级context，首个探索24请求/23响应；候选0、合格0，本批仍在运行。已检查真实 `context/request.json` 包含新版允许组件简化的规则，不能把尚无候选写成生成通过。

第5次GPU更新较长，因此将本次重生成先交由独立CPU/API进程调用已有 `process_job`，没有等待Student切换。该批只写自己的新目录和验收产物，不修改学习state；其 `external_process` 身份进入冻结作业，队列worker等待它完成，不并发消费同一Teacher预算。旧Teacher父调度主动退出，保留其已预留环境子进程直至自然结束；旧窗口16受移交影响的context保留原件和中断原因，不能归为模型业务失败。旧监督器对被停止的父进程显示failed，不能据此判断新版Teacher也失败。

Student边界移交仍由远端一次性程序等待，保存移交前state，核对actor/optimizer/步数/队列不变后调用同一 `enqueue_regeneration` 登记已有新批，并启动新监督器。新批候选只有在Student已切到支持新比较器的版本后才入池。此时Student切换尚未发生；新批生成已与GPU计算真实并行。

实际启动PID与过程状态在私有 `continuations/admission_v2_001/manual_generation.json`、`manual_status.json`，新批在 `runs/static_evolve_003/evolve/generation/window_008_retry_admission_v2_001/`。01:32:10，首个探索已完成，第二个探索正在执行；仍为0候选/0合格，Student维持20组/4次已提交更新。最终候选数、合格数和训练使用情况待完成后以回执更新；目前没有新版任务效果结论。

启动与真实请求证据19文件已回传并逐文件SHA核验，私有归档 `runs/v2_s2_20261116_01/retrieval/admission_20261117/start.tar.gz`，SHA `8f4c0bf7e65e2d67f2853cac69f66b7e2c0cb2f314a43e1d3bf173a8c0e321c8`。这是启动证据备份，不是整批最终产物。

## 启动与接续

沿用[运行说明](../docs/S2_STATIC_EVOLVE.md)的独立监督器，设置 `RECOVERAGENT_S2_EXECUTION=v2_s2_admission_20261117`。同一批重生成命令为：

```bash
$S2_PY scripts/run_v2_s2.py regenerate --window 8 --attempt admission_v2_001 \
  --deployment "$S2_DEPLOYMENT" --output "$S2_OUTPUT"
```

该命令要求Student完整组边界和控制器锁；当前运行通过一次性边界移交调用同一 `enqueue_regeneration`，不另开竞争写入学习state的控制器。同尝试ID保留原任务，不重复发起已消费工作。远端监督器继续采样/更新与正常反馈生成，到原截止保存停止；不租机、充值或自动延长授权。
