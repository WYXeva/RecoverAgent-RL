# RecoverAgent-RL — CodeAct

当前训练主线为 `reboot/codeact`：固定原生 **appworld(code)**、全新 Teacher 示范、多轮 SFT、从官方 Qwen3.5-9B 建立新 S0。计划见 [project.md](project.md)，事实见 [STATUS](docs/STATUS.md)，本次结果见 [重启报告](reports/codeact_reboot_20261119.md)。

不使用旧 Teacher 轨迹、actor、adapter 或优化器。旧实验与源码锁保留在历史分支及报告中，不是新主线的运行入口。

## 最新完成结果（2026-11-22）

新CodeAct S0之后的五轮GRPO＋CoEvolve已完成：387组、1548条轨迹、100次有效更新。
20/40/60/80/100步dev24为10/16/18/17/17题成功；最佳60步75%，最终100步70.83%，均无评分未知。
314道生成题入池，188道实际采样，108道贡献更新。控制器与推理服务已退出，不自动启动后续实验。
完整限制、成本、逐题比较和备份见[五轮报告](reports/codeact_evolve_five_rounds_20261122.md)。

最佳模型为远端 `recoveragent_codeact_evolve_20261121_13/experiment/updates/attempt_060/actor/`，
最终模型为 `recoveragent_codeact_evolve_20261121_14/experiment/updates/attempt_100/actor/`，均位于 `/root/gpufree-data/`。
本地核验镜像位于 `/mnt/d/RecoverAgent-RL-private/codeact_evolve_checkpoints_20261121/` 下的相同相对目录，
对应 `optimizer.pt` 在actor的上级目录。加载固定官方9B基座加对应LoRA；不是合并后的全量权重。
下面的历史运行入口供复现和查阅，本批已完成，不应重新启动。

## 用户指定保留的模型

`codeact_evolve_u040_dev16of24_20261120`：新CodeAct S0之后累计40次GRPO有效更新，
固定dev24为 **16成功/8失败/0未知（66.7%）**。实际lr1e-5、beta0；对应optimizer及重载回执一并保留。

- 远端独立副本：`/root/gpufree-data/recoveragent_codeact_evolve_20261120_09/retained_models/codeact_evolve_u040_dev16of24_20261120/`。
- 本地D盘备份已完整核验：`/mnt/d/RecoverAgent-RL-private/retained_models/codeact_evolve_u040_dev16of24_20261120/`；44份文件全部SHA通过，回执为该目录的 `backup_verified.json`。
- 模型加载使用目录内 `actor/` 的LoRA及tokenizer，加官方 `Qwen/Qwen3.5-9B` revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`；non-thinking、64K、CodeAct固定工具及保存的重复错误规则。它不是合并后的全量9B权重。
- `optimizer.pt` 对应这一版权重；`retention_manifest.json` 保存逐文件SHA与来源，`DO_NOT_DELETE` 标记禁止清理。后续训练另存新checkpoint，不覆盖这份模型。

原始 `experiment/updates/attempt_040/` 和dev24逐题原件保留。控制器状态副本仅作来源记录，
不是脱离原任务池/轨迹即可完整回退代码会话的独立续跑包。此处保留不启动新训练或评价。

## 本地采集与续跑

以下是本机实际目录；使用现有 Python 环境、AppWorld 数据和已授权 Teacher 凭据，不启动 GPU：

```bash
cd /home/eva/project/RecoverAgent-RL-codeact
export PYTHONPATH="$PWD/src"
export RECOVERAGENT_TEACHER_TOKENIZER_DIR=/home/eva/project/RecoverAgent-RL/runs/s2_context_validation_20261117/teacher_tokenizer
CODEACT_PY=/home/eva/project/RecoverAgent-RL/runs/teacher_local_20261114_01/worker_env/bin/python
CODEACT_ROOT=/home/eva/project/RecoverAgent-RL/data/appworld
CODEACT_RUN="$PWD/runs/codeact_train510_20261119_01"

python3 scripts/run_codeact_collection.py \
  --python "$CODEACT_PY" --root "$CODEACT_ROOT" \
  --credentials /home/eva/project/RecoverAgent-RL/.env --output "$CODEACT_RUN" \
  --runtime-name runtime_target256_20261119 \
  --target-successes 256 --skip-scoring-errors
```

启动器冻结代码和配置，独立后台进程运行。相同命令用于续跑；已有进程不重复启动，成功题不重复消费，普通失败最多从干净初态重试一次。**本批累计256条合格官方成功示范即停**，最多遍历原train510；耗尽队列也如实结束，不扩任务。到达目标后自动导出 `target_256_data/`，超64K示范仍另存deferred，不以截断凑可训练数量；正式训练不自动启动。

调度规则独立保存在 `collection_controls.json`，未修改原模型配置或轨迹身份。官方评分异常保留为未知、不导出，并在 `scoring_skips/` 记录原结果/轨迹SHA后跳过整题；不进行该题第二次尝试。Teacher、执行环境和凭据故障仍明确停止，不把所有异常一概跳过。`progress.json`、`collector.log`、`episodes/<task>/attempt_<n>/` 保存进度与原件。资源故障处理后使用同一命令续跑。新源码快照为 `runtime_target256_20261119/`，旧 `runtime/` 与旧manifest保持原样。

队列包含 train510；前20题覆盖20个不同场景，属于同一正式队列。首批完成后执行真实请求/官方评分/多轮编码检查，普通任务失败不阻塞后续采集；检查通过自动继续。`first_batch_audit.json` 与 `first20_data/` 是首批验收产物，不应在它们尚未生成时宣称首批完成。

查看最新进度（不调用模型）：

```bash
"$CODEACT_PY" -c 'from recoveragent.codeact.collect import summary; import json; s=summary("runs/codeact_train510_20261119_01"); s.pop("attempts"); print(json.dumps(s,indent=2))'
```

## 导出本轮新数据

本批已于11月19日16:20收齐256条合格成功示范并停止。最终导出目录为 `runs/codeact_train510_20261119_01/target_256_data/`，含252个完整可训练样本、4条超64K暂缓原轨迹引用，无截断；无需再次启动采集。

在采集期间可向**新的输出目录**导出当前成功快照；采集结束后再冻结最终清单：

```bash
"$CODEACT_PY" -m recoveragent.codeact.cli export   --collection "$CODEACT_RUN" --output runs/codeact_dataset_final
```

目录内 `train.jsonl` 为完整兼容多轮样本，`sources.json` 为来源/回合，`deferred.json` 为超长原轨迹引用，`manifest.json` 为 SHA、长度、目标 token 与归一化信息。历史轨迹/隐藏 reasoning 不进入此入口，超长不截断。

官方 tokenizer 首次准备（不下载模型权重）：

```bash
"$CODEACT_PY" -c 'from recoveragent.io import read_json; from recoveragent.v2_model import load_tokenizer; load_tokenizer(read_json("configs/codeact_reboot_20261119.json")["model"],local_files_only=False)'
```

## 新 S0 训练与评价

新S0已完成252样本/1 epoch/32次更新，0 OOM，重载通过。远端模型：`/root/gpufree-data/recoveragent_codeact_s0_20261119_02/training/S0_base/adapter/`；本地备份：`runs/codeact_s0_20261119_02/final_delivery/training/S0_base/adapter/`。模型由固定官方基座加新LoRA加载，不是完整合并权重。以下训练命令供新目录复现，当前无需重复训练；最新授权评价范围为固定dev24，见下文。

GPU 环境复用 [已验证训练依赖](requirements/v2-learning.txt)，AppWorld 使用 [固定环境版本](requirements/appworld-evaluation-20261113.lock.txt)。评价进程同时需要这两类能力；不要让升级破坏历史环境。将本分支源码、全新数据集及私有 AppWorld 数据放到 GPU 实例，`PYTHONPATH=src`。只下载官方基座到 HF cache，不拷贝旧 adapter：

当前训练配置 `configs/codeact_s0_20261119.json` 按用户要求启用完整样本OOM跳过：清空该累积批梯度后重算剩余样本；不截断，不吞非有限梯度或重载错误。最长烟测OOM时按长度递减尝试，首次通过即停止烟测；烟测OOM样本不再进入本批正式训练。`oom_skips/`、`sample_selection.json` 和实际更新回执分别记录遗漏样本及更新数。原采集配置、数据和失败原件不改。

```bash
python -c 'from huggingface_hub import snapshot_download; snapshot_download("Qwen/Qwen3.5-9B",revision="c202236235762e1c871ad0ccb60c8ee5ba337b9a")'

CODEACT_DATA=runs/codeact_train510_20261119_01/target_256_data

# 少量完整短样本＋最长样本，各自从官方基座重新创建 LoRA。
PYTHONPATH=src python -m recoveragent.codeact.cli train   --config configs/codeact_s0_20261119.json --dataset "$CODEACT_DATA" --output runs/codeact_training_smoke_01 --smoke

# 正式命令包含独立短/长检查，再重新加载官方基座做1 epoch。
PYTHONPATH=src python -m recoveragent.codeact.cli train   --config configs/codeact_s0_20261119.json --dataset "$CODEACT_DATA" --output runs/codeact_s0_01

# 全部固定 dev111，每题一次，CodeAct共享loop，官方评分；可续跑已完成项。
PYTHONPATH=src python -m recoveragent.codeact.cli evaluate   --adapter runs/codeact_s0_01/S0_base/adapter   --root /path/to/private/appworld --split dev --output runs/codeact_s0_dev111_01
```

正式 adapter 在 `S0_base/adapter`，含身份/文件哈希和重载回执；smoke 权重不能进入正式评价入口。评价支持 Transformers 与 vLLM 0.19.0，均使用固定CodeAct共享loop、Qwen原生模板、64K/non-thinking与官方评分。`--task-set dev24` 读取既定24题及顺序；默认 `all` 仍为对应split全部任务。已完成项不重复执行，绑定不符拒绝续跑。

当前已备份S0的本地评价命令（需安排GPU和AppWorld环境，不自动运行）：

```bash
PYTHONPATH=src python -m recoveragent.codeact.cli evaluate \
  --config configs/codeact_s0_20261119.json \
  --adapter runs/codeact_s0_20261119_02/final_delivery/training/S0_base/adapter \
  --root /home/eva/project/RecoverAgent-RL/data/appworld \
  --split dev --task-set dev24 --output runs/codeact_s0_dev24_local_01
```

11月19日现有L40S上的固定dev24采用独立配置 `configs/codeact_s0_dev24_20261119.json`：每题一次、最多80请求/32768累计生成token/1200秒，单回复4096，temperature=0。**已于19:24完成，8/24成功、16失败、0未知，vLLM已退出。** 私有监督脚本 `runs/codeact_s0_dev24_20261119_02/controller.py` 负责启动vLLM、核验已注册adapter及实际logits变化、运行24题、退出服务并归档；逐题结果与失败分析见[dev24报告](reports/codeact_s0_dev24_20261119.md)。不调用Teacher，不训练，不跑dev111/test。

以下是监督进程实际调用的评价命令，服务及 `server_receipt.json` 必须先就绪；不要与正在运行的同批进程重复启动：

```bash
CODEACT_EVAL=/root/gpufree-data/recoveragent_codeact_s0_dev24_20261119_02
CODEACT_ENV=/root/gpufree-data/recoveragent_s1_retrain_20261114_01
export HF_HOME="$CODEACT_ENV/hf_cache" HF_HUB_OFFLINE=1
PYTHONPATH="$CODEACT_EVAL/source/src" "$CODEACT_ENV/worker_env/bin/python" \
  -m recoveragent.codeact.cli evaluate \
  --config "$CODEACT_EVAL/source/configs/codeact_s0_dev24_20261119.json" \
  --adapter /root/gpufree-data/recoveragent_codeact_s0_20261119_02/training/S0_base/adapter \
  --root "$CODEACT_ENV/appworld_root" --split dev --task-set dev24 \
  --vllm-url http://127.0.0.1:18019 \
  --server-receipt "$CODEACT_EVAL/server_receipt.json" \
  --output "$CODEACT_EVAL/evaluation"
```

回执绑定官方基座revision、新S0的checkpoint SHA、CodeAct协议、64K/BF16与实际服务模型名。每次请求保存真实输入/输出token及vLLM原始响应，业务轨迹与官方评分分开记录；控制侧统计成功/失败/未知，不以训练loss冒充任务成绩。

## 相关软件验证

```bash
/home/eva/project/RecoverAgent-RL/.venv-v2/bin/python -m pytest -q   tests/test_codeact_collection_controls.py tests/test_codeact_reboot.py tests/test_teacher_collection.py   tests/test_teacher_data.py tests/test_evaluation_classification.py
/home/eva/project/RecoverAgent-RL/.venv/bin/ruff check src/recoveragent/codeact src/recoveragent/teacher.py scripts/run_codeact_collection.py tests/test_codeact_reboot.py tests/test_codeact_collection_controls.py
/home/eva/project/RecoverAgent-RL/.venv/bin/ruff format --check src/recoveragent/codeact src/recoveragent/teacher.py scripts/run_codeact_collection.py tests/test_codeact_reboot.py tests/test_codeact_collection_controls.py
```

新测试的模板检查使用上述已缓存官方 tokenizer。旧 S2 源码锁应继续拒绝当前修改，不重签旧锁；历史实验使用其绑定的旧提交复现。新主线不经过这些阶段锁。

私有凭据、原始任务/轨迹、环境状态与权重位于 `runs/`、`data/`，不提交 GitHub。后续路线：新 S0 → 同实例 Teacher 恢复 S1 → 从相同 S1 比较 Static 与 CoEvolve Full；本轮不实施后三项。

### CodeAct S0 → 一轮 GRPO + CoEvolve（2026-11-19）

用户已批准直接从全新 CodeAct S0 开始，暂跳过 Recovery-SFT。独立配置
`configs/codeact_grpo_coevolve_20261119.json`：最多40组×4次、20次有效更新、24小时；
当前actor累积两组有效成败混合组再更新，lr1e-5/beta0。每8个实际采样组读取四条反馈，
后台生成最多4个context×2候选；新池从空开始，只接受全新CodeAct Solver和共享Judge验收。
原题继续官方评分，dev/test不参与。全同奖励组保留反馈，评分无效组不更新。

现有实例的冻结运行目录为 `/root/gpufree-data/recoveragent_codeact_evolve_20261119_01/`。
其 `deployment.json` 固定路径及开始后24小时的绝对截止时间；启动和同目录续跑均使用：

```bash
python3 /root/gpufree-data/recoveragent_codeact_evolve_20261119_01/launch.py
```

`launch.py` 是私有部署包装，设置已配置的HF缓存、Teacher tokenizer及凭据路径；
公开执行入口是下面的命令，续跑仍用相同配置、deployment、输出目录及原截止时间：

```bash
python -m recoveragent.codeact.experiment run \
  --config configs/codeact_grpo_coevolve_20261119.json \
  --deployment /path/to/private/deployment.json \
  --output /path/to/private/experiment
```

deployment包含 `source_root`、`deployment_file`、`s0`、`worker_python`、`training_python`、
`vllm_python`、`base_path`、`kernel_overlay`、`port`、`appworld_root`、`credentials`、
`teacher_ledger` 和 `deadline`。新源码锁为 `configs/codeact_online_source_20261119.lock.json`，
旧锁保持原样。进度看 `experiment/summary.json`，逐组看 `groups/group_*/result.json`，
更新看 `updates/attempt_*/result.json`，Teacher开销在 `teacher_ledger/`。
同一目录禁止两个controller同时运行。达到采样、更新或时间上限会保存并关闭自身vLLM，
不自动关平台机器、不租机续费，也不自动启动dev/test。


### 历史五轮接续与评分分类v3

11月19日当时授权目标为 **40组训练→dev24，重复5轮**；当时实例使用原24小时绝对截止时间，
到期保存，等待后续授权，不自动延长。新配置和新锁分别为
`configs/codeact_grpo_coevolve_five_rounds_20261119.json`、`configs/codeact_online_eval_v3_20261119.lock.json`。
02目录继承01的actor/optimizer、组计数、未消费组、池和队列；01原件保留。

```bash
python -m recoveragent.codeact.experiment run \
  --config configs/codeact_grpo_coevolve_five_rounds_20261119.json \
  --deployment /root/gpufree-data/recoveragent_codeact_evolve_20261119_02/deployment.json \
  --output /root/gpufree-data/recoveragent_codeact_evolve_20261119_02/experiment
```

实际运行使用02冻结源码的 `PYTHONPATH=.../source/src`、worker Python及原HF缓存/Teacher tokenizer环境，
精确启动参数保存在私有 `command.json`。`deployment.source_lock` 指向新锁，不重签历史锁。
同一授权期内以上命令续跑；截止之后须先取得新的作业时限并保留原绑定，不直接修改旧截止时间放行。
轮末评价在 `experiment/evaluations/round_01..05/`；摘要的 `round_evaluations` 保存逐轮结果。
空答案分类更正证据在 `reward_audit/`，未重采模型或覆写旧奖励。

### 训练显存调整后的历史04入口（11月20日）

当前04目录从02的23组/2次有效更新继续，使用
`codeact_grpo_coevolve_memory_20261120.json`和`codeact_online_memory_20261120.lock.json`。
`launch()`在训练子进程启动前设置可扩展CUDA分配器，直接`update`命令也在CUDA初始化前设置；
Teacher/Student采样不改。`allocator.json`、`memory.jsonl`、更新回执和OOM traceback记录实际设置与显存。

```bash
cd /root/gpufree-data/recoveragent_codeact_evolve_20261120_04/source
export PYTHONPATH="$PWD/src"
export HF_HOME=/root/gpufree-data/recoveragent_s1_retrain_20261114_01/hf_cache
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export RECOVERAGENT_TEACHER_TOKENIZER_DIR=/root/gpufree-data/recoveragent_codeact_evolve_20261119_01/teacher_tokenizer
/root/gpufree-data/recoveragent_s1_retrain_20261114_01/worker_env/bin/python \
  -m recoveragent.codeact.experiment run \
  --config configs/codeact_grpo_coevolve_memory_20261120.json \
  --deployment /root/gpufree-data/recoveragent_codeact_evolve_20261120_04/deployment.json \
  --output /root/gpufree-data/recoveragent_codeact_evolve_20261120_04/experiment
```

此命令用于同一授权期内续跑，已有controller运行时不要再启动第二个。
原截止11月20日19:52:38不变；03目录独立OOM复测权重不是主训练actor。
复测命令、真实数据和限制见[报告](reports/codeact_grpo_oom_retest_20261120.md)。

### 历史05入口：每20次有效更新后评测

当前使用 `configs/codeact_grpo_coevolve_updates20_20261120.json` 和
`configs/codeact_online_updates20_20261120.lock.json`。
已有80采样组/18次更新及两份9/24旧评价保留；新增评价目录为
`experiment/evaluations/updates_000020`、`updates_000040`等，不混用旧 `round_01`/`round_02`。
40采样组仍作为生成题限频窗口，不再定义训练轮次。

沿用上文的worker Python、HF缓存和Teacher tokenizer环境，改用05冻结源码：

```bash
cd /root/gpufree-data/recoveragent_codeact_evolve_20261120_05/source
export PYTHONPATH="$PWD/src"
/root/gpufree-data/recoveragent_s1_retrain_20261114_01/worker_env/bin/python \
  -m recoveragent.codeact.experiment run \
  --config configs/codeact_grpo_coevolve_updates20_20261120.json \
  --deployment /root/gpufree-data/recoveragent_codeact_evolve_20261120_05/deployment.json \
  --output /root/gpufree-data/recoveragent_codeact_evolve_20261120_05/experiment
```

上述为05历史目录的续跑命令，正在运行时不重复启动。11月21日用户取消作业墙钟截止，
最新13继续至100次有效更新及里程碑dev24完成；单题及Teacher有限用量预算保留。

### 独立环境并发采样

新配置 `configs/codeact_grpo_coevolve_parallel_20261120.json` 的
`online.sampling_concurrency=2`（可设1–4），同步配置共享vLLM并发。
Student在相同代码/错误/状态连续出现时第3次提醒、第5次结束，Teacher不受影响。
新规则使用独立目录，不能直接覆盖旧run的配置或混用旧未消费回执。
用户随后授权四路试跑，独立配置 `configs/codeact_grpo_coevolve_parallel4_20261120.json`
设置并发4，配套 `configs/codeact_online_parallel4_20261120.lock.json`；四条依然共享一份9B权重。
启动/续跑命令及真实验证见[并发报告](reports/codeact_parallel_progress_20261120.md)。

### 当前仓库清理后的源码锁

清理后的双路部署使用 `configs/codeact_online_compact_20261120.lock.json`，
四路部署使用上述独立 parallel4 锁。06历史目录保留自己的 `74f9a28` 冻结源码和 parallel 锁；
07从06的完整组边界接续，沿用原截止和预算，不覆盖原运行。

后续08使用 `codeact_grpo_coevolve_adaptive4_20261120.json` 与
`codeact_online_adaptive4_20261120.lock.json`：每题4路，采样容量故障只对受影响尝试降2路补跑一次，
下一题仍4路。原始尝试和补跑分别保存，正常业务失败不触发降级；命令见上述并发报告。

最新14使用 `codeact_grpo_coevolve_completion_budget_20261121.json` 和 `codeact_online_completion_budget_20261121.lock.json`，
在反馈筛选修复后继续已有actor/optimizer及反馈，不重置更新数。
原始题池为train510扣除252个正式SFT任务后的258题，原始/生成槽保持交替；
扩充的原始题不会挤占生成题槽，同环境不同目标的生成题保留独立身份。
采样组及作业墙钟不设上限，仍以100次更新为5轮目标；Teacher请求次数预留8192，
原token/费用上限、既有账本及单题限制不变。100更新及最后dev24已完成，最新结果见本文顶部。以下为原续跑命令，本批不重复启动：

```bash
cd /root/gpufree-data/recoveragent_codeact_evolve_20261121_14/source
PYTHONPATH=src /root/gpufree-data/recoveragent_s1_retrain_20261114_01/worker_env/bin/python \
  -m recoveragent.codeact.experiment run \
  --config configs/codeact_grpo_coevolve_completion_budget_20261121.json \
  --deployment /root/gpufree-data/recoveragent_codeact_evolve_20261121_14/deployment.json \
  --output /root/gpufree-data/recoveragent_codeact_evolve_20261121_14/experiment
```

旧高层运行器和对应测试已退出当前分支，可在 `74f9a28` 找回；仍复用的训练/执行组件保留。
安装本项目后，`recoveragent` 命令对应 CodeAct 入口。清理范围及验证见[清理报告](reports/codeact_cleanup_20261120.md)。
