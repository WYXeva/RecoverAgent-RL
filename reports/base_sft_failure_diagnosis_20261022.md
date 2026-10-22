# P3-A v1 失败排查（2026-10-22）

本轮为控制侧诊断，不改 Prompt、工具协议、依赖、官方验证器或既有奖励；不补采、不训练。基线提交 `919e8c83fbe9c17a5f05384ed98781420abb8c02`。新增采集118次全部是 **B dynamic_discovery / execution_v5**；正式95条中B78/A17，A均为历史示范。

## 1. 全部失败重新分类

原始trajectory/evaluation SHA逐项与v1封存审计核对，118次全部对应，58成功、60未成功。下面按终止证据互斥统计；不能把“未成功”都解释为Teacher能力不足。

| 原始结果类别 | 次数 | 可核验证据 |
| --- | ---: | --- |
| Teacher传输失败 | 25 | 请求已存、没有完整响应；旧日志只保存TeacherTransportError |
| 本地请求大小拦截 | 3 | 136823、138279、150614字节，超过131072字节；请求未发出 |
| 调用未开放工具 | 7 | 函数存在于官方目录，但不在该回合tools schema中，整批0执行 |
| 输出截断 | 6 | finish_reason=length；4次达到4096，2次达到剩余额度2327/2024 |
| 回合预算耗尽 | 3 | 都达到40次模型请求 |
| 宣称完成但官方评价失败 | 16 | 其中3条播放轨迹需按评价运行异常解释，见下一节 |
| 官方成功 | 58 | 保留原始官方回执，不重写成绩 |

来源：`base_sft_failure_diagnosis_20261022.json`。历史58/118和58/90仍作为原报告口径保留；90是旧运行器标记的valid次数，并不证明其中每次评价都没有内部异常。本轮没有计算“修复后成功率”。

## 2. 首要新增发现：数据库URI兼容性，非模型选错歌曲

对`c901732_1`原始21次动作进行两次独立重放，对`c901732_2`原始24次动作重放一次。**共66次真实公开工具调用，返回逐条完全一致，0模型请求、0新Teacher示范。** 播放API及公开当前歌曲检查均成功；使用官方`evaluate_task(suppress_errors=False, save_report=False)`后，三次重放都抛出`OperationalError: no such table: songs`。

固定AppWorld源码`42b5bcf3cd334fee33f0c37c02070a9f5807add5`的`Database._get_engine_to_details()`从`engine.url.render_as_string()`反解析内存库名，并直接移除字面量`file:`。当前环境为SQLAlchemy 2.1.5、SQLModel 0.0.48；其`URL.render_as_string()`对database组件执行URL编码。最小真实探针：

```text
期望 home:      :memory:diagnostic
URL.database:   file:diagnostic/spotify.db
渲染后 URL:     sqlite:///file%3Adiagnostic/spotify.db?cache=shared&mode=memory&uri=true
实际解析 home:  :memory:file%3Adiagnostic
```

异常现场对象home包含一次`file%3A`，关系查询时类绑定的home包含两次，查询指向新内存库，缺少`songs`表。官方`TestTracker`默认抑制异常，原日志没有保留这类非AssertionError的异常正文，结果表现为普通失败；项目运行器因返回了tracker而将其标为valid。这解释了三条播放失败的共同异常特征；其中两个不同任务的动作已经真实复现，另一条是同任务的独立历史尝试，未另行重放。

进一步核对发现，90份已评价轨迹的终态`model_hashes.json`全部为空字典。在新的独立进程直接加载一个旧播放终态复评时，官方`changed_model_names()`返回空集；原始SQL增量仍包含真实播放修改，独立读取开始/结束行也不同。这与上述错误home导致计数键错位一致，但尚未完成全部90条跨进程复评。**不能把SHA/重新分词通过当成终态官方复评可复现。** 未发现足够证据认定95条成功全部失效，也不能跳过环境修复直接训练。

证据：`base_sft_failure_playback_probe_20261022.json`、`base_sft_failure_playback_repeats_20261022.json`；可复现脚本`probe_appworld_evaluator.py`。上游源码以本机固定安装为准；[SQLAlchemy URL接口文档](https://docs.sqlalchemy.org/en/21/core/engines.html#sqlalchemy.engine.URL)用于核对结构化database与渲染字符串的区别。没有更改上游源码或测试断言。

## 3. 服务与harness问题

- 2026-10-21独立诊断HTTP402，依据[DeepSeek官方错误码](https://api-docs.deepseek.com/quick_start/error_codes/)为余额不足。本轮未再次调用服务或查询当前余额；不能把旧25条传输错误全部事后标成402。
- `TeacherPolicy._post()`已把HTTP状态写进异常消息，但`p3a.run_teacher`只保存异常类名，因此最终丢失状态。应保存经过白名单处理的status/type/request_id，而非完整可能含凭据的响应正文。
- 所有批次`continue_after_infrastructure_error=true`；控制器没有连续故障熔断，服务不可用后仍进入下一任务及下一批。应将401/402这类全局阻塞立即终止，将429/5xx/超时做有限退避；每个请求仍进入成本账。此处为修复方案，尚未实施。
- 131072是项目本地JSON字节上限，**不是DeepSeek上下文上限，也不是32768个Qwen训练token**。可在新协议中按真实上下文能力重新设置并校验；不可静默丢弃历史或任意扩大预算。

## 4. B模式的具体行为失败

**7次可见性违规。** 都不是不存在的API：模型知道目录里有该函数，却未先获取对应完整schema。例如同时查询`follow_artist`文档并调用尚未开放的`show_artist_following`；查某个工具的文档不会开放另一个工具。当前整批预校验会在任何动作执行前终止整条任务。可考虑新版本显式反馈“未执行”，让模型在原预算内重新发现/重发；不能自动替它开放工具或执行半批。

**6次输出截断。** 4次试图在一条4096-token回复内输出24/25个长参数调用；另2次累计16384-token预算只剩2327/2024，仍尝试批量移动文件。当前拒绝截断整批是正确的完整性保护。应让模型按可用输出预算拆分独立调用、减少重复叙述；工具总调用次数不需重新设配额。冻结v5已经写了拆分规则，说明“再写一句prompt”不能保证解决，后续需真实验证预算提示/格式纠错协议。

**3次预算耗尽。** 两条`82e2fac_3`反复抄错login返回token，分别24/19次调用的token不等于任何此前同应用login返回，持续401并重登录；另一条`d0b1f43_2`已经得到空页，仍继续至page_index=27。全118次中共5条失败轨迹出现51个这种token不匹配调用；它是跨类别的辅助信号，不能与60次互斥类别相加。应精确复用凭据、为空页/重复401提供有限的停滞检测反馈，而非控制器改造token或代做业务。

**完成后仍不满足要求的其余13条。**

| 家族/类型 | 次数 | 原始公开证据与控制侧核对 |
| --- | ---: | --- |
| 分摊账单`3c13f5a` | 2 | 金额/收款人完成，但额外add_friend；官方create_payment_request不要求好友，违反最小修改范围 |
| 推荐统计`287e338` | 2 | 28首歌、41个歌手-歌曲关联；模型选中出现4次的歌手，公开完整计数最大为5 |
| 喜欢且下载的清理`229360a` | 2 | 公开集合可重算出应删13首/6专辑；两次分别删10/5和12/7，AND集合处理和终态核对错误 |
| 年份删除`ce359b5` | 1 | 公开逐首日期可得26个应删歌单-歌曲对，只执行23个，漏3个；读回仍宣称全部完成 |
| 评分`692c77d` | 1 | 歌单歌曲与已喜欢歌曲交集为11，实际处理14，额外3首 |
| 拒绝付款`6ea6792` | 2 | 使用Venmo好友与手机联系人关系时目标集合混淆；其中一次已读公开联系人足以发现漏拒和误拒 |
| 退款`60d0b5b` | 3 | 付款金额不符；一次query检索后改为无query的page1，漏掉新查询page0；另两次只看query首页。已观察的“最新已批准”金额支持模型局部选择，故不能说它仅仅算错钱；应补足稳定查询条件下的证据覆盖，再核对目标请求 |

这里的控制侧错误分析不得转换为Actor答案提示，也不据此挑选易成功任务重报总体成绩。公开报告不包含隐藏答案、完整任务、凭据或原始数据库。

## 5. 后续处理顺序与边界

1. **先修数据库兼容与评价可复现性。** 在新隔离环境验证兼容的SQLAlchemy/SQLModel组合，或采用官方兼容实现；保留旧依赖锁。不改官方验证逻辑。必须通过内存URI往返、真实修改保存/重载、关系查询、非空model-hash保存、独立进程官方复评。再审核已有成功示范能否作为正式训练输入。
2. 加HTTP结构化错误与连续故障熔断，防止账户错误扩散到多任务。
3. 新版本实验再验证可见性错误反馈、凭据原样复用、输出预算拆分、稳定分页与集合核对。v5及v1原包不改，A/B协议改动独立版本化。
4. 账户恢复后才能重新采集。当前最优先不是更换A重跑，而是先确保环境与评价正确；A也共享同一AppWorld环境问题。

诊断与最小复现 **PASS**；修复和全量独立复评 **NOT_STARTED / BLOCKED**；既有包SHA完整性不变，P3-B训练前置增加上述环境核验。本轮未将历史失败转成功、未生成训练数据、未启动SFT/GRPO。

实际主要命令：

```bash
.venv/bin/python scripts/diagnose_base_sft_failures.py --output reports/base_sft_failure_diagnosis_20261022.json
PYTHONPATH=src timeout 120 .venv/bin/python scripts/probe_appworld_evaluator.py --source runs/base_sft_v1_20261021_b02/teacher_997b7a07036e/trajectory.json --namespace diagnostic_playback_20261022_d --output reports/base_sft_failure_playback_probe_20261022.json
PYTHONPATH=src:. .venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
```

另两次21调用重放脚本/完整私有异常位于`.cache/failure_diagnosis_20261022/`；受限环境初次内核初始化未完成，0调用后中断，改在已授权非受限环境运行并设120秒硬超时。总计66次本地工具动作、0付费模型请求、0训练步；软件检查结果见`base_sft_failure_checks_20261022.txt`。
