# 新 CodeAct S0：一轮 GRPO＋CoEvolve

本轮用户批准“先跑1轮，最多24小时”，直接从新CodeAct S0开始，暂跳过Recovery-SFT。
当前为已启动的训练实验，不能写成整轮完成或学习效果提升。

## 起点与预算

- 新S0来自252条全新完整Teacher轨迹、32次Base-SFT更新。官方Qwen3.5-9B revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`；adapter参数SHA `c6eef44984ba5da42b7d2e58f5852bbee7d04b65905b26e5dff6c6a3794ff6f7`。
- 本次GRPO optimizer从0开始，无旧接口actor/optimizer/示范/生成池。train100只复用冻结的任务ID，仍来自既定train510场景划分。dev24旧轨迹不回流。
- 最多40组×4次、20次有效更新、24小时。lr1e-5、beta0、LoRA r16/alpha32/dropout0；64K、temperature0.8/top_p1、每题80请求/32768输出/1200秒。有效成败混合组积累2组更新一次，期间actor不变；尾批可单组。OOM跳过整批，保留上一权重与原始轨迹。
- CoEvolve每8个实际组处理一次，最多4批、16context、32候选；新生成池空起步。至多一次必要探索，三字段候选、简单去重、真实CodeAct Solver、共享Flash thinking/high/8192 Judge；Student Judge引用同题同初态Solver。未知评分整组不更新、不当业务失败。
- Teacher有限预留：4096请求/250万输出token/约42.95亿输入token/100美元公开价估算上限，均在同一24小时内。实际套餐费用与估算分开，没有购买或续费。原始任务官方验收，生成题LLM验收，不跑Static/dev/test。

## 实现与验证

新增 `codeact/experiment.py`、`online.py`、`evolution.py`；只复用独立的TaskQueue、组反馈选择、轻量出题提示、shared Judge及TRL GRPO损失，没有启动旧S2整体runner。
`episode.py`增加干净初态身份和生成题公开指令路径；原始题仍官方评分，生成题不误用来源原题的验收。
`inference.py`记录实际vLLM token/logprob/actor身份；训练读取校验实际发出请求和返回，不由重渲染内容伪造behavior。
`v2_grpo.py`加入可选segment loader及目标位置分块LM-head；兼容前缀才合并，完整Assistant目标不删减。

CPU命令：

```bash
/home/eva/project/RecoverAgent-RL/.venv-v2/bin/python -m pytest -q -s \
  tests/test_codeact_online.py tests/test_codeact_reboot.py \
  tests/test_codeact_collection_controls.py tests/test_evaluation_classification.py \
  tests/test_coevolve_light_llm.py tests/test_coevolve_generation_focus.py \
  -k 'not formal_limits_use_lightweight_settings and not real_episode_route_keeps_official_original_and_llm_generated'
```

**相关测试合计97项通过**：主回归94通过/3排除，随后补充的原始/生成episode路由和新源码锁3项通过。新增CPU测试串起模拟准入、同一Judge奖励、有效/无效组过滤、当前actor更新、生成题入池采样和同协议续跑。真实TeacherPolicy请求构造在网络处模拟，核对thinking/high/8192与reasoning保留；不冒充真实模型调用。

小Qwen/LoRA实算：逐回复8片段→合并4片段；累计输入48→32 token，16目标token保持不变。
loss绝对差 `1.4901161193847656e-08`；裁剪前梯度最大绝对差 `7.450580596923828e-09`，相对L2差 `5.367338049887645e-07`。
这是CPU合成数值对照，不是9B GPU加速比。

额外运行旧共享测试时，3项未通过：1项旧S2源码锁明确拒绝当前源码；2项旧FC模拟Session缺少既有token_history字段。保留历史锁与旧测试，没有重签旧锁或把这些结果写成全量通过。本次新CodeAct相关65项均通过，修改范围Ruff检查通过。

## 真实运行

- 现有L40S，独立远端 `/root/gpufree-data/recoveragent_codeact_evolve_20261119_01/`；本地 `runs/codeact_evolve_20261119_01/`。
- 19:52:38正式开始，固定截止11月20日19:52:38（北京时间）。前置核对官方权重、新S0、100题train归属、AppWorld环境、Teacher凭据和匹配tokenizer通过，前置检查没有模型调用。
- 实际快速内核为causal-conv1d1.7.0和FLA0.6.0，Torch2.10/Transformers5.23.0.dev0；没有升级训练栈。
- vLLM0.19已加载新S0并开始首组真实执行，保存processed行为概率。首次真实更新前将比较同actor、同token、同temperature的训练端logprob，随后核验有限非零梯度、参数变化与重载。
- 本地只读备份进程每分钟保存进度；作业退出后下载完整私有产物并核对SHA。权重、原始轨迹和Teacher凭据不提交Git。

公开入口、部署/续跑命令见[README](../README.md)。`experiment/summary.json`列已完成组、真实更新、奖励分布、入池数和Student用量；各update保留loss、实际lr/beta、group IDs、概率比/clip fraction、耗时、峰值显存；Teacher账本独立记录调用量和估价。
整轮完成前只报告实际进度；本轮尚无固定dev效果结论。历史CodeAct S0 dev24为8/24，不能用训练奖励与其直接比较。

## 22:20：空数字答案修复与五轮调度接续

用户要求修复已确认的评分分类遗漏，并将目标改为连续5轮：每轮40组之后评价固定dev24，再继续最新actor/optimizer；**仍保持原11月20日19:52:38截止时间**，到期保存，后续另行授权续跑。Student推理/训练参数、共享Judge和Teacher有限预算保持原样；不启动Static或test。

`evaluation.py`新增 `official_result_integrity_20261119_v3`。仅在原任务官方代码明确执行 `predicted_answer = float(predicted_answer)`，且实际 `predicted_answer is None` 导致TypeError时，归为 `task_answer_failure`。官方断言及分数不改；参考答案转换、非任务源码、非目标转换、shadowed float、其他TypeError和环境故障仍保持未知/异常。没有自动补填答案，也不禁止动作题的合法null。

对第3组保存的4个终态实际重新运行官方评测，未调用模型或重做业务动作：

| member | 原分类输出 | 修复后 | 官方报告 |
|---|---|---|---|
| 0 | 未知，TypeError | 有效失败0，空数字答案 | 完全一致 |
| 1 | 有效失败0 | 有效失败0 | 完全一致 |
| 2 | 成功1 | 成功1 | 完全一致 |
| 3 | 未知，TypeError | 有效失败0，空数字答案 | 完全一致 |

真实审计另存02目录 `reward_audit/`，原01轨迹、分数、Teacher准入及源码锁不覆写。
更正组为 `[0,0,1,0]`，可以进入正确的统计/反馈历史；它属于旧S0 actor，**不能补入已更新actor的待优化批次**。
已更新actor/optimizer和未消费的同策略有效组继续继承，训练计数不归零、已处理反馈窗口不重新付费生成。

新配置 `configs/codeact_grpo_coevolve_five_rounds_20261119.json`，新锁 `configs/codeact_online_eval_v3_20261119.lock.json`。
跨轮重置生成题本轮已用集合，保留任务池、历史反馈和原始题打乱队列；200组/最多100更新为五轮总目标，实际执行仍受现有24小时与费用上限约束。
每40组先做剩余有效组尾批，再评价当时最终actor；dev轨迹不进入组反馈、任务生成或GRPO。
评测使用vLLM、non-thinking、64K、temperature0、原dev24顺序/种子和单题预算，每题一次。
若硬截止中断一条dev尝试，保留其证据、记未知；以后只完成剩余任务，不把重新采样伪装成原尝试。

本次针对性验证：76项相关测试通过，另1项五轮配置/预算检查通过，共77项；Ruff/diff check通过。
包含缺失数字/合法null/参考答案错误/原始与生成评分路由、五轮CPU模拟顺序、尾批后dev、同协议续跑、评测中断不重复采样，以及新锁通过/旧锁拒绝。
五轮调度测试采用缩短的CPU合成组数；尚未完成真实40组或真实轮末dev24，不声称已经完成五轮。


22:25已完成实际切换：共14个已完成组、1次GRPO更新，原actor及optimizer SHA核对通过。
第12/14组均为同一更新后actor的未消费有效组，已交新调度进行下一次更新。
独立02的 `handoff.json` 记录父状态/绑定SHA、当前actor、optimizer SHA、组计数和原绝对截止。
没有重置额度，没有改写01结果；更正的旧第3组只用于准确统计/反馈，不补做过期策略训练。
