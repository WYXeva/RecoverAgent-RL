# Full732 A244 官方 DeepSeek 失败/未知专项复试

本轮将首轮40个FAILED和94个UNKNOWN任务固定为134项独立复试，使用第二组官方凭据及 `https://api.deepseek.com` 的 `deepseek-flash`。原110个成功任务（含8个超长）未重跑，原102候选和原244条结果保持不变。这是结果条件化的自定义研究，不能据此宣称无偏渠道优劣或官方排行榜成绩。

## 真实结果

| 项目 | 数量 |
|---|---:|
| 冻结清单/封存任务记录 | 134/134 |
| 有模型请求记录的任务 | 133 |
| 收到真实模型回复的任务 | 126 |
| 实际执行公开工具的任务 | 126 |
| 官方严格成功 | 34 |
| 官方完整评价失败 | 77 |
| 主链未知（包括中断等） | 23 |
| 新合格完整候选 | 28 |
| Assistant监督样本 | 343 |
| 监督Token | 68008 |
| 最长合格完整序列 | 30141 |
| 与原102条并集的合格任务覆盖 | 130 |

状态分布：`{"UNKNOWN": 22, "FAILED": 77, "SUCCEEDED": 28, "INTERRUPTED": 1, "DEFERRED": 6}`。确认成功占完整清单 34/134=25.37%，111项有完整官方成功/失败评价；未知不转换为奖励0。每项结果及原轮次对照见 [comparison CSV](full732_official_retry_20261029_comparison.csv)、[逐项JSON](full732_official_retry_20261029.json) 和 [成本/来源汇总](full732_official_retry_20261029_metrics.json)。不同Task和Scenario在本A侧一一对应；成功与合格数量分别报告。两轮候选保留独立包，只计算覆盖并集，不覆盖或直接拼接旧数据。

## 接入、执行与诊断

- 真实接入固定为官方host、`DEEPSEEK_API_KEY1`，模型目录/余额均真实核验。官方[模型更新](https://api-docs.deepseek.com/updates/)将Flash别名说明为V4.1-Flash-1017；别名非不可变权重，保留每份实际model/response ID/fingerprint，不使用relay任意别名策略。
- 保持execution_v6原文字节、dynamic_discovery、80请求/16384累计生成/4096单回复/600秒和完整Schema/Harness/AnswerGuard。Teacher允许thinking；reasoning仅原始审计和Teacher续传，Student数据使用Qwen3-4B固定non-thinking格式。没有调用Student、GPU或任何训练。
- initial9项后诊断连接异常；diagnostics继承9项并再执行3项，明确观察到request_open的URLError/SSLEOFError。现有本机代理路径不稳定，直连DNS失败；不能据此证明故障具体归属于代理、上游或网络哪一段。
- pooled继承12项、两项试点正常完成模型多轮调用；stable继承14项，错误体读取失败也保留HTTP401/402/403。连接复用依据[Requests官方说明](https://requests.readthedocs.io/en/latest/user/advanced/)，仍验证TLS、禁止重定向、禁止自动重试，不改模型payload。
- 原进程消失时第20项258796c_1只有claim且无请求/工具记录，封存INTERRUPTED，不当作Teacher失败、不自动重跑。新进程用独占锁和新日志恢复未领取任务。
- 用户在69项后暂停并离线整理成功数据，随后明确恢复。7e1be84_3原第23请求回复成功读取，无重发/新请求；按原协议纠错耗尽、官方失败封存为第70项，墙钟含用户暂停。原controller在worker退出后继续余64未领取项，冻结的131条成功整理包不改。
- 32项后仅修正未使用的继承说明字段：旧配置中fresh_campaign的relay价格/条款链接不适用于官方；实际teacher endpoint/key/pricing一直为官方。current继承32项后继续，其余执行参数不变。五版已执行配置、源码包和旧记录均保留。
- 唯一额外付费合成探针为“OK”连接测试，1请求/37输入/12输出Token，不属于Teacher示范。只读models/balance/网络探针也不算模型任务成功。所有已发出而无完整回执的请求保留未知用量，不自动重发。

## 数据、评分与成本

全部34条官方成功的终态均通过独立进程10项状态及评分一致性核验，包含超长暂缓成功。每条候选必须来自本次干净初态、真实公开工具执行、完整官方严格成功、独立进程终态评分/状态等价、合法历史和完整Qwen格式验收。32768上下文或4096目标超限保留deferred；不静默截断。只监督实际合法Teacher Assistant，拒绝回合、Harness反馈、工具返回、历史Assistant、System/User及Padding屏蔽。未知或停后诊断结果不改写原奖励。

新私有数据包：`data/full732_official_retry_20261029`。manifest SHA-256：`fd9ac317feba10945692ff6dc0e08ee73ab36ef1d07bd45893411f632ca6369d`。包内保留成功clean、合格候选、错误登记与各来源/文件SHA；原始请求、reasoning、工具和评价/数据库证据保持私有，通过来源路径和SHA引用。合格数据编码并不代表训练已经通过。

模型请求尝试（含合成探针）2059；已知输入 39107949、已知输出 1458073；真实工具调用 4320。按锁定[官方人民币峰值费率](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)估价 33.08476232 CNY；未知请求保守费用预留 27.68896000 CNY。两者都不是逐请求账单，未知Token也不能记为零消耗。各任务已记录episode墙钟合计 29110.794 秒，其中7e1be84_3的13908.924秒包含用户暂停；没有扣除或伪称正常600秒内自主执行。该总计也不包含全部调试、导出和复验开销。

官方接口两次账户快照显示余额净增加0.65 CNY，原因未核验。该区间余额变化不能用来计算本轮实际支出；缺少逐请求账单和账户独占证据，因此实际采集扣费仍未知，不能把估价或预留当作账单，也不公开余额绝对值/Key。原relay账单不与官方CNY金额混算。

## 验证和复现

- 535项pytest通过；Ruff check、145文件format check通过。历史调度的单元测试使用明确标记的临时测试锁，生产旧锁在改动checkout仍拒绝，不重签历史哈希。
- 各已运行源码版本在独立Git提交及私有frozen源码包中保存；对应锁逐文件核验。当前收集命令：

```bash
PYTHONPATH=src CUDA_VISIBLE_DEVICES='' HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 OMP_NUM_THREADS=1 \
  .venv-harness/bin/python -m recoveragent.official_retry \
  --config configs/full732_official_retry_current_20261029.json \
  --output runs/full732_official_retry_current_20261029 --credentials-file .env
```

该命令只用于核验已有队列/被授权的续跑；已有封存和领取attempt不自动重新付费。导出通过 `scripts/export_fresh_parallel.py`，只在controller退出并获得只读锁后执行。软件证据位于私有setup的pytest_final_20261029 / ruff_final_check_20261029 / ruff_final_format_20261029日志；真实AppWorld/模型/评分/恢复由逐项源目录提供，不能以软件测试代替真实成功。

## 遗留事项

剩余任务失败、未知、启动中断及超长数据均保留，后续统一审查；本轮不再额外重采、改Prompt、改官方评分或开始Base-SFT。官方API仍可能发生连接故障，连接复用并未被证明消除了所有故障。新旧候选来源不同，必须保留来源隔离；下一训练阶段另行核验完整数据准入与授权。


最终统一成功包另见[144成功/138候选报告](full732_teacher_dataset_complete_20261029.md)。原28条官方候选为未压缩口径；全部两轮来源统一重验后130原样+8无损派生=138长度合格候选，旧包保持不变。
