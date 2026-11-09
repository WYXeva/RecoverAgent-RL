# v2 第一轮学习执行记录（2026-11-09）

本轮以 `ddc6e8e4338eb168ec4784e906b4de66ab7dd297` 为代码基线，按 `project.md` 的交付 1 实现并启动真实学习链路。本文区分软件验收、真实环境探针与模型运行；状态以最终回执为准。

## 固定设置与入口

- Student：`Qwen/Qwen3.5-9B`，revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`；原生 non-thinking 模板 SHA-256 `a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715`。没有加载旧 4B adapter，也没有切换 8B。
- Full732 场景隔离自定义划分：170/37/37 个 train/dev/test 场景，510/111/111 个任务，seed `20261109`；保留原官方 split。清单 manifest SHA-256 `ff24d1c05f16e82795e0baa4dd855c03d8482bb32d6e2e07ce386a9af59bfe6b`。固定 feedback20 和 dev24，不按结果替换。
- 配置：[v2_round1_cpu_offload_20261109.json](../configs/v2_round1_cpu_offload_20261109.json)；源码锁：[v2_round1_cpu_offload_20261109_lock.json](../configs/v2_round1_cpu_offload_20261109_lock.json)，SHA-256 `9f12bf4e6c446fa93847f678456b93a753cdb7921080c04bfca985b29393fcfd`。历史锁、数据、4B 权重和成绩保持原样。
- 现有机器实际为 RTX 4080 SUPER vGPU、32 GB，BF16 可用；与计划参考的 80/96 GB 不同，必须以短/最长完整样本实测验收。只用已开机实例，不租机或续费。
- GPU 作业最初上限8小时；用户于本次运行中明确批准延长为总计12小时，已用时间计入，新绝对截止为北京时间2026-11-10 02:12:31。训练侧 Student 生成总量仍为20万token；每episode 80请求、16384生成token、4096单回复、32768上下文、1200秒；通用纠错15次。开发评估关闭训练快照。
- 用户确认 Teacher 不设独立金额上限。运行保留请求/token/时间限制、逐响应身份和费用估算；部署中的 5130 元是 30×80 有界请求按峰时价格推导的保守技术预留，不是用户另设的费用额度。Teacher 使用现有官方 `deepseek-flash`，没有自动收费重试。

入口复用现有 AppWorld、共享 nullable schema、StudentSession、Teacher 执行、快照与官方评价；新增薄接入层完成模型原生格式、可训练 adapter 续接、一个 Gmail 分类计数任务生成器以及 TRL GRPO loss 的实际多轮采样接入。

```bash
# 在独立 v2 环境与已授权机器执行；路径按私有部署配置填写。
export PYTHONPATH=src
export HF_HOME=/root/autodl-tmp/recoveragent_v2_20261109/hf_cache
export PYTORCH_ALLOC_CONF=expandable_segments:True
.venv/bin/python scripts/run_v2_round.py prepare --output base_data_new
.venv/bin/python scripts/run_v2_round.py download-model
.venv/bin/python scripts/run_v2_round.py run \
  --input deployment.json --data base_data_new --output runs/round_new
```

`deployment.json` 包含 `appworld_root`、`worker_python`、技术费用预留及绝对 `job_deadline_epoch`；运行目录只能新建。已经准备的数据应直接复用，不重复执行 `prepare`。完整依赖见 [requirements/v2-learning.txt](../requirements/v2-learning.txt)，AppWorld worker 另需现有锁定环境。私有 `.env` 不提交。

### 已验收 S0 的续跑准备（尚未真实执行）

当前代码增加 `--resume-base-from`，继续复用同一 Runner。独立配置为 [v2_round1_resume_20261109.json](../configs/v2_round1_resume_20261109.json)，新源码锁为 [v2_round1_resume_20261109_lock.json](../configs/v2_round1_resume_20261109_lock.json)；运行中的 `round_004` 及其源码没有替换。

只有源运行已经停止、正式 S0 与短/长烟测均更新参数并通过重载，且没有任何下游请求时才允许接续。逐文件验证 checkpoint SHA，核对数据、模型、冻结任务列表、原配置和来源锁；保留源目录，写入新目录及继承回执。缺烟测、未完成训练、数据/参数损坏、已开始反馈或试图重置 GPU 计时均拒绝。该配置仍为 8 小时，继续使用原绝对截止时间。

```bash
PYTHONPATH=src HF_HOME=/root/autodl-tmp/recoveragent_v2_20261109/hf_cache \
  PYTORCH_ALLOC_CONF=expandable_segments:True \
  .venv/bin/python scripts/run_v2_round.py run \
  --input deployment.json --data base_data --output runs/continued_new \
  --resume-base-from /path/to/stopped_run_with_verified_S0
```

合成续跑测试 15 passed；与现有 v2 测试合计30 passed。完整pytest为773 passed/1 skipped/3 warnings，Ruff check与format check通过。尚无真实S0续跑结果；这些测试不表示正式S0或后续学习已完成。

### 用户批准的12小时版本

后续用户明确授权当前实例总上限从8小时延长至12小时。新增 [resume12h配置](../configs/v2_round1_resume12h_20261109.json)及[独立源码锁](../configs/v2_round1_resume12h_20261109_lock.json)，锁SHA-256为 `5043d6e9a13d75467e737487b1e6ea97500f4282e8d829b84902fabc7fb23b36`。原8小时配置/锁均保持原样。实际接续命令沿用上面的 `--resume-base-from`，部署文件使用原截止时间加14400秒，输出使用独立 `runs/round_005`。

平台关机保护已调整到新的绝对截止；正在训练的协调进程及源码不热改。等正式S0验收回执生成后，在反馈采样前停止旧进程，经原异常清理路径落盘，再从独立源码目录继续。控制侧另记主动交接原因；旧进程的TimeoutError文字不作为“已耗尽8小时”的证据。接续前重新核验完整S0及两次烟测，参数不重新初始化。

新增测试确认延长预算仍按原启动时间计算；与v2及接续测试合计31 passed；完整pytest 774 passed/1 skipped/3 warnings，Ruff通过。2026-11-09 18:26（UTC+8）S0已完成130/181步，接续尚未发生；后续真实执行结果待补充。

## 数据与执行正确性

原包 manifest SHA-256 `96265f67a5828b101f5db32a804008478d7c419a01e079f6e468b8e5f164735f` 未变。新 train 场景有 107 条原始成功轨迹；新 tokenizer/template 编码后，95 条完整合格、12 条超长 deferred，共 1443 个 Assistant 样本、254396 个监督 token、16887943 个完整序列 token，最长 32666。没有截断目标或删除工具调用。

编码样本 SHA-256 `561041f89875778d50a3e7c13e822c404deba8668165c649811d83c393d4da24`，私有目录 `runs/v2_round1_20261109/base_data/`。数据中保留原 Schema、v2 转换后 Schema 及来源；任务、恢复后缀与生成任务继承 train 来源。dev/test 轨迹不进入训练。

S1 必须从 S0 的 trainable adapter 和精确参数哈希继续；只监督恢复后缀，按监督 token 与完整正常轨迹约 1:1 混合。GRPO 使用更新后的当前 Student 重新生成的 token 和实际采样 logprob，每组同一任务/初态，4 条轨迹，最多 16 组、2 组/更新；全零优势不记为参数更新。新任务必须实际贡献非零策略梯度，才计入“参与有效训练”。

生成器只支持 Gmail 分类计数汇总，目标的 read 条件/类别组合有实际变化。强模型探索真实公开 API；控制侧从实际返回构建程序验收，在独立新进程验证正例、无操作、漏完成和错答案。派生任务使用自定义验收，不冒称官方原任务成绩；原任务恢复仍使用原任务官方评分参照。

## 软件与环境验证

- 完整 pytest：758 passed、1 skipped（旧环境不含新模型栈）、3 warnings。
- 独立 v2 环境 tiny random CPU 测试：2 passed；覆盖真实梯度更新、保存重载、S0→S1 参数继承及 TRL loss 更新。这是随机小模型测试，不是预训练 9B 训练结果。
- v2 针对性测试：15 passed；覆盖场景隔离、共享 schema/恢复、原生工具格式、后缀监督、当前策略采样与预算。
- Ruff 0.8.0 check 与 format check 通过。
- 真实 AppWorld 无模型探针：派生任务公开指令与 SupervisorTask 数据库一致，187 个实际使用工具 Schema 与新编码完全一致。没有模型调用、任务成功验证或效果结论。
- 远程四个模型分片的大小及 SHA-256 均匹配固定 HF revision；下载镜像不作为模型身份依据。公开文件清单见 [v2_qwen35_weights.json](../configs/v2_qwen35_weights.json)。

验证命令：

```bash
PYTHONPATH=src .venv-harness/bin/python -m pytest -q
PYTHONPATH=src .venv-v2/bin/python -m pytest -q tests/test_v2_training_cpu.py
PYTHONPATH=src .venv-harness/bin/python -m pytest -q tests/test_v2_learning.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

## 真实运行结果

首次 `round_001` 短烟测完成 2 个 optimizer steps，loss 0.1737 / 0.0419，有限梯度且 LoRA 参数改变；在重载输出比较处明确停止，没有进入长烟测或正式训练。源码已固定于 `6489154702b12d8f123394fd1da585b91f471155`，原源码锁 `e8235088da4cac48a769f20956fdf458711bf0544d0453b044fdb0d7052fd37f` 与原配置不重签。

真实诊断确认，保存和重载后 adapter tensor SHA-256 均为 `00b32cff270891bf45128baae4f29f3cb45c137e05e7e76689d1a0deb2b05b19`。差异来自训练器探针启用 BF16 autocast，重载探针没有启用；两种计算模式最大 logit 差异 0.4375。使用相同 BF16 autocast 后，重载 logits SHA-256 与保存前完全一致：`135c9548e3de238e73d59eb92e9dd45cf3464f5b7363aa8569f23cdce98c17da`。

修正只统一两侧探针精度，并新增可核验的重载差异回执；没有放宽原有容差或参数哈希校验。独立新 revision/锁/`round_002` 从短烟测重新执行，原 GPU 绝对截止时间不延长。修正后相关测试 29 passed，独立 CPU 测试 2 passed。

`round_002` 短烟测通过：2 步、有限梯度、LoRA 哈希变化与重载验收，显存峰值 19.00 GiB。长烟测在 32666 token 完整样本反向传播时 OOM，0 个 optimizer steps，峰值 30.15 GiB；正式 S0 未启动。长烟测保留未更新的 partial adapter，不标记为合格 checkpoint。源码固定于 `5feba6e5d08776febded3d37b91e4b406fcb0dbb`，源码锁 `927fda64b25bacfef31bf7690a2dbde48cd5713d23f69d2ea787e4938cfb2046` 不重签。

针对这次实测显存阻塞，`round_003` 新配置采用 PyTorch [`save_on_cpu`](https://docs.pytorch.org/docs/stable/autograd.html#torch.autograd.graph.save_on_cpu) 暂存反向传播所需激活；保留 BF16、同一模型、完整数据及监督目标。容器内存上限 66571993088 bytes，GPU 绝对截止时间不变。重新执行短、长烟测后才允许正式训练。

`round_003` 两次真实烟测均通过：短样本 2 步，长样本 1 步；两侧 reload logits 完全相等，参数哈希均变化。长样本实际训练步 43.05 秒，PyTorch allocated 峰值 32104827904 bytes（29.90 GiB），reserved 峰值 33241956352 bytes。随后开始正式 S0，一轮预计 181 optimizer steps（1443 样本 / accumulation 8）。

`round_003` 正式 S0 完成 3 步后 OOM 停止。前三步分别 200.56/162.40/111.06 秒、梯度均有限；正式训练峰值 allocated 30050433536 bytes，未完成 adapter hash `1da7543b824e4f53cea891b50a4ae8f13f1c7764571361f1643c5ddb638e7e87`。它不是验收通过的 S0。OOM 报告有 3.31 GiB reserved/unallocated，却无法申请 1.33 GiB；因此在不改源码/模型/数据的 `round_004` 独立部署中启用 `PYTORCH_ALLOC_CONF=expandable_segments:True`，重新从 raw 开始烟测与正式一轮。失败尝试的3步不并入新运行的一轮步数。

用户曾询问缩小开发集，随后明确保持原规模；冻结dev24及其他规模未变，短暂进程暂停/恢复有独立控制回执，绝对截止时间未延长。

截至2026-11-09 16:08（UTC+8），`round_004` 两次烟测通过，正式S0已完成28/181步；最近一步55.06秒、梯度有限，无新增OOM。当前执行源码固定于 `24dd6842f6823dd6db398dab72e0d42d5e8da402`。这是进行中的进度回执，最终结果与限制将在读取完整回执后补齐。烟测和未完成 adapter 不能当作正式 S0，当前不得据此声称 S0/S1/S2 已验收或成功率提高。

私有远程根目录：`/root/autodl-tmp/recoveragent_v2_20261109/`；本轮运行目录 `runs/round_004/`，此前停止的回执保留在 `runs/round_001/`、`runs/round_002/`、`runs/round_003/`。权重、原始轨迹、状态与凭据不进入公开提交。

## S0正式验收与真实接续

2026-11-09 19:35（UTC+8），`round_004/S0_base` 完成全部181个optimizer steps、1 epoch、1443个样本及254396个监督token。训练段耗时14934.17秒（约4小时9分钟）；训练平均loss为0.095279，属于训练监控值。PyTorch峰值allocated为32320543232 bytes（30.10 GiB），reserved为32770097152 bytes。

- 训练前LoRA SHA-256：`bb43b54770b75d2b4fe4e5003aab6370a273d567bffaf1f246403d96f5a5930e`。
- 训练后及重载LoRA SHA-256：`b4b16e43f10d280722ce5544277858b94bcfbc0aa78de6817790bda8e203d235`。
- 重载probe最大logit差异为0，输出完全相等；有限梯度、参数更新和重载验收通过。
- 正式checkpoint回执SHA-256：`0be7c35229c516cedec67eb8170eb8f882c172ffa3d3e6afe93c85c1e2b6afe2`。
- 私有checkpoint：`/root/autodl-tmp/recoveragent_v2_20261109/runs/round_004/S0_base`。

两次烟测回执同时通过核验，短烟测2步/4样本、长烟测1步/完整32666 token样本。S0保存后，控制侧主动交接旧进程，在任何反馈请求前使用SIGALRM进入原异常清理路径。旧 `round_summary.json` 的8小时TimeoutError文字和空 `real_checkpoints` 字段保留原样；信号发生在S0验收回执写完、旧摘要尚未登记checkpoint的间隙，不表示8小时实际耗尽或S0失败。独立交接记录、正式回执及新流程的 `continued_base.json` 共同记录这一事实。

`round_005` 于19:35接续，执行源码为 `7a0c14b94ed92a1d51dd46eda17fa2410c25b36b`，目录为 `source_resume12h_7a0c14b`。新流程逐文件核验S0、两次烟测及原数据/配置/任务清单，继承同一参数而未重新训练。首个feedback20任务已实际发出模型请求并执行公开工具；截至本段更新，尚无该任务官方结果，Teacher恢复/生成、S1、S2及dev24成绩仍未产生。S0完整checkpoint已回传到私有 `runs/v2_round1_20261109/round004_S0_base.tar.gz`（324657225 bytes），压缩包SHA-256为 `8110d6135eadc6dda55a1d6d359d8f4a0801434592d02b624589993af9b778ae`，376个文件的回执SHA均核验一致。

## 会话时钟实际问题与接续

首个训练反馈任务已完成官方有效失败：30请求、2760生成token、29工具调用、1次协议拒绝，模型耗时273.47秒，实际墙钟382.08秒；文件控制侧时间差382.30秒。它在1200秒预算内完成，保留结果、不重复尝试。原环境/快照分项耗时为0及恒定1200秒remaining元数据不可信，原件保留并明确标记。

根因为AppWorld冻结普通`time.monotonic`；新`clock` revision在v2入口复用已有`runner.now`（CLOCK_MONOTONIC）并传入StudentSession。第二项只初始化、0请求时已停止。接续逐文件绑定源记录、保留已用token和绝对12小时截止，继续未发起请求的任务。首项快照恢复与后缀导出使用原锁定7a0c14b源码，不改写快照预期值；新任务使用新锁。独立输出名称避免覆盖旧初始化产物。

针对性合成测试40 passed，包括冻结时间下实际deadline递减、结果/成本继承、篡改/已开始第二任务拒绝及旧源码调度。尚未将这些软件测试写成Teacher恢复或学习闭环完成。裸pytest启动缺少仓库根路径导致6项导入收集错误，使用项目根路径的完整命令重新验证。

完整验证：`PYTHONPATH=.:src .venv/bin/python -m pytest -q` 为783 passed/1 skipped（18.56秒）；`.venv/bin/ruff check .` 与 `.venv/bin/ruff format --check .` 均通过。

## 首批9项真实反馈与评价UNKNOWN

`round_006` 尝试冻结清单前9项（含保留的首项）：1成功、7有效自然失败、1UNKNOWN，共19527生成token。第7项因上下文预算耗尽正常失败；第9项在完整官方评分中出现两个未知来源KeyError，按原严格分类保持UNKNOWN，不改写成失败奖励，不重跑，不进入Teacher恢复。原流程已停止，全部记录已回传，私有压缩包8601882 bytes，SHA-256 `7be1d3b0f2f3b718d95b52893cb5027e819e68994fd9b8a7d478086b284434f4`。

独立feedback配置/锁补齐已完成反馈前缀接续，核对原始结果、模型、任务顺序、源码锁和全部请求token成本，继续第10–20项。单项完整但未知评价可以保留后继续；worker异常、环境错误、未完整评分及未知生成用量仍停止。Teacher恢复只选择valid自然失败，旧快照绑定原版本执行器。没有修改官方评价器、Schema、提示、训练数据或checkpoint。

针对性合成测试50 passed，验证UNKNOWN保持null、已完成任务不重跑、成本不清零及不安全异常仍拒绝。这些软件测试不代表真实Teacher恢复或完整学习闭环完成。

本次完整验证：`PYTHONPATH=.:src .venv/bin/python -m pytest -q` 为793 passed/1 skipped（18.62秒）；Ruff check及format check通过。

## 用户将S0反馈缩为15项

用户要求将当前反馈缩到固定前15项。实际15项已经完成：3成功、11有效自然失败、1UNKNOWN，共40183生成token；不据此声称dev成功率。第16项在指令到达时已启动，立即中断，8个完整回复产生439个已知token，第9个请求未完成回复，保留4096token技术预留。已知训练侧总账为40622token，剩余额度另扣未知预留；中断项不继续、不进入Teacher恢复、不算作15项中的完成任务。

独立feedback15配置保存原固定清单的前15项，不重新训练或推理S0。接续验证旧来源链和原始结果，旧快照使用各自锁定Teacher源码；新配置继续恢复、生成、S1/S2及dev24。暂停/终止记录与源摘要分开保留，原SIGALRM错误文字不改写成实际12小时耗尽。

反馈15接续：针对性53 passed；完整pytest796 passed/1 skipped（18.42秒），Ruff check/format check通过。测试覆盖来源链、不重复成本、显式取消第16项的已知token及未知预留、其不进入训练数据。


## 恢复完成与本轮缩小接续

11次真实Teacher接管全部完成：6次原任务官方成功，4条恢复后缀在32k窗口内合格，2条超长暂缓，5次未成功。首条成功恢复6请求/12工具调用，111.28秒，2902输出token；CPU重新编码确认该条最长33961token，因此继续保留为deferred，没有裁剪或启动扩大窗口的GPU实验。此为真实恢复结果，不是S1效果验证。

用户明确保留32k，并把后续GRPO改为最多4组×4轨迹（3原始+1生成）；同条件阶段比较改为3个不同场景的固定dev任务，分别评价S0/S1/S2，最多9次episode。原始场景划分和dev24母清单不修改，子集在执行前独立保存。若没有合格生成任务，相应组空缺并披露；若所有GRPO组无奖励差异，不生成伪S2，只报告可用checkpoint比较。

接续配置为 `configs/v2_round1_finish4_20261109.json`，独立同名源码锁。复用原Runner，通过 `--resume-s1-from` 只读核验已完成S1及其S0父参数、数据、全部文件SHA、源配置/锁、原反馈与成本总账。不会重复Teacher调用或训练，不重置12小时截止。完整启动方式：

```bash
PYTHONPATH=src HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
OMP_NUM_THREADS=8 PYTORCH_ALLOC_CONF=expandable_segments:True \
.venv/bin/python scripts/run_v2_round.py run \
  --input ../deployment009.json --data ../base_data --output ../runs/round_009 \
  --resume-base-from ../runs/round_004 --resume-s1-from ../runs/round_008
```

实际探索中一条提案因公开只读 `supervisor__show_active_task` 未列入生成器允许列表而被拒。只补这一接口，允许对原始提案重新执行独立环境验收，原拒绝和轨迹不改写；无新Teacher请求。尚未将重验代码通过写成真实生成任务合格。

本次软件验证：针对性测试69 passed；完整 `PYTHONPATH=.:src .venv/bin/python -m pytest -q` 812 passed/1 skipped；Ruff check和format check通过。覆盖S1谱系与成本继承、预算/数据/来源篡改拒绝、3场景dev选择、3+1组、公开查询的已有提案重验与写操作继续拒绝。真实S1、GRPO和开发比较将在实际回执后更新。


## 最新用户决定：恢复原GRPO/dev规模，总计24小时

用户明确将GRPO恢复为最多16组×4轨迹，开发比较恢复固定dev24、三个阶段共最多72次episode；先前finish4已部署但自动启动保护在发起任何GRPO/dev请求前取消，未运行小规模比较。原15项反馈、11次Teacher恢复及32k限制不变。

用户同时批准当前实例总计24小时，已用时间计入，绝对截止2026-11-10 14:12:31 UTC+8。20万训练侧生成token及每episode预算不增加；即使最多16组，达到累计资源上限仍停止并记录实际数。新入口配置为 `configs/v2_round1_finish16_24h_20261109.json`，独立源码锁；部署与输出改为 `deployment010.json`、`runs/round_010`，仍使用 `--resume-base-from ../runs/round_004 --resume-s1-from ../runs/round_008`。旧finish4启动命令仅保留历史，不执行。

新接续在核对旧反馈时使用源运行的原12小时配置，在检查新运行时严格核对原起点+24小时，不能借接续清零时间或生成成本。针对性55 passed；完整813 passed/1 skipped及Ruff检查通过。以上为软件与配置验收；实际训练与开发结果继续单列。
