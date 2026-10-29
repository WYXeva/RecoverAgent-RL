# Full732 Teacher完整采集后的统一成功数据（2026-10-29）

官方专项复试**134/134记录已封存且调度器退出**：34官方成功、77有效官方失败、22未知、1启动中断；133任务实际发出模型请求，126任务收到模型回执并执行公开工具。没有给启动中断、未知评价或缺失回执伪造奖励。原110成功不重复采，首轮40失败+94未知是结果条件化清单，不能作为无偏渠道对照。

续跑从69封存开始，原7e1be84_3的第23份回复在恢复后读取，没有重发；该项原协议纠错耗尽、官方失败，13908.924秒墙钟包含用户暂停。随后仅执行余64未领取任务。原始任务结果、Prompt、源码锁、A身份及预算不变，本轮结束后不自动新采或训练。

## 统一成功数据

| 来源 | 官方成功 | 原样候选 | 无损派生候选 | 继续暂缓 |
|---|---:|---:|---:|---:|
| 原A244首轮 | 110 | 102 | 5 | 3 |
| 官方134专项复试 | 34 | 28 | 3 | 3 |
| 合计 | **144** | **130** | **8** | **6** |

最终**138条长度合格完整候选 / 138个唯一Task / 138个Scenario**。144个成功终态均复核原独立进程10项状态/评分一致性PASS；没有重复同任务来凑数。新整理读取真实源文件与封存review并核验SHA，未读取旧95条。

138候选包含**2,070个Assistant监督样本、338,413监督Token、22,444,500屏蔽Token**；最长完整序列32,693，全部≤32768且每目标≤4096。只监督当前合法Teacher Assistant；System/User/Tool/Harness反馈/拒绝回合/历史Assistant/Padding全部屏蔽。只使用当时可见Schema，Qwen3-4B non-thinking模板和parser不变。

原split来源：全部成功train23/dev13/test_normal40/test_challenge68；138候选train23/dev13/test_normal40/test_challenge62。全部是冻结Full732 A侧自定义私有研究资料，原split不改名，不是官方train-only或排行榜数据；B/C未运行、未重新划分。

## 超长处理

沿用已验收`json-tool-whitespace-only-v1`，只删除合法JSON Tool content字符串外的排版空白；数值词法、转义、正文/ID/日期/凭据字符串、键序、调用ID/参数/顺序、Schema和所有Assistant目标不变。**删除业务调用0、字段0**。原始历史先完整校验，派生输入另存实际history_hash、source_history_hash与每条变换SHA，不伪装为原Provider请求。

8条派生候选共139个监督样本、26218监督Token，累积重复历史输入减少630,418Token；这不是在线模型效果。所有监督目标token与原始版本逐项一致。其余6条仍整条deferred，不截断、不丢后续回合，完整成功原文仍保留。

| task_id | 原最长Token | 压缩后最长Token | 结果 |
|---|---:|---:|---|
| 042a9fc_1 | 35,797 | 27,192 | 派生候选 |
| 0d8a4ee_3 | 35,744 | 28,395 | 派生候选 |
| 277d81d_2 | 43,242 | 33,330 | 暂缓 |
| 3fcc458_2 | 50,466 | 38,757 | 暂缓 |
| 4441ee9_1 | 32,957 | 25,198 | 派生候选 |
| 5800354_3 | 57,370 | 44,460 | 暂缓 |
| 66b7899_1 | 62,533 | 48,152 | 暂缓 |
| 6f4b9a5_1 | 44,028 | 32,155 | 派生候选 |
| 92fe421_2 | 41,017 | 30,645 | 派生候选 |
| 9a16324_2 | 56,343 | 42,519 | 暂缓 |
| b4707e8_1 | 32,786 | 26,201 | 派生候选 |
| c1091c7_1 | 88,047 | 62,782 | 暂缓 |
| f6f2e67_2 | 35,406 | 29,094 | 派生候选 |
| fb05fed_2 | 33,131 | 25,400 | 派生候选 |

## 交付与迁移

最终目录：`data/full732_teacher_successes_complete_20261029`。归档：`data/full732_teacher_successes_complete_20261029.tar.gz`（18,479,899字节）。内容布局和加载注意事项沿用[整理说明](teacher_success_inventory_20261029.md)：successes保存144原始clean、native_candidates保存130原样、compact_candidates保存8派生引用、deferred保存6暂缓引用，assistant_examples.jsonl.gz保存138候选的全部2070编码样本，inventory/manifest记录来源与校验。原provider回执、thinking、AppWorld数据库及官方评价证据仍私有外置，不进入模型上下文。

可信manifest SHA：`96265f67a5828b101f5db32a804008478d7c419a01e079f6e468b8e5f164735f`。
可信归档SHA：`64c2a5854f0a3a9cfab72f71e13682dce5e535ff0b885768b98dc0073d6a7a48`。

```bash
PYTHONPATH=src CUDA_VISIBLE_DEVICES='' HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 \
  .venv-inference/bin/python scripts/verify_teacher_inventory.py \
  data/full732_teacher_successes_complete_20261029 \
  --expected-manifest-sha256 96265f67a5828b101f5db32a804008478d7c419a01e079f6e468b8e5f164735f \
  --retokenize
```

新冻结清单：`configs/teacher_success_inventory_complete_20261029.json`。重建用同commit的`python -m recoveragent.teacher_inventory --config <该清单> --output <新目录>`，需要私有源runs和缓存tokenizer。迁移后仅校验/重分词不需要源DB或API Key。完整数据包不上传公开GitHub。

此前102条首轮包（manifest53dd9f3…）及131成功/126候选快照（manifest0537176…）所有文件SHA保持不变。当前包为独立新增版本，不覆盖旧证据。原始官方专项导出`data/full732_official_retry_20261029/`是来源清单/clean导出；需要token精确复现与后续训练准备时使用本最终整理包，它保留Schema键顺序，已独立重分词逐token匹配。

## 成本、验证与边界

官方复试含1次独立合成连接诊断，共2059模型请求尝试、已知输入39,107,949/输出1,458,073Token、4320实际工具调用。诊断不计Teacher示范，原relay费用不混算。按锁定官方峰值费率估算33.08476232 CNY；未知请求费用保守预留27.68896 CNY不是实际扣费。结束余额查询首次SSL错误、一次只读复查200；两次账户快照净增加0.65 CNY，原因未核验，缺少独占账户/逐请求账单，**无法确认本轮实际扣费**。详见[复试报告](full732_official_retry_20261029.md)和[134项对照CSV](full732_official_retry_20261029_comparison.csv)。

真实Teacher执行、官方评分及跨进程终态验收由源回执证明；本次最终整理只用CPU tokenizer。全部2070样本独立重新分词token IDs/mask/元数据一致，压缩包解压复验PASS，旧包SHA通过；535pytest、Ruff check、145文件format全部通过。私有证据位于`runs/full732_official_retry_20261029_setup/`，公开[JSON](full732_teacher_dataset_complete_20261029.json)/[逐任务CSV](full732_teacher_dataset_complete_20261029.csv)。

采集已结束。模型失败、未知/中断和6条超长继续保留，未自动重试。尚未启动新M1 Base-SFT、Recovery-SFT、GRPO、Student模型/GPU或B/C。这个包通过数据/格式验收，不代表训练通过；后续新训练配置必须绑定本manifest、明确混合表示和任务来源并通过独立训练烟测，不能覆盖旧checkpoint或沿用旧95锁。
