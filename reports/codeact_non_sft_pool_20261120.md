# 取消采样组上限与SFT原题排除

2026-11-20，延续现有CodeAct GRPO＋CoEvolve；不重置模型或实验计数。

用户取消200采样组上限，允许使用其他train任务，随后明确SFT用过的原题不再使用，
且不能把原始题排在生成题之前。实现保持原始/生成槽交替，未采样优先只作用于原始槽。
生成题仍优先未训练的新题、每40采样组窗口限一次、后续窗口可复用；不足时原始题补位。

## 数据范围

新S0正式32份更新回执记录252个不同任务，各呈现一次，与训练文件的任务集合一致。
训练文件SHA为 `81acb5be1f53c28b105e6234953facbdf44a55af9fd37fbea2af9ab98280438e`。
独立配置保存252个排除ID、S0回执摘要SHA、32份更新回执集合SHA和既定scenario划分SHA。
4条超64K而未参加SFT的示范不计入实际SFT排除集合；dev/test仍不进入训练。

- train510扣除252个实际SFT任务，剩258个原始任务。
- 原train100保留66题、排除34题；加入192题。
- 切换时此前实际采样过96个不同原题，258题中尚未采样194题。
- 排除针对相同原题ID；新生成目标有独立身份，不因共用来源环境误当作SFT原题。
- 已完成更新/原始轨迹不修改。未更新的SFT原题组可另记retired，保留原奖励，不进入新更新；本次切换没有此类待消费组。

## 实现与预算

`experiment.py`支持`max_groups=null`，采样循环和每8组反馈不再受200阻断；
新配置同步取消旧24批/96context/192候选计数上限，单批最多4context/8候选不变。
原Teacher4096请求、250万输出token、公开价100美元估算上限及时间截止继续累计执行。
Student单题预算、4路故障降2路、9B、64K、LoRA、lr1e-5/beta0和奖励协议不变。
仍以100次有效更新为五轮目标；每20更新dev24；绝对截止保持11月21日12:52:38。

队列显式迁移保留已消费前缀、游标、窗口/周期和生成题历史；过滤未消费SFT原题，
加入新合规题并在原始槽内优先未采样题，保存新的随机状态。正常续跑不再次迁移或打乱。
采样入口拒绝不在新原始池的原题；新启动的反馈窗口过滤已排除原题，生成题反馈仍可使用。
历史未启动Teacher请求若包含排除原题则另记退出，不修改旧请求；已经完成的Teacher证据保留。
新配置和源码锁均独立，旧锁未重签。

## 验证与实际接续

执行：

```bash
python -m pytest -q tests/test_codeact_train_pool.py tests/test_codeact_schedule.py \
  tests/test_codeact_online.py tests/test_codeact_parallel.py tests/test_codeact_sampling_fallback.py
ruff check src/recoveragent/codeact/experiment.py src/recoveragent/s2_schedule.py \
  tests/test_codeact_online.py tests/test_codeact_train_pool.py
ruff format --check src/recoveragent/codeact/experiment.py src/recoveragent/s2_schedule.py \
  tests/test_codeact_online.py tests/test_codeact_train_pool.py
```

67项CPU测试通过（33.34秒），Ruff及diff通过。控制器模拟211组、104次更新，确认跨越200组、
反馈继续、有效组恰好消费一次、更新上限停止和同协议续跑；其他测试覆盖队列迁移、交替槽、
SFT排除、生成新目标保留及旧源码锁拒绝。这些模拟未调用真实模型。

远端11目录从10接续181组/48次真实更新、146道生成题、actor/optimizer和未消费组`[178]`；
队列游标与生成题历史/限频核验不变。切换后第182组是生成题，说明没有先运行原始题。
私有 `handoff.json`、`pool_verification.json` 保存来源状态/绑定、任务范围和相同deadline的核验。
接续组实际阶段耗时单独保存，控制器补写汇总耗时不用于速度对比。
退出回传监护覆盖01–11及指定40步保留模型。

此刻尚未实际越过200采样组，且没有新增dev结论。此前已完成的训练包含SFT原题，
本次是48步权重上的任务池调整，不能当作从S0起全程不重用SFT任务的独立实验。
下一次评测仍在60更新；继续命令见README，运行中不重复启动。
