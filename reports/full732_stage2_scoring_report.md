# Full732 Stage 2：初态与合法终态评分诊断

日期：2026-10-28。已fetch、ff-only pull并确认基线为`def15782a58e6547c940463e90cf9f6347127e09`，分支`codex/p0-p2-recovery`。严格限定Stage 2，先只读审查，再按冻结顺序检查三个官方train任务；没有执行Stage 3–6。

**结论：固定三任务的初态/合法成功终态边界验证通过；旧封存终态评分重现只有2/3通过。Full732整体仍BLOCKED。** 两个初态unknown任务的可信成功终态均可正常评分。控制任务的旧快照暴露了额外的评分元数据缺失，保留失败后，通过同一任务的官方train验证流程取得新正向对照。没有把unknown改成失败，也没有用新结果覆盖历史。

## 真实结果与证据级别

| 固定train任务 | 本轮初态 | 本轮独立恢复旧Teacher终态后评分 | 同任务额外官方正向对照 |
| --- | --- | --- | --- |
| `76f2c72_1` | tracker 1/2；官方false；项目unknown/null | tracker 2/2；官方及项目true，PASS | 未另跑 |
| `d0b1f43_1` | tracker 1/2；官方false；项目unknown/null | tracker 2/2；官方及项目true，PASS | 未另跑 |
| `07b42fd_1` | tracker 1/5；有效失败false | tracker 4/5；有效失败false，**旧成功终态未复现** | 官方`_verify_task` 5/5；另起进程重评5/5，两份tracker完全一致，PASS |

这里的分数是测试条目数，不是模型成功率。前三个正向候选来自原95条数据中已封存的Teacher状态，本轮**实际导入、校验、保存并重新调用官方评价**，不是只读取旧成功标记。导入前绑定可信数据集manifest、样本索引、轨迹、旧成功回执、checkpoint ID、全部终态文件以及完整历史/Schema；导入后核对初态、数据版本、原始任务/base DB文件和终态逻辑哈希。原始数据集manifest为`5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`。

控制任务的额外对照使用**未修改的**上游`appworld.verify._verify_task`，只传固定train ID、独立experiment、`raise_on_failure=False, ground_truth_mode="full"`。这是官方train参考程序验证，不是Teacher新示范或Student推理。没有调用会遍历train/dev的完整`verify_tasks`。参考程序只在控制侧执行，未进入Actor上下文、训练数据或公开仓库。原始tracker由只读profile观察，随后新进程使用现有项目分类器重评，结果逐字段一致。

## 两个初态unknown的实际根因

“缺目标对象”是待验证假设，**这两个样本不符合该假设**。实际初态保有任务记录，但答案是上游`NOT_GIVEN_ANSWER`占位字符串。两个task evaluator的第38行调用`test.case(..., normalize=float)`，进入官方`TestTracker.case`（`appworld/evaluator.py:132`），在`appworld/common/evaluation.py:251/252`的数值归一化处产生`ValueError`。

官方TestTracker将异常记录为失败条目，所以两个初态都是完整2条tracker、`official_success=false`。项目`exception_observation`没有把这类ValueError当作可信业务失败，`classify_official`保留原tracker并给出`evaluation_error/unknown`、`success=null`。这不是把原官方false悄悄改写；是明确区分原分数与该分数能否作为可信奖励。

官方`build_fail_trace`主要保留测试片段和AssertionError描述，仅看旧tracker的文本不能定位全部ValueError来源。本次独立诊断加只读trace观察，保存异常类型、文件、行号与函数；不修改官方函数、断言、异常处理或返回值，不记录frame局部值到公开摘要。成功终态答案可被正常归一化，因此同一套未修改的官方评价和项目分类器都返回成功。

## 旧控制快照为何没有复现成功

- 封存控制快照的原官方回执为5/5，但其`terminal/dbs/model_hashes.json`为空对象。该文件与原manifest哈希一致；不是本次篡改或导入漏拷贝。
- 本轮恢复后的**数据行逻辑哈希与旧终态完全一致**，任务已完成，保存/评分前后DB文件未变化；独立官方评价却有一条“变更模型集合”断言失败，其他4条通过。项目如实返回有效失败，不当作新Student失败或训练样本。
- 上游`ModelCollectionPair._changed_model_names`（`appworld/collections/models.py:432–436`）会先比较模型哈希、排除相同者，再比较记录。缺失的变更哈希导致此次目标变更未被识别。该检查在只比较逻辑行的fingerprint中没有覆盖。
- 新的官方参考执行保存了非空变更哈希；其live评价与独立进程评价均5/5。证明控制任务本身及当前官方评分可用，也证明不能用“行哈希一致”替代完整评分状态验收。
- **证据不足**：尚未追溯旧采集链路具体在哪一步遗漏了模型哈希。本轮不改Adapter、state.py、官方HashHandler，不补写旧快照元数据，不修改旧奖励。不能直接把所有空model_hashes判为非法：只读任务可以没有业务变更。

## 可信判据与现有门槛

| 情况 | 本轮采用的判据 |
| --- | --- |
| 初态有效失败 | 环境初始化成功、保存的是原初态；完整官方tracker且只含已识别的任务断言/状态失败。不是模型成绩。 |
| 终态有效失败 | 实际终态被独立评价、完整tracker且没有未知/环境异常；保留官方false。本轮旧控制快照提供了真实实例，不能抹去。 |
| 合法成功终态 | 真实状态来源/初态基准可核验，完整官方tracker严格成功，项目分类valid且success=true；不是模型自称完成。 |
| 环境故障 | 依赖、DB、worker或评价运行故障；没有可信奖励。本轮真实环境故障0，软件负例覆盖其分类。 |
| 未知评价 | 未知异常或tracker不完整，即使原官方写false也不自动给reward 0；本轮两个初态属于此类。 |

`shared_stage.prepare`目前只检查旧预检报告的`scoring_status == PASS`，旧报告是90个**干净初态**的检查，包含6个unknown。它作为“等待调查”的临时保守阻断合理；但把它解释为“这些任务或整个任务池的合法终态不可评分”，或永久要求所有初态都必须产生有效失败，**不合理**，本轮已有反例。

后续建议（**未实施**）：将环境/原初态完整性和终态评分证据分开；初态没有答案时可以记录unknown，不据此否定未来终态。终态仍必须取得完整官方结果，并校验评分依赖的状态元数据；未知和环境故障继续不给奖励。将本轮结果作为有范围的三任务诊断证据，不能给原90/732任务整体写PASS，也不能用它放行用途受限任务。

其他四个初态unknown变体没有运行；其余任务没有新增证据。Full732用途资格、全量终态评分覆盖、新M1与后续预算授权均未满足。历史Teacher正常执行成功回执与跨进程快照重现能力是两个验收项，前者未被重写，后者应补强。

## 执行、修改与软件验证

本轮只新增`scripts/probe_stage2_scoring.py`、`tests/test_stage2_scoring.py`、本报告及脱敏汇总`reports/full732_stage2_scoring_summary.json`，更新STATUS/DECISIONS和README状态。薄脚本仅绑定三个来源、调用现有Adapter的状态导入/保存和`evaluation.evaluate_here`，另行留存原tracker与异常栈；没有新评分系统。原评价模块、Student、Teacher、Harness、Memory、state.py、任务分配和所有旧源码锁不变。

先完成只读核验与19项针对性测试，再串行执行三个初态、三个旧终态，最后只对控制任务进行上述官方对照。7个状态执行/验证进程、8次官方评价（其中官方live与独立重评各一次）；4次成功、2次有效失败、2次unknown。不能将这些不同起点的评分合并为模型成功率。

原六次探针没有业务工具调用；官方参考程序日志记录15次公开API调用，最后为完成任务API。所有状态worker总耗时约185.003秒。真实模型/付费Teacher/GPU/SSH/训练调用均0，未加载权重；不是用合成结果替代真实AppWorld验证。

```bash
# task严格按76f2c72_1、d0b1f43_1、07b42fd_1；先完成全部initial，再positive。
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 CUDA_VISIBLE_DEVICES='' \
  .venv-harness/bin/python scripts/probe_stage2_scoring.py \
  --task 76f2c72_1 --state initial --output runs/NEW_STAGE2/initial_76f2c72_1
# --state positive使用封存终态；每次独立目录，后续控制器worker限180秒。
# 控制任务额外官方验证（私有脚本及日志留存，不含于公开训练数据）：
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 CUDA_VISIBLE_DEVICES='' timeout 180s \
  .venv-harness/bin/python runs/full732_stage2_20261028/verify_control.py

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_stage2_scoring.py tests/test_evaluation_classification.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/ruff check --no-cache src tests scripts
.venv/bin/ruff format --check --no-cache src tests scripts
```

以上为本轮运行命令；重跑必须使用新的输出/实验路径，已有目录会被拒绝，不能覆盖证据。

结果：**针对性19 passed；完整463 passed；Ruff check通过；131文件format通过**。软件覆盖unknown不回填、成功终态独立判断、有效失败/环境故障/未知分离、不完整tracker拒绝、冻结清单拒绝越界、来源锚点损坏在加载状态前拒绝。软件fixtures不计作真实AppWorld结果。

本机环境为既有`.venv-harness`（Python3.14.4、SQLAlchemy2.0.52、SQLModel0.0.48），AppWorld绑定官方revision`42b5bcf3cd334fee33f0c37c02070a9f5807add5`。沿用已知可初始化的本机非受限进程权限，无安装或依赖源码改动。

## 证据与完整性

完整私有证据在`runs/full732_stage2_20261028/`：`read_only_review.json`、冻结`execution_plan.json`、六组`result.json/evaluation.json/official_tracker.json/exception_frames.json/manifest.json`、`official_control/`的原始live与独立tracker、命令/日志和`integrity.json`。公开JSON仅保留分类、计数、来源/文件SHA和脱敏栈帧，不上传任务说明、参考程序、正确答案、DB或模型权重。

- `execution_v6.py`文件SHA保持`352d106b7b052cc1a2b5c249e19c6a512e989355fc60f6495034f32db24a4400`。
- Student v6锁SHA保持`8fdc382b64508f5f2440a8a099eb4b77225ad6972ad1a854f61cc647875fa3fa`，仍可验锁通过。
- Full732划分文件SHA保持`0466cec29d9ad770a1cfeacb4c93945e5fcbcad68b9e980c5264d447418121a2`。旧train方案及所有旧报告/锁未改。
- 三个原任务输入、base DB文件、三个封存源及已审阅的官方/项目评分源码均通过前后哈希核验。评价未写入保存的DB。

**Stage 2诊断已完成；边界验证成功、旧快照重现失败、未覆盖范围证据不足分别保留。** 未修改生产门槛，未进入Stage 3。提交推送后停止，等待审查。
