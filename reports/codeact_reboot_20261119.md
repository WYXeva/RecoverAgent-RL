# CodeAct 重启：实现与本地真实采集（2026-11-19）

分支 `reboot/codeact`，基于远端原开发分支 `bdaf6a3cead063f7aec29989bd5bb5000abf7afd`，独立工作区 `/home/eva/project/RecoverAgent-RL-codeact`。原工作区全部未提交工作保留；旧数据、模型、优化器与15步GRPO归档不进入新训练。

**截至北京时间 2026-11-19T00:50:18.349234，采集仍在运行；首批20与train510尚未完成。** 本报告随本分支提交，最终commit见Git记录。

## 实现与入口

固定原生工具 `appworld(code: string)`，共享短system与Teacher/Student loop；查文档只增加工具返回，不修改工具头。代码在持久AppWorld解释器执行，跨轮变量、循环和块内批量API均可用。API文档入口按固定AppWorld版本实测，原生安全限制保留；不引入旧工具注册/解锁/append-tools控制层。

`src/recoveragent/codeact/` 中：`protocol.py` 是工具及提示；`episode.py` 是共享loop和官方隔离评分；`client.py` 复用Teacher原生客户端；`collect.py` 是进程隔离/不可覆盖尝试/续跑队列；`data.py` 是精确模板多轮编码；`training.py` 和 `inference.py` 是新S0训练与评价。共享 `teacher.py` 仅增加明确的 `appworld_code` 原生模式支持。启动器冻结源码配置并独立后台运行，新主线不经过历史阶段锁。

沿用scenario划分170/37/37、510/111/111，保留官方来源split；这是项目自定义研究协议，不宣称官方榜单或全项目历史未见test。队列先覆盖170个场景再收其余变体，每题最多两次干净尝试、最多一条合格成功示范。前20题完成后自动检查实际工具请求、环境/官方验收和新模板导出，无成功率门槛；通过就继续同一510队列。

Teacher为现有Flash/thinking，独立1M窗口，以匹配tokenizer估算完整请求并预留输出；实际用量取接口。每回复4096、累计32768、最多80请求、1200秒、代码块60秒；未知传输用量保守计费预留，凭据/环境故障明确停止。模型输入仅有公开任务、工具和观察，`load_ground_truth=False`；官方答案与评分结果不送回模型。

## 已落盘的真实采集

| 指标 | 当前截面 |
| --- | ---: |
| 已完成不同任务 / 已完成尝试 | 11 / 12 |
| 官方成功尝试 / 有效失败尝试 / 未知 | 11 / 1 / 0 |
| 合格成功示范 | 11 |
| 已开始不同任务（含进行中） | 12 |
| 未完成任务（含进行中） | 499 |
| 请求 / appworld执行 | 237 / 237 |
| 文档 / 业务 / supervisor API | 135 / 306 / 44 |
| 输入 / 输出 / 缓存命中token | 2,633,327 / 57,411 / 2,391,808 |

一次Venmo首试虽完成目标转账，却额外充值，引发不符合官方要求的状态变化，**如实记失败**。第二次按规定从干净初态重新执行后官方通过；首试原件保留，未导出为成功数据，也没有把官方失败信息传给重试Teacher。没有基础设施故障。

原生工具格式拒绝 0 次，相同文档重复查询 2 次；每条完成轨迹均有真实业务API，不再呈现旧方案只查文档、0业务调用的现象。中途代码/API错误分类：`{'Exception HTTP401': 10, 'TypeError': 1, 'NameError': 1, 'Exception HTTP422': 6, 'Exception': 6, 'ExecutionError': 2}`；其中HTTP401/422是AppWorld业务API返回，不是Teacher服务鉴权失败。成功示范保留真实修正过程，不删改中间操作。不据小批Teacher成功推论Student效果。

已完成尝试墙钟合计 **1434.89秒**，按配置单价估价 **$0.155699748**。接口 `usage.cost` 合计0，实际Go订阅账单明细不可由此确定；在执行任务的新用量不包含在本截面内，原响应继续留存。

私有 `runs/codeact_train510_20261119_01/`，collector PID `2778181`，`runtime_manifest.json` 为冻结源文件身份。前5题由启动时同协议实现采集，之后转为独立冻结进程；原件不改写。首批20的 `first_batch_audit.json` 尚未生成，不声明该验收已完成。

## 实际多轮导出与训练准备

| 指标 | 结果 |
| --- | ---: |
| 成功轨迹 / 完整可训练样本 | 11 / 11 |
| 累计输入 / Assistant目标token | 208,159 / 19,903 |
| 最长 / 中位 / P90序列 | 36,486 / 16,783 / 30,783 |
| 超长deferred / 截断 | 0 / 0 |
| 逐回复重复前缀输入工作量 | 2,471,706 |

长度排序：`[10598, 10600, 11945, 12319, 15737, 16783, 18072, 22284, 22552, 30783, 36486]`。新数据集私有快照 `runs/codeact_delivery_20261119_02/dataset/`，SHA-256 `e1cac123a918cad2c2cf05c4774fb8f5f4601c20eeb17bedc12b84a110a0bf60`。这是累计输入工作量对照，**不是实测训练加速比**。

实际编码使用官方Qwen3.5-9B revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a` 与固定模板SHA，non-thinking。兼容多轮合并，Assistant内容及调用目标恰好监督一次；system/user/tool和服务商隐藏reasoning不监督。不兼容前缀另开保留完整上下文的样本；超64K整轨迹deferred。目标为均匀轨迹平均CE，分段时按目标token计权。

新训练只从官方基座创建LoRA，不加载旧adapter/optimizer；BF16、r16/alpha32/dropout0、lr5e-5、AdamW/weight_decay0/固定学习率、micro1/accumulation8、1epoch。完整序列一次主体前向，目标位置词表分块checkpoint。短/长检查及正式阶段各自重载基座，入口保存实际updates、loss、有限梯度、参数哈希、显存、耗时及重载证据。smoke权重不能作为正式S0进入评价；当前评价后端为Transformers。

## 验证与尚未执行

- **99项相关pytest通过**（新CodeAct17项＋Teacher/官方评分82项）；Ruff check、12文件format check、git diff check通过。新测试含固定工具/跨轮状态/失败重试/成功跳过/源码冻结启动、多轮mask/超长deferred、原生Qwen多行Python解析与旧adapter拒绝。
- 真实固定tokenizer验证全部多轮目标；首次调试修正Transformers默认BatchEncoding返回，明确 `return_dict=False`，原始示范未改写。
- 随机微型Qwen3.5＋LoRA **CPU** 对照：新分块路径与原目标位置CE loss差0、裁剪前梯度最大绝对差 `1.49e-8`。不是预训练9B或GPU证据。
- **真实AppWorld** 无模型探针2.45秒：完整参数文档、跨轮变量41→42、另一新实例同变量NameError通过。记录在 `runs/codeact_environment_probe_20261119_01/`。
- 另尝试旧S2上下文套件，6项在历史源码锁入口拒绝变更后的teacher.py；未重签旧锁。新测试明确保留该拒绝，未声称完整pytest全量通过。
- 用户明确暂不开GPU：**9B真实前向/反向烟测、GPU保存重载、正式S0及Student dev/test尚未执行**，没有新S0 checkpoint或学习收益结论。采集完成后的正式S0仍是下一步，不由collector自动启动。

准确的继续采集、导出、新S0短长检查/正式训练、dev111评价命令见 [README](../README.md)。当前后台无需再次启动，资源故障会保留具体日志；下一项是完成同一新队列并冻结最终成功/超长清单，然后安排GPU烟测与S0。后续S1、Static、Full路线只在主计划列出，本轮不实现或启动。
