# RecoverAgent-RL — CodeAct

当前训练主线为 `reboot/codeact`：固定原生 **appworld(code)**、全新 Teacher 示范、多轮 SFT、从官方 Qwen3.5-9B 建立新 S0。计划见 [project.md](project.md)，事实见 [STATUS](docs/STATUS.md)，本次结果见 [重启报告](reports/codeact_reboot_20261119.md)。

不使用旧 Teacher 轨迹、actor、adapter 或优化器。旧实验与源码锁保留在历史分支及报告中，不是新主线的运行入口。

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
