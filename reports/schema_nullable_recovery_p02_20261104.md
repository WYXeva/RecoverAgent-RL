# P0.2 — Nullable v2 采集与恢复接线验收

2026-11-04，审查基线 `73b9047717500de9ce7b16c3ddb9bd7c25e306d2`，开发分支 `codex/p0-p2-recovery`。开始时受跟踪文件无未提交差异；原有本地数据、运行目录及临时 checkout 保留。

**软件一致性验收 PASS；真实 AppWorld 任务验证未执行；模型效果验证未执行。** 本轮没有 SSH、GPU、模型请求、真实采集、训练或 C244 重跑；不推断成功率改善。

## 实际接通的入口

- `python -m recoveragent.p3c collect/episode --config configs/p3c_nullable_v2_20261104.json ...`：复用现有调度器、`run_episode`、`drive` 和完整批次检查点。先校验新配置/源码锁，再在 `initial_tools()` 和 `StudentSession` 构造前调用共享 `configure_schema_compatibility`。Student 初始化也强制检查新运行的 v2 绑定。
- `python -m recoveragent.p3c restore --config configs/p3c_nullable_v2_20261104.json --source ... --experiment ... --output ...`：CLI 将配置传入 `restore_probe`；读取快照 Schema revision，在新 Adapter 中通过同一转换模块重建 catalogue，然后校验 revision、原始/转换后 catalogue SHA、完整审计 SHA 和全部可见工具，再调用原 `state.restore`。直接调用恢复探针时，快照记录的新 runtime 也会选择新锁。
- `state.capture/restore`：对 v2 增加实际 catalogue/审计/可见工具绑定检查；恢复拒绝发生在数据库导入及工具执行之前。原有 manifest、文件、历史、task、完整 versions、逻辑状态、运行时及源快照复查全部保留，不改写快照预期值。
- `runner.run_episode(recovery_source=...)`：历史单调用入口也按快照重建 Schema；明确拒绝批量 Agent 快照，防止把新 Student 快照交给旧单调用协议。P3-C 的恢复探针仍是无模型验收入口；本轮没有新增批量 Agent 继续生成或预算恢复策略。

`schema_snapshot.py` 只负责版本重建和绑定校验；Nullable 转换继续使用已审查的 `schema_compatibility.py`，其内容未改。恢复后的动态发现仍从同一个转换后 `adapter.catalogue` 取工具。原始及转换后 Schema/来源分别保存在采集目录的 `schema_compatibility.json` 和恢复输出旁的 `.schema/schema_compatibility.json`，SHA 与快照绑定。

## 新配置与历史隔离

新 runtime 为 `p3c_nullable_v2_20261104`。新锁 `configs/p3c_nullable_v2_20261104_lock.json` 覆盖全部当前包源码、入口依赖、配置、模型发布引用和固定任务清单，共77项。锁 SHA-256：`84cbcc980a0c1dd6a5e50bc137992a10309a456b32680f739985a563902c9c23`。配置 SHA-256：`8e7b66dee10310e298e616be2b6c463a16f3746a3abf36bac602dd863299d521`。部署副本仅可调整 AppWorld 根目录、checkpoint 路径和私有 loopback 地址；协议、模型、划分和锁不能更换。

正式 Student 仍为固定 revision 的 `Qwen/Qwen3-4B`、non-thinking、原 execution_v6、动态发现及原40请求/16384生成/32768窗口/4096单回复/600秒上限，Memory关闭。新增 `p3c_nullable_v2_m1_release_20261104.json` 只把已有 M1 发布回执转换成现有 P3-C verifier 接受的文件清单格式，绑定原发布报告和训练回执 SHA；没有改动或重新加载 M1 权重。

旧 P3-C 的10–20题/历史95条数据入口不能代表 Full732 B244。因此新配置直接引用原冻结 Full732 清单及 `recovery_grpo` 角色，按 task ID 排序复现全部 B244，不改变 A/B/C 分配，不读取旧95条数据。`full732_recovery_tasks.py` 只验证身份/来源；Adapter 和独立 evaluator 传递同一 B 身份。为避免把官方 test/dev 来源伪标为 train，新增仅用于该 B runtime、v2 Schema、关闭 Memory 的快照格式5，记录真实 official split 与自定义研究身份；格式1–4仍保持原 train 限制。此接线不构成启动任务的授权，也不声称官方 benchmark 成绩。

所有历史配置、源码锁、P0.1 转换核心、execution_v6、M1、Teacher 原始数据及旧 C244 结果保留。旧 Student/P3-C 锁在当前改动源码上仍明确拒绝；没有重签旧锁或跳过校验。仅更新单测临时清单允许的本轮改动路径，生产锁不受影响。

## 软件测试

| 检查 | 结果 |
| --- | --- |
| P0.2 新增合成测试 | 37 passed |
| P0.1 + P0.2 Nullable 针对性测试 | 94 passed |
| 受影响 Adapter/Harness/Runner/快照及相关回归 | 199 passed |
| 完整 pytest | 727 passed，3条上游 Pydantic 弃用警告 |
| Ruff check / format check | PASS；178个 Python 文件格式通过 |

```bash
PYTHONPATH=src:. CUDA_VISIBLE_DEVICES='' .venv/bin/pytest -q tests/test_nullable_recovery.py tests/test_schema_compatibility.py
PYTHONPATH=src:. CUDA_VISIBLE_DEVICES='' .venv/bin/pytest -q tests/test_contracts.py tests/test_runner.py tests/test_p3c.py tests/test_harness.py tests/test_harness_review.py tests/test_harness_refactor.py tests/test_student_batch.py tests/test_student_v6.py tests/test_evaluation_classification.py tests/test_egtm.py tests/test_fresh_teacher.py tests/test_parallel_teacher.py
PYTHONPATH=src:. CUDA_VISIBLE_DEVICES='' .venv/bin/pytest -q
.venv/bin/ruff check src scripts tests
.venv/bin/ruff format --check src scripts tests
```

覆盖真实 Python 入口配合合成 Adapter/Tokenizer/生成回执：初始化顺序、相同绑定恢复、恢复后动态发现、合法 null、非 nullable null/缺必填/错误类型/额外字段拒绝、非法后缀整批不执行、版本/哈希/可见工具不匹配及畸形绑定拒绝、源快照不变、CLI配置传递、B身份传递、A/C拒绝、新锁校验及旧锁拒绝。测试未请求真实模型，未实例化真实 AppWorld 任务；公开类型检查只使用固定公开契约及静态源码。

日志与检查记录位于本地 `runs/schema_nullable_recovery_p02_20261104/`。首次完整回归暴露旧合成测试的临时源码清单及错误检查顺序问题，已修正并保留失败日志；上表为最终结果。

## 尚未验证与停止点

尚未验证真实 AppWorld 跨进程数据库恢复、真实部署环境下 catalogue 重建、B244 采集、M1 推理效果或任何性能变化；合成会话的恢复后工具测试不冒充完整 Agent 续跑。原预算/纠错状态仍保存在快照，不擅自实现新恢复策略。按本轮授权提交推送供审查，停止于 P0.2。
