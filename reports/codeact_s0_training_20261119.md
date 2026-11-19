# CodeAct 新S0训练（2026-11-19）

本轮由用户在采集完成后明确授权，使用现有L40S 48GB。当前状态：**短/长真实训练与重载检查通过，正式1 epoch运行中，未宣称完成或效果提升。** 训练源码 `fb2d793`，采集完成记录 `8d46738`，分支 `reboot/codeact`。

## 输入与配置

- 官方 `Qwen/Qwen3.5-9B`，revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`，原始权重SHA远端核验通过。新LoRA从基座创建，`parent=None/optimizer_restored=false`；旧SFT/GRPO权重与优化器不进入本轮。
- 独立 `configs/codeact_s0_20261119.json`：non-thinking/64K、BF16、r16/alpha32/dropout0、lr5e-5、AdamW固定lr/weight_decay0、micro1/acc8、1 epoch。实际lr写入每步回执。单轨迹多Assistant目标平均CE，再均匀平均轨迹；工具返回不计目标。
- 数据：252条完整多轮样本、4,421,765输入/371,172目标token，最长61,817。4条超64K成功轨迹仍暂缓；没有截断或混入旧Teacher数据。训练文件SHA `81acb5be1f53c28b105e6234953facbdf44a55af9fd37fbea2af9ab98280438e`。
- 957份传输文件核SHA，256份原始成功来源核SHA/train归属；两条短样本与最长样本远端重新分词，token/labels/目标位置逐项相同。复用torch2.10.0+cu128、transformers5.23.0.dev0、peft0.21.2，以及已验证causal-conv1d1.7.0/FLA0.6.0；实际快速分派通过，未安装新训练栈。

## 已完成的真实检查

| 阶段 | 更新数 | 输入长度 | loss | 梯度norm | 训练计算秒 | 峰值allocated GiB | 重载 |
| --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| 短样本 | 2 | 5,242 / 5,294 | 最后0.263375 | 最后1.083482 | 6.32 | 19.87 | 通过 |
| 最长样本 | 1 | 61,817 | 0.462812 | 0.578611 | 47.42 | 39.76 | 通过 |

均为真实9B前后向，有限非零梯度，参数哈希变化并保存/重载一致，0 OOM。训练秒数不含模型加载、权重SHA扫描及重载校验耗时。两项检查各自加载全新基座；正式阶段再次全新加载，不把这3步记入正式32步。

正式第1/32步已完成：8条完整样本、157,193输入token，loss0.394276，裁剪前梯度norm0.507634，实际lr5e-5，87.81秒，0 OOM。其余步骤继续运行；这些训练损失不是验证成绩。

## OOM规则与验证

用户要求遇到OOM跳过样本，已实际接入前后向路径。每条OOM完整记录在 `oom_skips/`，清空该累积批全部梯度，然后按剩余样本数重新归一化、重算整个幸存批；没有带着半批梯度更新。最长烟测按降序尝试，首次适合显存的完整样本更新/重载通过后停止烟测；已确认OOM样本列入 `sample_selection.json`，本批正式训练不重试。正式阶段OOM同样跳过完整样本，报告实际参与样本、目标数及更新数。

样本之外的模型加载或optimizer全局资源故障不能归因到某条样本，仍保留错误并停止；非有限梯度、参数未变化、重载失败也不因OOM策略放行。原采集配置/数据未修改，新配置显式启用 `training.skip_oom=true`。

CPU测试实际执行：`tests/test_codeact_reboot.py tests/test_codeact_collection_controls.py`，**29通过（6.22秒）**，Ruff check/format通过。合成前向与反向OOM都验证：先前梯度及OOM中途梯度被清除，剩余样本的loss、梯度与更新等于独立参考；全批OOM不更新；真实非有限错误不吞。GPU本次未触发OOM，不能用合成注入代替真实OOM验证。

## 运行位置、命令与剩余项

远端 `/root/gpufree-data/recoveragent_codeact_s0_20261119_02/`，训练监督PID684、worker706；本地部署及进度在 `runs/codeact_s0_20261119_02/`。第一次01启动在收到OOM指示后、正式0步时停止，日志与原件保留；02为独立目录，未覆盖01。

当前已运行，不要重复启动。复现命令（新输出目录）为：

```bash
cd /root/gpufree-data/recoveragent_codeact_s0_20261119_02/source
HF_HOME=/root/gpufree-data/recoveragent_s1_retrain_20261114_01/hf_cache \
HF_HUB_OFFLINE=1 OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false \
PYTHONPATH=/root/gpufree-data/recoveragent_append_probe_20261118_01/kernels:$PWD/src \
/root/gpufree-data/recoveragent_s2_20261116_01/training_env/bin/python \
  -m recoveragent.codeact.cli train \
  --config configs/codeact_s0_20261119.json \
  --dataset ../data --output ../training_reproduction
```

正式输出 `training/S0_base/adapter/`，`updates/` 每步保存实际lr、loss、有限梯度、样本及耗时，结束时保存optimizer、参数SHA、峰值显存和重载回执。控制器完成或失败均打包原始训练产物；本地只读备份进程每分钟查进度，结束后下载并核验归档SHA。

当前未完成正式训练，尚无最终S0成绩；未启动dev/test或下一阶段学习。GPU租赁实际账单未取得。下一项是完成本次1 epoch并核对真实更新/遗漏样本/可重载权重，再安排评价。
