# CodeAct 重启：实现与本地真实采集（2026-11-19）

分支 `reboot/codeact`，基于远端原开发分支 `bdaf6a3cead063f7aec29989bd5bb5000abf7afd`，独立工作区 `/home/eva/project/RecoverAgent-RL-codeact`。原工作区全部未提交工作保留；旧数据、模型、优化器与15步GRPO归档不进入新训练。

**最新：北京时间2026-11-19 16:20:33，256条合格成功示范已收齐，最终导出完成，采集自动停止。** 实现 `a293de6`、首批报告 `f9df583`、256目标/评分异常跳过规则 `e5eff03`。下文续跑与首批统计保留为历史记录。

## 最终采集与导出

| 指标 | 实际结果 |
| --- | ---: |
| 实际任务 / 全部尝试 | 270 / 289 |
| 官方成功 / 有效失败 / 评分未知（尝试） | 257 / 29 / 3 |
| 合格成功示范 | 256 |
| 两次失败的任务 / 评分异常跳过任务 | 10 / 3 |
| 64K内完整可训练轨迹 / 多轮样本 | 252 / 252 |
| 超长deferred / 截断 | 4 / 0 |
| 导出累计输入 / Assistant目标token | 4,421,765 / 371,172 |
| 可训练最长 / P50 / P90 / P95 | 61,817 / 14,876 / 31,607 / 39,666 |
| 同批逐回复重复前缀工作量 | 43,404,178 token |

257条官方成功中，`fb05fed_3` 第二次尝试含1次原生格式拒绝，按既定资格规则不进入SFT，未为凑数量放宽过滤。另3条官方异常保持未知，不作为普通失败或成功；没有重写原始评分。普通失败可干净重试一次，因此29次失败不等于29道最终失败题。终止原因287次 `task_completed`、2次 `format_corrections_exhausted`；前者仅表示提交结束，不代替官方成功判断。尚未逐题完成29条业务失败的细粒度归因。

超长完整轨迹：`383cbac_3` 77,216；`66b7899_2` 91,577；`83a7951_1` 90,915；`9a16324_2` 110,714 token。仅暂缓训练，成功原件和全部Assistant目标仍保留，不截断或重新请求Teacher。数据在 `runs/codeact_train510_20261119_01/target_256_data/`；`train.jsonl` SHA-256为 `81acb5be1f53c28b105e6234953facbdf44a55af9fd37fbea2af9ab98280438e`。

本地用真实训练读取入口加载252行，核验数据SHA、全部256来源SHA/train归属、隐藏reasoning不进入结构化训练消息，以及输入/监督token总数。私有核验回执 `final_export_verification_20261119.json`；这是数据加载与来源核验，不是9B前后向或训练速度测试。逐回复与合并工作量差也不当作实测加速比。

全程4581请求，59,194,932输入/1,227,511输出/53,329,152缓存命中token；配置估价$5.150162112，包含未知调用保守预留。API返回的 `usage.cost=0` 不能解释为服务免费，实际订阅账单未知。全部尝试墙钟合计27,064.56秒（约7小时31分），不含先前评分故障后等待用户/续跑的时间。

16:26检查进程已退出，`progress.status=target_reached`，没有多采第271题。未启动GPU、SFT/GRPO或dev/test。下一项是在用户安排的GPU上，对这批新数据从官方9B做独立短/长训练烟测，再进行新S0正式训练；准确命令已指向最终数据目录。

## 256条目标续跑（11:17快照）

原进程03:02因第84题 `988af8e_1` 官方评分中的AttributeError停止，未一直运行。控制器保留 `success=null/eligible=false`，没有把异常伪装成普通失败或成功。用户指示继续、评分异常跳过并累计到256条后，本次只修改采集调度/CLI/启动器及针对性测试，没有修改模型提示、任务划分、预算、AppWorld或官方评分源码。

- 已完成91次尝试：79官方成功、11有效失败、1未知；79条成功均符合原始示范资格。处置84题＝79成功＋4题两次失败＋1题评分异常跳过。现第85题 `fb05fed_3` 已收到9次真实Teacher响应，PID2944992在本地继续。
- 评分异常单独保存 `scoring_skips/<task>.json`，引用原结果/轨迹SHA，跳过整题而非再次尝试。新旧续跑均遵循该规则；真实业务失败仍最多两次。凭据、Teacher与执行环境故障不被此规则吞掉。
- 累计256条合格官方成功示范即停止新请求并导出 `target_256_data/`；最多原train510，不自动补任务、追加额度或训练。成功总数与64K可训练数分别统计，超长保留deferred、不截断。
- 调度保存于独立 `collection_controls.json`，新冻结源码 `runtime_target256_20261119/`；原模型配置、队列binding、首批验收、旧runtime manifest和异常轨迹/结果SHA已核验未变。续跑不重复成功题；同一命令见README。
- **107项pytest通过，7.63秒**：`tests/test_codeact_collection_controls.py tests/test_codeact_reboot.py tests/test_teacher_collection.py tests/test_teacher_data.py tests/test_evaluation_classification.py`。Ruff check、13文件format check、diff check通过。新增8例采用模拟worker/导出，只验证调度；真实续跑与模拟测试分别记录。
- 截至恢复前的完成尝试用量：1466请求、19,755,101输入/382,760输出/17,830,144缓存命中token，配置估价$1.143779964；订阅实际账单未知。恢复后的在途请求另记实时usage，未包含于该数值。

AgentEvolver只作为固定代码执行接口的设计参考，未直接导入其模块或替换为其整体Harness。本项目 `codeact/episode.py` 调用AppWorld原生执行器、复用官方评分与已有基础组件；旧动态工具Harness不进入新链路。

仍未完成256条、79条全量编码或任何9B GPU训练。下一动作继续当前队列到目标并检查最终数据清单；GPU仍按用户指示不开机。

## 实现与入口

固定原生工具 `appworld(code: string)`，共享短system与Teacher/Student loop；查文档只增加工具返回，不修改工具头。代码在持久AppWorld解释器执行，跨轮变量、循环和块内批量API均可用。API文档入口按固定AppWorld版本实测，原生安全限制保留；不引入旧工具注册/解锁/append-tools控制层。

`src/recoveragent/codeact/` 中：`protocol.py` 是工具及提示；`episode.py` 是共享loop和官方隔离评分；`client.py` 复用Teacher原生客户端；`collect.py` 是进程隔离/不可覆盖尝试/续跑队列；`data.py` 是精确模板多轮编码；`training.py` 和 `inference.py` 是新S0训练与评价。共享 `teacher.py` 仅增加明确的 `appworld_code` 原生模式支持。启动器冻结源码配置并独立后台运行，新主线不经过历史阶段锁。

沿用scenario划分170/37/37、510/111/111，保留官方来源split；这是项目自定义研究协议，不宣称官方榜单或全项目历史未见test。队列先覆盖170个场景再收其余变体，每题最多两次干净尝试、最多一条合格成功示范。前20题完成后自动检查实际工具请求、环境/官方验收和新模板导出，无成功率门槛；通过就继续同一510队列。

Teacher为现有Flash/thinking，独立1M窗口，以匹配tokenizer估算完整请求并预留输出；实际用量取接口。每回复4096、累计32768、最多80请求、1200秒、代码块60秒；未知传输用量保守计费预留，凭据/环境故障明确停止。模型输入仅有公开任务、工具和观察，`load_ground_truth=False`；官方答案与评分结果不送回模型。

## 首批20：已完成的真实验收

| 指标 | 首批固定20场景 |
| --- | ---: |
| 实际任务 / 尝试 | 20 / 21 |
| 官方成功尝试 / 有效失败尝试 / 未知 | 20 / 1 / 0 |
| 合格成功示范 | 20 |
| 请求 / appworld执行 | 355 / 355 |
| 文档 / 业务 / supervisor API | 220 / 498 / 68 |
| 输入 / 输出 / 缓存命中token | 3,540,290 / 84,877 / 3,173,632 |

首批顺序在运行前冻结，没有替换或追加任务凑成功。Venmo首试虽完成转账，却额外充值，引发不符合官方要求的状态变化，**如实记失败**；第二次从干净初态重新执行后官方通过。首试原件保留，不导出为成功数据，官方失败信息也未传给重试Teacher。

原生工具格式拒绝0次，相同文档重复查询2次；每条完成轨迹都有真实业务API，不再呈现旧方案只查文档、0业务调用的现象。中途代码/API错误分类：`{'Exception HTTP401': 12, 'TypeError': 1, 'NameError': 1, 'Exception HTTP422': 6, 'Exception': 8, 'ExecutionError': 2}`；HTTP401/422是AppWorld业务API返回，不是Teacher服务鉴权失败。成功示范保留真实修正过程，不删中间操作。不据Teacher小批成功推论Student效果。

首批尝试墙钟合计 **2148.36秒（约35.81分钟）**，按配置单价估价 **$0.230891592**。接口 `usage.cost` 合计0，实际Go订阅账单明细不可由此确定；后续任务用量不包含在首批统计内。

`first_batch_audit.json` 已真实生成：`passed=true`、`problems=[]`。实际请求的固定工具Schema、每次官方结果有效性、完整新模板编码均通过；队列已自动进入第21题，无额外确认。剩余490题包含在执行任务，未声称它们已完成。

私有 `runs/codeact_train510_20261119_01/`，collector PID `2778181`。`runtime_manifest.json` 为冻结源码身份；前5题由启动时同协议实现采集，随后转为独立冻结进程，原件不改写。首批汇总 `first20_result_summary.json`、原始尝试和官方评分仅保存在私有目录。

## 实际多轮导出与训练准备

| 指标 | 首批20结果 |
| --- | ---: |
| 成功轨迹 / 完整可训练样本 | 20 / 20 |
| 累计输入 / Assistant目标token | 325,427 / 32,081 |
| 最长 / P50 / P90（最近观测值） | 36,486 / 13,549 / 22,552 |
| 超长deferred / 截断 | 0 / 0 |
| 逐回复重复前缀输入工作量 | 3,402,390 |

长度排序：`[6354, 7011, 10493, 10598, 10600, 11380, 11945, 11947, 12077, 12319, 13549, 15737, 16783, 18072, 21979, 22284, 22478, 22552, 30783, 36486]`。实际数据 `runs/codeact_train510_20261119_01/first20_data/`，SHA-256 `70cd6b991e2917907e7fc9dbb709d738c52aa4a1ab9eae0317c92e66e5744584`。全部成功轨迹完整编码，没有通过丢弃Assistant目标凑长度。这是输入token工作量对照，**不是实测训练加速比**。

实际编码使用官方Qwen3.5-9B revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a` 与固定模板SHA，non-thinking。兼容多轮合并，Assistant内容及调用目标恰好监督一次；system/user/tool和服务商隐藏reasoning不监督。不兼容前缀另开保留完整上下文的样本；超64K整轨迹deferred。目标为均匀轨迹平均CE，分段时按目标token计权。

新训练只从官方基座创建LoRA，不加载旧adapter/optimizer；BF16、r16/alpha32/dropout0、lr5e-5、AdamW/weight_decay0/固定学习率、micro1/accumulation8、1epoch。完整序列一次主体前向，目标位置词表分块checkpoint。短/长检查及正式阶段各自重载基座，入口保存实际updates、loss、有限梯度、参数哈希、显存、耗时及重载证据。smoke权重不能作为正式S0进入评价；当前评价后端为Transformers。

## 验证与尚未执行

- **99项相关pytest通过**（新CodeAct17项＋Teacher/官方评分82项）；Ruff check、12文件format check、git diff check通过。新测试含固定工具/跨轮状态/失败重试/成功跳过/源码冻结启动、多轮mask/超长deferred、原生Qwen多行Python解析与旧adapter拒绝。
- 真实固定tokenizer验证全部多轮目标；首次调试修正Transformers默认BatchEncoding返回，明确 `return_dict=False`，原始示范未改写。
- 随机微型Qwen3.5＋LoRA **CPU** 对照：新分块路径与原目标位置CE loss差0、裁剪前梯度最大绝对差 `1.49e-8`。不是预训练9B或GPU证据。
- **真实AppWorld** 无模型探针2.45秒：完整参数文档、跨轮变量41→42、另一新实例同变量NameError通过。记录在 `runs/codeact_environment_probe_20261119_01/`。
- 另尝试旧S2上下文套件，6项在历史源码锁入口拒绝变更后的teacher.py；未重签旧锁。新测试明确保留该拒绝，未声称完整pytest全量通过。
- 用户明确暂不开GPU：**9B真实前向/反向烟测、GPU保存重载、正式S0及Student dev/test尚未执行**，没有新S0 checkpoint或学习收益结论。采集完成后的正式S0仍是下一步，不由collector自动启动。

准确的采集/导出、新S0短长检查/正式训练、dev111评价命令见 [README](../README.md)。最终状态以上方16:20完成记录为准，采集已停止；下一项安排GPU烟测与S0。后续S1、Static、Full路线只在主计划列出，本轮不实现或启动。
