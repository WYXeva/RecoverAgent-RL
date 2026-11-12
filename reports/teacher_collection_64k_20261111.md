# 64k Teacher 数据补采

本轮已于2026-11-12 14:17:44（UTC+8）完成累计256条合格成功示范，并正常停止。此前成果计入目标，以下历史检查点和异常记录完整保留。

## 最终结果

| 项目 | 实际结果 |
| --- | --- |
| 合格成功示范 | 256条、256个唯一任务，全部属于固定v2 train |
| 来源 | 旧数据106条＋本轮新增150条 |
| 新登记尝试 | 272个唯一任务，各一次；其中1项在API请求前启动失败 |
| 新尝试结果 | 151项官方成功（150合格、1因重复无进展循环过滤），98项失败，23项异常另列 |
| 超长 | 旧train成功1条仍超过64k暂缓；新成功没有因超长暂缓，不截断 |
| Assistant训练样本 | 5,261个；731,298监督token |
| 最长编码样本 | 63,290 token，未超过65,536上限 |
| 新任务调用成本 | 7,334请求；123,884,458输入、91,633,024缓存输入、2,342,650已知生成token |
| 未知用量 | 23,257生成token预留；不冒充已知实际用量 |
| 费率估价 | USD13.036408344＋未知费用预留USD1.9153452；非订阅账单，不含此前已有数据成本；3次接口探测另列 |
| 耗时 | 首条登记2026-11-11 17:53:32至达标约20.40小时，包含中断/等待；episode累计59,306秒（约16.47进程小时） |
| 后续动作 | 采集/worker/守护正常结束，未启动训练或下一轮 |

最终私有数据目录：`runs/teacher_go_64k_20261111_04/exports/pool_256_1794464265574112402/`。`successes.jsonl`保留完整结构化消息及来源；每题的64k Assistant-only编码、原始执行、工具Schema、请求/响应和评分保留在Manifest来源路径。不同来源的Teacher身份原样保留，训练编码统一使用固定9B tokenizer和模板。

- Manifest SHA-256：`3e66b757b49cd874a412d5d440c6a2e7a0e47eb5098d593af0347646da4970df`
- `successes.jsonl` SHA-256：`215d17df3cf3ee61836b89c28c5e66f2635321ad28ad101a77ca287e96356166`
- 独立最终验收：`runs/teacher_go_64k_20261111_04/final_validation_20261112.json`，PASS。逐条复核唯一train身份、严格示范契约、Manifest文件哈希、来源sample一致性及256份编码文件SHA，汇总样本数/监督token/最长序列。没有调用模型或重跑任务。
- 完成记录：`completion_20261112.json`。守护共6次真实自动接续，最终target_reached退出，systemd Result=success；已禁用这个完成run的自动启动，原服务单元及实施快照保留。

## 历史检查点

11月12日上午曾达到193/256条合格，还缺63条；原106＋新增87。当时193条合并池已保存，之后继续采集直到最终256条。下面保留此前检查点及中断事实。

11月11日晚检查点：累计154条（原106＋新48），还缺102条。新采集共有75项正常评分：48成功、27失败；另1项0请求启动异常、3项数值答案评分异常、1项网络异常。run04的154条池已保存于`exports/pool_154_1794403614807828403/`。采集继续使用run04；下述108/124/130条记录为先前检查点。

后续守护部署验收时已增至156/256，新增合格结果逐项保存在run04内；154是最近一次合并导出检查点，不是当前总数。

11月12日晨检查：已增至179/256（旧106＋新73），179条已合并导出`exports/pool_179_1794412212593516635/`。累计128个attempt中120项正常评分：74官方成功、46失败；其中1项成功因重复无进展循环被原过滤规则排除，故新增合格为73。另1项0请求启动异常、6项评分异常、1项网络异常。已完成任务3415请求、55495965输入/1098640输出token，Go费率估价USD6.009476604＋未知预留USD0.319488，非账单。

必须如实记录夜间停顿：11月11日23:50采集器遇到`f86d850_3`的完整官方负报告（14测试、1通过/13失败），因缺失任务状态导致AttributeError等评分异常，原完整性分类仍是unknown。watchdog保持在线但将其当作未审查非网络故障，没有继续派发；直到次日09:37检查已停约9小时47分。此前“持续接续”的交付对此类问题覆盖不足。核查后原未知评分、失败轨迹和用量保留、不重试；服务认证检查通过，09:40左右原collector恢复下一未尝试任务，冻结源锁不变。守护规则补齐工作与采集并行，不把在线心跳等同于实际采集进度。

11月12日守护修复已完成：只改独立watchdog和测试，完整且身份/哈希/计数一致、环境自检通过的单题官方负报告可隔离，不再枚举数字trace或异常类型。保留原unknown、eligible=false，审计仅保存引用/哈希/计数，不将评分内容发送给Actor。f86原attempt和collector结果分别保留SHA（后者多一个worker_wall_seconds），两者连同轨迹和评分均未修改，已持久登记quarantine及handled_faults。新守护SHA `e1da32c94c0ec31ab8de6408fdd52f72db1c4451f4a6f7e5fa43e382e967fb2f`，59项合成测试及Ruff通过；PID537413、collector534256、2个worker正常，合格数已增至180。真实恢复由root核查后启动；新自动分支已通过真实旧回执只读识别，尚无部署后新故障的自动恢复记录。证据为`watchdog/quarantine_reviews/`及`service_deployment_20261112_*.json`，旧守护实现/配置留在`implementation_history/`。

## 已有成果

11月12日上午后续记录：守护已实际自动隔离`ec437da_1`评分异常并启动下一批，之后又自动恢复`37a8675_1`与`a97e35d_3`的ProxyError，审计分别为`watchdog/resume_reviews/1794448410897127737.json`与`1794449792453035534.json`。10:27 `22cc237_2`首个请求遇SSLError，旧白名单未覆盖而停顿；10:50检查后修复独立watchdog，保留证书验证，探测SSL错误等待重查、InvalidURL仍阻塞。61项合成测试和Ruff通过。

10:59新守护自行完成认证GET /models（200、当前模型存在），自动启动collector PID558813并执行两个未尝试任务；root未手动重启collector。恢复审计`1794452352975895598.json`保留原SSL故障的4096未知生成及USD0.319488预留，该题未重试，总停顿约32分钟。新监控SHA `4592757e2fc3393cac302205993bff3e67d951b66ed5324fae0b17cbdaf4a28e`，旧实施快照/配置已封存，冻结采集源锁不变。

停止检查点累计150个attempt：138项正常评分（88官方成功、50失败），其中1项成功因重复无进展循环过滤，故新增合格87；其余1项0请求启动异常、7项评分异常、4项网络异常。合并池`exports/pool_193_1794450448411644053/`保存193条。已完成任务4029请求、65810084输入/1280114输出token，估价USD7.029842832＋未知预留USD1.2763692，非账单。

- 当前分支基线：`87500e43674c3742b2d0d8171352038cb3524f15`。
- Full732 v2场景隔离划分保持170/37/37场景、510/111/111任务；只有train用于采集和训练数据。
- 原数据包`data/full732_teacher_successes_complete_20261029/`未修改，Manifest SHA-256为`96265f67a5828b101f5db32a804008478d7c419a01e079f6e468b8e5f164735f`。
- 原144条官方成功中107条属于当前train；重新使用固定Qwen3.5-9B tokenizer和non-thinking模板完整编码后，106条合格、1条超过65,536暂缓。其余18条dev和19条test不纳入。
- 首批3项真实任务中2成功、1失败；2条成功均通过回执过滤和64k编码，累计108条合格，距目标还缺148条。此为早期检查点，实时数以最新run04的`progress.json`为准。
- 逐Assistant目标编码，前缀、工具输出和控制信息为`-100`；保留原始消息及来源SHA，Schema转换另存审计，不截断或删工具调用。

## 执行配置

新入口`scripts/collect_teacher_64k.py`，当前配置`configs/teacher_collection_64k_quarantine_20261111.json`，源码锁`configs/teacher_collection_64k_quarantine_20261111_lock.json`，锁文件SHA-256为`02ae0bad908e6bf70f6f771a617d8d9b1d7ab2d29f166746ba5e9966c6dab0a8`。之前initial/clock/export配置及锁均保留，不重签；run03使用export锁`8186bf18f41c072d697b7010d88a8db330db4b7c53fdf32617fb4794fa092058`。

Teacher为OpenCode Go的`deepseek-v4.1-flash`服务别名，不宣称不可变权重身份。Go专用URL为`https://opencode.ai/inference/go/openai/v1`，每个独立任务使用稳定且不同的`x-opencode-session`，凭据从私有`.env`读取。2026-11-11三次真实接口探测通过文本、nullable工具调用和结果回传，共780输入/72输出token；记录在`runs/teacher_go_probe_20261111T093900046814Z/`。

采集从原始初态执行，复用execution_v6、nullable v2、严格Harness及原任务官方验收。每任务80请求、16384累计生成、4096单回复、1200秒、15次通用协议纠错，最多2个隔离进程。初始任务先单进程检查。

64k的精确定义是固定9B tokenizer下的完整公开历史、当时可见工具加预留回复不超过65,536；保存每次请求的计数回执。Teacher服务原生token usage另计，不能将Qwen计数冒充DeepSeek服务端计数；服务端原生计费预留上限保留。没有改变旧Student的32k实验，也没有启动64k Student推理、训练或显存验证。

费用采用Go公开DeepSeek峰值USD费率进行技术预留和usage估价，非实际扣费账单；不修改套餐、额度外计费或充值设置。服务错误停止采集，不自动重试已登记的任务。

## 启动问题与当前运行

`runs/teacher_go_64k_20261111_01/`首个worker在首条API请求前segfault，0模型请求。原因是freezegun扫描Transformers延迟模块，在本地Python扩展中崩溃。原失败、配置、源码锁和精确源码归档均保留。

clock版本复用项目已有的`transformers/torch/huggingface_hub`时间冻结排除设置。真实AppWorld初始化和关闭通过，在`runs/teacher_go_64k_20261111_02/`实际完成3项任务：2项官方成功，1项用尽16384生成token而失败。两条成功最初被Guard/Harness中遗留的A244来源检查误滤。该检查现复用示范模块已有的协议分派，实际Guard/Harness重放和回执验证保持严格。

run02已等待所有在途任务结束，再封存原始评分、结果、源码锁及精确源码。export版本在`runs/teacher_go_64k_20261111_03/`只读重新过滤和编码两条成功，不修改旧结果、不重跑模型。run03的`prior_results/`保存新过滤结果及源轨迹/评分SHA；`reused/`保存合格示范与Assistant-only编码。当前剩余399项候选已冻结，所有先前登记任务（含0请求基础设施失败）均排除。

前3项实际任务共62次请求，输入594332、输出23228、缓存命中446464 token，episode耗时合计约662.8秒；Go峰值费率估价USD0.074912784，非订阅账单。初始基础设施失败是0请求；上述3次接口探测成本另列，未计入任务成功率。

实时状态读取`runs/teacher_go_64k_20261111_04/progress.json`；逐项结果位于`results/`，原始请求、响应、轨迹、终态和官方评价位于`attempts/`，来源和固定任务名单位于`campaign.json`。成本汇总包含先前run的任务；正在执行任务的usage尚未计入已完成合计。每次停止导出独立`exports/pool_*/successes.jsonl`和Manifest，权重及原始任务数据不进Git。创建run目录下的`STOP`文件会停止新派发，等待在途任务完成后导出，不中断已付费请求。

run03第二次数值答案评分异常发生在`166f4ff_2`，异常处为公开报告中的`predicted_answer = float(predicted_answer)`，提交答案不可解析。两题原unknown分类均保留，不算失败训练奖励或成功数据，不重评分、不重试。run04仅增加调度隔离：完整官方负报告、唯一且明确的数字转换ValueError、环境自检通过、公开提交答案不可解析、无provider错误时继续下一题；其他服务/环境或未知错误仍停机。原`evaluation.py`及成功过滤逻辑未修改。34个先前attempt去重排除，余下369项按原顺序继续。累计811请求、11654709输入/217668输出token，估价USD1.117992924，非账单。两个真实历史异常回执已只读识别通过；新的在线隔离行为以之后实际回执为准。

run04真实执行已验证1次新的数字答案异常自动隔离，随后任务继续；这仍是unknown排除，不是改判成功。后来`30e8586_2`第22次请求因`ProxyError`未获得HTTP响应，采集器按约定停止并等待另一在途任务完成，保存154条池。该题21次已知回复及4096未知生成/美元0.319488费用预留完整保留。检查时本地代理端口可达，既定服务的认证GET `/models`返回200且包含当前Teacher；没有调用生成探针，从下一未尝试任务恢复。停止前总计2045请求、32483264输入/642203输出token，估价USD3.544219536＋未知预留USD0.319488，非订阅账单。恢复审计和独立collector日志保存在同一run内，配置、源码锁和旧结果均未修改。

启动命令（本地CPU，无需GPU）：

```bash
HF_HOME="$PWD/.cache/v2-hf" PYTHONPATH=src TOKENIZERS_PARALLELISM=false \
  .venv-v2/bin/python scripts/collect_teacher_64k.py prepare \
  --output runs/<全新run_id>

HF_HOME="$PWD/.cache/v2-hf" PYTHONPATH=src TOKENIZERS_PARALLELISM=false \
  .venv-v2/bin/python scripts/collect_teacher_64k.py collect \
  --output runs/<已准备run_id>
```

本run已达到256条目标，collector与守护均正常结束，服务自启动已禁用，无需重新启动。以上命令保留用于复现；运行中的campaign若需接续，使用`collect`不会重发有claim的任务，未决claim需先核对原进程及回执。

## 持续守护

用户要求子agent持续监控并在异常断开后接续。子agent新增`scripts/watch_teacher_collection.py`和独立合成测试，由root部署至用户级systemd服务`recoveragent-teacher-64k-20261111-04.service`，运行期间enabled、Linger=yes、Restart=on-failure、RestartSec=15、KillMode=process，达标后服务已inactive/disabled。每30秒检查本run进程和状态；网络不可用时每60秒检查既定服务的模型列表，不调用生成探针。守护程序自身失败由系统重新拉起，在途collector/worker不随守护停止被kill。

自动接续保留每题一次：先等待在途worker；在互斥锁内补收已完成result；若进程确认中断且没有完整结果，核对已有回执并恢复已知/未知用量、另建unknown中断记录，不重试旧题。源锁/配置、半写证据或未知执行错误保持明确阻塞并持续心跳；不为了续跑把异常算成功。短暂网络故障在认证模型列表检查通过后启动原collector继续下一未尝试题。402/429不会仅凭models200放行。达标、池耗尽、费用上限及STOP结束监控；若最后结果齐全但collector来不及导出，补齐合并包。

11月11日版watchdog源码SHA `29c0f3acf6ce49da357f0fbb8c34f4145e8462bdb1a79d035612fbd6e23873ba`，冻结采集源锁未改变。该版39项合成测试和Ruff check/format通过。实际强制结束守护主进程，systemd自动重启且原collector PID374604不变，付费任务继续；当时守护PID383716，heartbeat=monitoring。11月12日版本与夜间覆盖缺口见前述记录，不将守护存活或合成测试写成真实续采成功。

私有证据：`watchdog/heartbeat.json`、`state.json`、`events.jsonl`、`resume_reviews/`、`interrupt_reviews/`、`service_deployment.json`、`service_restart_test_before.json`和`service_restart_test_after.json`。服务单元原件在`/home/eva/.config/systemd/user/recoveragent-teacher-64k-20261111-04.service`，副本在run04的`watchdog/service.unit`。

查看服务：`systemctl --user status recoveragent-teacher-64k-20261111-04.service`。整体停止用`touch runs/teacher_go_64k_20261111_04/STOP`，等待在途请求结束并导出；`systemctl --user stop`只停止守护，不终止付费worker。

## 软件和真实证据

继续运行检查：累计124条合格（旧106＋新18），已经导出`exports/pool_124_1794394266434501991/`。22项正常评分任务为18成功/4失败，其中3项生成预算耗尽、1项64k上下文耗尽；另有1项最初0请求启动异常、1项数值答案评分异常。任务`16be9ce_2`提交的公开答案不能直接解析为浮点数，官方完整报告在`normalize=float`处记录`ValueError`；保留原`evaluation_error/unknown`，不重评分、不算成功、不重试模型。此异常触发自动停止后，核对同时间另一任务成功、无provider错误及所有claim已有结果，沿用相同配置/锁恢复下一道未尝试任务，检查依据保存在`resume_reviews/`。已完成任务共575请求、8402202输入/150705输出token，Go峰值费率估价USD0.844066344（非账单、不含在途任务）。

| 检查 | 结果 |
| --- | --- |
| 新采集、Teacher、Harness、Schema、SFT质量针对性测试 | 147 passed |
| v2、nullable恢复、B10及新采集回归 | 86 passed |
| 最终导出修复：`pytest -q tests/test_teacher_collection.py tests/test_harness_refactor.py tests/test_teacher_data.py` | 63 passed |
| export版本：`PYTHONPATH=.:src .venv/bin/python -m pytest -q` | 908 passed、1 skipped、3上游弃用警告 |
| quarantine版本：`.venv/bin/pytest -q tests/test_teacher_collection.py tests/test_harness.py tests/test_harness_refactor.py tests/test_teacher_data.py` | 91 passed |
| quarantine版本：`PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/test_v2_learning.py` | 15 passed |
| 持续守护：`.venv/bin/pytest -q tests/test_teacher_watchdog.py` | 39 passed |
| 11月12日完整单题负报告隔离：同上测试命令 | 59 passed |
| 11月12日SSL等待/恢复：同上测试命令 | 61 passed |
| 最终256条数据、train归属、唯一性、严格示范契约、Manifest与编码文件SHA复核 | PASS；无新增模型调用 |
| `.venv/bin/ruff check src scripts tests` | 通过 |
| `.venv/bin/ruff format --check src scripts tests` | 198文件通过 |

在`.venv-v2`直接执行全量测试曾因相同freezegun/ML扩展组合崩溃，未记为通过；上述完整结果来自项目标准`.venv`测试环境。新采集worker单独应用已验证的兼容设置。

软件测试验证train隔离、旧成功去重、64k边界及输出预留、Assistant-only mask、整条超长暂缓、Go会话头、累计目标停止、未决请求不重发及旧锁拒绝新源码。最终新增v2完整采集→导出合成测试、篡改来源/Guard/Harness/回执拒绝，以及停止派发测试。真实采集登记272个唯一任务attempt：151项官方成功、98项失败、23项异常，未将异常冒充完成评分。最终256条合格示范（旧106＋新150）完成64k编码及独立文件验收；本轮没有进行模型训练或效果评估。
