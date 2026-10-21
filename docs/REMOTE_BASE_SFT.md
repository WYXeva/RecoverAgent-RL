# Base-SFT v1 私有迁移与远程验收

本轮只准备数据与配置，**没有连接远程服务器，没有加载 Qwen3-4B 权重，没有进行 Student 参数更新**。下面带“远程”的命令尚未执行。正式设备是计划中的 NVIDIA RTX PRO 6000 96GB；本机 RTX 5050 8GB 的预检结果不能代替它。

## 1. 数据与可信来源

最终路径、数据集 manifest SHA-256、压缩包 SHA-256 和大小见已提交的 `reports/base_sft_dataset_v1_release.json`。以 GitHub 提交中的摘要作为信任起点，不只相信随压缩包一起收到的摘要。

`data/base_sft_v1/` 与归档只保存在私有工作目录。结构为：

- `model_input/{train,validation}.jsonl`：完整 Teacher 历史及每回合实际 Schema；不包含控制侧来源/状态/评价引用。
- `tokenized/{train,validation}.jsonl`：真实固定 Qwen tokenizer 生成的逐 Assistant 回合样本、labels、mask、example_weight。
- `model_input/{deferred,reserve,duplicates}.jsonl`：暂缓或未入选完整轨迹，绝不交给 Trainer。
- `control/`：不可变来源包副本、全部成功/失败尝试、官方评价引用、分组/权重/源码锁。这些只能由数据审计程序读取，不能拼进模型输入。
- `manifest.json` / `manifest.sha256`：逐文件哈希、版本、数量及质量指标。

AppWorld 派生轨迹与控制证据仍属于受限数据，只通过已授权服务器的 SSH/SCP/rsync 通道传输；不上传公开仓库、对象存储公开桶或模型 Hub。公开仓库只包含代码、配置、任务 ID 清单和不含原文的报告。AppWorld 原始数据及模型权重不在此归档中。

## 2. 环境与版本

准备 Python 3.12 独立虚拟环境，先核实 GPU 型号/显存、驱动和 BF16 支持，再安装 `requirements/base-sft.txt`。本机数据验证使用 Python 3.14.4；因此远程必须重新分词复验，不能仅依版本号假定跨环境一致。

```bash
python3.12 -m venv .venv-sft
.venv-sft/bin/python -m pip install torch==2.10.0+cu128 --index-url https://download.pytorch.org/whl/cu128
.venv-sft/bin/python -m pip install -r requirements/base-sft.txt
.venv-sft/bin/python -m pip install --no-deps -e .
.venv-sft/bin/python -m pip check
.venv-sft/bin/python -m pip freeze > remote_environment.lock.txt
nvidia-smi
```

固定 Qwen3-4B revision `1cfa9a7208912126459214e8b04321603b3df60c`，chat template SHA `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`。tokenizer 和权重在授权环境从官方模型仓库准备到 HF cache；运行入口只使用本地缓存，不自动联网下载权重。显式设置该缓存的 `HF_HOME`，运行验证时设置 `HF_HUB_OFFLINE=1`。首次本地预演漏设这两个变量导致缓存未找到/metadata 联网失败，已有日志保留，修正环境后通过。

主要依赖锁为 torch 2.10.0+cu128、transformers 4.57.6、tokenizers 0.22.2、PEFT 0.18.0、Accelerate 1.12.0。这是**待远程验证的组合**；本机没有安装后两个包。依据是 [Qwen 官方模型说明](https://huggingface.co/Qwen/Qwen3-4B)、[PEFT 0.18.0 官方安装约束](https://github.com/huggingface/peft/blob/v0.18.0/setup.py) 与 [Accelerate 1.12.0 官方安装约束](https://github.com/huggingface/accelerate/blob/v1.12.0/setup.py)。依赖声明兼容不等于 96GB 上的实测通过。

## 3. 私有导入与重新分词（远程未执行）

将下列`/private/`替换为服务器上已授权且可写的私有目录。在可信 checkout 中从公开 release 报告读取摘要，示例 shell 变量仅为摘要，不含凭据：

```bash
dataset_manifest_hash=$(.venv-sft/bin/python -c 'import json; print(json.load(open("reports/base_sft_dataset_v1_release.json"))["manifest_sha256"])')
dataset_archive_hash=$(.venv-sft/bin/python -c 'import json; print(json.load(open("reports/base_sft_dataset_v1_release.json"))["archive_sha256"])')
PYTHONPATH=src .venv-sft/bin/python -m recoveragent.dataset_transfer import --input /private/incoming/base_sft_v1.tar.gz --output data/base_sft_v1 --expected-archive-sha256 "$dataset_archive_hash" --expected-manifest-sha256 "$dataset_manifest_hash" --report /private/import_verification.json
PYTHONPATH=src HF_HUB_OFFLINE=1 .venv-sft/bin/python -m recoveragent.formal_dataset retokenize --input data/base_sft_v1 --expected-manifest-sha256 "$dataset_manifest_hash" --output data/base_sft_v1_remote_tokens --report /private/retokenization_verification.json
PYTHONPATH=src .venv-sft/bin/python -m recoveragent.sft_recipe preflight --config configs/base_sft_qwen3_4b_smoke.json --report /private/sft_preflight.json
```

导入拒绝已有目标目录、错误归档 SHA、路径越界、软硬链接和超限展开量；之后重新核对全体源包、来源、划分、mask、权重及文件 SHA。重新分词必须与包内两个 token JSONL **逐字节相同**。本机另用官方 AppWorld 的 `Task.load(load_ground_truth=False)` 复核 train 成员、初始任务和动态 Schema；纯训练服务器不需要安装 AppWorld，仅复核已签入可信摘要的数据来源证据。

重新分词和真实训练入口还强制核对当前checkout中的源码/配置与包内`control/protocol_lock.json`。应使用这次发布对应的Git提交；后续若更改受锁定文件，须显式发布新数据/协议版本，不能跳过这个检查。只读数据完整性审计可使用`formal_dataset verify --check-runtime`附加相同检查。

## 4. 训练入口与 loss 契约（远程未执行）

`sft_recipe` 的 `validate`/`preflight` 都不训练，只有明确的 `execute` 会训练。后续获得实际训练启动授权、且导入/重新分词/环境预检全部通过后，先使用 `configs/base_sft_qwen3_4b_smoke.json`：2 个 optimizer step，最多 4 个已有短样本，**不截断**。再使用 `configs/base_sft_qwen3_4b_length_smoke.json`：1 step，训练和验证各取现有最长完整样本，用于验证真实长上下文显存。每次使用新输出目录。

```bash
PYTHONPATH=src HF_HUB_OFFLINE=1 .venv-sft/bin/python -m recoveragent.sft_recipe execute --config configs/base_sft_qwen3_4b_smoke.json --dataset data/base_sft_v1 --expected-manifest-sha256 "$dataset_manifest_hash" --output checkpoints/base_sft_smoke_001 --report /private/base_sft_smoke_001.json
```

正式配置 `configs/base_sft_qwen3_4b_lora.json`：单 GPU BF16、LoRA r=16/alpha=32/dropout=0.05，7 个 attention/MLP 投影层，batch=1、gradient accumulation=8、学习率 1e-4、1 epoch、gradient checkpointing、SDPA；不做 packing，不截断，不自动发布。它是可审查起点，尚未做效果调优或证明全部长样本适合目标显存。

每个训练样本只监督一个 Teacher Assistant 回合。其之前所有 Assistant、System/User/Tool、被 harness 拒绝回合和 padding 均为 -100。动态 Schema 使用该回合真正可见的版本。权重按家族→任务→相似行为簇→轨迹→回合分配等额质量，再在分区内归一为平均 1。实现先平均单个样本的监督 token CE，再乘 example_weight，最后按 microbatch 平均；不能直接使用全 batch token 均值替代这个目标。

固定 transformers Qwen3 的 `logits_to_keep` 只计算监督位置对应的前一 token logits，避免展开全部长前缀的词表 logits。本机用随机初始化的微型 Qwen3 架构验证了 loss/梯度与普通完整 logits CE 一致；这是 CPU 软件测试，0 optimizer step，不是 Student 训练证据。

## 5. Checkpoint 与阶段验收

真实烟测必须记录非零有限梯度产生的 LoRA 参数哈希变化、有限 train/eval loss、最大显存、训练样本身份、有效配置、版本和数据 SHA；保存 adapter/tokenizer，再新加载固定基座与 adapter，核对 LoRA tensor 哈希及同输入 logits 误差。代码会检查这些条件并保存 `recoveragent_checkpoint.json`。失败目录与日志保留。

Trainer 的周期 checkpoint 包括 adapter/optimizer/scheduler/RNG/trainer 状态；最终目录用于推理重载。目前入口不提供自动 resume，正式恢复训练需后续明确选择 checkpoint 并验证 optimizer/RNG 连续性，不能用“加载 adapter”冒充精确续训。

这两种烟测通过仍不等于 P3-B 完成。还需要真实 Qwen3-4B 批量调用后端接入、长上下文推理与同版 non-thinking 解析、保存后 Student 在固定官方 dev 的正常任务评价，以及 Raw Base / Base-SFT-only 对照。dev 不进入训练包。本次内部 validation 是按 scenario 隔离的 train 派生数据，部分场景曾用于历史 Prompt 开发，不能宣称是完全未接触的泛化测试。

远程推理沿用冻结 `execution_v5`、`dynamic_discovery` 和 `numeric_answer_self_correction_v1`；函数调用解析器 `student.parse_batch` 与训练目标回读共用。工具调用必须在生成时已可见；批次全部结果返回后才生成下一次，动态依赖不得在同批猜测。正式长 SFT 与 GRPO 本轮不启动。

现有 StudentSession 分别检查输入和单次输出长度。真实后端接入时还必须将生成上限设为 `min(4096, 32768 - 实际输入token数, 剩余生成预算)`；没有余量则以明确预算原因终止，不能让输入32768再额外生成4096。此后端尚未接入，不能把数据长度检查当作推理总窗口已验收。

固定采样seed可以重建任务清单，固定源文件与tokenizer可以重建分组、mask及token文件；DeepSeek是带温度采样的在线别名，provider没有承诺不可变revision，不能保证重新付费生成得到逐token相同的轨迹。原始回执与backend指纹必须保留。
