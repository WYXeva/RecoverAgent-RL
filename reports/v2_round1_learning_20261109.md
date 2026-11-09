# v2 第一轮学习执行记录（2026-11-09）

本轮以 `ddc6e8e4338eb168ec4784e906b4de66ab7dd297` 为代码基线，按 `project.md` 的交付 1 实现并启动真实学习链路。本文区分软件验收、真实环境探针与模型运行；状态以最终回执为准。

## 固定设置与入口

- Student：`Qwen/Qwen3.5-9B`，revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`；原生 non-thinking 模板 SHA-256 `a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715`。没有加载旧 4B adapter，也没有切换 8B。
- Full732 场景隔离自定义划分：170/37/37 个 train/dev/test 场景，510/111/111 个任务，seed `20261109`；保留原官方 split。清单 manifest SHA-256 `ff24d1c05f16e82795e0baa4dd855c03d8482bb32d6e2e07ce386a9af59bfe6b`。固定 feedback20 和 dev24，不按结果替换。
- 配置：[v2_round1_cpu_offload_20261109.json](../configs/v2_round1_cpu_offload_20261109.json)；源码锁：[v2_round1_cpu_offload_20261109_lock.json](../configs/v2_round1_cpu_offload_20261109_lock.json)，SHA-256 `9f12bf4e6c446fa93847f678456b93a753cdb7921080c04bfca985b29393fcfd`。历史锁、数据、4B 权重和成绩保持原样。
- 现有机器实际为 RTX 4080 SUPER vGPU、32 GB，BF16 可用；与计划参考的 80/96 GB 不同，必须以短/最长完整样本实测验收。只用已开机实例，不租机或续费。
- GPU 作业上限 8 小时，训练侧 Student 生成总量 20 万 token；每 episode 80 请求、16384 生成 token、4096 单回复、32768 上下文、1200 秒；通用纠错 15 次。开发评估关闭训练快照。
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
