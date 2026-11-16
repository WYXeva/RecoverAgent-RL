# S2 采样并发与时间估计

2026-11-16；原实验源码dbcae9b、基线复用记录0899f5f。该改动仅优化执行调度，原S2实验配置、任务数量、学习率、KL reference、工具协议、随机种子与单题预算不变。

## 已有真实时间依据

- 最早6个完整组共24条：1成功、23有效失败、0基础设施异常；平均单条113.3秒，477次请求、44,742生成token。这只是早期采样统计，不是dev效果。
- 首个真实GRPO更新已完成：98个实际Assistant请求片段，1个有奖励差异的组进入更新，另一组全失败不贡献梯度。训练697.70秒，allocated峰值24.51GiB，梯度范数0.0419719，256个梯度张量；参数变化、保存及重载通过。固定S0 reference SHA保持 `5da6444aa979e2ce0957b3bba82bd239ba0f59fad40024a7ea0a35b7f4534966`，新actor SHA为 `be5271f2a012bc249ee63c3ad4acfe33215d9c7bac117975d91ae9bd4d2150e2`。
- 按113.3秒线性外推，1600条训练轨迹约50.4小时；240条轮末dev加333条最终test约18.0小时，S0 dev24已复用。这约68小时尚未计入训练更新、Teacher出题/验证、加载切换，且早期样本不能代表全部任务难度。串行全程应按数天估计，不能承诺24小时内完成。
- 训练日志提示缺少causal_conv1d优化实现，正在使用PyTorch fallback；尚未安装或宣称该项获得加速。已检查当前Transformers实现和[上游内核说明](https://github.com/Dao-AILab/causal-conv1d)，后续需独立数值/梯度与兼容验证，当前不更换训练依赖。

## 本次改动

新增 `configs/v2_s2_parallel_20261116.json` 与独立184文件源码锁。显式环境变量 `RECOVERAGENT_S2_EXECUTION=v2_s2_parallel_20261116` 选择执行版本，原实验配置与source lock文件保持原样；新源码仍拒绝旧锁。新的执行来源记录与原实验记录分别保存，不改写state binding或历史轨迹。

同组4条按原seed和member目录执行，最多2条并发；先在主线程准备vLLM与tokenizer，HTTP session按线程隔离，结果按member次序汇合后才观察反馈和更新。每条AppWorld仍是独立进程及初态。两分支同样启用；dev/test保持原串行方式。vLLM仍是已验收的0.19.0、双sequence/64k配置，不增加上下文、改变精度或裁剪轨迹。

继续使用原run和原24小时绝对截止，切换时保存完整边界证据并保留旧源码。并发并不保证2倍加速；真实吞吐需用后续组的墙钟和执行回执验证。

## 软件验证

- 针对性：`PYTHONPATH=src:.:tests .venv/bin/python -m pytest -q tests/test_s2_parallel.py tests/test_s2_experiment.py tests/test_s2_storage.py tests/test_v2_s1.py tests/test_v2_learning.py`：73 passed。
- 完整：`PYTHONPATH=src:. .venv/bin/python -m pytest -q`：1140 passed，2 skipped，3项既有Pydantic警告。首次回归发现4个测试仍将旧S2锁当作最新锁，已改为显式选择新执行版本；历史拒绝断言保留。
- `ruff check .` 与 `ruff format --check .` 通过，252个Python文件。
- 新测试验证并发上限2、稳定顺序/seed/策略、更新屏障、新actor接续、已有serial部分组不重复派发、HTTP session隔离及新旧源码锁分离。均为合成测试；不将其称为真实成功率或吞吐提升。

## 实际启用

新源码已在远端独立目录 `source_parallel_001/` 部署并核验；原实验配置SHA仍为 `bda65dbff63da0cd975775c4d46c97c0e3176a202e5d175a8e673e5aa52ed98c`。新源码锁SHA为 `b9bf7aadec6e7c68a3281b63e3b4bf2a8444910efff85eb80f22a94fdd050f90`。15:03:38在第7组完整结束、0活动任务worker时保存原state SHA并停旧控制器；28条为4成功/24有效失败/0未知。15:04:58新控制器接续，启动时再次核验state SHA完全未改，1个optimizer更新与当前actor保持不变。15:07实测两个独立AppWorld worker同时发送请求并收到回复，同一更新后policy、不同seed（20261144/20261145），并发已真实生效。尚未完成受控串/并行吞吐对照，不预报加速倍数。

私有证据：远端run的 `continuations/parallel_001/{safe_boundary,live_probe}.json`、原state备份，以及 `runs/static_evolve_002/executions/v2_s2_parallel_20261116.json`。当前原28条实际平均120.31秒，早期耗时估计仍会随任务复杂度变化。后续组耗时在group.json的execution字段记录；既有原始证据及旧源码目录保持原样。
