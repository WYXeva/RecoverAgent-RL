# S0短Recovery-SFT与同协议dev24配对实验

40步短训练及第20/40步重载已验收；正在执行新协议S0/S1配对dev24，尚无完整效果结论。

## 实验约定

源码基于`f2fd57f`，新运行`v2_s1_short_20261115_01`。主模型固定Qwen/Qwen3.5-9B revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`，起点为已验收S0：658步、adapter tensor SHA `5da6444aa979e2ce0957b3bba82bd239ba0f59fad40024a7ea0a35b7f4534966`，receipt SHA `828251a14a515d2e8703d54c529abbdb1c5d1b05b4ae17d9566dc7aa92d1064b`。不继承44步或152步的旧S1。

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

正式训练实际40步、epoch1.0、320行、40169监督token；40份梯度均有限且非零。receipt训练耗时3171.95秒（52.87分钟，不含最终保存/重载），峰值allocated38.624GiB/reserved39.248GiB。第20/40步重载参数哈希相等，BF16 logits最大差均为0。

- 第20步adapter tensor SHA：`cb9d97fe71ba68a27d440f158cd9f828a3fcdc90109bef1e40aa69345bf11b39`。
- 第40步adapter tensor SHA：`722018b0ccc6a3edb4413a07a01d98a8f738e369afb8dcf19eb04aaf26c61e0f`。
- 候选权重：远端`/root/gpufree-data/recoveragent_s1_short_20261115_01/runs/short_001/S1_short40/`；中间权重在其`checkpoint-20/`。通过现有`load_base(identity)`与`PeftModel.from_pretrained(base, checkpoint)`加载，原S0目录不变。

训练验收后，最初归档启动器使用系统Python，缺少`hashlib.file_digest`而停止。原失败状态与日志已保存；改用既有Python3.12执行器，先核验两个已保存checkpoint，再接续归档和评价。没有重训、追加更新或重跑已执行评价任务；异常发生时dev模型请求为0。

同一L40S、同一Transformers后端、同一新公开格式，S0与新S1分别执行固定dev24各一次。每题80请求/16384累计生成token/1200秒，上下文65536，单次最多4096输出，temperature0.8/top_p1.0/seed42。保留所有失败及未知，不重试挑分。

|模型|成功|失败|未知|状态|
|---|---:|---:|---:|---|
|S0＋新格式|—|—|—|执行中|
|短40步S1＋新格式|—|—|—|待执行|

历史旧格式S0 9/24、44步S1 6/24、152步S1 4成功/19失败/1未知仅供参考，不作为本轮增量基线。尚未得到候选模型改善结论，S0继续保留。

## 格式分析边界

软件检查发现现有`v2_model._parameter`在Schema同时包含string/number时优先保留字符串。合成的XML `answer`正文`123`被解析为JSON字符串；狭义单计数问题的既有AnswerGuard会拒绝该类型。这是新提示与最终工具参数类型之间的既有解析局限，不应归因于SFT能力。当前两组均保持相同解析器；后续逐题结果将区分类型/单位问题与业务执行错误。没有修改答案、删单位或更改评分。该发现来自合成检查，尚不能声称解释了本轮真实失败。

## 验证与启动

- 针对性测试：`PYTHONPATH=src:. .venv/bin/pytest -q tests/test_v2_s1_short.py tests/test_teacher_answer_format.py tests/test_v2_learning.py tests/test_v2_s1_retrain.py tests/test_v2_s1.py tests/test_v2_training_cpu.py`：68 passed，1 skipped（通用软件环境未安装训练依赖）。
- 完整软件：`PYTHONPATH=src:. .venv/bin/pytest -q`：1100 passed，1 skipped；`.venv/bin/ruff check .`及`.venv/bin/ruff format --check .`通过。测试覆盖实际消息接线、生成任务、快照继承、历史token不变、三类权重、40步参数/20步回调、两模型一致评价请求。它们不是模型效果验证。
- 另在本地固定训练依赖环境运行`OMP_NUM_THREADS=1 PYTHONPATH=src:. .venv-v2/bin/python -m pytest -q tests/test_v2_training_cpu.py`：3 passed。随机小模型CPU测试确认可训练父adapter继续更新及公共loss/重载回归，不是预训练模型效果或真实GRPO阶段。
- 真实启动检查：原S0文件/身份、worker模板与Jinja2、实际NOTICE渲染、BF16 L40S可用；复用同一环境此前9应用119次API/快照验收，未宣称本轮重复执行119次探针。

远端根目录`/root/gpufree-data/recoveragent_s1_short_20261115_01`，部署文件`deployment.json`保存当前实例全部路径和既有截止时间。独立入口依次调用：

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
$GPU_PYTHON scripts/train_v2_s1_short.py baseline --output ../runs/short_001 --deployment ../deployment.json
$GPU_PYTHON scripts/train_v2_s1_short.py evaluation --output ../runs/short_001 --deployment ../deployment.json
```

当前独立控制器已启动，完成后自动封存归档，本地只读守护断点回传并逐文件SHA核验。Teacher采集仍停止，不执行GRPO/CoEvolve。资源沿用原实例24小时截止：北京时间2026-11-15 21:13:36。
