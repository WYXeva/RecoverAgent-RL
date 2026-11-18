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

python3 scripts/run_codeact_collection.py   --python "$CODEACT_PY" --root "$CODEACT_ROOT"   --credentials /home/eva/project/RecoverAgent-RL/.env   --output "$CODEACT_RUN"
```

启动器冻结代码和配置，独立后台进程运行。相同命令用于续跑；已有进程不重复启动，成功题不重复消费，失败最多从干净初态重试一次。`progress.json`、`collector.log`、`episodes/<task>/attempt_<n>/` 保存进度、日志和每次原件。资源或环境故障明确停止，不把异常算普通失败。凭据和额度故障应修复后用同一命令续跑。

队列包含 train510；前20题覆盖20个不同场景，属于同一正式队列。首批完成后执行真实请求/官方评分/多轮编码检查，普通任务失败不阻塞后续采集；检查通过自动继续。`first_batch_audit.json` 与 `first20_data/` 是首批验收产物，不应在它们尚未生成时宣称首批完成。

查看最新进度（不调用模型）：

```bash
"$CODEACT_PY" -c 'from recoveragent.codeact.collect import summary; import json; s=summary("runs/codeact_train510_20261119_01"); s.pop("attempts"); print(json.dumps(s,indent=2))'
```

## 导出本轮新数据

在采集期间可向**新的输出目录**导出当前成功快照；采集结束后再冻结最终清单：

```bash
"$CODEACT_PY" -m recoveragent.codeact.cli export   --collection "$CODEACT_RUN" --output runs/codeact_dataset_final
```

目录内 `train.jsonl` 为完整兼容多轮样本，`sources.json` 为来源/回合，`deferred.json` 为超长原轨迹引用，`manifest.json` 为 SHA、长度、目标 token 与归一化信息。历史轨迹/隐藏 reasoning 不进入此入口，超长不截断。

官方 tokenizer 首次准备（不下载模型权重）：

```bash
"$CODEACT_PY" -c 'from recoveragent.io import read_json; from recoveragent.v2_model import load_tokenizer; load_tokenizer(read_json("configs/codeact_reboot_20261119.json")["model"],local_files_only=False)'
```

## 新 S0 训练与评价（待安排 GPU）

GPU 环境复用 [已验证训练依赖](requirements/v2-learning.txt)，AppWorld 使用 [固定环境版本](requirements/appworld-evaluation-20261113.lock.txt)。评价进程同时需要这两类能力；不要让升级破坏历史环境。将本分支源码、全新数据集及私有 AppWorld 数据放到 GPU 实例，`PYTHONPATH=src`。只下载官方基座到 HF cache，不拷贝旧 adapter：

```bash
python -c 'from huggingface_hub import snapshot_download; snapshot_download("Qwen/Qwen3.5-9B",revision="c202236235762e1c871ad0ccb60c8ee5ba337b9a")'

# 少量完整短样本＋最长样本，各自从官方基座重新创建 LoRA。
PYTHONPATH=src python -m recoveragent.codeact.cli train   --dataset runs/codeact_dataset_final --output runs/codeact_training_smoke_01 --smoke

# 正式命令包含独立短/长检查，再重新加载官方基座做1 epoch。
PYTHONPATH=src python -m recoveragent.codeact.cli train   --dataset runs/codeact_dataset_final --output runs/codeact_s0_01

# 全部固定 dev111，每题一次，CodeAct共享loop，官方评分；可续跑已完成项。
PYTHONPATH=src python -m recoveragent.codeact.cli evaluate   --adapter runs/codeact_s0_01/S0_base/adapter   --root /path/to/private/appworld --split dev --output runs/codeact_s0_dev111_01
```

正式 adapter 在 `S0_base/adapter`，含身份/文件哈希和重载回执；smoke 权重不能进入正式评价入口。现有评价后端为 Transformers，64K/non-thinking；本轮尚未进行真实9B训练/Student评价，不把CPU测试当GPU成功。后续可复用现有 vLLM 部件，但本轮不额外部署服务。

## 相关软件验证

```bash
/home/eva/project/RecoverAgent-RL/.venv-v2/bin/python -m pytest -q   tests/test_codeact_reboot.py tests/test_teacher_collection.py   tests/test_teacher_data.py tests/test_evaluation_classification.py
/home/eva/project/RecoverAgent-RL/.venv/bin/ruff check src/recoveragent/codeact src/recoveragent/teacher.py scripts/run_codeact_collection.py tests/test_codeact_reboot.py
/home/eva/project/RecoverAgent-RL/.venv/bin/ruff format --check src/recoveragent/codeact src/recoveragent/teacher.py scripts/run_codeact_collection.py tests/test_codeact_reboot.py
```

新测试的模板检查使用上述已缓存官方 tokenizer。旧 S2 源码锁应继续拒绝当前修改，不重签旧锁；历史实验使用其绑定的旧提交复现。新主线不经过这些阶段锁。

私有凭据、原始任务/轨迹、环境状态与权重位于 `runs/`、`data/`，不提交 GitHub。后续路线：新 S0 → 同实例 Teacher 恢复 S1 → 从相同 S1 比较 Static 与 CoEvolve Full；本轮不实施后三项。
