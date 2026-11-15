# S0短Recovery-SFT与同协议dev24配对实验

40步短训练及第20/40步重载已验收，两组vLLM评价已结束。S0为8成功、15失败、1人为中断未知；S1为8成功、16失败。在23个双方有有效评分的任务中，两者均8/23，新增成功1题、退步1题，净增量0。**本轮没有证明短更新优于S0，继续保留S0作为主模型。**

## 实验约定

训练源码基于`f2fd57f`，已按实际执行源归档固化为`5647b30`；训练运行`v2_s1_short_20261115_01`，评价另建`v2_s1_short_vllm_20261115_01`及仅承接未执行题的`v2_s1_short_vllm_20261115_02`。主模型固定Qwen/Qwen3.5-9B revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`，起点为已验收S0：658步、adapter tensor SHA `5da6444aa979e2ce0957b3bba82bd239ba0f59fad40024a7ea0a35b7f4534966`，receipt SHA `828251a14a515d2e8703d54c529abbdb1c5d1b05b4ae17d9566dc7aa92d1064b`。不继承44步或152步的旧S1。

沿用`teacher_answer_format.py`原RULE/NOTICE/`public_numeric_answer_format_v1`，新Student与Teacher实际输入统一。先生成新任务的公开任务，再加NOTICE；恢复继承Student快照历史。单个金额/计数默认JSON数字，显式输出格式、名称、ID、日期、百分比和多个结果遵从原题。只给模型公开输出指令，不修改模型答案、原系统提示或官方评分。

新配置：[v2_s1_short_20261115.json](../configs/v2_s1_short_20261115.json)；源码锁：[v2_s1_short_20261115_lock.json](../configs/v2_s1_short_20261115_lock.json)。锁SHA `32401db0d40ed7296caabe9fa8a5fbd2ccc190cd43739b26def765147493f637`。旧配置与源码锁未重签。

## 固定数据

历史编码及原始格式字段保持真实值，不为了新推理格式改写旧输入或目标。旧回放读取完整256池（5261行/731298监督token），不是上一轮24条子集。完整池来源为已核验`final_1794626440.tar.gz`中的`runs/s1_004/data/normal/examples.jsonl`，成员SHA `3503254041efc4c9dff4630eca57521ce0a81887019d6785a3a16f42e32291f4`。

冻结seed20261115，按场景、未选任务和任务次数均衡选择。每行保留完整上下文和Assistant目标；仅部分回合参与更新，不宣称训练每条轨迹的所有目标。

|来源|完整池任务数|选中任务/场景|选中行数|监督token|权重总和|token平均loss目标|
|---|---:|---:|---:|---:|---:|---:|
|old_replay|256|160/124|160|21675|160|50%|
|fresh_full|21|21/18|96|9087|96|30%|
|recovery_suffix|18|18/18|64|9407|64|20%|
|合计|295|199/138|320|40169|320|100%|

合计场景数已按选中清单去重；分类场景数不能直接相加。最长选中序列55636 tokens。所有fresh/recovery任务至少选中一行。权重为`alpha × 320 × 行监督token / 本类选中监督token`，Trainer普通随机打乱，没有来源加权采样。恢复前缀与工具输出始终为-100。

[320行冻结清单](../configs/v2_s1_short_20261115_plan.json)含来源行号/哈希、任务/场景、轨迹与Assistant回合及目标边界，不含私人文本。清单SHA `0504a583d24c5e3b93ffc7a6e8b49653335af131f3258a908b59510a24a0960b`；编码SHA `34fd11f07473ab70d17b3defe671b7297c872afb8c52f3e614017f536f2adac6`。

## 训练与评价

短2步/长1步真实烟测均通过，均从原S0独立加载；长样本55636 tokens，峰值allocated38.40GiB，参数变化且重载通过。正式再加载S0。LR5e-6、micro batch1、gradient accumulation8、max_steps40；原64k、non-thinking、BF16、CPU activation offload与LoRA不变。step20保存并核验重载，最终step40默认评价。烟测不计入正式40步。

本轮同时降低学习率、更新步数与恢复目标权重；配对结果检验组合方案，不能分别归因于某一个参数。

正式训练实际40步、epoch1.0、320行、40169监督token；40份梯度均有限且非零。receipt训练耗时3171.95秒（52.87分钟，不含最终保存/重载），峰值allocated38.624GiB/reserved39.248GiB。第20/40步各自保存与重载前后参数哈希一致，BF16 logits最大差均为0。

- 第20步adapter tensor SHA：`cb9d97fe71ba68a27d440f158cd9f828a3fcdc90109bef1e40aa69345bf11b39`。
- 第40步adapter tensor SHA：`722018b0ccc6a3edb4413a07a01d98a8f738e369afb8dcf19eb04aaf26c61e0f`。
- 候选权重：远端`/root/gpufree-data/recoveragent_s1_short_20261115_01/runs/short_001/S1_short40/`；中间权重在其`checkpoint-20/`。通过现有`load_base(identity)`与`PeftModel.from_pretrained(base, checkpoint)`加载，原S0目录不变。

训练验收后，最初归档启动器使用系统Python，缺少`hashlib.file_digest`而停止。原失败状态与日志已保存；改用既有Python3.12执行器，先核验两个已保存checkpoint，再接续归档和评价。没有重训、追加更新或重跑已执行评价任务；异常发生时dev模型请求为0。

按用户追加要求，两组评价改为同一L40S上的vLLM 0.19.0。原Transformers阶段完成1题、另一题中断，记录单独保留，不计入新比较；新vLLM运行重新执行完整固定dev24。两份模型同一新公开格式，各题各一次。每题80请求/16384累计生成token/1200秒，上下文65536，单次最多4096输出，temperature0.8/top_p1.0/seed42。保留所有失败及未知，不重试挑分。

|模型|成功|失败|未知|24题中的确认成功比例|双方有效的23题|
|---|---:|---:|---:|---:|---:|
|S0＋新格式|8|15|1|33.33%（另1题未评分）|8/23，34.78%|
|短40步S1＋新格式|8|16|0|33.33%|8/23，34.78%|

未知项不是S0模型失败，S0完整24题成功数只能确定在8–9之间。S1在该项失败，不能据此认定S1退步，也不能把未知当失败来凑齐24对。历史旧格式S0 9/24、44步S1 6/24、152步S1 4成功/19失败/1未知仅供参考，不作为本轮增量基线。

唯一新增成功是`d9987f6_2`：S0在无关支付查询中耗尽80次请求，S1完成原任务，29次请求、103.3秒。唯一退步是`d9987f6_1`：S0成功，S1选中商品评分等于公开要求阈值，未满足严格“大于”的条件。其余21个有效配对结果不变，其中7题共同成功、14题共同失败。不能把净零解释成更高恢复能力或稳定提升。

独立评价配置为[v2_s1_short_vllm_20261115.json](../configs/v2_s1_short_vllm_20261115.json)，新锁SHA `32b76021b20c5a9d731f86d2d253b37fa920db90fbf12e452329f3681ae1364a`。训练锁保持原样，在`5647b30`可核验；当前修改后的源码按新锁运行，不重签旧锁。vLLM独立虚拟环境采用Torch2.10.0、Transformers4.57.6，[完整依赖锁](../requirements/v2-vllm-20261115.lock.txt)留存；AppWorld worker仍用已验收的5.23开发版tokenizer，由客户端渲染并传入完整token IDs。两个阶段使用同一服务、不同验收LoRA绑定，64k/BF16、prefix caching、chunked prefill；任务按原顺序串行，服务容量2并不意味着本轮并行跑两题。

两份模型的实际服务探针均通过：绑定目录/文件SHA、原基座对照、短输入、重复缓存输入和两个65,520 token输入。合成探针仅验证加载/长窗口，不算真实任务或效果改善。GPU UUID为`GPU-eca7cc1a-aa88-2f3e-2e97-c31f193e837e`。

## vLLM警告误判与接续

首次vLLM运行完成10题（2成功/8失败）后，控制侧看到“原始模块名不在目标模块白名单”的警告，过早判断部分LoRA被忽略并暂停评价。随后检查固定0.19.0的实际加载源码与GPU：该路径会将原始q/k/v、gate/up合并到qkv/gate_up；警告使用的原始名称检查不代表这些参数真的丢失。

在与服务相同的vLLM配置下，通过只读worker扩展逐项比较文件、注册权重与实际GPU buffer，S0和S1各256个张量、80个合并模块全部精确匹配（包括LoRA缩放），两份验收adapter完整生效。`gpu_lora_audit_v2.json`与检查脚本、原始警告和初次诊断接口异常均私有保存。这里的检查是模型加载验证，不是任务成功验证；原`failed/IncompleteLoRALoad`控制状态保留，另附纠正回执，不改写成从未中断。

暂停打断S0第11题`9b2dc64_2`，已记录33请求/32回复，未得到官方评分，单列未知且不重试。此前10份真实评分全部保留；在独立`v2_s1_short_vllm_20261115_02`只接续原顺序的剩余13题，再执行S1全24题。执行源码仍为`50c5cb6`、同一源码锁/模型/后端/硬件/预算，接续控制清单和原结果SHA独立冻结。最终最多23个双方有效评分的配对，不能声称48份官方评分均有效。

## 配对核验、失败类型与成本

[逐题结果与成本CSV](v2_s1_short_paired_dev24_20261115.csv)列出固定顺序的全部24题，两组结果、终止原因、请求/token/时间，以及逐题失败分类。原始轨迹、任务正文、完整评分细节均留在私有目录，不进入公开报告。

从已逐文件核验的归档读取实际请求与结果：24题的首轮模型可见消息、工具Schema、输入token IDs及采样参数完全一致；23个双方完整记录的初态哈希一致。人为中断题没有完整初态回执，不补造哈希。两组均校验模型revision、实际adapter身份、dev来源及预算。GPU实参加载核验与任务官方评分是两项不同的证据。

|主要失败分类|S0|S1|观察|
|---|---:|---:|---|
|对象/条件选择错误|8|10|歌单集合、购物规格或评分阈值、闹钟与支付请求筛选错误|
|目标状态不完整或额外改动|3|6|待办迁移、闹钟等未达到目标状态；部分产生无关改动|
|业务无进展并耗尽请求|2|0|S0查询偏离目标，80次请求用尽|
|上下文耗尽|2|0|S0支付任务查询累积到64k，未完成目标操作|
|人为中断，评分未知|1|0|独立于模型失败，不能计为S0失败|

分类是对实际终态和轨迹的归纳，每个失败只计一个主类。S1的16个失败均在声明完成后被官方验收拒绝；没有耗尽预算不等于任务能力提升。例如支付任务从S0的查询耗尽变成S1确实操作，但仍选错对象，完整成功率没有增加。未观察到仅靠修正数字输出形式即可使这些失败通过的证据。

|实际成本|S0（含中断题已知请求用量）|S1|
|---|---:|---:|
|模型请求|991|922|
|累计输入token（每次请求完整输入，非去重）|23,478,441|19,268,848|
|已知生成token|89,855|85,718|
|未知生成预留|4,096|0|
|已知模型生成秒数|2,980.09|2,670.76|
|完整记录的环境执行秒数|24.45|21.28|
|官方评分秒数|69.12|67.85|
|已评分episode墙钟合计|3,598.39秒／23题|3,326.23秒／24题|

S0中断题33次请求、32次回复，已知2,693生成token/99.14秒模型时间已包含在表内；其完整episode墙钟与环境总耗时缺失，不填0冒充测量值。4,096是未知请求的生成预留，不是已消费token。逐题episode时间含启动与渲染等开销，不能只把模型和工具秒数相加替代。

只看相同23个有效配对：S0为958请求、87,162生成token、59.97分钟episode合计；S1为890请求、82,795生成token、53.17分钟。S1本次用量较低，但包含更早的错误完成声明，不能据此认定质量提高。全部24题中S1平均2.31分钟/题；S0的23个已评分任务平均2.61分钟/题。此为本轮vLLM实测，不与旧硬件/旧协议速度混算。

40步正式训练耗时52.87分钟另计；小/长烟测、模型加载与重载、服务探针、LoRA诊断、人为暂停和文件回传不计入上述episode合计。初次Transformers部分成本另列于备份段。没有将训练loss作为验证成绩，也没有以更低loss认定模型改善。

下一项最值得改进的是训练范围内的**公开条件到精确对象集合、再到最终状态核对**，尤其严格不等式、遗漏与额外操作。本轮不追加调参、重采或训练；dev轨迹与评分不回流训练或任务生成。可选的旧失败快照恢复专项没有执行，因此本轮不评价检查点接续能力。

## 格式分析边界

软件检查发现现有`v2_model._parameter`在Schema同时包含string/number时优先保留字符串。合成的XML `answer`正文`123`被解析为JSON字符串；狭义单计数问题的既有AnswerGuard会拒绝该类型。这是新提示与最终工具参数类型之间的既有解析局限，不应归因于SFT能力。两组均保持相同解析器，没有修改答案、删单位或更改评分。

真实执行中，`258796c`三道标量数值答案题在S0/S1均3/3成功，实际提交均为JSON数字。本次未发现仅由单位或JSON答案类型导致的最终失败；主要失败是对象筛选和目标状态不完整。上述解析局限来自合成检查，不能据此宣称解释了本轮失败，或宣称当前模型无法提交数字。统一新规则已真实进入两组输入，但本轮没有旧/新规则单独消融，不能单独量化规则的收益。

## 验证与启动

- 针对性测试：`PYTHONPATH=src:. .venv/bin/pytest -q tests/test_v2_s1_short.py tests/test_teacher_answer_format.py tests/test_v2_learning.py tests/test_v2_s1_retrain.py tests/test_v2_s1.py tests/test_v2_training_cpu.py`：68 passed，1 skipped（通用软件环境未安装训练依赖）。
- 完整软件：`PYTHONPATH=src:. .venv/bin/pytest -q`：1100 passed，1 skipped；`.venv/bin/ruff check .`及`.venv/bin/ruff format --check .`通过。测试覆盖实际消息接线、生成任务、快照继承、历史token不变、三类权重、40步参数/20步回调、两模型一致评价请求。它们不是模型效果验证。
- 另在本地固定训练依赖环境运行`OMP_NUM_THREADS=1 PYTHONPATH=src:. .venv-v2/bin/python -m pytest -q tests/test_v2_training_cpu.py`：3 passed。随机小模型CPU测试确认可训练父adapter继续更新及公共loss/重载回归，不是预训练模型效果或真实GRPO阶段。
- vLLM追加接线：相关75 passed；最终完整`PYTHONPATH=src:. .venv/bin/pytest -q`为1107 passed/1 skipped，Ruff check及237文件format通过。验证两份checkpoint绑定、新配置与原训练一致、拒绝错误parent/步数/文件、相同消息与预算通过vLLM分派，不加载Transformers GPU模型。
- 真实启动检查：原S0文件/身份、worker模板与Jinja2、实际NOTICE渲染、BF16 L40S可用；复用同一环境此前9应用119次API/快照验收，未宣称本轮重复执行119次探针。

训练入口在冻结源码`5647b30`执行；已完成训练，不应再次调用`train`来进行评价。原启动命令如下：

```bash
cd /root/gpufree-data/recoveragent_s1_short_20261115_01/source
export PYTHONPATH="$PWD/src:$PWD"
export HF_HOME=/root/gpufree-data/recoveragent_s1_retrain_20261114_01/hf_cache
export HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=8
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
WORKER_PYTHON=/root/gpufree-data/recoveragent_s1_retrain_20261114_01/worker_env/bin/python
GPU_PYTHON=/root/gpufree-data/recoveragent_s1_retrain_20261114_01/env/bin/python
$WORKER_PYTHON scripts/train_v2_s1_short.py prepare --output ../runs/short_001 --deployment ../deployment.json
$WORKER_PYTHON scripts/train_v2_s1_short.py train --output ../runs/short_001 --deployment ../deployment.json
```

vLLM评价在新源码和新输出目录运行；现有服务同时只读挂载验收S0与S1。启动入口：

```bash
cd /root/gpufree-data/recoveragent_s1_short_vllm_20261115_01/source
export PYTHONPATH="$PWD/src:$PWD"
WORKER_PYTHON=/root/gpufree-data/recoveragent_s1_retrain_20261114_01/worker_env/bin/python
for stage in probe-s0 probe-s1 baseline evaluation; do
  "$WORKER_PYTHON" scripts/evaluate_v2_s1_short_vllm.py "$stage" \
    --output ../runs/eval_001 --deployment ../deployment.json || break
done
```

服务端使用vLLM官方的[LoRA模块限制参数](https://docs.vllm.ai/en/v0.19.0/features/lora/#restricting-lora-to-specific-modules)。实际启动参数如下；`BASE_SNAPSHOT`、`S0_DIR`、`S1_DIR`分别取部署文件的`model_snapshot`、`s0`、`s1`，实际运行的PID写入`vllm_pidfile`，随后才执行上述探针与评价。

```bash
/root/recoveragent-vllm-20261115/env/bin/python -m vllm.entrypoints.openai.api_server \
  --model "$BASE_SNAPSHOT" --served-model-name Qwen3.5-9B-pinned \
  --host 127.0.0.1 --port 18000 --dtype bfloat16 --max-model-len 65536 \
  --gpu-memory-utilization 0.88 --max-num-seqs 2 --max-num-batched-tokens 4096 \
  --enable-chunked-prefill --enable-prefix-caching --enable-lora \
  --max-lora-rank 16 --max-loras 2 \
  --lora-target-modules qkv_proj o_proj gate_up_proj down_proj \
  --lora-modules "S0_base=$S0_DIR" "S1_short40=$S1_DIR" \
  --logprobs-mode processed_logprobs --limit-mm-per-prompt '{"image":0,"video":0}' \
  --generation-config vllm --no-enable-log-requests
```

部署文件保存只读checkpoint、服务URL/LoRA名称/加载回执与截止时间；服务完整命令保留在两份probe回执和私有`start_server.sh`。独立控制器已完成，推理服务及其GPU子进程已退出；自动封存归档、本地断点回传与逐文件SHA核验均已完成。Teacher采集仍停止，不执行GRPO/CoEvolve。资源沿用原实例24小时截止：北京时间2026-11-15 21:13:36。

## 已核验备份

本地私有`runs/v2_s1_short_20261115_01/retrieval/`：checkpoint归档654,293,431字节/114文件，SHA `bfe73181ab38f9b04e4f0b10812063bc934b99fc9808bc22ea2fda6785738a1e`；训练与被替代的Transformers运行完整归档1,208,838,076字节/703文件，SHA `2d6cc8062b1413d2412430113ab531cb6f856d1eac97a9769f6202e84f7114a2`。归档及逐文件SHA均通过，已fsync。原Transformers完成题的终态另有36文件备份；中断题不虚构有效评分或完整快照。

切换前Transformers额外记录104次请求、103次完整回复、9,997已知生成token、857.43秒模型时间；另1次中断请求用量未知。这部分单列成本，不计入vLLM的48项结果。

未增加可选的旧快照恢复比较；本轮效果结论仅针对固定dev24，不将其称为恢复能力专项验证。

评价私有备份均已回传并逐文件SHA核验、fsync：

|本地run／归档|文件数|归档SHA-256|
|---|---:|---|
|`v2_s1_short_vllm_20261115_01/retrieval/failed_1794722038943735005.tar.gz`|2908|`5ac817a7eb8ad471a293121d96ce55bd5438c33418b48f0ce440cd072a7ebd8f`|
|同run `environment_1794722053229908654.tar.gz`，10题终态|182|`94632cdb191a2d94aafa4fae9e0af2eb35a67efae978365c184b9b39cd6d9e3b`|
|同run `lora_diagnostic.tar.gz`，加载诊断及纠正|7|`8e612d65ed28465a6f712736f6970c2cfbbc35366fbac48df9d7db26528d799b`|
|`v2_s1_short_vllm_20261115_02/retrieval/final_1794728075622859334.tar.gz`|7243|`93e9820738b877f19e6a546a782b062a253ba3098d896362f08b33b8399fb56a`|
|同run `environment_1794728098760902785.tar.gz`，37题终态|642|`daa4eb6adafb5350b2c2478e4f6e1d6807150caa16c25ed1b2485bee9777fba6`|

上述路径均相对本地`runs/`，不提交原始产物。`v2_s1_short_vllm_20261115_02/paired_audit.json`保存逐题输入/状态/模型核对及成本，数据来源绑定上述训练与评价归档SHA。原中断状态、所有真实评分与原模型保持不变，接续控制器complete表示剩余执行完成，不表示原中断题获得评分。

最终确认GPU为0MiB/0%且无compute进程；平台实例仍需用户在控制台关机，未声称停止计费。没有启动新的Teacher采集、GRPO或CoEvolve。
