# Base-SFT v1 私有迁移与远程验收

> 2026-10-22更新：旧AppWorld/SQLAlchemy2.1.5的URI与model-hash问题已在新隔离SQLAlchemy2.0.52环境完成本机修复验证；原95条示范的完整独立重放结果见`../reports/harness_v1_report.md`与`../reports/harness_revalidation_20261022.json`。旧环境、原包、旧奖励和旧锁保持不变，复验不是新增Teacher示范。远程仍须安装并实测`requirements/appworld-harness.lock.txt`，完成真实状态保存恢复与新进程官方评价；不能把本机通过或原包SHA通过当成远程训练PASS。

> 源码版本注意：harness v1更新了共享运行和审计模块，原v1包的50文件源码锁不会随之改写。训练旧包时应使用与其锁匹配的源码checkout（本轮基线`473693c3b768940679a2367d149fd54293e27496`的这50文件匹配），或另行构建并验证新版本数据包；不要修改旧manifest/源码锁放行。新版AppWorld验证worker可以使用独立当前checkout和兼容环境，实际依赖须另存运行清单。真实4B训练及后端仍未验收。

2026-10-23 实际P3-B训练已完成：RTX PRO6000 Blackwell Server Edition（97887MiB），固定4B BF16+LoRA，短/长烟测及正式116步全部通过，重载logits差0。原数据SHA不变；Raw/Base-SFT固定6任务评价各有2个官方运行异常，有效结果0/4对1/4，尚无泛化提升证据。私有checkpoint和证据已回传核验，平台shutdown后SSH不可达。最终结果见`../reports/p3b_base_sft_report.md`与`../reports/p3b_checkpoint_release_20261023.json`。

**第1–5节保留历史准备阶段的说明与原命令。** 当时“未执行/待验证”不代表当前状态；本轮执行必须使用末节记录的BF16补充runtime、独立评价源码锁与实际报告，不直接用旧reload上下文重复训练。旧数据/源码锁不得改写。

## 1. 数据与可信来源

最终路径、数据集 manifest SHA-256、压缩包 SHA-256 和大小见已提交的 `reports/base_sft_dataset_v1_release.json`。以 GitHub 提交中的摘要作为信任起点，不只相信随压缩包一起收到的摘要。

`data/base_sft_v1/` 与归档只保存在私有工作目录。结构为：

- `model_input/{train,validation}.jsonl`：完整 Teacher 历史及每回合实际 Schema；不包含控制侧来源/状态/评价引用。
- `tokenized/{train,validation}.jsonl`：真实固定 Qwen tokenizer 生成的逐 Assistant 回合样本、labels、mask、example_weight。
- `model_input/{deferred,reserve,duplicates}.jsonl`：暂缓或未入选完整轨迹，绝不交给 Trainer。
- `control/`：不可变来源包副本、全部成功/失败尝试、官方评价引用、分组/权重/源码锁。这些只能由数据审计程序读取，不能拼进模型输入。
- `manifest.json` / `manifest.sha256`：逐文件哈希、版本、数量及质量指标。

AppWorld 派生轨迹与控制证据仍属于受限数据，只通过已授权服务器的 SSH/SCP/rsync 通道传输；不上传公开仓库、对象存储公开桶或模型 Hub。公开仓库只包含代码、配置、任务 ID 清单和不含原文的报告。AppWorld 原始数据及模型权重不在此归档中。

新增复验证据独立于原包：私有`.cache/harness_revalidation_20261022_a/`包含固定plan、每条receipt、独立官方评价和新terminal_dbs；公开`reports/harness_revalidation_20261022.json`绑定原轨迹SHA、原包manifest SHA，以及新receipt/evaluation/terminal_manifest SHA。远传这些控制证据时先核对可信公开摘要，再逐文件核对terminal_manifest；隐藏官方评价和终态不能进入训练消息。尚未执行这次远传。

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

原数据包封存的历史 StudentSession 分别检查输入和单次输出长度；当前 Harness及review_20261023运行时已经将生成上限设为 `min(4096, 32768 - 实际输入token数, 剩余生成预算)`；没有余量则以明确预算原因终止，不能让输入32768再额外生成4096。真实4B后端尚未接入，当前软件窗口校验通过不等于真实模型推理已验收。原包继续使用对应封存训练源码，当前Harness/兼容AppWorld worker按独立源码锁部署；不得修改原包锁以绕过版本检查。

固定采样seed可以重建任务清单，固定源文件与tokenizer可以重建分组、mask及token文件；DeepSeek是带温度采样的在线别名，provider没有承诺不可变revision，不能保证重新付费生成得到逐token相同的轨迹。原始回执与backend指纹必须保留。

## 2026-10-23真实执行入口

新增`scripts/run_p3b_training.py`编排封存checkout；本轮显式指定`--runtime-module recoveragent.sft_bf16_runtime`，不编辑原50文件、锁内配置或数据。新runtime及`configs/p3b_bf16_runtime_lock.json`作为额外文件放入封存checkout，验证旧锁后再核验补充锁。显式传入可信manifest SHA、已验证的重新分词报告和缓存路径；按短烟测→最长完整样本烟测→正式顺序执行，任一参数更新/重载检查失败即停止。输出根目录必须不存在，保存实际命令、每步日志、GPU采样和最终checkpoint。脚本不会下载权重或静默截断。

`student_backend`在远程单GPU加载固定Raw Base或已核验adapter，仅绑定loopback，核对实际输入token、模板和checkpoint身份。`student_evaluation`使用冻结execution_v5/B动态发现及review_20261023 Harness，按`configs/p3b_paired_evaluation_20261023.json`固定清单在独立AppWorld进程执行。官方非断言异常保留unknown并继续预定评价，worker/backend故障停止该arm、保存未知用量和未尝试清单；不改Teacher采集熔断或官方奖励。

训练Python与AppWorld Python隔离：训练栈使用requirements/base-sft.txt固定组合；AppWorld先按appworld-harness.lock安装，再只补齐固定tokenizer依赖（HF Hub0.36.2），最终pip check及完整pip freeze另存，原依赖锁不回写。

网络诊断中直连HF失败，本机Xet无进展；AutoDL官方学术加速有503/低速。Qwen官方ModelScope镜像可作为纯传输来源，但每个权重必须与独立取得的HF固定revision LFS SHA-256完全一致，普通配置/tokenizer文件校验HF Git blob或LFS哈希，不能仅相信镜像master名称。参考[AutoDL官方学术加速](https://api.autodl.com/docs/network_turbo/)和[Qwen官方镜像](https://modelscope.cn/models/Qwen/Qwen3-4B)。

本轮用户要求结束后关机：先下载并验证私有adapter/checkpoint与运行证据，再执行AutoDL官方`shutdown`并核对断连；不释放/删除实例数据，也不把SSH断连当作已核验账单结算。[AutoDL关机说明](https://www.autodl.com/docs/save_money/)。


实际run_003两项烟测和正式训练均PASS，最终checkpoint私有本机位置为`runs/p3b_remote_20261023/checkpoints/run_003/formal/`。可信adapter文件SHA为`14ef88e8d9d460e7806b9decf7ddd73a2fcd926486feefcb77800359e394f2ff`，tensor哈希为`aefac6f068361cb5c0fc76c6a26f8b45231953b37d141f1ec9352a29917cf9c0`。新机器须核验公开交接摘要及归档、加载同revision基座、固定tokenizer/template，再用同一BF16上下文加载adapter；不能仅凭目录名认定身份。

实际评价使用`configs/p3b_evaluation_runtime_v2_lock.json`（p3b_real_clock_transformers_v2）。它保留冻结公开任务时钟，对Student预算注入Linux真实CLOCK_MONOTONIC，并使用官方Freezegun configure API排除控制侧Transformers延迟模块；旧v1失败目录/源码锁保留。新后端已用真实4B权重验收，完整模型请求和工具调用记录已回传。未验证精确optimizer续训，未启动P3-C/Recovery-SFT/GRPO；不要把保存optimizer状态等同续训验证通过。


## 2026-10-26 A100独立运行

用户明确授权在新A100-SXM4-80GB上重新训练及补齐Student测试。新的硬件契约为`a100_80gb_20261026`，`sft_a100_runtime.py`与`configs/p3b_a100_runtime_20261026_lock.json`只新增硬件预检及记录；原50文件数据锁、473693c训练源码、旧BF16补充锁仍保留。除硬件元数据外，execute函数AST与已验收BF16版本一致。原三个训练配置不改；编排器显式传`--runtime-module recoveragent.sft_a100_runtime`。重新分词必须在封存训练源码cwd及PYTHONPATH下执行，不能从当前worker源码绕过旧数据锁。

当前worker使用`p3c_shared_execution_20261026`和`p3b_shared_execution_20261026`，绑定shared_execution_20261025。训练全部通过后，用`scripts/prepare_student_validation.py --training <新三阶段目录> --dataset <原95条目录> --appworld-root <官方数据根目录> --output <不存在的新配置目录>`生成独立checkpoint交接摘要、paired和P3-C配置；默认A100容量门槛75000MiB，固定任务清单/单任务预算不改，不能把旧checkpoint目录充当本轮训练结果。运行证据见`docs/STATUS.md`及本轮报告。


本次已完成短/最长/正式2/1/116步及Raw/SFT固定对照。新adapter位置`runs/a100_remote_20261026/checkpoints/run_001/formal/`，可信摘要`reports/a100_checkpoint_release_20261026.json`。A100新运行与旧PRO6000的报告和checkpoint分别保留；详细结果和命令见`reports/a100_sft_p3c_20261026_report.md`。P3-C有5条预算中断隔离，不可作为恢复训练样本。此次平台命令因未设置关机密钥失败，用户报告手动关机；不将AutoDL旧平台关机方式/结果套用于当前平台。
