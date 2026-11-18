# 追加式 Student 输入与多回复 GRPO

基于当前分支 `f1aff70` 继续实现。正式 GRPO 保持暂停；本轮只有本地软件、CPU 随机小模型和归档检查，未连接远端、启动 GPU、调用 Teacher、训练 9B 或评测 dev/test。

## 实际改动

- 新协议 `student_append_tokens_v1`，独立配置 `v2_s2_append_20261118`，258 文件源码锁。`student_tokens.py` 构造固定 `prompt_tools` 工具头；`student.py` 在成功 `show_api_doc` 的**当次工具观察**中加入实际兼容 Schema 的 `tool_definitions`，保留用途、必填、nullable、约束、默认值和返回说明，重复参数说明沿用现有投影去重。新 API 从下一回复开始可用。
- 执行继续复用 AppWorld 公开 catalogue、已有 `session.tools` 发现记录、`call_code` 和 Harness。**没有新增 callable_tools/注册表、通用 call_api 或 CodeAct**。未发现 API 仍拒绝；同批发现再调用仍整批拒绝。`request.tools` 是执行/解析的可见工具，不再参与新协议后续工具头渲染。
- 初始模型输入构造一次；每轮原始生成 IDs 原样追加，随后只编码新的工具返回/纠错和 native generation boundary。旧 Assistant 不重渲染，也不 decode→encode 替换。真实 EOS 属于目标；缺 EOS 时程序补上的关闭边界单独标记、不计 loss。错误 XML、被拒绝调用、普通无调用回复都保存真实采样记录。
- 结构化 messages 留给执行、评分、审查。请求/响应保存协议、固定头、实际 IDs、行为 logprob、程序片段及 SHA 链。vLLM、Transformers 后备和 RPC 共用校验：后端重算初始输入与新观察编码作核验，并对照相邻回执的真实 Y；不重建历史 Assistant。训练读取同样核验 `P[t+1] = P[t] + Y[t] + 程序追加内容`。新协议不能混入旧 episode；旧 SFT 导出明确拒绝静默重渲染它。
- 复用已有 `grpo_segments.coalesce`、TRL GRPO 和目标位置 logits/128-token 词表分块。每条轨迹内的兼容回复合并前向/反向，默认每段最多4096个目标 token；达到上限另起带完整前缀的段，**不删目标、不截断上下文、不串联独立轨迹**。完整轨迹目标分母、组优势、clip、权重及一次 optimizer step 保持原规则。逐回复路径继续可选。
- 正式 S2 配置和 episode 已接通新编码，状态/评价绑定包含编码身份。Teacher 仍使用服务商原生工具协议，生成/Solver/Judge 不改为 Student 的 token 编码。公开 API 语义与权限相同，序列化方式不同。

实际训练设置仍为当前9B、64K、BF16、LoRA r16/alpha32/dropout0、lr=1e-5、beta=0、temperature0.8/top_p1、四轨迹组；不重新 Base-SFT。激活仍全部 CPU 卸载，避免用未测显存推断擅自改变策略。

## 已运行的验证

相关 **240 tests passed，22.26秒**；另使用备份真实9B tokenizer运行9项新协议测试全部通过（7.12秒）。Ruff check、305文件 format check、diff check 通过。覆盖执行器可见性/同批拒绝、参数 Schema、数字/JSON/null、错误回复、真实前缀、回执篡改拒绝、两种后端请求、旧编码读取、拒绝旧导出重写、训练与恢复回归、同协议绑定。小模型为随机初始化 Qwen3.5 混合 linear/full attention + LoRA，真实 PyTorch/TRL 前反向，非预训练9B效果实验。

| 检查材料 | 逐回复段数 → 合并段数 | 累计处理序列 token | Assistant 目标 token |
|---|---:|---:|---:|
| 真实9B tokenizer，合成 API/回复的7轮链 | 7 → 1 | 10,286 → 1,937 | 331 → 331 |
| 同权重CPU微型模型、4轨迹，每条含一个不兼容分支 | 12 → 8 | 68 → 52 | 24 → 24 |
| 暂停归档的旧协议8轨迹，原始保存 IDs | 261 → 124 | 4,905,886 → 2,248,924 | 20,063 → 20,063 |

第一行6处相邻前缀全部相同，包含两次发现、批量调用、拒绝、无EOS及错误反馈；每个目标与原 behavior logprob 按顺序逐个相等。使用备份 actor 自带 tokenizer，只运行编码/解析，未加载模型推理。测试中的 native template fixture 原样来自该公开模型模板（SHA `a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715`）。

CPU 对照分别使用不卸载、全部CPU卸载、阈值卸载；三种设置的数值一致：

- loss 绝对差：`2.9802322387695312e-08`。
- **全局裁剪前**梯度最大绝对差：`7.450580596923828e-09`，L2相对差：`6.26758999881772e-07`；原梯度L2约`0.02353743`。
- 更新后参数对照也通过；另外验证了全同奖励诊断只反向、不调用optimizer、不改权重。该诊断的零梯度不能作为非零学习信号证据。

历史253处相邻请求中：137处本来就是精确前缀；96处工具表变化且前缀不兼容；20处工具表未变仍不兼容。历史system+tools头独立编码估计累计838,786 token（仅构成估计，不是重写训练输入）。旧轨迹未修改，未将其旧logprob套到新提示。上表54.16%的历史序列量减少是CPU统计，**不是实测GPU加速比**。

私有结果：`runs/s2_append_20261118/` 的 `final_tests.txt`、`native_tokenizer_audit.json`、`numeric_all.*.json`、`historical_audit.json`、`local_kernels.json`、`prepared_probe/plan.json`。本地已对备份15步actor及optimizer运行真正的 `prepare` 并核验原回执/文件SHA。

复现相关测试：

```bash
PYTHONPATH=src:. .venv-v2/bin/python -m pytest -q \
 tests/test_student_append_tokens.py tests/test_append_probe.py \
 tests/test_grpo_efficiency.py tests/test_s2_segment.py tests/test_s2_training_cpu.py \
 tests/test_v2_training_cpu.py tests/test_tool_discovery_compact.py \
 tests/test_student_backend.py tests/test_student_batch.py tests/test_student_v6.py \
 tests/test_schema_compatibility.py tests/test_completion_submission_v2.py \
 tests/test_harness.py tests/test_harness_refactor.py tests/test_harness_review.py \
 tests/test_s2_experiment.py
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
```

## 后续有限 GPU 验证入口（本轮未运行）

`scripts/probe_s2_append.py` 薄包装正式 BackendRuntime/vLLM/AppWorld rollout 和现有 GRPO trainer。固定 train100 首题 `32616b5_2` × 种子 `[42,142,242,342]`，同初态、同15步actor，最多4条完整轨迹，不按结果补题/重试；原始题官方评分，0 Teacher/Judge调用。单题仍80请求/16384生成token/1200秒/64K。采样与比较各至多2小时，并要求显式未来截止时间；当前暂停授权不会自动转成新GPU作业。

在新源码目录内运行，`DEPLOYMENT` 指向现有私有部署文件，保留 `model_snapshot`、`s0`、`appworld_root`、`worker_python`、`vllm_python`、`training_python` 等实际路径；入口不租机、不续费。示例：

```bash
TRAIN_PY=/root/gpufree-data/recoveragent_s2_20261116_01/training_env/bin/python
PARENT=/root/gpufree-data/recoveragent_s2_effective_segment_20261118_01/run/evolve/updates/001/attempt_1795000273069974960
DEPLOYMENT=/root/gpufree-data/recoveragent_s2_effective_segment_20261118_01/deployment.json
PROBE=/root/gpufree-data/recoveragent_append_probe_001
KERNEL_DIR=/root/gpufree-data/recoveragent_grpo_kernels_20261118

# 独立内核修复/检查；不覆写原环境，不运行9B更新。
"$TRAIN_PY" scripts/install_grpo_kernels.py --target "$KERNEL_DIR"
PYTHONPATH="$KERNEL_DIR:src:." "$TRAIN_PY" scripts/probe_grpo_kernels.py \
 --cuda-smoke --output /root/gpufree-data/append_kernel_probe_001.json

export PYTHONPATH="$KERNEL_DIR:src:."
"$TRAIN_PY" scripts/probe_s2_append.py prepare --output "$PROBE" \
 --actor "$PARENT/actor" --optimizer "$PARENT/optimizer.pt"

# 两个变量由后续获准的实际截止时间填入 Unix 秒；不复用过期授权。
: "${APPEND_SAMPLE_DEADLINE:?填入已授权采样截止时间}"
"$TRAIN_PY" scripts/probe_s2_append.py sample --output "$PROBE" \
 --deployment "$DEPLOYMENT" --deadline-epoch "$APPEND_SAMPLE_DEADLINE"
: "${APPEND_COMPARE_DEADLINE:?填入已授权对照截止时间}"
"$TRAIN_PY" scripts/probe_s2_append.py compare --output "$PROBE" \
 --deployment "$DEPLOYMENT" --deadline-epoch "$APPEND_COMPARE_DEADLINE"
```

`sample` 退出时关闭自己启动的服务，保留每次请求、响应、结构化历史、真实token、官方成绩及组反馈；不更新。`compare` 用同一批新协议完整轨迹，两个独立进程各从同一权重重新加载，先核对采样actor/logprob定义，再分别逐回复/合并前反向，保存**裁剪前**梯度、loss、序列/目标量、概率比、wall time和显存峰值。0 optimizer steps，原optimizer只核验、不消费。无效组明确拒绝比较；全同奖励允许计算成本诊断，但报告零优势，不伪造奖励。结果在 `comparison/comparison.json`；两侧均使用同一内核/卸载设置，内核修复不混入加速比。

本机仅CPU Torch，实际检查为 causal_conv1d/FLA fallback；暂停训练日志已记录远端缺 causal_conv1d。复用独立overlay安装与CUDA检查（causal-conv1d1.7.0，保留既有FLA0.6.0），未整体升级训练栈。本轮未安装到远端。9B新协议是否能可靠调用新增API、9B数值误差、速度和峰值显存均**待验证**；此前3305.86秒/38.787GiB allocated是旧更新，不作为新路径测量。

## 暂停状态与衔接

有效父actor SHA `114dbcb4e28a763674fcbda8d1e80055eb918edf6b90cd60f3642fbb503484ff`；optimizer SHA `47698eea5b6b79e6e2aa070fbb11f103f1c40b835746cae4ffa3fa7eca09b206`。旧14＋新1＝15次更新，未消费group IDs 47、49和全部原始轨迹仍在原暂停归档。中止更新002不用。新配置拒绝混用旧实验绑定；旧锁未重签，旧冻结运行需对应旧checkout。短验证完全独立，不消费这两个组，也不自动启动长训/评测/Teacher。

后续分别回答“同一新轨迹的训练计算是否等价/更快”和“现有模型在新输入协议下是否还能正确执行”，不将CPU等价写成任务成功率提升。
