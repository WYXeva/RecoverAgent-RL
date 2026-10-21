# v0.10 单值格式检查与新任务 A/B 采集

日期：2026-10-21。基线 `f5ce0596abbddf7cb6d3e8f19f9fd371933363de`，开发分支 `codex/p0-p2-recovery`。本轮实现与软件/真实环境接口验收 **PASS**；真实正常Teacher采集/数据格式验收 **PASS**；真实金额自纠正效果 **NOT_OBSERVED**；正式P3-B及后续训练 **NOT_STARTED**。

## 实际修改与证据边界

用户批准的方案为：明确单一金额/数量使用JSON number；harness只按公开指令保守识别，拦截明确数值字符串，返回标明来源的harness_format_error；下一次真实模型回复是唯一同值纠正机会，原预算不增加。不存在静默改答案或读取隐藏参考值。Teacher和Student复用检查器，原始provider响应与执行参数一致性检查保持。被拒绝的Assistant整回合保留为上下文、loss=-100，真实纠正输出可监督。细节见[协议](../docs/ANSWER_GUARD.md)、D024。

旧四条自然金额失败离线回归全部通过；其中纠正回复是模拟调用，0模型/0环境调用，不改旧奖励、不加入训练数据。独立真实AppWorld探针验证拒绝不修改状态、不完成任务，随后脚本数值调用才执行；1真实工具调用，0模型请求，无任务成功成绩。固定真实Qwen3 tokenizer合成探针验证错误金额/反馈全屏蔽，仅26个纠正token监督，0权重/训练。这三个层次分别见 `answer_guard_offline_20261021.json`、`answer_guard_world_20261021.json`、`answer_guard_qwen_mask_20261021.json`。

## 固定新任务与真实结果

预固定seed=2026102105，官方train的10个任务/5个新场景家族，排除以前12家族。只用公开成员/scene映射；没有隐藏难度、答案或结果挑选。A/B各一次、交替执行先后、独立进程和原始初态，失败不替换。AppWorld固定 `42b5bcf3cd334fee33f0c37c02070a9f5807add5`；Teacher `deepseek-flash`，provider声明revision `DeepSeek-V4.1-Flash-1017`非不可变ID，实际backend及全部回执在审计JSON中。正式Student仍为固定revision的Qwen/Qwen3-4B，未运行权重。

| 指标 | A：固定API选择+公开认证依赖校验 | B：动态发现 |
| --- | ---: | ---: |
| 尝试 / 官方成功 | 10 / 7 | 10 / 9 |
| 真实观察成功率 | 70% | 90% |
| 合格示范 / 唯一成功任务 | 7 / 7 | 9 / 9 |
| 成功家族 | 4 | 5 |
| Actor生成回合 | 75 | 107 |
| Predictor请求（含纠错） | 12 | 0 |
| 总模型请求 | 87 | 107 |
| 真实工具调用 | 194 | 280 |
| 其中文档发现调用 | 0 | 69 |
| 累计输入token | 721,734 | 1,290,737 |
| 其中缓存输入token | 633,344 | 962,816 |
| 输出token | 30,151 | 38,222 |
| 估算费用USD | 0.066498264 | 0.150019596 |
| worker墙钟秒 | 366.751 | 391.734 |

全部20次有效，0基础设施错误，16条官方成功且通过现有筛选，覆盖10唯一任务/5成功家族。筛除4条，原因均为 `official_not_successful`，没有隐藏丢弃。A有2次公开认证选择纠错，属于Predictor阶段，**不是金额答案纠正**。中途口头分模式数字曾误写8+8，已更正；最终数字来自全量回执审计7+9。

| task_id | A官方0/1 | B官方0/1 |
| --- | ---: | ---: |
| 771d8fc_2 | 1 | 1 |
| e7a10f8_3 | 1 | 1 |
| afc0fce_3 | 0 | 1 |
| e85d92a_3 | 1 | 1 |
| 229360a_3 | 1 | 0 |
| 771d8fc_3 | 1 | 1 |
| e7a10f8_1 | 1 | 1 |
| afc0fce_1 | 0 | 1 |
| e85d92a_1 | 1 | 1 |
| 229360a_1 | 0 | 1 |

4条负例：A在afc0fce_3与afc0fce_1未选到可确定室友/同事关系的证据工具，执行模型明确status=fail，未完成点赞/评论状态目标（各5条官方断言失败）；B的229360a_3、A的229360a_1声称库清理完成，但删除专辑集合不满足官方要求（各1条断言失败）。均不是金额答案格式错误，其他已通过断言不等于全任务成功；未对此调提示或重试择优。

**该预定清单没有当前保守识别器可识别的单值数值问句**：A/B各10个unclassified，0格式拦截、0纠正请求。首次格式通过率和纠正成功率分母均为0，应记N/A，不能用16/20宣称金额修复有效。没有为了触发修复而换任务。新旧任务不同，不能将本轮70%/90%与旧60%/80%解释为提升；这也不是Student成绩或完整官方benchmark。

## 成本与预算

全部194模型请求=182 Actor+12 Predictor；474真实AppWorld调用（含发现、验证、失败和完成），另有独立脚本探针1调用不计入Teacher成功率。输入2,012,471（缓存1,596,160）、输出68,373 token。控制器墙钟783.174秒。费率估算USD 0.216517860，实扣账单未知；0未知用量/费用预留。费率来源为配置中2026-10-21核对的 [DeepSeek公开价格](https://api-docs.deepseek.com/quick_start/pricing/)，输入miss/cache/output分别USD0.30/0.006/1.20每百万token，不把估算当账单。

沿用用户“费用不限”授权，实际执行仍有边界：每尝试40端到端请求、16384累计输出、600秒、USD1；总20尝试/800请求/327680输出/12000秒/USD20；每回复4096。工具次数不限。选择纠错和最终格式纠正均消耗原预算。没有模型权重加载、GPU训练、远程任务或新付费服务。

## 数据、mask与迁移

16条Clean示范全部适配当前32k/4096目标限制，143个Assistant目标、44,126监督token。最长完整序列30,474，最长生成前prefix30,361，最大目标2,703。25次动态schema变化、381工具参数回读，真实Qwen模板/Assistant-only/padding mask通过。没有截断、没有暂缓长轨迹。本次真实数据拒绝回合为0，拒绝回合的mask分支由单独真实tokenizer合成探针覆盖。

逐回合训练输入1,509,571 token，其中1,465,445是屏蔽上下文，占97.077%；相邻相同prefix累计1,056,044，逐回合输入/各轨迹最后序列总量=6.379。这是计算开销统计，不是语义去重率；没有实施packing/家族权重。

| 场景ID | 示范 | Assistant目标 | 监督token |
| --- | ---: | ---: | ---: |
| 229360a | 2 | 22 | 10,287 |
| 771d8fc | 4 | 34 | 15,339 |
| afc0fce | 2 | 34 | 11,208 |
| e7a10f8 | 4 | 31 | 6,284 |
| e85d92a | 4 | 22 | 1,008 |

原包 `runs/v5_ab_20261021_a/clean_dataset/`，manifest SHA-256：

```text
9efed4033f06bc2705b8e742ac96f6ee9696d06e634d2eb0bf6b997e52684964
```

真实AppWorld版本/train成员/全部原始回执验证与独立本机复制复验通过，副本 `data/answer_guard_transfer_rehearsal_20261021_a/`；未实际远传。分词产物 `data/answer_guard_sft_20261021_a/`，完整脱敏视图 `outputs/v5_ab_20261021_a/<task>_<mode>.md`，另有合成纠正视图 `outputs/answer_guard_synthetic_mask_20261021.md`。原始任务/示范/验证结果/视图均不上传公开仓库。旧4个包26条合格示范只读复验通过，旧4条金额失败仍维持旧官方奖励；不混写新旧数据或成功率。

## 可复现命令与公开证据

公开配置 `configs/p3a_v5_ab.json` 不含授权或价格凭据；已授权实际配置仅本机 `.cache/v5_ab_20261021_a.json`。所有输出要求新路径，下面是本轮已运行命令：

```bash
PYTHONPATH=src:. .venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
PYTHONPATH=src .venv/bin/python scripts/probe_answer_guard.py offline --run runs/v4_ab_20261021_a --report reports/answer_guard_offline_20261021.json
PYTHONPATH=src .venv/bin/python scripts/probe_answer_guard.py world --report reports/answer_guard_world_20261021.json
PYTHONPATH=src:. .venv/bin/pytest -q tests/test_answer_guard.py::test_teacher_guard_receipts_export_and_context_only_loss --basetemp .cache/answer_guard_pytest
PYTHONPATH=src HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 .venv-inference/bin/python scripts/probe_answer_guard.py tokenizer --sample .cache/answer_guard_pytest/test_teacher_guard_receipts_ex0/dataset/demonstrations.jsonl --report reports/answer_guard_qwen_mask_20261021.json
PYTHONPATH=src .venv/bin/python -m recoveragent.p3a compare --config .cache/v5_ab_20261021_a.json --output runs/v5_ab_20261021_a --credentials-file .env
PYTHONPATH=src:. .venv/bin/python scripts/audit_answer_guard.py runs/v5_ab_20261021_a --output reports/answer_guard_checks.json
PYTHONPATH=src .venv/bin/python -m recoveragent.demonstrations verify runs/v5_ab_20261021_a/clean_dataset --write-receipt
PYTHONPATH=src HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 .venv-inference/bin/python -m recoveragent.sft_quality runs/v5_ab_20261021_a/clean_dataset --expected-manifest-sha256 9efed4033f06bc2705b8e742ac96f6ee9696d06e634d2eb0bf6b997e52684964 --output data/answer_guard_sft_20261021_a --report reports/answer_guard_quality.json
```

真实world/Teacher运行在既有授权执行环境，避免受限沙箱的TestClient初始化问题，依赖源码未改。软件测试142项通过、Ruff和60文件格式通过；日志为 `.cache/answer_guard_{collect,audit,verify,quality}.log`，公开元数据见 `reports/answer_guard_*.json`、`*_final.txt`。本轮开发初期预算终止名断言与既有实现不一致已修正；首次临时fixture路径查找失败后改用固定basetemp。临时汇总脚本结构读取错误已纠正，原始执行证据不变。

P0–P2原17项、v3五文件、v4提示与选择器均不变；v4旧8文件在f5ce059精确复核，新的共享驱动采用v5独立源锁。历史v4复现需固定checkout，不改旧锁来掩盖变化。见 `answer_guard_source_checks.json`。

## 尚未完成

正式Student批量接口与共享格式检查完成；真实4B权重后端、checkpoint身份、长上下文实际生成仍待验收。P3-B还需要按scenario分组的数据/验证集、采样权重及规模决策、远程96GB硬件和环境预检、安全迁移后的可信SHA复验及明确训练启动授权。尚无SFT参数更新、保存重载或GRPO结果。下一步针对公开数值契约另固定验证清单，验证真实Teacher的首发格式与自然自纠正；不人为注入错误并伪称自然恢复、不把本轮无触发写成纠正成功。
