# 项目状态

- 最后更新：2026-10-26（Asia/Shanghai）
- 当前需求：`PROJECT_PLAN.md` / `AGENTS.md` v0.15；正式 Student `Qwen/Qwen3-4B`。
- 当前状态：**A100 Base-SFT训练与P3-C基础验收PASS**。短/最长烟测2/1步、正式116步更新/重载通过；固定6任务Raw0/6、SFT4/6（train2/2、内部validation1/2、dev1/2）。P3-C12任务/11家族、7有效自然失败、5预算中断隔离、225完整边界、11独立恢复通过。私有1.74GB包已回传并逐文件校验；自动关机缺平台密钥失败，用户已报告手动关机。泛化与P4-A未验收。
- P0–P2旧自然失败/四分支及原源码锁保留；当前state.py增加默认v1兼容的可选batch v2。新源码有新锁，复现旧实验使用其原commit，不改历史哈希。
- GitHub：<https://github.com/WYXeva/RecoverAgent-RL>；开发分支 `codex/p0-p2-recovery`，本轮A100拉取基线 `60085420299a82f7deb12cf88441f2d221a7900d`（历史P3-C基线5aed1fb保留）。独立克隆 `/tmp/recoveragent-diagnosis-20261022`，主工作区 `.git` 仍为空且只读。没有更改公开可见性、覆盖提交或强制推送。

## 新A100重训与P3-C（2026-10-26，完成）

- **训练/数据PASS**：95/75/29和18来源重验、固定模型revision权重、925/147重新分词逐字节一致；原manifest不改。A100 81920MiB、12vCPU/120GiB、49GiB盘，BF16+LoRA正式116步/3775.971秒，train loss0.228531、内部eval0.165857、重载logits差0，GPU采样峰值39853MiB。
- **固定开发效果**：Raw0/6、SFT4/6，全部12个官方结果有效，6对初始状态/首次输入一致。清单曾被诊断查看，不能外推完整benchmark。SFT两失败为歌曲发行日期证据缺失/业务判断与上下文耗尽；详细任务及用量见报告。
- **P3-C PASS，有隔离项**：12任务/11家族（8未见task、4已见），7自然失败/6家族，0成功，5零派发预算中断隔离。225完整边界，7 late-failure/2 post-error/2 pre-error共11候选独立进程恢复通过；post-error真实错误见证通过，源不变。未Teacher接管。
- **必要控制修复**：原第二任务预算在生成后、第一工具前耗尽，被旧Runner归为基础设施故障并停止整批。原结果/奖励/不完整历史不变；`f9bfbf4`新独立控制锁只续跑剩余10项，严格核验零派发/无未知用量/状态一致，其他故障仍停，不重试旧请求。5条全部排除训练池；旧宽泛分类保留，详见D046。
- **验证/交付PASS**：272测试、Ruff check及107文件format；原数据/旧checkpoint7文件与263历史报告/锁字节不变。四片/整包/12163内容文件SHA通过。新checkpoint私有`runs/a100_remote_20261026/checkpoints/run_001/formal/`，归档`runs/a100_remote_20261026_verified.tar.gz`。
- **成本/关机**：422模型请求、4772713输入/38083输出token、502工具调用；0Teacher/付费API请求，GPU账单未知。后台16:21完成至21:53首次关机请求约5小时32分，包含空闲和回传，不计作训练。平台shutdown缺关机密钥返回255，用户随后报告手动关机，未冒称自动成功，未释放实例。
- **证据/命令**：`reports/a100_sft_p3c_20261026_report.md/json`、`a100_checkpoint_release`、`a100_backup_verification`、`a100_historical_integrity`、`a100_shutdown`、`a100_software_checks_20261026.txt`。本地重算`PYTHONPATH=src:. .venv/bin/python scripts/summarize_remote_validation.py --input runs/a100_remote_20261026 --output <新JSON>`；训练/评价/续跑完整argv在私有归档及报告。
- **剩余事项**：保存快照约20秒/次，影响600秒预算，预算失败单列；跨主机实际恢复、Teacher接管/后缀过滤、精确optimizer续训未验收。P4-A尚未实现，本轮无Recovery-SFT/GRPO。后续使用7条有效失败/9个核心候选，2个pre-error仅消融，不使用5条不完整批次。

## Harness小范围重构（2026-10-25，工程PASS；模型效果未测）

- **基线与范围**：同步`0d59b0099aae67626d56fa32c12746e99bcb4948`，工作区无未提交差异。先列代码问题并复现3个失败用例；已有精确Schema反馈/评价分类不重复开发。仅4个核心文件调整，没有新运行框架。
- **实际修复**：精确反馈详情能通过重放/导出，receipt从原响应和可见schema核验详情防篡改；Teacher/Student共用工具返回/计数/落盘；去除真实返回数对AnswerGuard的依赖。后端身份/边界失败保留未知token与费用，Teacher现场/中断重算复用核验。协议、业务、执行、评价未知、最终任务失败分层；原两次纠错与熔断不变。
- **验证**：252测试、Ruff check和全量99文件format通过；最终真实AppWorld A/B脚本各4调用，业务401保留、非法批次0执行、写后丢返回保存真实副作用且不可恢复。开发与冻结两轮合计16个真实工具调用，0模型调用/计费/训练。固定Qwen tokenizer对合成纠错样本得到1目标/24监督token/2238序列，反馈/历史/padding屏蔽及parser回读通过；不计真实Teacher示范。
- **兼容**：95轨迹/75任务/29家族、18来源包与原manifest重验通过；原adapter7发布文件SHA一致，264个旧锁/报告及保留源码与基线相同。evaluation.py、Adapter、state、AnswerGuard、Prompt、训练mask实现均未修改。
- **新入口**：`shared_execution_20261025`、`configs/p3a_harness_refactor.json`、`configs/harness_refactor_protocol_lock.json`。Student默认同版本；旧P3-B/P3-C源码锁不适用新checkout，必须用其原commit复现。配置付费授权仍false。
- **证据/命令**：`reports/harness_refactor_report.md`，`harness_refactor_{world,world_final,mask,integrity,validation}_20261025.json`、`harness_refactor_checks_20261025.txt`。实际命令`PYTHONPATH=src:. .venv/bin/python -m pytest -q`、`ruff check src tests scripts`、`ruff format --check src tests scripts`；真实探针与tokenizer命令见报告。私有回执在`runs/harness_refactor_20261025_{a,b}`，日志`.cache/harness_refactor_*`。
- **限制/下一步**：未测真实DeepSeek/Qwen纠错效果，没有新增Teacher示范、没有训练或Recovery。P3-C远程状态本轮未重新检查；旧真实模型采集BLOCKED不变。后续在匹配新运行锁的受控小样本中验证模型效果，不靠Harness代做业务。测试开发中发现fixture序列化/重复ID错误并修正，没有降低生产验收。

## P3-C评价修复、自然失败采集与多检查点（2026-10-25）

- **完成**：新评价分类保留完整官方结果，区分任务失败、环境故障、未知；新Schema反馈说明缺失/未知字段和类型，Teacher/Student共有实现，仍两次纠正。默认旧Harness行为和所有历史数据不反写。
- **采集实现**：新增`recoveragent.p3c`、固定清单、资源/模型/源码预检、完整回执、自然失败索引、state v2快照与独立恢复入口。execution_v5+B；正式4B adapter原文件哈希通过，原95条及18个来源包重验通过。P3-C runtime独立锁定，未重训。
- **预固定计划**：12 train任务/11家族/seed20261025，其中8个未见、4个已见；内部分组validation家族全部排除。真实Student任务0、自然失败0、模型失败检查点0、模型请求/计费/训练步数均0。
- **真实环境探针PASS**：最终`probe_c`脚本8次公开API（含真实写入及写后丢返回），6个完整快照；两进程恢复DB/历史/Schema/环境runtime一致，Agent runtime SHA一致，源不变。非法混合批次0执行，不完整有副作用批次只作审计，不能进自然失败Buffer。此前`probe_b`11快照/2次恢复保留；两个探针共16实际工具调用，不计模型成绩。
- **测试**：238项完整软件测试通过；Ruff check与9个改动Python文件格式检查通过。新增评价来源/完整性、具体参数、旧反馈兼容、批次state v1/v2、运行时防篡改、自然失败/基础设施隔离、部分写入、任务分层、controller停止与未知用量保留测试。
- **阻塞/错误**：SSH退出255；TCP连接后没有SSH banner，远程shell/GPU不可验证。实测本机RTX5050 8151MiB，被90000MiB门槛阻止。初次受限AppWorld探针卡住后终止，保留目录；后续硬超时探针通过。没有自动启动或租用机器，未新增付费调用。
- **命令/证据**：`reports/p3c_failure_collection_report.md`及`p3c_{validation,dataset_audit,checkpoint_audit}_20261025.json`、`p3c_checks_20261025.txt`。`PYTHONPATH=src:. .venv/bin/python -m pytest -q`；`ruff check src tests scripts`；真实命令`PYTHONPATH=src timeout -k 5 300 .venv-harness/bin/python scripts/probe_p3c.py --namespace p3c_probe_20261025_c --output runs/p3c_20261025/probe_c`。采集入口实际运行到预检BLOCKED：`python -m recoveragent.p3c collect --config configs/p3c_collection_20261025.json --output runs/p3c_20261025/collection_preflight`。完整私有日志`.cache/p3c_*`与`runs/p3c_20261025/`。
- **下一步**：用户恢复既有远程可访问性后核验GPU与真实Base-SFT后端，按冻结12任务跑模型，分层统计。至少一条可靠自然失败及post-error/late-failure活动完整边界独立恢复通过后，才进入P4-A Teacher接管；本轮未启动Teacher恢复/Recovery-SFT/GRPO。P3-C阶段尚未通过验收。

## P3-B离线诊断（2026-10-24，完成；未重新训练）

- **统计口径更正**：原12份官方tracker全部返回完整测试计数，Raw原始官方成功0/6、SFT1/6（16.7%），train0/2对1/2、内部validation和dev均0/2。旧0/4、1/4来自项目guard排除非断言异常，是条件统计，不能作为总体成绩。旧结果及null标记保留，运行分类尚未修改。
- **异常原因已定位**：推荐歌单未创建引发官方空列表IndexError/后续UnboundLocalError；文件未改名移动引发目标路径KeyError。不能笼统解释成环境坏了。按完整原始官方tracker另报失败，不回写旧数据/奖励。
- **模型实际问题**：6任务中5个前两轮均漏show_api_descriptions必填app_name；压缩任务将登录字段放入文档工具；歌手粉丝数误用“本人是否关注”；文件任务猜错home目录并4096截断。删除旧歌任务真实删除14首，依据当时公开release_date其中9首不满足年份条件，歌单未处理；下一轮上下文32936而耗尽。
- **数据/训练核对**：925+147目标重新解码，函数/参数与Teacher及当时schema一致，监督/前缀mask通过；105次原示范show_api_descriptions均带app_name。原manifest SHA不变，未发现参数未监督、权重未更新或adapter未加载证据。95条都没有后来新增Harness提示，部署分布差异已确认，因果效果未做消融。
- **证据/命令**：`reports/p3b_diagnosis_20261024.md/json`；`PYTHONPATH=src HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 .venv-inference/bin/python scripts/diagnose_p3b.py --input runs/p3b_remote_20261023 --dataset data/base_sft_v1 --output reports/p3b_diagnosis_20261024.json`。210测试、新脚本Ruff通过，0模型请求/0工具新执行/0GPU训练；远程保持关机。
- **下一步**：独立版本修正评价分类与报告口径、让参数错误反馈指出缺失字段，先做匹配Prompt的首轮/Teacher-prefix诊断，再决定新版本数据或训练；不靠放宽验证或Harness代做业务任务。真实训练PASS、基础任务能力仍弱且未证明泛化提升。

## P3-B远程执行（2026-10-23，训练完成、效果验收部分通过）

- **真实训练/交付PASS**：实测RTX PRO6000 Blackwell 97887MiB，固定4B BF16+LoRA，先短/最长烟测2/1步再正式116步、1epoch。训练loss0.227391，最终内部验证loss0.164290；116次有限非零梯度检查，LoRA真实变化，保存/重新加载tensor一致、logits误差0。正式阶段2701.740秒，CUDA allocated峰值22.77GiB，nvidia-smi采样峰值40029MiB。
- **原数据PASS且不可变**：95轨迹/75任务/29家族，train82条/925样本/229776监督token，validation13条/147样本/43227监督token。可信manifest `5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`与原归档SHA不变；18来源重验、远程重新分词逐字节一致，最长31102/32610均完整。原50文件训练源码锁对应473693c，未被修改。
- **真实对照PARTIAL**：预先固定train/内部validation/dev各2任务，B dynamic_discovery/execution_v5，Raw与SFT使用相同预算、初态和首次输入。两端各6尝试、4有效官方结果、2评价运行异常；Raw成功0/4，SFT1/4，唯一成功在训练集。内部验证/dev均无成功，不能声称泛化提升。未知结果不改成0分。
- **错误/成本全保留**：run_001解释器错误0更新；run_002短烟测真实2更新后BF16上下文不一致，补充版本锁修复而不降低门槛。总实际121更新。首次评价2条Freezegun/Transformers清理错误保留；新v2独立目录重跑固定清单。全部14任务尝试共190模型请求、2162051输入/18685输出token、213工具调用；0Teacher请求，GPU货币账单未知。
- **运行修复**：实际冻结时间探针发现monotonic不前进，Student新增可选真实时钟注入；Freezegun官方configure排除控制侧tokenizer模块，任务时间不变，并保留清理主错误。训练补充锁`p3b_bf16_runtime_lock.json`和评价v1/v2锁独立保留；实际评价代码975b506，详见D035–D039。
- **备份/关机完成**：三份私有归档共2318088201字节，整包/分片/文件SHA通过；最终checkpoint在`runs/p3b_remote_20261023/checkpoints/run_003/formal/`，含checkpoint-100/116 optimizer状态。约21:25执行平台shutdown，远端关闭连接，后续SSH exit255、无认证提示；未删除实例、未核验平台账单。精确optimizer断点续训仍未测试。
- **软件/审计PASS**：210测试、Ruff check/format；实际checkpoint文件、原始请求token计数、6对初态/首输入及结束数据SHA复验。训练、真实工具探针与软件模拟分别报告。
- **命令/证据**：完整命令与异常见`reports/p3b_base_sft_report.md`、`p3b_training_pipeline_20261023.json`、`p3b_results_20261023.json`、`p3b_checkpoint_release_20261023.json`、`p3b_shutdown_20261023.json`。本机`PYTHONPATH=src:. .venv/bin/python -m pytest -q`及`scripts/summarize_p3b.py`；真实日志/模型回执在私有`runs/p3b_remote_20261023/`，未上传权重/数据/凭据。
- **下一步**：控制侧诊断2个固定任务的官方非断言异常，明确有效开发评价口径；随后从已保存4B Base-SFT checkpoint规划P3-C新train失败与完整快照。本轮不启动Recovery-SFT/GRPO，不把内部validation/dev失败回灌训练。

## 余额恢复后的真实Teacher验证（2026-10-23，完成）

- 用户确认DeepSeek已充值；前后只读余额接口均HTTP200、可用且有正余额。运行冻结在`8ea72d8`，没有改源码、Prompt或官方评价器。
- 预先固定旧v5清单前两个任务771d8fc_2/e7a10f8_3、seed2026102301，使用B dynamic_discovery；一次/任务，合计上限80请求/32768输出token/1200秒/USD2，无注入错误或结果替换。
- **真实执行PASS**：官方成功2/2、完整过滤2/2，15模型请求、49工具调用、128599输入+6327输出token，整批85.48秒。原始独立Clean Dataset有SHA清单；原正式95条及哈希不变，新增2条尚未并入正式v1。
- **Qwen格式PASS**：2条均可放入32768；15个Assistant目标/5730监督token，最长14211、最长目标2776，动态Schema、Assistant-only/历史/pad mask及解析回读通过；无权重更新。首次因缺verified sidecar拒绝编码，补做官方成员/来源校验后通过，未绕过校验。
- **纠错效果NOT_OBSERVED**：协议拒绝、数值纠正、notice和业务API错误均0；不能把正常成功解释为出错后修复，也不为触发纠错扩大任务数。基础设施/评价异常均0。余额已不再是阻塞。
- **费用**：真实usage按冻结峰时价估算USD0.015351492；账户窗口余额差额CNY0.03，非逐请求账单，billed_cost仍null。2次只读余额查询，无额外模型请求、无训练。
- **证据/命令**：`reports/harness_funded_validation_20261023.md`及`harness_funded_20261023_*.json`；新公开参数/任务清单`configs/p3a_harness_funded_20261023.json`、`train_harness_funded_20261023.json`。真实数据/日志`runs/harness_funded_20261023_a/`与`.cache/harness_funded_20261023_*`仅本机保留；实际collect/verify/sft_quality命令见报告。Ruff通过，原201测试源码基线未变。
- 下一步：后续采样观察自然纠错；本轮两任务验证已停止。远程96GB和P3-B真实训练仍未启动。

## Harness审查修订（2026-10-23，完成）

- **修复/简化PASS**：活跃任务纯文本意外停止纳入原两次纠错预算，合法status=fail及已完成任务不拦截；连续两次普通worker初始化异常熔断；评价子进程异常单列；中断批次逐调用intent/返回计数及实际DB导出，两端共用纠错事件和中断保存。非完整批次明确不可直接恢复，不补造结果。AnswerGuard、官方Adapter/evaluator、RULES及execution_v3/v4/v5不改。
- **版本冻结**：`public_protocol_correction_v1` + `runtime_revision=review_20261023`，新入口`configs/p3a_harness_review.json`、新锁`configs/harness_review_protocol_lock.json`；旧v1/旧数据源码锁保留。详见`reports/harness_review_report.md` A–E、`docs/HARNESS_V1.md`、D033。
- **软件PASS**：全套201测试、新增22个回归用例；Ruff check/format通过。Teacher/Student相同协议反馈一致；Provider401/402/403立即停止，业务API401仅真实观察/有限提示；数值纠正不借通用预算延长。
- **真实环境PASS（脚本，非模型）**：两次写入后返回丢失探针均保留副作用/部分历史/未知调用，第二次完整验收通过；A/B各一次发现验证；封存清单首条成功Teacher重放40调用，观察及快照恢复一致，独立官方成功。首次探针错误地要求未完成状态必得有效0分，官方非断言异常使脚本退出1；仅修正探针预期，原证据保留。异常类型不等于环境故障，不产生有效策略奖励。
- **数据/mask PASS**：原95条/75任务/29家族、1072目标及273003监督token不变；18独立源包（21路径含副本）复验。manifest `5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`、archive `68231f625b876db259c5085d08e70c77604ffbaaf6b0809b807852e2bfc3b9ed`不变；真实Qwen tokenizer在合成纠错样例上验证mask/parser，非训练。
- **资源/阻塞**：46真实工具调用、1只读余额查询、0模型生成/新增费用/训练步；DeepSeek余额仍不可用，真实Teacher纠正效果BLOCKED。没有重放全部95条、没有新采集。远程96GB/4B真实更新和重载仍未验证；当前checkout与原包训练源码锁不匹配是预期保护，按REMOTE_BASE_SFT版本分工使用。
- **证据/命令**：`reports/harness_review_{data,source,world,modes,success,mask,provider}_20261023.json`、`harness_review_checks_20261023.txt`；`PYTHONPATH=src:. .venv/bin/pytest -q`、Ruff、两个probe脚本及单条revalidate完整命令见审查报告。私有`.cache/harness_review_*`保留原始日志、状态和评价，不上传。
- **下一步**：账户恢复后少量真实Teacher纠错验收；远程就绪后使用原数据/对应封存源码做短长样本LoRA烟测。当前无新增Harness正确性阻塞，不代表P3-B训练已通过。

## Harness v1工程化（2026-10-22，完成）

- **实现PASS**：新增`public_protocol_correction_v1`，Teacher/Student共用执行前整批检查和最多2次通用纠正；不可见工具、参数/ID错误、截断、已观察同应用token抄错均零执行反馈。原始非法生成仅存回执，规范harness消息进入后续上下文；拒绝回合不监督，不代改参数或做业务动作。A固定工具集、B下一回合发现；最多2条无进展提示，原数值纠正仍仅紧接一次。Student输入加输出<=32768，单回复4096，工具数量不限。契约`docs/HARNESS_V1.md`、D031。
- **服务保护PASS（软件验证）**：安全保存HTTP状态和未知费用预留，401/402/403立即停止，连续2次传输/worker故障停止；停止信号传到campaign并保留未执行清单，无隐式付费重试。新配置/参数锁`configs/p3a_harness_v1.json`、`harness_v1_protocol_lock.json`；公开模板不自动授权付费，私有已授权配置已准备但未采集。
- **环境与原数据复验PASS（真实AppWorld）**：新独立SQLAlchemy2.0.52/SQLModel0.0.48/greenlet3.2.4，原环境2.1.5不改。原95条/75任务/29家族在新进程逐条重放，3087真实工具调用全部反馈一致；95条初态一致、保存/恢复一致、model hashes非空、独立官方评价全成功。原包6616文件、manifest及归档SHA不变。两次另行24调用播放诊断均成功；不把脚本重放算新增Teacher或反写旧失败。新评价只读观察原TestTracker异常，非断言/顶层异常单列，不改官方验证器。详见D032。
- **A/B边界探针PASS（真实工具、脚本动作）**：2个隔离world，无效混合批次均0执行/状态不变；各1次合法发现后B新增Schema、A固定。它不是Predictor模型试验/Teacher成绩。**软件179 passed、Ruff及83文件格式PASS**；真实固定4B tokenizer合成拒绝/纠正样本24监督token，前缀/padding mask与Student解析回读通过，0权重更新。封存18来源包重验通过。
- **历史公开请求离线检查**：118尝试/1235响应，检测7不可见工具、6截断、51含错误token回复，涉及17条历史失败；58条原成功0误拒。不是修复后成功率，不能宣称模型会利用反馈成功自纠正。
- **真实Teacher效果BLOCKED**：只读余额HTTP200、is_available=false、无正余额；配置/凭据/既有授权检查通过不代表资金可用。本轮0模型生成请求/0生成token/0新增Teacher示范/0模型费用/0训练；现有授权仍有效。需资金恢复后用新run验证真实纠正。真实工具合计3137次，95条复验3186.094秒（53.10分钟），单worker串行，未加载Student权重或远程GPU。
- **证据/命令**：完整报告`reports/harness_v1_report.md`；机器证据`harness_revalidation_20261022.json`、`harness_state_and_evaluation_20261022.json`、`harness_world_probe_20261022.json`、`harness_historical_detection_20261022.json`、`harness_qwen_mask_20261022.json`、`harness_software_checks_20261022.txt`、`harness_source_integrity_20261022.json`。复验主命令`PYTHONPATH=src timeout 5400 .venv-harness/bin/python scripts/revalidate_teacher_environment.py --output .cache/harness_revalidation_20261022_a`，随后`audit_harness_release.py`绑定源SHA；其余实际命令见报告。
- **错误与限制**：初次依赖下载受网络限制、pip check缺greenlet，分别通过授权网络及补齐依赖修复；曾改旧teacher.py导致v3锁拒绝，已恢复原文件，采用新增捕获层保存安全状态。原17个P0–P2、v3五文件、v4/v5提示和旧锁不变；更新共享驱动需新锁，精确旧v5用基线commit。新环境本机通过不等于远程通过；当前checkout与原v1封存训练源码锁不同，见`docs/REMOTE_BASE_SFT.md`，不允许改旧锁绕过。
- 下一步：恢复账户资金后，在已固定清单与原预算内做少量真实Teacher自纠正验证；另行决定补采/新版本封装。远程96GB、真实4B批量后端、SFT更新/保存重载及dev评测仍未验收，本轮到此不训练。

## B失败排查（2026-10-22，诊断通过，未启用修复）

- 范围：118次新增全部B dynamic_discovery/execution_v5，A仅历史示范。逐份原trajectory/evaluation SHA与封存metrics一致，重算58成功、60未成功：25传输错误、3本地请求字节边界、7未开放工具、6输出截断、3回合耗尽、16完成后官方失败。
- **新增关键环境问题**：当前SQLAlchemy2.1.5将数据库URI的file:编码成file%3A，固定AppWorld字符串解析得到错误memory home。两个不同播放任务三次独立原动作重放，共66真实工具调用，返回全匹配，官方suppress_errors=False均抛OperationalError/no such table:songs。3条历史播放失败不能简单归因模型选错歌；原默认异常抑制使其成为普通失败。90份终态model_hashes均为空，单例全新进程复评漏识别实际修改。先修兼容及独立进程复评，再确认正式训练就绪；不改历史分数/验证器/冻结依赖。详见D030。
- 其他证据：两次40回合循环抄错token持续401；另一次读空页仍翻到27。额外加好友、推荐计数、交并集、漏删、分页条件切换导致业务失败。HTTP状态在Teacher异常消息里有、最终轨迹只存类型；continue_after_infrastructure_error=true且无连续熔断，放大服务故障。7个未开放API都真实存在，函数目录不等于当前可调用schema。6次截断中4次4096、2次为累计预算剩余2327/2024。
- 证据：reports/base_sft_failure_diagnosis_20261022.md/json、base_sft_failure_playback_probe_20261022.json、base_sft_failure_playback_repeats_20261022.json；scripts/diagnose_base_sft_failures.py、probe_appworld_evaluator.py。命令见报告。本轮0付费模型请求、0示范、0训练；首次受限内核启动未完成已中断，后续3次真实重放均有120秒硬超时。66调用只计诊断，不计Teacher成功数。
- 验证：155软件测试、Ruff及76文件格式检查通过；封存50源码锁及manifest/归档SHA不变，见base_sft_failure_integrity_20261022.json。
- 下一步：独立版本验证兼容依赖/官方实现，测试URI往返、真实状态保存恢复、model-hash持久化及新进程官方评价，保持v1不可变；再做HTTP记录/熔断和协议纠错。95条原始成功证据保留，但远程正式训练前置尚未通过。本轮未擅自切换A、充值或修改prompt。

## 正式Base-SFT v1构建（2026-10-21，已交付）

- **数据PASS / 数量PARTIAL**：正式私有集`data/base_sft_v1/`，95条/75任务/29家族；目标约120条未达。历史15包78条目重验为46独立合格轨迹（45长度合格、cap后41）。本轮118次/77任务/30家族、58官方成功，28基础设施错误；条件成功率58/118=49.15%，有效评价58/90=64.44%，不能称为无偏B总体成绩。全来源104条，5条整轨迹deferred、4条cap reserve、0精确重复；原包和P0–P2均不改。
- **采集停止**：前5批完整，最后一批20项计划只执行18项，剩余2项unattempted。余额查询无正值，独立诊断HTTP402（官方余额不足）；旧25个无响应请求仅记TeacherTransportError，不能倒填HTTP状态。所有worker落盘后结束两个控制进程，离线seal只增加中断说明/summary/数据包，未改Teacher轨迹。后续需充值后用新run/dataset版本补采；既有调用授权不需重复确认。证据`base_sft_provider_diagnostic.json`、`base_sft_collection_interruption.json`、D029。
- **分组/权重**：split_seed2026102120，预先固定24训练/6验证家族，实际入选训练82条/64任务/24家族，内部验证13条/11任务/5家族；每task最多2条，家族/任务/相似簇/轨迹/回合平衡loss。1个验证家族未成功，不隐去。内部验证并非完全未接触的Prompt开发场景，官方dev未入训练。
- **Qwen格式PASS**：固定Qwen3-4B/non-thinking tokenizer，1072个Assistant样本、273003监督token（训练925/229776，验证147/43227）；总输入11548307、屏蔽上下文11275304（97.636%），最长序列32610/前缀32272/目标3259。436批量目标、3087工具调用回读、257次动态Schema变化。整轨迹超32k的5条保留，未截断；本次真实harness纠正0次，不声称其效果提升。
- **成本**：新增采集1260请求、3362真实工具调用；已知14509858输入/354004输出token，25请求缺响应，费用已知usage部分USD1.468554942估算+USD7.9872保守预留，非实扣。另1次16-token诊断HTTP402，未知预留USD0.314592、0工具调用。纳入来源的历史+新增+诊断共2009请求，已知usage USD2.354900190，未知预留USD8.301792；billed_cost未知。Teacher控制器约4378.654秒，中断批次计时basis单列，未加载正式4B权重/未GPU训练。
- **验证PASS**：155软件测试；Ruff及74文件format检查；全回执/成本/公开任务与Schema重建、动态可见性重放、Assistant-only/padding mask、Student无损回读；正式包逐字节重新分词、本机归档导入、4类篡改拒绝。77任务初态哈希核对，41重复任务一致。P0–P2 17项、v3五项、v5十四项、collection十八项及原15包manifest无改动。证据`base_sft_*_final.txt`、`base_sft_dataset_v1_{verify,retokenize,import,negative}.json`、`base_sft_initial_state_check.json`、`base_sft_source_checks.json`。
- **私有交接**：归档`data/base_sft_v1.tar.gz`，37773083字节；manifest SHA `5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`，归档SHA `68231f625b876db259c5085d08e70c77604ffbaaf6b0809b807852e2bfc3b9ed`。可信摘要`reports/base_sft_dataset_v1_release.json`；源码/配置锁50文件。控制评价/来源与训练输入分开，包和原始任务/评价/凭据/权重均不上传GitHub。未远传。
- **远程准备，训练NOT_STARTED**：BF16+LoRA、2步短样本/1步最长样本烟测配置及真实更新/梯度/保存重载检查入口已准备。CPU微型随机Qwen3梯度等价检查0 optimizer step，不是Student训练。缺远程96GB连接/环境/PEFT+Accelerate、固定4B权重与真实批量后端、总窗口检查、真实烟测及Raw Base/Base-SFT-only固定dev评价。说明`docs/REMOTE_BASE_SFT.md`。
- **命令/证据**：完整报告`reports/base_sft_dataset_v1_report.md`及可重算机器指标`base_sft_dataset_v1_metrics.json`；实际封装/验证命令`base_sft_execution_commands.json`。采集：`PYTHONPATH=src .venv/bin/python scripts/collect_base_sft.py --campaign configs/base_sft_v1_campaign.json --config .cache/v5_ab_20261021_a.json --credentials-file .env`；中断封存：`PYTHONPATH=src:. .venv/bin/python scripts/seal_interrupted_collection.py --campaign configs/base_sft_v1_campaign.json --run runs/base_sft_v1_20261021_b06`。日志`/tmp/recoveragent-base-sft-{campaign,finalize}.log`及`.cache/base_sft_*`。早期预演缓存环境错误已修正，保留失败目录；方案D025–D029。

## v5金额检查与一次模型自纠正（2026-10-21，历史阶段）

- **实现 PASS**：`answer_guard.py` / `execution_v5.py`仅按公开任务保守识别单值格式。明确数字/金额字符串在完成前拦截，返回显式harness反馈；最多下一次模型同值纠正，原请求/token/时间/费用预算不增加。Teacher/Student共享，不改写答案、不读隐藏标签。拒绝Assistant保留为上下文、loss=-100，纠正后的真实输出才监督。契约`docs/ANSWER_GUARD.md`、D024。
- **真实采集 PASS**：`runs/v5_ab_20261021_a`，seed2026102105，固定10新train任务/5新家族，A/B各一次。20/20有效、0基础设施错误，**A7/10（4成功家族）、B9/10（5家族）**，16条通过官方验证和筛选，10唯一成功任务/5家族。4条过滤均为official_not_successful：A两条缺关系证据、A/B各一条专辑删除范围错误。保留全部负例，无择优追试。中途口头8+8误拆已纠正，以全量回执7+9为准。
- **效果边界**：清单20次均不在当前保守单值识别范围，0拦截/0纠正；格式通过率与纠正成功率分母为0（N/A）。本轮不能证明真实金额纠正有效，未为触发检查换任务，也不能将新旧不同任务成绩作因果比较。A的2次Predictor认证纠错单独计量，不是答案纠正。
- **成本**：194模型请求=182Actor+12Predictor，474真实工具调用；2,012,471输入（1,596,160缓存）/68,373输出token；控制器783.174秒，USD0.216517860费率估算，实扣未知，无未知用量。A87请求/194调用/USD0.066498264，B107请求/280调用/USD0.150019596。沿用已授权DeepSeek；每次40请求/16384输出/600秒/USD1，总USD20；单回复4096、工具数不限。另有独立脚本探针1真实调用，0模型请求，不计Teacher成功率。无GPU权重/训练。
- **软件与探针 PASS**：142测试、Ruff和60文件格式通过。四条旧金额失败离线检查、真实AppWorld拒绝无状态副作用探针、真实固定Qwen tokenizer合成mask回归通过；这些合成纠正不是Teacher示范/模型成绩。合成mask只有26个纠正token监督。证据`reports/answer_guard_{offline,world,qwen_mask}_20261021.json`、`*_final.txt`。
- **数据准备 PASS**：16条全部适配32k，143Assistant目标、44,126监督token，最长完整序列30,474/生成前prefix30,361/目标2,703。25次schema变化、381工具参数回读和Assistant-only/padding mask通过。逐回合输入1,509,571，其中97.077%为上下文，重复开销仍高；未应用家族权重/packing。真实采集无拒绝回合，mask特殊路径由独立合成探针验证。
- **完整性 PASS**：新包`runs/v5_ab_20261021_a/clean_dataset/`，manifest SHA `9efed4033f06bc2705b8e742ac96f6ee9696d06e634d2eb0bf6b997e52684964`。原始回执/公开输入/费用/官方train及版本验证通过；独立本机复制复验通过，未远传。分词`data/answer_guard_sft_20261021_a/`，20份完整脱敏视图`outputs/v5_ab_20261021_a/<task>_<mode>.md`。旧4个包26条合格示范只读重验，旧金额失败奖励不改，原始数据不上传GitHub。
- **历史/源码**：P0–P2 17项、v3五文件、v4提示与选择器保持原字节；v4旧8文件在f5ce059重验。共享p3a驱动更新，旧v4锁不改、精确复现需固定checkout。新v5独立锁绑定执行/检查/Student/数据mask路径。`reports/answer_guard_source_checks.json`。
- **命令/日志/错误**：采集命令`PYTHONPATH=src .venv/bin/python -m recoveragent.p3a compare --config .cache/v5_ab_20261021_a.json --output runs/v5_ab_20261021_a --credentials-file .env`；完整探针、回执审计、verify、tokenizer命令见`reports/answer_guard_report.md`。本机日志`.cache/answer_guard_{collect,audit,verify,quality}.log`。开发中预算终止名断言与临时fixture路径错误已修正，汇总脚本结构误读已纠正，未改原始模型/环境结果。
- **下一步/P3-B缺项**：另固定公开数值契约验证清单，测真实Teacher首发格式及自然自纠正；按scenario设计训练/验证划分、规模与家族权重；接入真实4B后端，远程96GB环境与权重/长上下文验收、安全迁移及SHA复验。获得训练启动授权后才验收Base-SFT真实更新/保存重载；本轮不启动SFT/GRPO。

## 金额答案格式复核（2026-10-21，分析，未启用新harness）

- 用户质疑两条B金额回答语义上正确，询问能否由harness规避。只读核对全部4条A/B账单轨迹：从各自真实工具观察提取并按文件去重的4张账单合计，均与模型答案中的金额一致。
- 控制侧调用固定AppWorld的`assert_plus(raw_answer, "==", ground_truth_answer, tolerance=0.51, normalize=float)`，4条均因货币符号字符串触发ValueError；只去掉该符号、保持数值不变后，4条均通过相同官方答案比较。隐藏参考值仅在控制侧比较，未暴露给Actor；不是重新执行完整任务，也不重写历史8/10、6/10或将诊断样本入集。0模型请求、0环境动作。
- 证据`reports/currency_answer_diagnostic_20261021.json`仅发布数量/布尔/来源哈希，不含原始答案或隐藏参考值；本地详细结果`.cache/currency_answer_diagnostic_20261021.json`。将这类情况归为答案表示不兼容，不等同于数值推理错误。
- 候选下一步：显式版本化的最终答案适配层，仅在公开声明的数值/金额契约下规范化无歧义的单个金额，记录原始生成与实际执行参数，A/B/Teacher/Student共用；歧义则回馈模型修改。不依据隐藏验证器决定答案类型，不在评价失败后试改直到通过。当前仅分析，生产harness、数据和奖励未改，详见D023。

## execution_v4 与新场景配对采集（2026-10-21，v0.9）

- **实现/真实采集 PASS**：用户授权按诊断方案修改并重采A/B。旧execution_v3五文件锁和P0–P2原17文件不变；新增明确完成/正文/聚合/认证规则，以及A公开认证依赖检查/至多一次Predictor纠错。模型自行选择最终工具，控制器不补业务动作、不改answer；所有选择成本计入，Predictor不进入Actor/SFT。
- **固定新场景**：seed=2026102104，10不同任务/5此前未采样家族，每族2变体，排除先前7家族。清单`configs/train_v4_ab_20261021.json`，协议锁`configs/execution_v4_protocol_lock.json`，配置`configs/p3a_v4_ab.json`。A/B各一次、交替顺序、独立初态/进程、同模型/预算，全部10对检查通过，无换任务或付费追试。
- **真实结果**：20尝试、20有效评价、0基础设施错误；A6/10、B8/10，共14条官方成功且筛选合格示范，8唯一成功任务/4成功家族。A成功3家族，B4家族。不是Student成绩或统计优势；新旧任务不同，不能据此证明提示修正因果收益。
- **负例保留**：两个账单变体A/B共4条金额答案带货币符号，无法通过官方float规范化；两个付款任务A漏联系人/关系查询（认证校验不覆盖业务证据），一个如实失败停止，另一个猜测接受全部26请求且回复4096截断、整批未执行。后者不是工具配额拒绝。A在另一个任务真实触发一次漏密码来源纠错，自行补齐后成功，完整回执可重建。
- **完整成本**：221模型请求（210Actor+11Predictor）、680工具调用，2,505,885输入（1,877,248缓存）、66,825输出token；835.845秒控制器墙钟；USD0.280044588费率估算，实扣未知，无未知预留。A88请求/283调用/USD0.069158364，B133请求/397调用/USD0.210886224。每次40请求/16384输出/600秒/USD1，总USD20，单回复4096、工具次数不限；实际使用远低于运行预算，沿用用户“费用不限”授权。
- **数据格式 PASS，长度准备 PARTIAL**：固定4B tokenizer的14条/168Assistant目标、45,403监督token，最长完整序列34,507、最大目标3,259。6104387_1的B轨迹4回合超过32768，整条暂缓而非截断；其余13条（A6/B7）/148目标/39,658监督token，最长序列31,102、生成前上下文30,976。35次schema变化、623工具参数回读及Assistant-only/padding mask通过。全部输入中97.949%为上下文；正式分组/权重未定。
- **完整性与交接 PASS**：新包`runs/v4_ab_20261021_a/clean_dataset/`，manifest SHA `336bb99c268fe777e4f61c0e2d25ed23c94271f6a43eafa5ea5c30311b08319a`。官方train/版本/回执校验及独立本地复制复验通过，远程未传输。分词`data/v4_ab_sft_20261021_a/`，20份完整脱敏视图`outputs/v4_ab_20261021_a/<task>_<mode>.md`。旧3个v3包只读重验通过，新旧仍分包保留，原始数据/视图不公开。
- **验证与错误**：最终117软件测试、Ruff/55文件格式通过；公开输入重建、失败截断回执、全部成本、验证前终态和两版协议锁均通过。首次测试命令缺少PYTHONPATH中的`.`导致scripts导入失败，修正命令后通过；首次新版LLM视图导出局部events覆盖Actor索引导致AttributeError，修复变量并补组合路径断言后20份视图通过。均不影响模型/工具数据。沙箱NVML不可见，正常环境实测RTX5050/8151MiB；未加载权重、不训练。
- **实际命令/证据**：`PYTHONPATH=src .venv/bin/python -m recoveragent.p3a compare --config .cache/v4_ab_20261021_a.json --output runs/v4_ab_20261021_a --credentials-file .env`。日志`.cache/v4_ab_20261021_{collect,audit,receipts,verify,quality}.log`，原控制器日志`/tmp/recoveragent-v4-ab-collect.log`；详细审计、tokenizer、verify和测试命令见`reports/v4_ab_report.md`，机器证据为`reports/v4_ab_*.json`与`*_final.txt`。
- **下一步/P3-B缺项**：正式数据规模、场景分组/验证集、家族权重和长轨迹处理；真实4B批量后端与权重/长上下文验收；远程96GB硬件/匹配环境、安全迁移与可信SHA复验。需后续明确训练授权再做Base-SFT真实更新、保存重载和评测。本轮不启动SFT/GRPO/远程训练；数值格式和A业务依赖失败作为后续独立版本问题保留。

## B失败诊断与冻结A复试（2026-10-21）

- 用户要求分析失败并用A再试一次，随后要求解释根因及修改方法。对B全部6个有效失败/4家族固定清单，A从原始初态各执行一次。采样manifest新增诊断标记与来源SHA，明确使用了历史结果选择，不称为无偏A/B比较或检查点恢复。常规采样原manifest不改。
- **原B原因**：4条只错在动作完成时误填answer，业务状态断言全部通过；1条另有短信多余前缀触发官方子串检查；1条读全28首后聚合错误，实际最高作者5次却选择4次者。公开complete_task schema一直要求动作任务省略answer，execution_v3措辞存在可解释歧义，但模型内部成因只能推断。原失败仍奖励0，不修补历史示范。
- **A真实结果**：2/6成功，为29caf6f_1与29caf6f_3（省略answer、正文仅列表）；b0a8eae_1/_3仍填answer摘要失败；287e338_3仍统计错；22cc237_1 Predictor选14API但漏simple_note.login，23次公开工具错误，最后4096输出截断、未创建付款请求。后者不是总token/工具次数额度耗尽。6评价有效、0基础设施错误，官方通过2条均通过筛选。
- 全部冻结提示及A/B源文件哈希不变；A Predictor从公开任务/目录完整重构一致，Actor没有B历史/诊断/隐藏标签；同任务初态、Teacher/backend和每任务预算一致。已有两正例/依赖提醒仍未保证预测覆盖，所以不能把A2/6解释为工具模式整体更强。
- 实际新增71模型请求（6 Predictor+65 Actor）、97工具调用、626630输入（559616缓存）、24314输出token，采集277.082秒；费率估算USD0.052638696，实际账单未知，无未知费用预留。每任务40请求/16384输出/600秒/USD1，总USD6，单回复4096、工具数量不限，无付费重试。没有训练或模型权重加载。
- 两条新数据/2唯一任务/1家族，4B tokenizer得到15个Assistant目标、2349监督token，最长序列12458、最长目标332；格式/schema/mask全部通过。新包 `runs/a_failure_retry_20261021_a/clean_dataset/`，manifest SHA-256 `2e30bc2e55689e0a0d474dfd76ed23f73492deb1cc0f08c1c5f0428021a2519d`。当前三个execution_v3包共12条/10唯一任务/6家族，仍分包保留，没有合并为正式训练集。
- 107软件测试和Ruff/格式通过；P0–P2原17项未变。公开报告 `reports/a_failure_retry_report.md`、逐任务 `a_failure_retry_comparison.json`，原始日志 `.cache/a_failure_retry_20261021_a.log`；本地完整视图 `outputs/a_failure_retry_20261021_a/<task_id>.md`。原始隐藏评价/任务/凭据不上GitHub。
- 实际命令：`PYTHONPATH=src .venv/bin/python -m recoveragent.p3a collect --config .cache/a_failure_retry_20261021_a.json --output runs/a_failure_retry_20261021_a --credentials-file .env`；官方verify、控制侧诊断、tokenizer和测试命令见新报告。离线统计最初误用artists字典作计数键，已按ID展开/去重并加测试；没有影响Actor或计费。截断尾响应独立审计，不伪造完整Assistant消息。
- **修改方案已整理、尚未启用**：`docs/PROMPT_FIX_PROPOSAL.md` 给出明确省略answer、精确正文、完整嵌套聚合及A公开schema依赖检查/缺工具停机的候选条款与验收方法。后续另建版本，固定新场景与预算检验；不修改官方验证器、不偷偷补工具或清空answer。本轮没有宣称修正生效。P3-B缺项仍为正式数据/分组与权重、远程预检/迁移、真实4B后端和训练授权。

## 冻结提示与场景扩样（2026-10-21，v0.8）

- **实现/真实采集 PASS**：冻结 execution_v3、dependency_examples_v2 与 A/B 五个文件哈希，不再用旧两个Spotify任务调参。新采样器使用官方train ID→scenario映射，支持家族均衡与显式清单；seed=20261021，12个不同任务/6家族，每族2个，排除旧82e2fac家族。清单预先固定，不按隐藏难度/结果筛选，不替换或重跑失败。本轮统一B dynamic_discovery，A未运行，不是新A/B比较。
- **真实结果**：12尝试、12有效官方评价、0基础设施错误；6次成功、6唯一成功任务、4成功家族，6条全部入集；另6条过滤原因为official_not_successful。全部终止为completion_claim，模型自称完成不等于官方通过。全部159回执及失败成本重算通过，完整分母保留。
- Teacher仍为deepseek-flash，fingerprint全程 `e72d3c7f663d3e1383b00b3884cb4501`。实际159请求、382工具调用、1,809,110输入（1,287,936缓存）、54,678输出token；573.408秒采集墙钟，按公开费率估算USD0.229693416，实际账单未知。每任务40请求/16384输出/600秒/USD1，12任务总预算USD12，单回复4096、工具次数不限，无付费重试。沿用“费用不限”授权，无新服务/算力购买。
- **数据格式 PASS**：6条/6任务/4家族，82个Assistant目标、34,559监督token，最长完整序列29,039、最长生成前输入28,922、最长目标3,161；全部满足32768/4096检查。23次schema变化、254个工具参数回读及Assistant-only/padding mask通过，无静默截断。逐回合输入927,022，其中96.27%为上下文；文件系统家族占73.16%监督token，需正式采样/权重设计。
- 旧execution_v3成功包只读重验4条/2任务/1家族，31目标/9844监督token。新旧合计10条、8唯一任务、5家族，但未合并正式集；旧任务已用于提示诊断，不能做独立效果验证。更早不同协议数据仍单列保留，不混算本轮成功率。
- **Student协议实现/验证 PASS，模型运行未验收**：新增独立StudentSession，真实输入/输出token IDs与模型/模板/checkpoint契约，批量整体验证后串行执行，依赖结果与新工具只进入下一生成回合；A固定/B动态。中途错误保留结果并终止。JSON/schema不保证任意动作语义独立，无行为log probabilities。旧P0–P2的17项哈希不变。
- 104项软件测试、Ruff/格式通过；新旧10条真实Qwen tokenizer目标回读通过；另用真实AppWorld+真实tokenizer、脚本式生成完成A/B各4次公开工具读取。共8次、0模型请求，不能当Teacher示范或真实Student成绩。证据 `reports/student_batch_probe.json`；全部测试和成本见 `reports/scenario_expansion_report.md`。
- **错误记录**：新Student解析器首次未接受模板im_end后的换行，依据实际tokenizer修复并回归；升级质量审计后公共报告名重复触发append-only保护，结果原样另存v2，旧文件保留，CLI新增提前路径检查。两次沙箱Student探针卡在官方TestClient/AnyIO初始化；保存栈后在已批准的正常执行环境通过，未改上游依赖。
- Clean Dataset：`runs/scenario_expansion_20261021_a/clean_dataset/`，manifest SHA-256 `ca7e0d3e74567818f21d0a903b49e8a6291829e985fc5818df5cbe82f9d84707`。分词结果 `data/scenario_expansion_sft_20261021_b/`；本地12份完整LLM视图 `outputs/scenario_expansion_20261021_a/<task_id>.md`。原始数据/凭据/权重不公开，远程迁移未执行。
- 实际命令：`PYTHONPATH=src .venv/bin/python -m recoveragent.p3a collect --config .cache/scenario_expansion_20261021_a.json --output runs/scenario_expansion_20261021_a --credentials-file .env`；日志 `.cache/scenario_expansion_20261021_a.log`。官方verify、tokenizer、批量探针和测试命令详见新阶段报告；协议见 `docs/TEACHER_EXPANSION.md` / `docs/STUDENT_PROTOCOL.md`。
- **下一步/P3-B缺项**：确定正式数据规模、补更多成功场景并按scenario分组验证，处理监督量不均衡；核验远程96GB资源、同版AppWorld及安全传输哈希；接入真实4B批量推理后端并做长上下文/权重身份验收。后续明确授权训练后才做Base-SFT参数更新、保存重载与评测。本轮无SFT/GRPO、无4B权重加载/远程训练，未宣称效果提升。

## A/B 提示规范化（2026-10-21，v0.7）

- **实现、软件及真实回归 PASS**：默认 `execution_v3` 为 A/B 共享全局执行规则和六阶段循环，仅工具获取段落不同。A 默认 `dependency_examples_v2` 明确初始状态的认证/参数/覆盖/验证依赖并加入两个公开 API 自制正例；B 明确按需发现、schema 下一请求开放和依赖补齐。旧原版/增强版/Actor 提示保留，历史21次真实回执重新审计通过。
- 两个正例为笔记置顶和愿望单商品评价计数，官方公开 API 的输入/返回/分页契约核对通过；不是执行过的 Teacher 轨迹，不使用 hidden required_apis，不进入 Actor/SFT。规则、源文件及审查说明见 `docs/PROMPTS.md`。
- 两个先前已用的同族 train 任务，B1→A1→A2→B2 独立进程、原始初态、相同预算；**A 2/2、B 2/2 官方成功，4/4 入集**，0基础设施错误/截断/重试。这是提示回归，不是总体优势或正式 Student 结果。
- 实际33模型请求、204工具调用、254099输入（182656缓存命中）、11959输出 token、143.86 worker秒。估算USD0.036879636，逐请求实扣未知；不记为免费。每条20请求/8192输出/450秒/USD1.25、单回复4096，调用次数不限；A所有预测成本计入。
- A任务2先19次读取不含播放次数的私有信息，再自行改查歌曲详情，总44调用；B同任务32调用。成功不代表没有冗余，也不能将A泛化为更省工具。保留模型自己的纠错证据，没有据结果改提示再重跑。
- 软件 **80 passed**，Ruff/格式通过；33新回执、公开 Predictor 重建、Actor共用规则、示例隔离、完整分母与SHA-256审计通过；P0–P2原17文件哈希未变。证据：`reports/structured_prompts_{report.md,comparison.json,audit.json,checks.json,paths.json}`、`structured_prompt_examples_check.json`。
- Clean Dataset：`runs/structured_prompts_20261021_a/clean_dataset/`，manifest SHA-256 `6ce2fff2a2163e2a408a16ec4bd134161a387a191027bee02376ec070904be1e`。真实4B tokenizer得到31个Assistant目标、9844监督token；仅格式与mask检查，无权重/参数更新。证据 `reports/structured_prompts_sft_format.json`。
- 本地完整视图：`outputs/structured_prompts_20261021_a/{a,b}_{82e2fac_1,82e2fac_2}.md`。实际运行命令和日志位置见新报告。没有远程迁移或训练；下一步冻结提示后扩大train场景/重复采样，继续检查字段探测和分页冗余。

## 取消调用次数配额与 prompt 诊断（2026-10-20，v0.6）

- **实现/真实环境验证 PASS**：用户明确取消工具调用次数限制。新默认 `max_tool_calls=null`、`max_tool_calls_per_turn=null`，不存在原 40/64 数量配额；通过官方整数构造参数的 sys.maxsize 哨兵避免旧底层 100 次限制。4096 单回复、累计 token/请求/时间/费用预算及完整调用校验保留，P0–P2 不改写。
- 原 B1 任务的真实公开反馈含 8 歌单、60 条记录、57 首独立歌曲。模型先查 20 首再查 20 首，两批无重复、ID 均来自历史观察；第二批仅 696 输出 token。旧 prompt 允许批量却没有告知 40 次总额度。按原逐首路线预计需 66 次，不能把截停归结为模型不会调用工具。
- 原历史重放：28 条观察逐条一致，原拒绝的 20 次全部真实执行（累计 48）；另一个 world 连续 101 次公开读调用通过。149 次环境动作、0 模型请求，36.14 秒。明确不作为新 Teacher 示范。证据 `reports/unlimited_replay.json`。
- 新鲜原始初态、相同 Teacher/预算/Actor v2、三个独立进程：B 动态发现 **67 调用成功**；A 官方原提示 **5 调用失败**（仍漏登录/凭据）；A `dependency_aware_v1` **61 调用成功**，模型自行选择认证工具。每条件一次；只支持该案例诊断，不支持总体/Student 优势。官方原提示仍保留为默认 A，增强版单独标记。
- 实际新 Teacher 开销：21 请求、128735 输入（85888 缓存命中）、5737 输出 token、133 工具调用、99.99 秒。高峰费率估算 USD 0.020253828，账户观察差额 CNY 0.00、逐请求实扣未知；无训练。每组预算20请求/8192输出/450秒/USD1.50，三组总请求/token/时间预算均未触达。
- 新合并包 `data/unlimited_clean_20261020_a/` 接受 2/3 条，1 个唯一任务；manifest SHA-256 `0a13edb56232ceff8b554f09c2f3885c7e93522423da0c573c3019a3ed994d7f`。真实 4B tokenizer 得到 14 个 Assistant 目标、3988 监督 token；预测提示不入目标，未加载权重。原失败/重放证据不混入成功样本。
- 软件 **75 passed**，Ruff/格式通过；真实 21 回执离线审计重算、两种 A Predictor 公开输入重建、A Actor 提示相同、初态/模型/预算一致、P0–P2 17 哈希未变。证据 `reports/unlimited_prompt_report.md`、`unlimited_prompt_comparison.json`、`unlimited_prompt_audit.json`、`unlimited_sft_format.json`。
- 三份本地完整视图 `outputs/unlimited_prompt_20261020_a/{b_dynamic,a_official,a_dependency}.md`。实际命令见报告。下一步为更多预先固定 train 场景的重复提示对照；保持原版与增强版独立、所有失败完整计入，不启动训练。

## 双工具模式比较与 4096 token 上限（2026-10-20）

- **实现和真实配对验证 PASS**：`api_predictor` 复用固定上游官方源码和提示，只用公开任务/API 简介、零 gold 示例；执行开始后工具固定。`dynamic_discovery` 保留动态注册。所有新默认 Teacher/Predictor 回复最多 4096 token，预算不足时下调；旧记录的 2048 不改写。协议及命令见 `docs/TOOL_MODES.md`。
- 两个官方 train 任务、四条全新 Teacher 轨迹：B1/A1/A2/B2 串行独立 world/进程，同任务初态与 Teacher backend 一致；每次相同 20 总请求/8192 输出 token/40 工具调用/450 秒预算，A 预测开销包含在内。结果 **B 动态发现 1/2，A 官方零示例 Predictor 0/2**，0 基础设施错误；这不是正式 Student 或完整官方 benchmark 成绩，不支持总体优劣结论。
- A 两次预测都漏选登录/凭据工具；B1 在已执行 28 次后申请 20 次工具调用、超过剩余 12 次额度，整批拒绝，非 token 截断。B2 `teacher_5601c6f4d156` 经 8 回合/29 次调用获官方成功。没有自动补认证工具或根据失败结果改写本次协议。
- 真实开销：21 模型请求（含 2 次 Predictor）、60 工具调用、4314 生成 token、122290 输入 token、120.92 秒。公开高峰费率估算 USD 0.023085432；前后余额回执观察差额 CNY 0.00，逐请求实扣未知，不能写成免费。无 GPU 训练、无新付费服务。原始数据在 `runs/tool_modes_20261020_a/`。
- 新 Clean Dataset 接受 1/4 条，SHA-256/train/来源/成本复核通过；固定 4B tokenizer 得到 8 个 Assistant 目标、1474 监督 token，未加载权重或更新参数。Dataset manifest SHA-256 `fc1367decb73b4e70d1bbe9cb052ba3c291f721942f33edaa914cdc03dbe4932`。未宣称远程迁移/训练已执行。
- 65 项软件测试与 Ruff/格式检查通过；离线审计全部 21 份响应，按官方公开输入重建两次 Predictor 消息、解析结果及 schema 一致。P0–P2 原 17 项哈希不变。证据 `reports/tool_modes_report.md`、`tool_modes_comparison.json`、`tool_modes_audit.json`、`tool_modes_sft_format.json`；测试命令见报告。
- 查看器 `--all` 已能分开展示 Predictor 和 Actor：本地四份视图 `outputs/tool_modes_20261020_a/teacher_<ID>.md`，无哈希警告。原始/派生任务数据不公开。下一步是预先固定更多 train 场景与重复采样；若补入认证工具，作为另一个明确标记的变体保留本次负结果。SFT/GRPO 仍 `NOT_STARTED`。

## LLM 请求视图与排错工具（2026-10-20）

- 状态 `PASS`：新增 `recoveragent.inspect_teacher`，离线读取每轮真实 API request/response，展示完整 messages、当时工具 schema 及工具增减、原始响应、后续反馈、token 用量、控制侧诊断和原始回执哈希。
- 严格区分请求前输入、响应后观察与控制侧奖励；不读取官方 evaluator 文件，不从最终历史/最终 schema 回填早期请求，截断响应不会被修补。展示时一致隐藏已识别的密码/访问令牌/API Key；原始记录只读，输出不覆盖已有文件。
- 实际产物：`outputs/llm_view_20261020/success_turn1.md`、`success_all.md`、`truncated_turn6.md`，仅本地保留。用法和当前预算/动态工具发现规则见 `docs/LLM_VIEW.md`。
- 验证：新增 7 项测试，全套 **59 passed**，Ruff/格式检查通过。离线核对现有 6 条真实 Teacher 轨迹的 28 次请求，历史/工具数量、响应终止原因一致、回执无哈希警告，原始文件未修改；证据 `reports/llm_viewer_validation.json`、`reports/llm_viewer_checks.json`。
- 该查看器开发轮的模型请求、AppWorld 新执行、GPU 训练及费用均为 0；当时单回复上限 2048、每轨迹累计上限 16384。后续 v0.5 上限及新采样见上节；历史 C 轮失败为单响应截断，不能据此推断 Teacher 在更宽预算下必然失败。
- 下一步可先用 `--turn N` 定位上下文、工具可见性或截断问题，再决定后续采集参数；本轮没有扩大采样或进入训练。

## P3-A 开发与真实验证（2026-10-20）

- 用户在异步费用授权问题回复“费用不限”后才启动 DeepSeek 请求。本轮保留 2 个唯一 train 任务、累计 80 次请求/32768 生成 token/1800 秒的小试上限，未购买算力或启动训练。默认配置仍关闭付费调用。
- Teacher 为 `deepseek-flash`，供应商文档版本 DeepSeek-V4.1-Flash-1017；实际响应 fingerprint `e72d3c7f663d3e1383b00b3884cb4501` 全程一致。该 API alias 不是不可变权重 revision，也没有与 4B Student 做正式能力比较。
- 原生 Teacher adapter、正常任务 Runner 和批量历史模块已实现。所有业务步骤由 Teacher 决定，只看到公开任务/工具/已发生历史；终态先保存，再关闭 world 并调用官方 `evaluate_task`，隐藏评价不进入消息。
- 三轮均从官方前两个 train 任务 `82e2fac_1` / `82e2fac_2` 的原始初态执行。A：2 次失败（单调用协议拒绝批量）；B：2 次失败（禁用并行参数仍返回批量）；C：1 次截断失败、1 次官方成功。共 **6 次尝试、6 次有效评价、0 基础设施异常、1 次成功**，全尝试成功率 **1/6**；最终协议 C 为 **1/2**。协议不同，不能当作固定策略基准成绩。
- 成功轨迹 `runs/p3a_teacher_20261020_c/teacher_ad808a15ce5a/`：8 个 Teacher 回合、29 次真实工具调用、官方严格奖励 1。其余失败、回执、终态和筛选原因全部保留，不进入成功示范。
- P3-A 允许最多 64 个原生调用组成一批，整批先校验、逐个执行、收齐全部结果再保存边界；截断或无效批次整体拒绝。新终态格式只用于 P3-A 评价证据，不冒充 P0/P2 恢复快照。设计见 D012。
- `data/p3a_clean_20261020_all/`：合并所有 A/B/C 尝试成本，筛得 1 条完整正常 Teacher 示范。Dataset ID `868da089c98626a256308edfa3111d6b914d95ebd3d375d74536a1268181e9bd`；manifest SHA-256 `464ea9bef9e794082b266ae6ed53bc5be358eba3e0c74a026325526a70214b30`。官方 train 成员核验、哈希/来源/顺序重算、同机换目录交接检查通过。原始数据不上传公开 GitHub。
- 正式 4B tokenizer revision `1cfa9a7208912126459214e8b04321603b3df60c`。真实示范转换为 **8 样本、1146 监督 token**，最大样本 12115 token；29 次工具参数往返、前缀保持、非目标上下文及 padding mask 通过。仅 tokenizer/CPU 准备，没有模型权重、optimizer 或 SFT 更新。
- 旧 Qwen 格式问题重算：100 回合中 50 次格式拒绝均为无 tool tags 的普通文本，全部提及 access token；另有 2 次工具执行错误。没有证据说明 JSON parser 丢失了合法调用，也没有证据说明 4B 的错误已被训练解决。
- 软件测试 **52 passed**（含合成 transport/world，仅验证软件）；Ruff、格式、compileall 和两个 venv 的 pip check 通过。真实 API/AppWorld 验收与软件测试分开报告。
- 资源：28 次模型请求、140388 输入 token（含 79232 缓存命中）、7008 输出 token、47 次工具调用；三轮累计采样墙钟 170.22 秒。按公开高峰 USD 费率估算 0.027231792 美元；官方账户前后余额减少 **人民币 0.04 元**，仅为账户级观察差额，未排除其他并发使用，逐请求账单未取得。
- 初始授权前预检和空包 (`p3a_preflight_20261020_a`、`p3a_empty_handoff_20261020_a`) 仅保留为历史记录；不是当前阻塞或成功数据。首次 tokenizer 检查缺 jsonschema，固定安装 4.25.1 后通过。
- 完整命令/限制/证据索引：`reports/p3a_report.md`。交接说明：`docs/P3A_HANDOFF.md`。主要新增代码：`teacher.py`、`teacher_history.py`、`p3a.py`、`demonstrations.py`、`base_sft.py`。
- 远程 RTX PRO 6000 96GB、SSH 传输、远程 AppWorld worker 均未实际验证。下一项工作是扩大 train 场景覆盖、按场景分组准备数据及完成远程预检/迁移；本轮到此停止，不进入正式 Base-SFT、Recovery-SFT 或 GRPO。

## 此前 v0.4 模型与硬件路线记录（2026-10-20，仅文档）

- **唯一正式 Student**：`Qwen/Qwen3-4B`。此前本机 RTX 5050 8GB 上的 `Qwen3-1.7B` 是 P0–P2 基础设施历史，不作为正式 4B SFT/GRPO 的模型或算法结论。
- **本机 P3-A**：执行 AppWorld Teacher 正常任务、真实官方验证/过滤、导出含 SHA-256 manifest 的未分词多轮轨迹；Teacher 是否可用与费用仍需检查。
- **远程 P3-B 以后**：目标单张 RTX PRO 6000 Blackwell 96GB（尚待 SSH、实际 GPU 型号、显存、CUDA/驱动和权限验证），执行 Qwen3-4B Base-SFT、4B Student 自主任务与新失败、多检查点 Teacher 恢复、Recovery-SFT/GRPO 和迭代。需远程可执行的同版本 AppWorld worker，不只复制模型训练数据。
- **交接前置**：保存 Teacher/model/任务/划分/AppWorld 源码数据版本、schema、完整消息、官方成功证据摘要、过滤分母、文件 SHA-256；远程重新使用 Qwen3-4B tokenizer 构建 Assistant-only loss mask，不能直接使用 1.7B token IDs。数据通过经授权安全通道转移，不上传受限轨迹/权重/凭据到公共 GitHub。
- 本次仅文档更新；没有新增 Teacher 成功示范、远程训练、96GB 硬件预检或 4B Student 结果；不得将计划冒充完成。

## v0.3 原训练顺序记录（2026-10-20）

- **已确定的训练顺序**：P3-A Teacher 从 AppWorld 干净初态完成**正常成功轨迹** → P3-B Student **Base-SFT**（全部 Teacher 正常轨迹 Assistant token）→ P3-C Base-SFT Student 独立执行产生**新自然失败** → P4-A 从多个合法检查点由 Teacher 真实修复 → P4-B Student **Recovery-SFT**（仅 Teacher 恢复后缀 Assistant token）→ P4-C Student 恢复 GRPO → P5 每轮新失败 + 新 Teacher 示范 + 增量 SFT/GRPO → P6 最终评价。
- **主要增量**：Multi-Checkpoint Recovery Learning；Post-error/Late-failure 主实验、Pre-error 独立预防消融。V1 规则选点先行，U_SFT=p_T(1-p_S)、U_RL=4p_S(1-p_S) 属于 V2 待验证启发式。
- **下一轮范围**：先核对已知 Qwen 工具调用格式错误及 P3-A Teacher 正常任务成功示范的接口/低负载试点；没有合法可用 Teacher 与费用授权时只做不付费组件/测试。完成经验证的正常 Teacher 轨迹且资源允许才进入 P3-B，绝不按旧方案先做基座失败的 Recovery-SFT 或直接 GRPO。
- **本次修改**：PROJECT_PLAN.md、AGENTS.md、docs/STATUS.md、docs/DECISIONS.md、README.md；未修改实际运行代码、权重、轨迹或旧报告，没有运行训练。

## P0–P2 历史执行记录（2026-10-19）

- 当时依据 v0.2 完成基础设施；后续执行顺序以 v0.3 新用户要求为准。
- 独立开发分支 `codex/p0-p2-recovery`，真实 Git 克隆 `/tmp/recoveragent-dev-20261019`，origin 已核对为用户指定仓库。
- 实现：公开 API 白名单、JSON/schema 参数校验、模型单调用协议、独立推理服务、正常与恢复 Runner、外置不可变快照、完整历史边界、train-only Failure Buffer、四进程串行调度与预算。
- 软件测试：`.venv/bin/python -m pytest -q` → 26 passed（软件逻辑，不替代 AppWorld），证据 `reports/software_tests.txt`。
- AppWorld 实际安装：`0.2.0.dev0`，commit `42b5bcf3cd334fee33f0c37c02070a9f5807add5`，Python 3.14.4。官方数据已下载到 `data/appworld`。
- 真实状态探针：`runs/p0_state_20261019_d` 完整通过。四个独立 PID 的写入、导入、重复恢复和官方评价可核查；公开摘要 `reports/p0_state_evidence.json`。这是基础设施探针，不计入自然失败。
- 真实 Student 自然失败：`runs/p0_p2_20261019_a/normal_dbf9101f8c55/trajectory.json`，train 来源，40 个生成回合、22 次真实工具调用、1471 生成 token、266862 输入 token；终止原因 `budget_exhausted`，官方严格奖励 0。没有人工注入故障或 Runner 补做业务步骤。
- Failure Buffer：同一运行的 `failure_buffer/`；匹配快照为正常轨迹的 `terminal/`。Buffer 重读、train 来源与状态/历史绑定均通过。
- 四分支恢复：4 个独立 PID 串行执行，每条 15 个生成回合、7 次真实工具调用；实际首轮消息、工具 schema、状态与运行时指纹一致，源快照在结束后仍完整。官方严格奖励均为 0，没有发现成功，也没有奖励差异；不能据此判断状态不可恢复。证据 `reports/p2_protocol_checks.json`。
- 最终阶段报告与成本重算：`reports/p0_p2_report.md`、`reports/p0_p2_evidence.json`；原始 worker 日志、token IDs、轨迹、快照和官方评价保存在 `runs/p0_p2_20261019_a/`，不上传原始基准内容。
- 错误证据：`.cache/appworld-unpack*.log`、`.cache/state-probe-b.log`、`.cache/state-probe-c.log`；诊断与兼容决策见 `docs/DECISIONS.md`。
- 当前阻塞与限制：P0–P2 无未解决阻塞。只有 1 个 train 起点；该自然失败的数据库记录与初始状态相同，尚未覆盖 Student 误写业务数据后的补救。P0 的真实写入探针为独立人为测试。没有 dev/test 总体评价或跨主机验收。无行为 log probabilities，当前轨迹不能直接用于在线 GRPO；Teacher SFT 尚未进行。
- 资源：RTX 5050 8GB、约 15GiB 系统 RAM，固定 Qwen3-1.7B BF16、单环境 worker。模型实验共 100 个生成回合、50 次工具调用、919188 输入 token、4404 生成 token、1647.63 秒（27.46 分钟）；基础设施错误和重试均为 0。未触达 150000 生成 token / 90 分钟上限。安装、下载及状态探针另见环境报告。付费服务、租用算力和训练步数均为 0，推理服务已停止。
- 实际实验命令：`PYTHONPATH=src APPWORLD_CACHE="$PWD/.cache/appworld" IPYTHONDIR="$PWD/.cache/ipython" timeout 5400 .venv/bin/python -m recoveragent.cli run --config configs/p0_p2.json --output runs/p0_p2_20261019_a`，日志 `.cache/p0-p2-a.log`。
- 复现与验证命令：真实状态探针、推理服务、主实验、摘要重算、pytest 与 Ruff 的完整实际命令见 `reports/p0_p2_report.md`；环境安装和兼容性日志见 `reports/environment_report.md`。
- 当时的下一步计划已由 v0.3 替代；现在 **先 Teacher 正常成功轨迹与 Student Base-SFT**，再从 Base-SFT Student 新自然失败生成 Teacher 恢复示范与 Recovery-SFT，最后才是 GRPO。

## 上次文档发布记录：已完成

- 阅读 `AGENTS.md`、`PROJECT_PLAN.md`，确认初始项目只有两份设计文档，没有代码、依赖、模型或实验结果。
- 通过已连接的 GitHub 账号核对身份为 `WYXeva`。
- 添加 `README.md`，明确项目路线和未实施状态。
- 添加 `.gitignore`，排除凭据、环境、原始数据、快照、轨迹和权重。
- 保留既有计划与工作约定原文。
- 在可写的临时目录准备独立 Git 发布副本：`/tmp/recoveragent-publish-20261019-i0v5ybuw`，分支为 `main`，仅包含上述两份原始文档及新增的 README、忽略规则、状态记录，共五个文件。
- 五个文件均为小型 UTF-8 文本；常见 token/私钥模式检查未发现匹配，预期敏感路径忽略检查与新增文件空白检查通过。模式检查不代表完整秘密审计。
- 复查远端初始提交 `a9c5b1ba769bab3e17e4a8affe5b446e0f202b65`，确认仅有一行项目标题的 README；在其上追加文档提交，没有强制覆盖历史。
- 通过 GitHub 连接器完成文档发布提交 `c13b316d0939ea92b367f0f91c99cfb6210b7334`，包含 `.gitignore`、`README.md`、`AGENTS.md`、`PROJECT_PLAN.md`；本状态文件随后单独提交。
- 回读发布树 `c20bfc95d80b2cc8f1326a2a87fff80987c40e13`，上述四个文件的 Git blob SHA 与本地逐字节计算结果全部一致。

## 上次文档发布：检查证据与当时阻塞

实际执行的检查：

| 命令或接口 | 实际结果 |
| --- | --- |
| `git status --short` | 当前目录不是有效 Git 仓库；现有 `.git` 为空且只读。 |
| `gh auth status` | 报告认证检查失败；因后续发现网络不通，不能据此断言 token 已失效。 |
| `gh api user --jq '{login: .login, id: .id, name: .name}'` | 无法连接 `api.github.com`。 |
| `curl -I --max-time 15 https://api.github.com` | 无法解析 `api.github.com`，退出码 6。 |
| 使用提升权限的 `curl` 检查 GitHub | 自动审批拒绝执行：当前会话禁止 sandbox 权限提升。 |
| GitHub 连接器 `get_profile` | 成功核对账号为 `WYXeva`。 |

以上是首次尝试的阻塞记录。继续执行时，终端仍无法连接 GitHub，项目根目录 `.git` 仍只读；但远端仓库已存在，GitHub 连接器对该仓库具有写入权限，因此通过连接器完成发布，不依赖终端 `git push`，也没有绕过终端权限限制。

本轮实际执行的远端操作（仓库均为 `WYXeva/RecoverAgent-RL`）：

| 操作 | 实际结果 |
| --- | --- |
| GitHub `get_repo`、`fetch`、`fetch_file` | 核对账号、公开可见性、`main` 分支与原有 README。 |
| `create_tree(base_tree_sha=e8a5957d53d32bcda1aa71592e7242e03049270e)` | 生成文档树 `c20bfc95d80b2cc8f1326a2a87fff80987c40e13`。 |
| `create_commit(parent_sha=a9c5b1ba769bab3e17e4a8affe5b446e0f202b65)` | 生成文档提交 `c13b316d0939ea92b367f0f91c99cfb6210b7334`。 |
| `update_ref(branch_name=main, expected_sha=a9c5b1ba769bab3e17e4a8affe5b446e0f202b65, force=false)` | 成功推进 `main`，保留初始提交。 |
| `fetch` 回读文档树；Python `hashlib.sha1` 计算本地 Git blob SHA | 四个项目文档全部匹配。 |

GitHub 发布已完成；本地原始工作目录的 Git 初始化及与远端同步仍受当前 `.git` 写入限制影响。临时发布副本中的 `4d84f74` 是首次准备提交，不是本次远端发布提交，不能用它覆盖当前远端历史。

## 上次文档发布：资源与验证范围

- 仅进行文件、Git 与 GitHub 连接检查；没有安装项目依赖或启动实验。
- 模型生成 token、训练步数、GPU 用量及付费模型调用均为 0。
- 没有真实 AppWorld、Teacher SFT、GRPO 或效果提升结果。

## 上次文档发布：当时下一步

1. 后续需要本地 Git 工作流时，在允许 Git 写入且可联网的终端中接入当前远端历史；无需再次创建 GitHub 仓库。
2. 用户授权开始实现后，按计划推进 P0–P2；本轮发布任务不启动训练。
