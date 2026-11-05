# B10 M1 自然失败与 Teacher 快照恢复 — 最终验收

2026-11-05；分支 `codex/p0-p2-recovery`；基线 `4127c7812cfcfccff4e2459f014405bed8258fdc`。同一冻结B10真实试点已完成，原始产物已回传并校验。未训练、未改M1、未运行C244或后续阶段。机器关机命令已执行，随后SSH连接被拒绝；关机延误单列如下。

## 实际结果

| 指标 | 实际数量 |
| --- | ---: |
| Student任务/官方成功/有效自然失败 | 10 / 1 / 9 |
| 基础设施异常或未知结果 | 0 |
| 独立合法检查点/Teacher尝试 | 13 / 13 |
| post-error / late-failure | 4 / 9 |
| Teacher官方成功/失败 | 5 / 8 |
| 成功覆盖的不同任务 | 4 |
| 长度合格恢复后缀/超长deferred | 3 / 2 |
| 合格Assistant样本/监督token | 23 / 5066 |

9条失败中5条没有可选择的已观察错误后边界，只取late-failure；4条有两个不同合法边界。成功Student任务不恢复；没有虚构post-error或pre-error重做，没有为了凑满20次扩大采样。各分支仅一次，无付费重发。所有13次Teacher都有真实回执和原任务官方评价。

| 成功任务 | 边界 | 数据处理 | Assistant样本 | 监督token |
| --- | --- | --- | ---: | ---: |
| 042a9fc_2 | late-failure | 完整上下文超32768，deferred | 0 | 0 |
| 07b42fd_1 | late-failure | 合格 | 8 | 908 |
| 09b0ee6_1 | post-error | 合格 | 8 | 2813 |
| 09b0ee6_1 | late-failure | 完整上下文超32768，deferred | 0 | 0 |
| 0a9d82a_1 | late-failure | 合格 | 7 | 1345 |

## 中途预算调整

用户在运行中明确要求后续会话改为80请求、1200秒、15次通用协议纠错。等待第七题在途Student按旧预算封存后才换版本；旧7个Student和6次Teacher不重跑。第七题待尝试的两个Teacher分支采用新预算，之后执行剩余三题。

| 预算组 | Student预算 | Teacher预算 | Student成功/任务 | Teacher成功/尝试 | 合格/deferred |
| --- | --- | --- | --- | --- | --- |
| 原版本 | 40请求/600秒/2次通用纠错 | 80请求/600秒/2次通用纠错 | 1/7 | 2/6 | 1/1 |
| 用户调整后 | 80请求/1200秒/15次通用纠错 | 80请求/1200秒/15次通用纠错 | 0/3 | 3/7 | 2/1 |

两组均保持累计生成16384、单回复4096、输入加目标不超32768、数值答案仅一次同值纠正、2条无进展提示、工具调用数不限和原严格整批校验。第15次通用拒绝仍允许下一次纠正，第16次拒绝停止；没有额外数值纠正机会。结果是**混合预算、固定小样本描述**，不是同预算模型对比，也不能推出增加预算的因果收益。

## 接通的实际执行路径

`recoveragent.b10_pilot` 通过现有隔离模块绑定复用 `p3c.run_episode` / `drive`、`StudentSession`、原始未合并M1 deadline后端及 `p3a.run_teacher_episode`。`recovery_suffix` 复用现有数据过滤和编码器。没有复制整套执行循环、扩展Schema核心或修改 `state.restore`、`execution_v6`。

采集、保存、接管和动态工具发现共用 `schema_nullable_compatibility_v2`。Teacher先经共享 `prepare_snapshot_adapter` 按快照记录重建catalogue，核对revision、catalogue哈希、可见工具，再进入原严格状态/历史恢复。13个源检查点均在独立进程/输出目录中只读恢复，前后文件SHA核对；原始副作用和同刻前缀保留，评分仍参照原任务，不参照检查点。源快照是恢复起点，Teacher没有获得未来轨迹或评分内容。

原前缀后加入固定通用user接管消息，以开始Teacher thinking API新回合，不为Student历史伪造reasoning。前缀、接管消息、工具和控制信息全部屏蔽；只监督Teacher新增且通过过滤的Assistant目标。Teacher真实reasoning只保留私有审计/自身续传，不进入Student目标。数值纠正状态随快照继承，Teacher通用纠错独立且有界。超长成功整条deferred，不截断、不删操作。

Student请求包含与同机后端一致的绝对 `deadline_monotonic`；客户端等待为剩余会话时间加5秒，校验后端变体和deadline回显。实际236份请求/响应的deadline、输入token和模型policy逐一一致。本轮没有生成中途deadline回执，6次Student预算终止发生在既定循环预算检查；服务器到期分支由软件测试覆盖，不能写成此次真实触发。未再使用180秒默认等待。收尾保存和官方评分会使记录的端到端时间略超过会话限额。

## 身份、版本和可复现性

- 原B244顺序前10项：`configs/b10_recovery_plan_20261104.json`，SHA `aa01f3d237a9901210893bd7a4e69d0b7143c3eeecdbc26ac27d844440fa4b00`。官方来源5 test_normal、4 test_challenge、1 train；保留Full732自定义研究标识，不冒称官方benchmark。
- M1：Qwen/Qwen3-4B，revision `1cfa9a7208912126459214e8b04321603b3df60c`，non-thinking模板SHA `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`。51份发布文件在执行和收尾再次匹配；实际GPU加载LoRA参数SHA `3b9cf8f3aed4b36b9d55d0289d3a2f99faef400af80480f7179d09fcf3aa400c`。原始BF16+PEFT，未合并、替换或训练权重。
- 机器：已授权RTX 4080 SUPER，32760 MiB，未开新实例、续费或租机。
- Teacher：既定官方 `deepseek-flash`、thinking协议、请求/响应身份及用量回执。别名不构成独立的固定权重验证；保留实际response model/ID。
- 原配置/锁：`configs/b10_execution_20261104{,_lock}.json`；锁SHA `2835f25c0d5f8241a4fa11caa1faeec1cb01621afdccfd34d22bd156d39d162f`。原运行精确源码另存提交 `90617f707e4be7c92c25faf137082e8e18a60642`，对应tree `9eac1042842acc6dc15523431ed7c04f72296821`。
- 新配置/锁：`configs/b10_execution_budget_20261105{,_lock}.json`；锁SHA `18941a89eb0bdcbfe34e0069a1fd186a1fff89b46f095532c27745261e683cde`。新锁包含本轮执行依赖；旧锁不重签，新代码仍被旧锁拒绝。
- 旧阶段不可变manifest SHA `78fccc9df3471386e51805507fa2fb86e83afb13b79eea738035499e96c5fee5`，续跑先核对全部文件和已封存claim，只处理未尝试任务/分支；全局最多20次包含旧尝试。
- 实际公共Schema原始catalogue SHA `d3adfb084a940a05daacd95c85cc9f31ef922269f54f1afd59a2a935343db44b`；转换后 SHA `221d0651ea48aa8d919415b92d7cd33dd528afe4d1f98484d1826944f6cd9611`。所有选定检查点一致。

## 失败原因和成本

Student 9条失败：6条会话时间预算耗尽，3条声明完成但官方未通过。所有Student通用协议拒绝为0，因此本轮不能证明把纠错次数提高到15产生了实际收益。Teacher 8次失败：4次声明完成但官方未通过，3次生成token耗尽，1次协议纠错耗尽且累计生成达到16384。没有把官方失败当作环境故障，也没有把“完成声明”当作成功。此处是可核验的终止原因分类，不推测未完成任务的业务语义根因。

| 预算组/角色 | 请求 | 输入token | 生成token | 记录的端到端秒 | Teacher费率估算CNY |
| --- | ---: | ---: | ---: | ---: | ---: |
| 原Student | 120 | 1092349 | 7915 | 3759.63 | — |
| 新Student | 116 | 1310808 | 10809 | 3640.58 | — |
| 原Teacher | 84 | 2477018 | 46031 | 951.30 | 1.45045296 |
| 新Teacher | 90 | 2355601 | 75555 | 1099.68 | 1.45259176 |

Student总236请求、2403157输入、18724生成；Teacher总174请求、4832619输入（3946368缓存）、121586生成。完整失败Student成本计一次，Teacher恢复另计，不重复累计共享前缀；每个分支仍保存截至检查点的prefix usage和完整失败usage供审计。Teacher合计峰值费率估算 **2.90304472元**，未知用量/费用预留为0，实际扣费未知；GPU租赁账单未获取。

Student端到端合计7400.22秒，其中后端实际生成1661.29秒；其余包含工具、快照、初始化、序列化、评分等，不能归因于GPU生成速度。实际PyTorch分配峰值15289070080 bytes（约14.24 GiB）。Teacher端到端合计2050.98秒。首任务claim为北京时间11月4日23:21:29，最终summary落盘为11月5日02:10:35，经过约169.10分钟，包含中途预算调整、部署和封存。

**关机收尾延误**：最终完成后后端仍驻留，直到本次状态检查后才停止并回传；11月5日08:49:11执行 `bash /usr/bin/shutdown`，退出码0，随后新SSH返回 `Connection refused`。完成到关机约6小时39分钟，此期间不是试点执行时间，不能隐去或算成模型耗时。未及时执行用户的“跑完关机”要求是本次操作疏漏。没有平台控制台/账单证据，不声称独立确认计费已停止。

## 验证、回传与边界

```bash
PYTHONPATH=src:.:tests .venv/bin/pytest -q tests/test_b10_execution.py
PYTHONPATH=src:.:tests .venv/bin/pytest -q \
  tests/test_b10_execution.py tests/test_harness.py tests/test_harness_refactor.py \
  tests/test_harness_review.py tests/test_answer_guard.py tests/test_student_batch.py \
  tests/test_teacher_data.py tests/test_schema_compatibility.py tests/test_m1_vgpu_reference.py
PYTHONPATH=src:.:tests .venv/bin/pytest -q
.venv/bin/ruff check src scripts tests
.venv/bin/ruff format --check src scripts tests
```

原版本新增10项、针对性/相关回归206项、完整748项通过；预算更新后新增测试共14项，完整 **752 passed**、3条上游Pydantic警告，Ruff check/format check通过。首次旧测试有54项因源码锁拒绝新代码失败；修正临时测试清单后通过，未重签任何旧生产锁。合成测试覆盖接管、状态/Schema、分支隔离、原任务评分参照、suffix-only mask、超长deferred、异常费用、deadline及预算/续跑边界。这些软件结果独立于上述真实模型/真实AppWorld结果。

已回传完整私有归档 `runs/b10_execution_20261104/final_b10_all.tar.gz`（29512471 bytes），SHA `f57e8dff938ae0aec55c61c60efe0eb963d4f40b20b08af5773b94540ee8d91e`。23个episode的完整文件清单、13条Teacher同刻前缀/可见工具/状态引用、真实响应回执、原任务评价、3条合格样本的目标边界/labels/loss_mask和最终数据来源SHA全部通过本地只读核验；236份Student客户端/后端deadline回执一致。

最终私有恢复数据 `final_returned/live_002_budget80_1200/recovery_dataset/`，manifest SHA `039e10afbb4df3930f56bbb6bfa5ec614eb67592edb14dda6e502a427ea9eb51`，包含3条结构化恢复样本、23个编码样本及源attempt引用。5次成功全部保存，包括2条超长原件；8次失败也有完整原始记录。私有凭据不在归档中，远端临时凭据已移除。公开仓库只提交代码、配置、测试和汇总报告/JSON，不含原始任务内容、状态数据库、模型消息、权重或凭据。

本轮真实验证了同一主机独立AppWorld进程的快照接管、Teacher修复及原任务官方评分。未验证跨主机恢复，未进行Recovery-SFT/GRPO、M1参数更新、C244或效果提升对比；3条合格后缀只是后续训练候选，不代表训练已完成。小样本且混合预算，不外推整体成功率。完成后停止，等待审查。

## 本次实际启动命令

以下为已执行命令的复现记录，不是重跑授权。输出及claim只创建一次。原版本使用上述源码保留提交，目录 `source` / `backend_001` / `live_001`，配置 `configs/b10_execution_20261104.json`，没有 `--prior`。预算续跑使用最终源码、已封存旧阶段：

```bash
base=/root/autodl-tmp/recoveragent_m1_20261030
job=/root/autodl-tmp/recoveragent_b10_20261104
cd "$job/source_budget_20261105"
export PYTHONPATH="$job/source_budget_20261105/src:$job/source_budget_20261105"
export HF_HOME="$base/hf_cache"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false

# 独立终端一；等 identity.json/READY。
"$base/.venv-sft/bin/python" -m recoveragent.b10_pilot serve \
  --config configs/b10_execution_budget_20261105.json \
  --adapter "$base/training/run_001/formal" \
  --probe "$base/runs/synthetic_probe_002/progress.json" \
  --output "$job/backend_budget_001"

# 独立终端二。
"$base/.venv-appworld/bin/python" -m recoveragent.b10_pilot run \
  --config configs/b10_execution_budget_20261105.json \
  --prior "$job/live_001" \
  --root "$base/appworld_root" --adapter "$base/training/run_001/formal" \
  --credentials "$job/teacher_credentials.private" \
  --output "$job/live_002_budget80_1200"
```

机器关机需在完整结果回传和SHA校验后执行，并在调度收尾中可靠触发；本次没有自动关机钩子，导致上述延误。未把该缺陷写成已修复或另开工程阶段。
