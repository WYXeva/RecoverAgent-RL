# execution_v4：新场景 Teacher A/B 采集 · 2026-10-21

**实现、真实环境采集、回执/交接校验 PASS；32k数据准备 PARTIAL（14条成功中13条长度合格）；正式训练 NOT_STARTED。** 固定10个新train任务/5家族，20次尝试全部保留。A成功6/10，B成功8/10，得到14条合格原始示范、8唯一成功任务、4成功家族。

## 本轮实际修改

- 新 execution_v4 明确纯动作省略answer、精确正文不加叙述、完整嵌套计数与核对、认证失败不猜凭据。两个通用完成例子不是任务答案或Teacher示范。旧execution_v3五文件和全部历史不变。
- 新A项目变体 api_predictor_validated_v1 检查公开同App token/密码获取路径，缺失时最多一次Predictor纠错。工具仍由模型选择，Actor前一次性固定；不补登录、改答案或暴露隐藏标签。B仍动态发现，共用v4执行规则。
- 多次选择回执纳入费用、导出和离线LLM视图；Predictor对话不进入Actor/SFT。Student批量运行接口保留，真实4B目标通过parser回读；未加载4B推理权重。详见 [协议](../docs/EXECUTION_V4.md)。

## 预先固定的实验

从远端dbe205162ddf5ace72726f6f4942cdf895000185继续。seed=2026102104，公开train ID→scenario均衡采样，每族2变体，排除此前7个诊断家族。清单configs/train_v4_ab_20261021.json、代码锁configs/execution_v4_protocol_lock.json均在模型调用前生成；不用隐藏难度/答案/结果选任务，不换失败任务或付费追试。

同一deepseek-flash、non-thinking、temperature=0.2，221响应均观察到fingerprint e72d3c7f663d3e1383b00b3884cb4501；模型别名不是不可变权重。每任务A/B独立原始初态/进程，按任务交替先后顺序，10对状态/后端一致且worker独立。每次40模型请求（含预测/纠错）/16384输出/600秒/USD1，合计800请求/327680输出/12000秒/USD20；单回复4096，工具次数不限。

## 真实结果和全部成本

| 指标（各10次尝试） | A 公开认证校验 | B 动态发现 |
| --- | ---: | ---: |
| 官方成功 / 全尝试 | 6/10（60%） | 8/10（80%） |
| 成功场景家族 | 3 | 4 |
| 官方成功且筛选合格示范 | 6 | 8 |
| 当前32k长度合格示范 | 6 | 7 |
| Actor回合 | 77 | 133 |
| Predictor请求（含纠错） | 11 | 0 |
| 全部模型请求 | 88 | 133 |
| 工具调用 | 283 | 397 |
| 输入token | 748,729 | 1,757,156 |
| 其中缓存命中 | 652,544 | 1,224,704 |
| 输出token | 30,323 | 36,502 |
| 工具文档发现调用 | 0 | 75 |
| 业务工具调用 | 283 | 322 |
| worker秒 | 375.816 | 435.072 |
| 费率估算USD | 0.069158364 | 0.210886224 |

总计221模型请求、680工具调用、2,505,885输入（1,877,248缓存）、66,825输出token，控制器墙钟835.845秒。费用估算 **USD0.280044588**，逐请求实扣未知（billed_cost=null），未知用量/费用预留为0。A的11次Predictor共89,940输入、601输出、USD0.002301600，已包含在总量，不能重复相加。缓存/顺序影响费用与延迟，失败的完整开销不扣除。费率沿用2026-10-21已核验的DeepSeek公开高峰费率，真实usage来自回执。

| 任务 | A官方奖励 | B官方奖励 |
| --- | ---: | ---: |
| ccb4494_1 | 1 | 1 |
| 6ea6792_3 | 0 | 1 |
| e3d6c94_2 | 1 | 1 |
| 6104387_1 | 1 | 1 |
| 76f2c72_1 | 0 | 0 |
| ccb4494_3 | 1 | 1 |
| 6ea6792_1 | 0 | 1 |
| e3d6c94_3 | 1 | 1 |
| 6104387_3 | 1 | 1 |
| 76f2c72_3 | 0 | 0 |

本轮是新10任务的一次配对描述性结果，不证明统计优势。与旧12任务不同，不能据新旧成功率直接宣称提示修正带来因果提升；新A包含认证检查组件，也不能单独归因于提示。没有SGC总体评测或Student模型成绩。

## 六条失败与一次真实纠错

- 76f2c72_1、76f2c72_3：A/B共4条只失败在答案断言。模型把金额写成带货币符号的字符串，不能通过官方normalize=float。工具无执行错误。PDF不可读是轨迹中的观察，不足以断言它是官方失败原因；本报告没有根据隐藏期望改写答案后重新评分。
- 6ea6792_1 A：只选Venmo/supervisor共6API，缺少关系/联系人查询。认证检查通过，但不覆盖该业务证据缺口；Actor最终报告缺工具并失败停止，未修改付款请求。B通过动态发现获取关系证据后官方成功。
- 6ea6792_3 A：同样漏关系查询。Actor意识到缺口后仍猜测应接受全部26条请求，生成26个调用时达到4096输出上限，finish_reason=length。该批次未执行，保存原始截断响应；不是工具配额不足。此前7次工具调用无报错。筛除原因为invalid_call_format和official_not_successful。B官方成功。
- e3d6c94_3 A：真实触发一次公开密码来源校验错误；Predictor自行重选完整列表、补supervisor.show_account_passwords后，Actor正常执行并官方成功。第二次预测的请求/响应、公开纠错消息、费用均可追溯。这证明本次具体路径工作，不能证明所有缺依赖均被检测。

本轮没有出现旧的“动作完成时填摘要answer”失败，但未重新测试旧已知任务，也没有覆盖所有聚合/精确正文边界。四个数值格式失败和两个A业务依赖失败说明v4仍不充分。本轮采集结束后仍保留冻结配置，没有现场加提示再刷同任务。后续可另设数值答案格式、业务证据依赖和截断后策略的实验，需新版本及预定任务/预算。

## Base-SFT质量与数据交接

14条原始Clean Demonstration均通过官方严格成功、train身份、Teacher身份、完整消息/工具边界、当时schema/参数、原始请求响应一致性和既有循环筛选。另6条全部保留在attempts/evidence和费用分母；0基础设施错误。验证前终态哈希、历史/文件一致性、公开Actor/Predictor输入重建以及221回执成本重算全部通过。额外安全约束仍仅覆盖官方终态检查，未证明没有所有潜在风险。

固定Qwen/Qwen3-4B tokenizer（revision 1cfa9a7208912126459214e8b04321603b3df60c）测得：14条/168个Assistant目标、45,403监督token；最大完整序列34,507、最大目标3,259；35次动态schema变化、623个工具参数回读、Assistant-only与padding mask全部通过。

6104387_1的B轨迹有4回合超过当前32768完整序列限制，因此**整条暂缓**，原始成功不删除，训练目标不静默截断。其余13条（A6/B7）、8唯一任务/4家族，148目标、39,658监督token；最大完整序列31,102、生成前上下文30,976、目标3,259。ready_assistant_examples.jsonl只包含这13条。

14条逐回合输入2,213,689，其中97.949%为屏蔽loss的上下文；相邻相同前缀累计1,527,001 token，累计输入/各轨迹末回合长度=8.373。这是计算重复成本，不能叫语义去重率。最长家族6104387占全部监督token约47.84%，长度合格数据中约40.29%；尚未实施正式采样权重、packing或分组验证。

本地数据包：runs/v4_ab_20261021_a/clean_dataset/；可信manifest SHA-256：

`336bb99c268fe777e4f61c0e2d25ed23c94271f6a43eafa5ea5c30311b08319a`

本机官方成员/版本verify及独立复制data/v4_ab_transfer_rehearsal_20261021_a后的完整校验通过。分词数据data/v4_ab_sft_20261021_a；20份完整脱敏LLM视图outputs/v4_ab_20261021_a/<task>_<mode>.md，含A纠错回执。原始任务、隐藏评价、快照、示范和视图不提交GitHub。未执行远程传输；远端必须使用可信SHA和同版AppWorld重新verify，不能复用本机verified文件冒称远端验收。旧3个execution_v3成功包只读重验通过，仍分包保留，不混算新旧成功率。

## 验证、错误及实际命令

117项软件测试、Ruff和55个文件格式检查通过；这些测试包括模拟依赖缺失/一次纠错/预算耗尽/回执篡改/未返回响应视图，不等同于真实环境验收。真实环境验收来自上述20次Teacher运行。P0–P2的17个原文件和旧五文件锁均未变。

工程错误：首次软件测试命令遗漏PYTHONPATH中的`.`，scripts导入失败，修正命令后通过。新版LLM视图首次导出时，局部纠错events列表覆盖Actor事件索引，触发AttributeError；改为独立变量并补实际组合路径断言，20份视图重生成/核对通过。均发生在本机测试/离线报告，不影响付费调用、环境动作或示范内容。沙箱NVML不可见，正常执行环境只读确认RTX5050/8151MiB；本轮不加载权重/不占用GPU训练。

```bash
PYTHONPATH=src .venv/bin/python -m recoveragent.p3a compare --config .cache/v4_ab_20261021_a.json --output runs/v4_ab_20261021_a --credentials-file .env
PYTHONPATH=src:. .venv/bin/python scripts/audit_v4_collection.py runs/v4_ab_20261021_a --output reports/v4_ab_checks.json
PYTHONPATH=src .venv/bin/python scripts/audit_teacher_pilot.py runs/v4_ab_20261021_a --output reports/v4_ab_receipts.json
PYTHONPATH=src .venv/bin/python -m recoveragent.demonstrations verify runs/v4_ab_20261021_a/clean_dataset --appworld-root data/appworld --expected-manifest-sha256 336bb99c268fe777e4f61c0e2d25ed23c94271f6a43eafa5ea5c30311b08319a --write-receipt
PYTHONPATH=src HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 .venv-inference/bin/python -m recoveragent.sft_quality runs/v4_ab_20261021_a/clean_dataset --expected-manifest-sha256 336bb99c268fe777e4f61c0e2d25ed23c94271f6a43eafa5ea5c30311b08319a --output data/v4_ab_sft_20261021_a --report reports/v4_ab_quality.json
PYTHONPATH=src:. .venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
```

以上命令已执行，报告路径采用只创建不覆盖；复现用新run/output名称。日志.cache/v4_ab_20261021_{collect,audit,receipts,verify,quality}.log。公开机器证据：v4_ab_checks.json、v4_ab_receipts.json、v4_ab_quality.json、v4_ab_data_balance.json、v4_ab_ready_lengths.json、v4_ab_handoff.json、v4_ab_source_checks.json、v4_ab_legacy_dataset_checks.json、v4_ab_tests_final.txt和v4_ab_ruff_final.txt。

## P3-B仍缺什么

正式数据规模/场景分组与验证集、家族/任务权重和超长示范处理尚未定稿；本轮成功家族仅4个。真实Qwen3-4B批量推理后端、权重身份/长上下文行为未验收。远程96GB GPU、同版AppWorld和安全迁移需实测；还需明确训练授权，才能开展Base-SFT真实参数更新、保存重载及开发集评价。本轮SFT/GRPO/远程作业均NOT_STARTED。
