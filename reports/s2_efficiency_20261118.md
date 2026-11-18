# GRPO 前缀合并与工具发现精简（2026-11-18）

基线 `31bbbf7`，分支 `codex/p0-p2-recovery`。本轮只完成实现、本地验证与后续运行准备。正式 GRPO 继续暂停；未连接远端、未启动 GPU/Teacher/Student、未做 dev/test。旧14＋新1次更新、两个未消费有效组及完整备份保持原件。

## 实际修改

1. `grpo_segments.py` / `v2_grpo.py`：同一 episode 内，仅当前一次完整输入＋输出与下一次输入的 token 前缀完全相等、采样定义一致时合并。Schema/历史改写立即分段。新增的工具/上下文 token 只参与前向，不参与 loss；原 Assistant token 和行为 logprob 原样保留。沿用 TRL clipped GRPO、每条轨迹目标 token 平均及四成员/组归一化。LM head 只计算目标位置，每条链最多4096个目标 token，超过则另起链、不截断。回执保存原请求/响应 SHA、实际序列工作量、目标数及 CPU 卸载段数。
2. `tool_discovery.py` / Adapter / Student / Teacher / 快照入口：独立 `public_tool_discovery_compact_v1` 增加按应用、英文关键词和分页查找公开 API；查找不执行业务、不自动解锁工具，仍通过 `show_api_doc` 动态发现。完整应用目录入口保留。参数 Schema 继续完整展示并严格校验；工具文档去掉其重复参数字段/相同描述，保留返回结构、非空额外约束和未知说明。无法识别的文档原样返回，业务观察不精简。原始/可见文档与 SHA 私有留存。只在新公开任务添加说明，不修改 execution_v6。
3. `grpo_kernels.py` 与安装/检查脚本：根据已固定 Transformers 实际绑定的 callable 检查 causal-conv1d 和 FLA，避免“包存在但导入失败”被误报为加速。新运行在正式更新前拒绝静默使用参考实现。独立 overlay 安装 `causal-conv1d==1.7.0`，保留原训练环境和 FLA 0.6.0；CUDA 烟测入口只使用128 token合成张量验证 BF16 前反向与有限非零梯度，不加载9B/不做 optimizer update。
4. CPU 激活卸载支持显式 `cpu_above_tokens`。新配置暂设0，即仍对所有非空序列卸载；实际 GPU 测量后才能确定更高阈值。本轮没有凭空认定48GB卡能承载某个免卸载长度。

独立运行配置 `configs/v2_s2_efficiency_20261118.json` 与252文件新源码锁覆盖上述入口、依赖及执行模块。Teacher探索/Solver与Student采样/评价共享该工具契约；普通历史Teacher采集不切换。快照恢复重建记录版本并核验 catalogue/search Schema/说明哈希，保留原状态与历史严格检查。实验/评价绑定加入工具发现身份；旧状态不能仅换环境变量就续接，也不改写未消费轨迹。新配置是准备材料，不续期原墙钟/费用授权。

## 已完成的验证与实际证据

- CPU 微型随机 Qwen3.5＋LoRA，通过真实 TRL loss/backward/optimizer 对照：合并与不合并的 loss、梯度、更新后参数在声明的浮点容差内一致；覆盖不卸载、全部卸载、按长度卸载。另有 beta=0/非零、optimizer恢复、保存重载回归。不是9B训练或学习效果实验。
- 合成 Adapter/Student 测试覆盖查找→发现→真实提交链、模型可见 Schema 与 dispatch 相同、合法 null、严格参数、原始文档留证、业务返回不改写、同版本快照及哈希篡改拒绝。Teacher配置和新旧源码锁也有实际测试。
- 只读复用首个真实更新的8条轨迹：261次请求→124条精确前缀链；处理序列 token **4,905,886→2,248,924（减少54.16%）**；**20,063个 Assistant 目标及对应行为logprob完全保留**。原输入/输出未改写，未将历史样本冒充新协议采样。
- 上述记录中的33份不同 API 文档均可投影：40,440→15,505字符（减少61.66%）。这是文档字符数，不是整个上下文的 token 降幅；不能与前缀合并百分比直接相加。
- 历史首次更新耗时3305.86秒，日志明确报告缺少 causal_conv1d、使用慢速参考实现。本机检查结果为 CPU PyTorch，causal-conv1d/FLA均未安装；它只能验证软件路径。**远端安装、CUDA烟测、9B合并训练峰值显存及实际提速尚未实测，不能承诺55分钟会降至某个时间。**

本地测试命令（网络/任务环境为夹具，微型模型在CPU）：

```bash
PYTHONPATH=src:tests .venv-v2/bin/pytest -q \
 tests/test_grpo_efficiency.py tests/test_tool_discovery_compact.py \
 tests/test_s2_training_cpu.py tests/test_s2_segment.py tests/test_s2_experiment.py \
 tests/test_completion_submission_v2.py tests/test_schema_compatibility.py \
 tests/test_nullable_recovery.py tests/test_student_batch.py tests/test_harness.py \
 tests/test_harness_refactor.py tests/test_tool_modes.py tests/test_s2_goal_integration.py
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
git diff --check
```

上述相关测试最终 **221 passed（21.65秒）**；Ruff check通过、300个Python文件format check通过、diff check通过。源码锁含252文件；历史锁保持原样并继续拒绝本工作区。私有证据 `runs/s2_efficiency_20261118/`，历史完整备份仍在 `runs/s2_effective_segment_20261118_01/`，未提交原始轨迹、权重或凭据。

## 已准备的命令

本地 CPU 检查，不需要开机：

```bash
RECOVERAGENT_S2_EXECUTION=v2_s2_efficiency_20261118 PYTHONPATH=src \
 .venv-v2/bin/python -c 'from recoveragent.v2_runtime import verify_lock,S2_RUNTIME; print(verify_lock(S2_RUNTIME))'
PYTHONPATH=src .venv-v2/bin/python scripts/probe_grpo_efficiency.py \
 --episode /private/episode_directory --output /private/new_prefix_audit.json
```

`--episode` 指含 `session/requests/*.request.json` 的未清理目录，可重复传入；已归档请求需在独立临时目录只读解包。审计无 GPU、无参数更新。

后续在已有GPU实例上安装/检查（本轮未执行）：

```bash
# 在新源码目录下；使用原训练Python，新增独立overlay，不更新旧site-packages。
TRAIN_PY=/root/gpufree-data/recoveragent_s2_20261116_01/training_env/bin/python
KERNEL_DIR=/root/gpufree-data/recoveragent_grpo_kernels_20261118
"$TRAIN_PY" scripts/install_grpo_kernels.py --target "$KERNEL_DIR"
PYTHONPATH="$KERNEL_DIR:src" "$TRAIN_PY" scripts/probe_grpo_kernels.py \
 --cuda-smoke --output /private/new_kernel_probe.json
```

安装需要可用CUDA编译环境或兼容预编译扩展；失败保留日志，不能改为静默慢路径。后续训练worker必须使用同一overlay；其 `kernels.json` 记录实际分派与版本。既有 `run_v2_s2.py` 的正式配置/worker已接入优化，无第二套训练框架。

恢复正式训练前，应先在独立目录用已保存短片段测量9B前反向、峰值显存和耗时，再决定卸载阈值；工具新输入需有少量新采样检查，并显式建立新协议运行身份。不得直接对旧暂停目录执行新协议 `resume`，不得重签旧锁或重编码待消费请求。旧权重/optimizer/采样状态均保留；本轮没有迁移或消费它们。
