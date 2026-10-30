# M1推理耗时诊断、合并LoRA候选与L40 48GB可行性

2026-10-30：仅本地软件准备与CPU验证。没有SSH、远端计算、开机、租机、续费或新增C244任务。M1训练仍PASS；C244保持46/244尝试后STOPPED，未知4项，不给完整TGC。

## 有证据的耗时与限制

正式训练约2小时9分；C244启动至最终停止约2小时41分，后者含交接、诊断和等待，不等于纯GPU计算或账单时长。最终串行任务25次请求、24份回复；最后输入17994 token。已保存回执例为31 token/3.38秒、161 token/12.22秒，含prefill，不能报告为纯decode速度。

当前`full732_m1_backend.py`使用Transformers 4.57.6 + PEFT 0.18.0的单请求`generate`。LoRA未合并，每个生成步仍执行额外低秩投影；每轮工具交互重新prefill完整输入，不跨请求复用KV缓存。原代码已经`use_cache=True`，因此回复内部有KV复用；该Transformers版本也已自动设置`logits_to_keep=1`，这两点不是本轮新优化。没有GPU profiler细分记录，不能给上述开销各自占比。

之前双路是同一GPU上两个模型进程，不是一个引擎连续批处理；未测得可靠加速比。串行与双路均触发旧客户端HTTP180秒超时，后一次总任务墙钟仅326秒，尚未耗尽600秒会话预算。扩大HTTP等待只是时限修正，不会提高tokens/s；将来接入新的任务入口需使用剩余会话deadline、服务端取消和有界退出，不能改成无限等待。当前旧HTTP、协议、源码锁与成绩保持原样。

## 已实现的独立候选

- 新模块`src/recoveragent/m1_inference_probe.py`：核验正式M1 checkpoint及504个LoRA张量来源，保持固定基座revision、BF16、SDPA和non-thinking模板，比较原PEFT模型与`merge_and_unload(safe_merge=True)`后的内存模型。不保存合并模型、不写回原checkpoint，结束再次核验原文件。
- 合并去掉推理时LoRA额外分支；BF16舍入可能变化，因此是独立运行候选，不能冒称逐bit或随机采样序列等价。少量合成输入的数值一致更不等于完整C244等价；本轮配置含三种合成输入，任何微测试通过均不自动启用正式评估。
- 公开自制重复文本经固定tokenizer实际编码为2073、16409、32633输入token，最后一种加128输出为32761，接近32768上限；没有读C244内容或历史95。每种长度合并前后各3次，预热另计，记录完整生成ID、耗时、prefill单独探针、峰值allocated/reserved、版本与生成配置。为了公平测速，**只在合成探针**固定输出128 token；不得将该设置用于真实任务或修改真实Assistant目标。
- 默认命令只打印计划。显式GPU探针由父进程监督，最多300秒（包括模型加载），超时/中断终止自己的子进程组并保留日志，不自动重试或执行C244。该限制不会自动关掉云服务器，实例费用仍需外部停止机制。
- 数值差异阈值与首个greedy token检查仅为探针门槛；失败直接停止。中位速度必须两边同输入长度/输出长度/重复数才能比较；没有提速如实标记，所有结果`production_activation=false`。

默认只检查源码并显示计划（本轮已执行，不加载权重）：

```bash
PYTHONPATH=src .venv/bin/python -m recoveragent.m1_inference_probe
```

将来在用户确认可用设备后执行的GPU命令模板（**本轮未执行**，使用具备固定依赖的独立推理环境）：

```bash
PYTHONPATH=src .venv-sft/bin/python -m recoveragent.m1_inference_probe \
  --execute --adapter /path/to/verified/run_001/formal \
  --output /path/to/new/synthetic_probe_run
```

这只是短探针，未提供自动启动服务器或恢复C244的路径。正式使用还需完整请求/工具协议和近32K实测验收；原46项、未知及历史评估实现不能静默改写。

## 验证结果

- 全套软件602 passed in 10.45s；增加近32K合成长度后15项针对性测试再次通过，Ruff通过。
- `scripts/check_m1_merge_cpu.py`在CPU上使用真实Transformers/PEFT构建微型随机Qwen3，七个投影、r16/alpha32、非零LoRA B，确证adapter有实际作用；合并前后FP32最大logits差1.49e-7、BF16差0.0029296875，临时adapter文件SHA不变。CUDA未初始化。这不是4B M1实际验收，也不是GPU性能证据。
- 固定模板与三种输入ID独立CPU重分词通过。证据`reports/m1_inference_merge_cpu_20261030.json`、`reports/m1_inference_probe_tokenizer_20261030.json`。
- 新来源锁：`33c401e1c86e86fc0997adb234a4e8bcc873dcd82837c97cd93383c97f998818`。原训练/C244源码和历史锁均未修改。
- M1 GPU合并数值验收、速度、L40实际显存、完整任务时间与成本：**NOT_RUN/UNKNOWN**。不宣称已加速若干倍。

## L40 48GB：显存可行，性价比看实测

NVIDIA官方L40规格为48GB GDDR6 ECC，支持BF16，带宽864 GB/s；原A100 80GB SXM带宽2039 GB/s。[L40规格](https://images.nvidia.com/content/Solutions/data-center/vgpu-L40-datasheet.pdf)、[A100规格](https://www.nvidia.com/en-us/data-center/a100/)。不能从带宽比直接推出任务耗时比。

本地34项C244快照中，单个推理进程的PyTorch allocated峰值最高16631590912 bytes，即15.4894 GiB；这是已观察子集，不是244项的全局最坏情况，也不等于设备总占用。正式训练allocated23.4 GiB与reserved33.94 GiB是另外两项训练指标，不是推理必需容量。

据模型规模与已有推理证据，**完整独占L40 48GB是合理的M1 BF16推理候选，单进程容量有较大余量**。先单进程、保留原精度和32768窗口，实测接近32K输入/显存；不要沿用同卡两个独立模型进程来追求表面并行。无需为搬到48GB卡预先量化或截断。实际L40运行尚未验证，不承诺并发数、OOM绝不发生或确切吞吐。

成本应按`每任务成本 = 每小时租价 × 平均任务秒数 / 3600`比较，并另计部署/空闲与其他收费。举例仅为算术：若L40时租是A100的一半、实测任务耗时是1.4倍，则任务GPU费用约为70%；实际租价和速度未提供。换卡不会自动消除现有LoRA/长历史/调度开销。

进一步高吞吐方向是一个vLLM引擎支持多个隔离AppWorld worker，使用连续批处理及精确前缀缓存。前缀缓存只减少相同前缀的prefill，动态工具schema改变前缀时收益受限，也不减少输出decode成本；更换采样后端需独立运行身份与协议验收，不能静默接入旧结果。本轮未实现或部署vLLM。[vLLM前缀缓存文档](https://docs.vllm.ai/en/v0.11.0/features/automatic_prefix_caching.html)。LoRA合并依据见[PEFT官方说明](https://huggingface.co/docs/peft/main/en/conceptual_guides/lora)。
