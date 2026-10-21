# B失败分析与冻结A单次复试（2026-10-21）

本轮基线 `e4a6f1ae296524f775a09b00b7178f2a00d51ea6`。用户明确要求分析上一轮B失败，并用A再试一次，随后要求解释原因和修改方法。**真实诊断复试/数据验证 PASS；修正方案未启用，效果未验证；SFT/GRPO仍未启动。**

## 实验边界

选择上一轮12任务中的全部6个有效失败（4个场景家族），固定原顺序，从原始初态各跑A一次，不恢复B终态。使用历史结果选子集是本次用户授权的条件诊断，manifest明确标记，不伪装成不看结果的抽样；不能用B子集0/6、A2/6宣称A总体更强。也不能把原B12次与新A6次混算为一次同预算总体成功率。

同一deepseek-flash/fingerprint、同任务初态、环境seed42；execution_v3/dependency_examples_v2与五文件协议锁均不变。每任务40请求（包括A Predictor）、16384输出token、600秒、USD1，单回复4096、工具数不限；六任务总240请求/98304输出/3600秒/USD6。费用授权沿用“费用不限”，无付费重试。

6条A Predictor输入从公开Task(load_ground_truth=False)和目录重新构建一致；Actor初始输入仅是冻结A提示和与B一致的公开任务。未传入B历史、诊断或隐藏评价；A执行期间schema保持固定。隐藏评价仅离线控制侧分析，没有改验证器、修正终态或补做业务步骤。

## 原B失败原因与A结果

| 任务 | 原B直接失败原因 | A复试 | A实际行为/失败原因 |
| --- | --- | --- | --- |
| 29caf6f_1 | 短信业务检查7项全过；动作任务多填answer | 成功 | 发出正确列表，最终complete_task({})，省略answer |
| 22cc237_1 | 付款业务检查3项全过；完成摘要误填answer | 失败 | Predictor漏simple_note.login；无法读笔记，23次工具错误，末回复4096-token截断，没有创建请求 |
| b0a8eae_3 | 播放器状态检查4项全过；完成摘要误填answer | 失败 | 播放状态再次通过，但仍填了answer摘要 |
| 29caf6f_3 | answer多填；短信前缀还触发禁含其他标题的子串检查 | 成功 | 只发送逗号分隔标题，完成时省略answer |
| b0a8eae_1 | 播放器状态检查4项全过；完成摘要误填answer | 失败 | 正确播放，仍把完成摘要写入answer |
| 287e338_3 | 读全推荐后聚合错误 | 失败 | 仍读全20/8/0三页，仍选了出现4次者；真正最高5次 |

原B六条中：4条仅完成参数错误，1条完成参数+短信格式/子串问题，1条统计错误。不能简单归为“不会使用工具”或“预算不够”。全部原B失败保持官方奖励0，不因状态检查已通过而补成成功示范。

### 为什么会发生，应该怎样改

**1. 模型把动作完成说明当成answer。** 官方公开schema明确要求仅问答任务提供answer，动作任务省略。execution_v3也写了“no invented answer”，但真实完成说明不被模型当作编造；这是可能的提示歧义，不是可观测的内部推理。可以确认的是提示/schema一直可见、参数却未遵从。JSON/schema校验只能验证字符串类型合法，不能证明答案语义符合任务。候选修改：明确动作任务必须省略answer键，摘要放普通Assistant文本；问答/混合任务按公开指令判别，不凭问号。不要由Runner根据隐藏答案静默删字段。

**2. 多余自然语言影响精确输出。** 第二条短信添加英文介绍，官方检查采用归一化子串匹配、不区分词边界，前缀碰到了不应出现的短标题。正文主要推荐并非错误，但用户要求逗号分隔列表，加入前缀也没有必要。验证器对前缀敏感是局限，不能据此改验证器。候选修改：工具的正文参数只含要求的序列化内容，叙述放在普通Assistant文本，不针对隐藏词做禁词表。

**3. 获取数据与正确聚合是两步。** 推荐任务两模式都读完28首、无重复song_id；8首有多作者，共41个song-artist配对。按完整artists列表重算最高5次，模型选择4次者。不能断言它内部只数第一作者，但公开证据明确把错误定位在聚合。候选修改：按stable ID逐页计数、展开全部作者、核对配对总数和候选最大值。离线重算用于诊断，不能直接给Actor答案；若新增计算工具，必须另设工具协议和成本记录。

补充：B的b0a8eae_1把22首总时长写成5371秒，真实公开duration合计5171秒，A该次计算正确。它没有改变足够长的歌单选择，官方播放状态仍通过，因此是伴随算术错误，不是本次最终失败的直接原因。

**4. A提示要求补依赖，但没有组合可执行性检查。** 付款任务Predictor正常stop，只输出91token、14个合法API，未触达20种上限，却漏simple_note.login。功能简介和两个正例无法保证依赖闭合；名称合法不等于具备认证路径。Actor又违反“不要猜凭据”的已有规则，20次search_notes、1次show_note和2次venmo.login报错（另有成功调用），最后长篇重复产生4096输出截断；全任务输出12498，未触达16384总额度。不是工具调用配额截停。

候选修改应包含新A变体的公开schema依赖校验：一次性开放前检查所需token/ID的获取路径，最多一次向Predictor返回公开缺依赖信息并重新选择，预测成本计入；校验仍失败则停止。不要Runner偷偷补工具或登录。Actor也需在缺少可用认证路径时报告失败，认证重试须有新证据；重复错误停机应另设版本并测误停。

上述候选完整条款、通用正例和验收方式见 `docs/PROMPT_FIX_PROPOSAL.md`。**未修改冻结提示，未启用新变体，未声明这些修复已经改善成功率。** 两条A成功仅是一次重采样观察，不能将改进全归因于工具模式。

## 用量与筛选

| 同一6任务条件子集 | B原执行 | A复试 |
| --- | ---: | ---: |
| 模型请求（含预测） | 77 | 71 |
| Actor回合 | 77 | 65 |
| 工具调用 | 128 | 97 |
| 输入token | 905059 | 626630 |
| 缓存输入token | 588544 | 559616 |
| 输出token | 14982 | 24314 |
| 费率估算USD | 0.116464164 | 0.052638696 |
| worker秒 | 238.526 | 269.685 |

A新增总成本为71请求（6 Predictor+65 Actor）、97工具、626630输入（559616缓存）、24314输出token；USD0.052638696估算，实际账单未知，未知预留0。采集墙钟277.082秒。A输入/费用下降但输出更多，受一条缺认证任务循环影响；不能据条件子集推广总体成本优势。旧B成本已发生，不作为本轮新增费用重计。

| 任务 | A官方成功 | 模型请求 | 工具 | 输入token | 输出token |
| --- | --- | ---: | ---: | ---: | ---: |
| 29caf6f_1 | 是 | 9 | 11 | 69588 | 1512 |
| 22cc237_1 | 否 | 23 | 29 | 213405 | 12498 |
| b0a8eae_3 | 否 | 12 | 24 | 138300 | 4229 |
| 29caf6f_3 | 是 | 8 | 10 | 44981 | 1333 |
| b0a8eae_1 | 否 | 12 | 17 | 130481 | 4037 |
| 287e338_3 | 否 | 7 | 6 | 29875 | 705 |

6次有效官方评价、0基础设施错误，A成功2/6=33.33%（条件成功率）。两条通过来源/格式/官方/循环筛选，4条因official_not_successful拒绝；付款任务另有invalid_call_format。两条新示范属于1个家族，固定Qwen3-4B tokenizer得到15个Assistant目标、2349监督token，最长完整序列12458、最长目标332；schema固定和mask通过，没有加载权重或训练。

历史execution_v3包4条 + 上轮B包6条 + 本轮A包2条，共12条合格完整示范、10唯一任务、6家族，仍分包保留，未混成新的总体成功率或正式训练集。本轮是原始初态复试，不是Teacher从失败检查点恢复。

## 代码、证据与命令

采样器新增显式条件诊断来源及SHA绑定，旧随机采样manifest保持原哈希。诊断脚本只输出原因类别、计数和哈希，完整隐藏评价保留本地；截断响应单独验证未执行尾部与已完成前缀，不把截断消息伪造成合法Assistant示范。107软件测试及Ruff通过，P0–P2原17项和冻结提示五文件不变。

- `reports/a_failure_retry_comparison.json`：逐任务诊断、同初态/模型/预算与公开Predictor重构、完整成本。
- `reports/b_failure_diagnosis.json`、`a_failure_retry_specific_checks.json`：原失败分类、公开观察重算与关键细节。
- `reports/a_failure_retry_receipts.json`、`a_failure_retry_checks.json`：回执/终态哈希与完整LLM视图索引。
- `reports/a_failure_retry_quality.json`、`a_failure_retry_tests.txt`、`a_failure_retry_ruff.txt`：数据与软件证据。
- 原始证据 `runs/a_failure_retry_20261021_a/`；完整凭据脱敏视图 `outputs/a_failure_retry_20261021_a/<task_id>.md`，保留本地。
- 新Clean Dataset manifest SHA-256：`2e30bc2e55689e0a0d474dfd76ed23f73492deb1cc0f08c1c5f0428021a2519d`。原始受限数据/密钥不提交GitHub。

实际运行命令（输出已存在，复现时换新run/report路径）：

```bash
export PYTHONPATH=src
export APPWORLD_CACHE="$PWD/.cache/appworld" IPYTHONDIR="$PWD/.cache/ipython"
.venv/bin/python -m recoveragent.p3a collect --config .cache/a_failure_retry_20261021_a.json --output runs/a_failure_retry_20261021_a --credentials-file .env
.venv/bin/python -m recoveragent.demonstrations verify runs/a_failure_retry_20261021_a/clean_dataset --appworld-root data/appworld --write-receipt
.venv/bin/python scripts/analyze_failure_retry.py --previous runs/scenario_expansion_20261021_a --retry runs/a_failure_retry_20261021_a --output reports/a_failure_retry_comparison.json
.venv/bin/python scripts/audit_teacher_pilot.py runs/a_failure_retry_20261021_a --output reports/a_failure_retry_receipts.json
HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 .venv-inference/bin/python -m recoveragent.sft_quality runs/a_failure_retry_20261021_a/clean_dataset --expected-manifest-sha256 2e30bc2e55689e0a0d474dfd76ed23f73492deb1cc0f08c1c5f0428021a2519d --output data/a_failure_retry_sft_20261021_a --report reports/a_failure_retry_quality.json
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
```

采集日志 `.cache/a_failure_retry_20261021_a.log`，tokenizer日志 `.cache/a_failure_retry_quality.log`。最初离线统计误把artists字典当可哈希对象，已改为按公开artist ID展开并去重，新增多作者/重复页测试；该错误没有进入Actor执行或计费请求。

下一项为审查并选择候选修正版本，在新的预定场景上检验其收益；保持原提示和本次负例。正式数据规模、分组验证、远程96GB环境和真实4B后端仍待完成，没有正式训练结果。
