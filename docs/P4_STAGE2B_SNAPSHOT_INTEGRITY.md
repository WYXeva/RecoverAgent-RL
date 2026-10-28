# Stage 2B — AppWorld 快照评分保真度（单独验收）

> **当前唯一后续工作：Stage 2B。** Stage 2 的诊断已完成（提交 `c8828924e5827301ad9b04330c570ee3f0db56d8`）。本文件不进入 Student/Teacher Prompt；`src/recoveragent/execution_v6.py` 严禁更改。只有用户明确把对应执行 Prompt 交给 Codex 后才开始。
>
> **停止条件：** 形成可重复的最小复现、根因证据与最小回归后提交并停止。未经用户另外授权，不进入 Stage 3/Teacher 采集/Base-SFT/Recovery-SFT/GRPO。

## 1. 为什么插入 Stage 2B

Stage 2 已用三个冻结的官方 train 任务验证：`76f2c72_1`、`d0b1f43_1` 的原始初态报 `unknown`，但已封存 Teacher 成功终态各自独立重评 2/2；控制任务 `07b42fd_1` 的旧 Teacher 终态原报告 5/5，导入后实际只有 **4/5**，而同任务新的上游官方 train 验证流程 live 与独立重评均 **5/5**。

原控制快照的 `terminal/dbs/model_hashes.json` 是空对象，但快照完整 SHA、逻辑数据库记录哈希均匹配。官方 `ModelCollectionPair._changed_model_names` 依赖模型哈希、记录哈希；**只验证 DB 行相等无法确保重新评分等价**。

Stage 2B 只解决这个工程正确性前提：**真实执行生成、保存/导出、跨进程导入、独立官方评价的状态元数据是否忠实保留；若不保留，缺口出现在哪一边界。**

## 2. 允许检查的最小集合

- 固定唯一写入控制任务：`07b42fd_1`（官方原始 train）。使用上次已经生成的合法官方 train 正向终态、封存旧 Teacher 终态和完整原始 tracker；如必须新建正向状态，只允许上游该**单任务**合法验证流程，不进行批量验证。
- 可读对照：既有 `76f2c72_1` 成功终态及其评分证据（不得扩大为所有旧任务全面扫描）。对照用于说明“空的 model_hashes 并非总是错误”，而不是为旧数据强写哈希。
- 至多一个已有、合法且已独立检查过的 **train P3-C** 完整检查点用于只读结构检查；**不自动调用模型、工具完成任务或追加其他恢复任务**。
- 固定清单预先登记，不按输出成功率更换任务；其它 732 题不逐题读取/评分。

## 3. 严格工作顺序

### 2B-1 只读根因定位

审查上游固定 AppWorld revision：
- `environment.py` 的 `save_state` / `load_state` / `_save_state`；
- `collections/models.py` 的 `model_hashes` / `ModelCollectionPair._changed_model_names`；
- `apps/lib/models/db.py` 的 `ModelHashHandler.save/load/reset`。

审查本项目：
- `adapter.py` 的 `export_state` / `import_state` / `save_terminal` / `fingerprint`；
- `teacher_history.capture_terminal`、`state.capture/restore` 和已封存来源。

画出状态及评分元数据的数据流，并标明每一步的磁盘路径、哈希和是否真正发生。明确区分：
1. 原始旧采集时已缺失；
2. `save_state` 写出时缺失；
3. `load_state` / `close_all` 期间丢失；
4. evaluator 读取到了不同格式/不同位置；
5. 缺少信息导致评分无法证明，而不是直接断言是哪一种。

### 2B-2 一次真实 AppWorld 无模型正向 / 往返验证

只在 `07b42fd_1` 使用独立环境和官方 train 可用的原生单任务验证执行：
1. 在官方成功运行结束前和调用 `save_state` 后检查模型哈希的**存在性/键集合/非空情况/摘要**，不要记录敏感值。
2. 把同一真实成功终态经现有 `adapter.export_state` 导出为新目录，再由新进程 `adapter.import_state` 读入。
3. 对比 live 终态、保存文件、恢复终态和独立评分使用的 **DB 逻辑内容 + model_hashes.json 元数据 + 必要数据库变更追踪信息**。
4. 使用**未修改**的官方 evaluator 对 live 和恢复终态各自评分；如通过，应与原成功 5/5 对照一致；如失败，应完整记录差异，不能造出成功。
5. 不改旧封存快照、不补写缺失哈希、不重算旧正式评价奖励。失败或不确定时保留全部原始回执，停止该对象的后续扩展测试。

这不是新 Teacher/Student 示范，不得进入新训练样本或验证集成绩。

### 2B-3 最小正确性门禁和回归

- 只在有可靠证据的**写入任务**上检测“声明变更但快照/评分元数据缺失或不等价”。**禁止**使用 `model_hashes.json == {}` 作为普遍非法规则，合法只读任务也可能没有变更。
- 为下一阶段提出明确的 `snapshot_scoring_integrity` 门禁，要求成功终态和关键恢复检查点在跨进程后能独立获得与原状态等价的官方评分信息；不必在本轮直接迁移所有历史检查点。
- 先写 2–4 个精准回归测试（成功写入元数据保真、空哈希只读例外、拒绝篡改、失败回执保留）；能用原模块就不用新框架。
- 如根因只在历史采集链路，不为历史文件临时补元数据；新入口可独立版本化，保留旧流程可复现。若必须修改生产源码，先标识新运行锁与兼容边界；不得修改上游 AppWorld 官方源码或重签旧锁放行。
- Stage 2 已发现的初态 `unknown` 继续保留，不能改成训练奖励0；**不要在本轮解锁 `shared_stage.prepare` 的全局 `scoring_status=PARTIAL`**。

## 4. 硬约束

- 不读取/运行原 test_normal/test_challenge 的单任务内容、评价/答案；Full732 自定义候选 A244/B244/C244 和用途受限状态不变。
- 不修改 `execution_v6.py`、原任务划分、已有 Base-SFT adapter、旧 Teacher 数据包、旧 checkpoint、历史报告、官方 evaluator/Harness/Memory 语义。
- 不启动 GPU、Student 模型推理、付费 Teacher 请求、Base-SFT/Recovery-SFT/GRPO；至多一个官方 train 参考程序的**受控**正常业务调用。
- Stage 3–6 不得实现或启动；本轮只修一个确凿的快照评分一致性问题。

## 5. 最终交付

- `reports/full732_stage2b_snapshot_integrity.md`：失败的根因或仍存在的证据空缺；“旧终态 4/5 vs 新参照 5/5”的逐边界解释；live/save/restore/evaluate 对照；影响范围**只按已实际检查的样本**报告。
- 有必要才新增一份小型脱敏 JSON 与针对性测试/小脚本；严禁新建庞大的状态框架。
- 测试通过、Ruff 通过、记录实际工具调用/执行次数与成本；旧资源/源码锁保持可核对。
- 给出明确的 **PASS / PARTIAL / BLOCKED**。若未能复现同态评分，不要宣称快照问题修好。
- 提交推送后**立即停止**。下一步是否启动任何训练，等待用户审查。
