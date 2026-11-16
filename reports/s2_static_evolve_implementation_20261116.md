# S2 Static / Evolve 实现验收

2026-11-16，开发分支 `codex/p0-p2-recovery`，从 `671cb2b2b8511c8ff1015b6df30f64c8c52fbe41` 继续。交付调度、学习衔接、报告与启动命令；本次没有执行9B S2或新的AppWorld任务，不存在新的成功率或学习增益结论。

## 已完成

`scripts/run_v2_s2.py` 提供prepare/run/resume/report/evaluate；独立配置 `configs/v2_s2_static_evolve_20261116.json` 和182文件新锁，不重签历史锁。两模式独立S0起点，5轮×40组×4次，每两组一次更新机会，LR1e-6/KL0.001；仅GRPO，既有S1结果不成为起点。

复用 `v2_episode` / `v2_inference` / `v2_vllm` 的真实执行和采样记录；复用 `v2_grpo` 的TRL loss/update、`v2_training` 的加载/保存/重载；复用 `coevolve_generation` 的模板、探索、Solver、semantic judge、程序验收与新颖性，不复制整套执行框架。新 `s2_*` 模块只负责队列、组级反馈、分支状态、更新衔接、汇总和新run存储。

| 验收点 | 实现与验证范围 |
| --- | --- |
| 跨轮任务采样 | 原始连续随机队列先覆盖100题；生成题每轮限一组、下一轮可再次执行，新题优先、不足补位；Static合成五轮为155原始组+45生成组 |
| 8组选组 | 仅当前窗口最新完整同题组，未知/不一致排除；固定优先级及all_failed保留名额；forgetting仅引用组前成功 |
| 四轨迹context | 输入保留四份公开历史与SHA、策略、结果；复用六字段；仅完整输入实测超窗才摘要，关联消息索引/原记录 |
| 探索与入池 | 两次干净独立探索，合并真实动作引用，最多2候选；仅合并输入超窗回退各1；新题入池后在后续槽真实调度（合成执行器测试） |
| 在线更新 | 两组当前策略轨迹、实际token/logprob；固定S0 reference；保存并恢复optimizer/RNG；非零优势才更新，下一批使用新actor |
| 断点续跑 | 完整组/已提交更新不重复消费，部分派发slot标unknown，剩余slot继续；保留队列、池、窗口和轮次；零更新轮继续 |
| 评价 | 同GPU/vLLM/公开格式、原始初态和固定顺序；S0基线+轮末dev24，最佳成功率同分取更早轮；最终test111仅显式评价入口可用，训练仍拒绝test |
| 过程报告 | 每8组/每轮计数、拒绝原因、原始/初始生成/新生成组奖励、更新贡献、token/耗时、案例来源链、dev曲线与逐题新增成功/退步 |
| 存储 | 新run轮末和最新actor/optimizer保留，退休中间权重明确不可加载；提交后请求原件逐成员SHA压缩，token/logprob不截断；历史不动 |

最多24生成批次、96context、192新增候选；已有9条不占新增额度。每分支800训练slot，评价另597slot，分别保存异常/未知与完整轨迹数量。Teacher有限预算单列且不隐式扩GPU时限。训练/评价阶段由同一控制器分时使用现有GPU。

## 实际验证

```bash
PYTHONPATH=src:. .venv/bin/python -m pytest -q \
  tests/test_s2_experiment.py tests/test_s2_evolution.py tests/test_s2_storage.py

PYTHONPATH=src:. .venv/bin/python -m pytest -q

PYTHONPATH=src:. .venv-v2/bin/python -m pytest -q \
  tests/test_v2_training_cpu.py tests/test_s2_training_cpu.py

.venv/bin/ruff check src scripts tests
.venv/bin/ruff format --check src scripts tests
```

- S2针对性 **27 passed**；包含完整五轮Static零更新承接、Evolve 24窗口/新题实际被后续采样、断点不重发、最佳轮选择和最终评价输入审计。
- 完整 **1136 passed, 2 skipped**；3项已有Pydantic弃用警告。普通软件环境跳过训练依赖模块，另在训练环境实际运行下一项，不把skip称为通过。
- CPU训练 **4 passed**，包含新的微型随机Qwen3.5/LoRA两次真实TRL更新、optimizer状态继承、S0 reference固定、参数变化及checkpoint重载；这不是9B预训练模型实验，也不验证64k显存。
- Ruff check通过，**251文件**format check通过；旧配置/锁原件未改。当前新锁通过，历史入口仍按原锁拒绝被改动源码。
- 私有日志在 `runs/v2_s2_20261116_prepare/`。首次直接调用pytest可执行文件因缺仓库根路径导致8个脚本模块收集错误；用上述明确PYTHONPATH的项目命令重跑后全通过，保留原错误日志，不修改业务代码掩盖收集问题。

## 真实资产与尚未运行部分

已实际读取并校验既有归档：

- 路径：`runs/v2_s1_train100_20261113_01/retrieval/final_1794626440.tar.gz`，1,480,103,860字节。
- SHA-256：`4b6de950134f0b35fb9e71d1cd0acf872ac00c1fe346f5fe4a8b95305ec051cd`。
- 只导入原9条合格生成任务与100条train公开历史，逐项SHA固定于 `configs/v2_s2_inputs_20261116.json`；bundle已生成于 `runs/v2_s2_20261116_prepare/initial_bundle/`，重新加载校验通过。没有把旧9条算作新增任务。
- 共同S0：固定Qwen/Qwen3.5-9B revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`，adapter tensor SHA `5da6444aa979e2ce0957b3bba82bd239ba0f59fad40024a7ea0a35b7f4534966`；原权重只读。

只读SSH确认既有L40S（46068MiB）在线且GPU空闲，数据盘约14GiB可用。本实例原授权截止为2026-11-15 21:13:36（UTC+8），已经过期；程序拒绝过期deadline的无模型检查通过。**正式运行缺新的授权绝对截止时间**，并非SSH不可达。新配置不会自动把旧24小时额度重新开始，不自动租机、充值或重启已停止的补采服务。

新9B参数更新0、Student真实训练轨迹0、新Teacher调用0、新增候选0、新dev/test执行0。尚未验证当前Teacher额度、真实完整64k GRPO显存与吞吐、生成任务的实际贴合程度、两分支五轮曲线与最终效果；这些只能由后续真实运行给出。当前磁盘采用新run有限保留，但完整长轨迹占用仍取决于实跑。

## 启动与续跑

完整部署路径、环境变量、预算和输出说明见 [S2运行说明](../docs/S2_STATIC_EVOLVE.md)。在新源码目录配置私有deployment、迁移已校验bundle并写入实际授权deadline后：

```bash
python scripts/run_v2_s2.py run --mode both --deployment /private/deployment.json --output /private/s2_run
python scripts/run_v2_s2.py resume --mode both --deployment /private/deployment.json --output /private/s2_run
python scripts/run_v2_s2.py report --output /private/s2_run
python scripts/run_v2_s2.py evaluate --deployment /private/deployment.json --output /private/s2_run
```

这里的python须为说明中已配置的CPU控制器环境；训练worker和vLLM使用deployment指定的各自环境。`evaluate`接续最终冻结test，必须先完成两分支五轮dev。当前交付是可运行实现及软件/资产验收，不是已经完成1600条真实训练的实验结果。
