# 已成功 Teacher 数据汇总与超长轨迹处理（2026-10-29）

当前冻结 **131条官方成功 / 131任务 / 131场景**，来自Full732 A244首轮110成功及官方API专项复试已封存的21成功。复试仍暂停在69/134封存，`7e1be84_3`暂停中的未完成记录不进入整理。旧95条未读取或复用，没有新增模型/API请求、GPU、AppWorld业务执行或训练。

## 可用数量与来源

| 来源 | 原样长度合格 | 无损压缩派生候选 | 继续暂缓 | 官方成功 |
|---|---:|---:|---:|---:|
| 新A244首轮中转站 | 102 | 5 | 3 | 110 |
| 官方API专项复试（暂停69项） | 18 | 1 | 2 | 21 |
| 合计 | **120** | **6** | **5** | **131** |

官方成功与可训练长度是两个不同条件。11条暂缓原本已经通过严格官方成功及独立进程终态10项状态/评分一致性核验；无损表示处理不会产生新的任务成功或修改官方奖励。

最终126条长度合格候选包含**1,900个Assistant监督样本 / 306,947监督Token / 20,398,740屏蔽Token**，最长完整序列32,693；所有完整序列≤32768、单目标≤4096。6条派生候选贡献99样本、17,405监督Token，在这些回合重复历史中共节省437,182输入Token。这是离线SFT样本输入成本，不是在线推理节省或模型效果。

成功来源的原官方split为train22、dev12、test_normal37、test_challenge60；其中合格候选为train22、dev12、test_normal37、test_challenge55。全部属于冻结A侧的独立私有Full732自定义研究，原split保持原名，不冒称官方train-only数据或排行榜成绩。没有改变A/B/C清单、引入B/C任务或随机重新划分。

## 处理方法与逐条长度

本次只实行`json-tool-whitespace-only-v1`：对可严格解析为JSON对象/数组的Tool content，词法扫描去掉字符串外的空格、换行、缩进；保留每个其他字符。不会通过JSON重新序列化去舍入数值、改写转义或去重键。字符串内部的正文、空格、日期、ID、路径、Token和错误内容原样保留；非JSON原样保留。

**删除工具调用0条、删除JSON字段0个、修改Teacher Assistant目标0个。** 保留原任务、每回合当时工具Schema、调用ID/参数及顺序、完整返回配对和Harness反馈。只在原合法历史完整验证之后，用独立renderer编译新输入，不改原历史哈希；新输入保存自己的history_hash及source_history_hash和明确representation。

| task_id | 原最长序列Token | 无损压缩后 | 处理结果 |
|---|---:|---:|---|
| 042a9fc_1 | 35,797 | 27,192 | 压缩派生候选 |
| 0d8a4ee_3 | 35,744 | 28,395 | 压缩派生候选 |
| 277d81d_2 | 43,242 | 33,330 | 继续暂缓 |
| 3fcc458_2 | 50,466 | 38,757 | 继续暂缓 |
| 4441ee9_1 | 32,957 | 25,198 | 压缩派生候选 |
| 5800354_3 | 57,370 | 44,460 | 继续暂缓 |
| 66b7899_1 | 62,533 | 48,152 | 继续暂缓 |
| 6f4b9a5_1 | 44,028 | 32,155 | 压缩派生候选 |
| b4707e8_1 | 32,786 | 26,201 | 压缩派生候选 |
| c1091c7_1 | 88,047 | 62,782 | 继续暂缓 |
| fb05fed_2 | 33,131 | 25,400 | 压缩派生候选 |

5条仍超长分别保留完整原件和deferred索引，当前训练候选不收它们。没有因为只超出少量Token就自动删除前置证据，也没有把超长后的Assistant回合丢掉后宣称完整轨迹合格。

不建议直接按长度删调用、邮件/订单字段、分页结果或“重复”查询。后续参数可能依赖它们；相同查询还可能发生在不同状态。若将来需要字段投影或引用去重，应先定义能在真实运行中一致使用、保留来源且可验证的编译协议，再重新验收；或者另行授权从干净初态采集更简洁的完整Teacher轨迹。本轮不启动这些操作。

## 整理包与使用

唯一推荐使用目录：`data/full732_teacher_successes_20261029`，私有归档`data/full732_teacher_successes_20261029.tar.gz`，16,710,877字节。此前两份试导出目录仅留审计；最终清单绑定不可变暂停receipt，不依赖未来可删除的stop_requested文件。

- `successes.jsonl`：全部131条完整clean结构化轨迹，Teacher thinking不进入Student数据；原始provider回执、thinking和控制侧评价/数据库仍保留在原runs，通过来源SHA引用。
- `native_candidates.jsonl`：120条原样长度合格轨迹。
- `compact_candidates.jsonl`：6条派生策略及每条工具返回的原/新内容哈希、消息位置和调用ID；原文仍在successes，通过`source_demo_sha256`关联，不能冒充原provider请求。
- `deferred.jsonl`：5条仍超长的索引及原因。完整轨迹保留在successes和源runs。
- `assistant_examples.jsonl.gz`：126条合格候选的1,900个已编码样本，包含input_ids、labels、loss_mask、实际可见tools_hash、原/新历史哈希和representation。只监督当前合法Teacher Assistant；System/User/Tool/历史Assistant/Harness反馈/拒绝回合/Padding不监督。
- `inventory.json`：131条来源、split、源review/manifest哈希、两种长度、每次变换记录。
- `manifest.json` / `manifest.sha256`：模型revision、模板、编译源码、文件校验和计数。

可信manifest SHA-256：`05371767befbeca451c2a87a4f6fc94893653166fef935727f657d642b7f7a11`。
可信归档SHA-256：`013bef6549f4441d4ae240561df558c4fcaa52581c435b26124bef6f3d0e80a8`。

远程只迁移这个私有归档及同commit代码。校验归档后解压，用固定本地tokenizer执行：

```bash
sha256sum data/full732_teacher_successes_20261029.tar.gz
PYTHONPATH=src CUDA_VISIBLE_DEVICES='' HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 \
  .venv-inference/bin/python scripts/verify_teacher_inventory.py \
  data/full732_teacher_successes_20261029 \
  --expected-manifest-sha256 05371767befbeca451c2a87a4f6fc94893653166fef935727f657d642b7f7a11 \
  --retokenize
```

归档SHA必须与上面可信值比较，不只相信包里自带的哈希。不要上传这个包到公开GitHub。它是新候选整理包，不能拿去替换旧95条数据锁；未来新M1训练需新recipe绑定本manifest、接受明确的混合表示和来源，接入现有BF16+LoRA管线并完成训练烟测。当前Student线上工具返回并未启用该压缩，不宣称部署协议已变更或训练已通过。

## 复验与发现

- 131条原始成功source及review所有文件SHA核对，独立恢复receipt的10项check全部为true；原A244报告和102候选manifest未变。无模型的新AppWorld复跑没有执行，已有真实环境证据与本轮离线检查分开。
- 131条用缓存的固定Qwen/Qwen3-4B tokenizer实测全回合，不加载模型权重。6条派生全部Assistant目标token逐项与原文一致，Schema按当时回合使用，工具参数解析无损，完整上下文及输出预算检查通过。
- 最终包二次独立进程重读后重新分词：1,900个样本的全部字段/token IDs/mask与首次输出一致。归档解压后的SHA、结构和计数复验PASS。
- 初次导出发现canonical JSON排序改变嵌套Schema键序；例如024c982_3第一样本1688→1678token。已拒绝该试导出，保留日志/旧包/源码审计。修复为原始字段顺序序列化，新增回归测试，生成独立最终包后完整重验PASS。原采集源码/旧数据/运行锁未修改。
- **535 pytest通过、Ruff check通过、145文件format check通过**。新增20项覆盖JSON字符串/数值/转义/重复键/非法文本、调用与原始约束不变、Schema顺序、未来Schema拒绝、mask/目标/遗漏回合、超长整轨迹暂缓、源SHA与迁移篡改拒绝。

公开元数据：[JSON](teacher_success_inventory_20261029.json)、[逐任务CSV](teacher_success_inventory_20261029.csv)。私有命令和测试日志：`runs/teacher_success_inventory_20261029/`。实际新代码仅离线整理模块、校验脚本及测试；采集仍暂停，无自动续跑、重采、训练或GPU。
