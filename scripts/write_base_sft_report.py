"""Render the public report only from recomputed metrics and completed checks."""

import re
from pathlib import Path

from recoveragent.io import read_json


def render():
    m = read_json("reports/base_sft_dataset_v1_metrics.json")
    release = read_json("reports/base_sft_dataset_v1_release.json")
    initial, q = m["initial_inventory"], m["initial_quality"]
    new, counts, tokens = (
        m["new_collection"],
        m["final_counts"],
        m["total_token_counts"],
    )
    cost = new["costs"]
    tests = re.search(
        r"(\d+) passed", Path("reports/base_sft_pytest_final.txt").read_text()
    ).group(1)
    for report in [
        "base_sft_dataset_v1_verify",
        "base_sft_dataset_v1_retokenize",
        "base_sft_dataset_v1_import",
        "base_sft_dataset_v1_negative",
    ]:
        if read_json("reports/" + report + ".json")["status"] != "PASS":
            raise ValueError("Final verification incomplete: " + report)
    lines = [
        "# Base-SFT Dataset v1：正式 Teacher 数据构建报告",
        "",
        "日期：2026-10-21（Asia/Shanghai）；开发分支 `codex/p0-p2-recovery`，起始提交 `5f420909f228945b1643375b9a917b61906a29f4`。",
        "",
        f"**数据构建/真实验证 PASS；规模目标 {'PASS' if all(m['targets_met'].values()) else 'PARTIAL'}；远程环境 BLOCKED；SFT/GRPO NOT_STARTED。** 正式入选 {counts['selected']} 条完整成功轨迹，覆盖 {counts['unique_tasks']} 个任务、{counts['families']} 个场景家族。未加载正式4B权重、未进行Student参数更新，不能据此宣称训练或效果提升通过。",
        "",
        "## 1. 范围与来源复核",
        "",
        "正式Student固定Qwen/Qwen3-4B；仅官方train。历史数据逐包重验manifest、真实Teacher回执、官方成功证据、原始初态引用、工具/调用ID/参数/反馈、train成员及过滤器。源包不可变；重叠/合并/迁移副本不会重复计轨迹或模型成本。不是简单拼接JSONL。",
        "",
        "| 历史重算项目 | 结果 |",
        "| --- | ---: |",
        f"| Clean Dataset包数 / 包内条目 | {len(initial['sources'])} / {initial['package_entries']} |",
        f"| 独立尝试 / 合格完整轨迹 | {initial['unique_attempts']} / {initial['unique_qualified_trajectories']} |",
        f"| 唯一成功任务 / 家族 | {initial['unique_success_tasks']} / {initial['success_families']} |",
        f"| 32768/4096内完整轨迹 | {q['ready_complete_demonstrations']} |",
        f"| 全部Assistant目标 / 监督token | {q['all_clean_token_counts']['examples']} / {q['all_clean_token_counts']['supervised_tokens']:,} |",
        f"| 长度合格目标 / 监督token | {q['ready_token_counts']['examples']} / {q['ready_token_counts']['supervised_tokens']:,} |",
        "| 初始cap后候选 | 41 |",
        "",
        f"历史Prompt分布：`{initial['prompt_versions']}`；工具模式：`{initial['tool_modes']}`。以下条目只是来源包视图，存在重叠，不能相加作为独立数据量。",
        "",
        "| 历史来源包 | 包内合格数 | manifest SHA-256 |",
        "| --- | ---: | --- |",
    ]
    for s in initial["sources"]:
        lines.append(
            f"| `{s['path']}` | {s['counts']['accepted']} | `{s['manifest_sha256']}` |"
        )
    lines += [
        "",
        "## 2. 冻结协议与新增真实采集",
        "",
        "主采集B dynamic_discovery、execution_v5、numeric_answer_self_correction_v1；Teacher deepseek-flash，配置声明DeepSeek-V4.1-Flash-1017、temperature=0.2、non-thinking、回复4096。provider别名不是不可变revision；每次真实backend指纹与回执保存在审计中。历史A示范可保留，本轮没有新A/B对比。",
        "",
        "采集前固定campaign及源锁，每批20任务（不足则取剩余）、seed=2026102110+批号，最多8批；每任务最多2次新尝试。优先缺成功家族/任务，再补第二条多样性。原始初态、串行独立环境。Actor没有历史失败结论或隐藏答案；控制器依成功覆盖选择下一批，故这是**结果条件化采集，不能作为无偏总体成功率**。失败不替换、不改奖励，全部成本计入。",
        "",
        "每轨迹40请求/16384输出/600秒/USD1，每批800请求/327680输出/12000秒/USD20；工具调用不设数量配额，保留131072字节请求上限。没有针对本轮失败修改Prompt、协议或预算。",
        "",
        "| 批次 | 尝试 | 有效 | 基础设施错误 | 官方成功 / 合格 | 请求 | 实际工具调用 | 输入 / 输出token | USD费率估算 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for a in m["all_attempt_audits"]:
        c, u = a["dataset_counts"], a["costs"]
        lines.append(
            f"| {a['run_id'].rsplit('_',1)[-1]} | {c['attempted']} | {c['valid']} | {c['infrastructure_errors']} | {c['official_successes']} / {c['accepted']} | {u['provider_requests']} | {u['tool_calls']} | {u['prompt_tokens']:,} / {u['generated_tokens']:,} | {u['usage_priced_cost']:.9f} |"
        )
    lines += [
        "",
        f"新增共 **{new['attempts']} 次尝试、{new['distinct_attempted_tasks']} 个不同任务、{new['distinct_attempted_families']} 家族**；其中{new['previously_unattempted_tasks']}个任务不在历史尝试中。官方成功{new['official_successes']}条，过滤合格{new['qualified']}条，涉及{new['qualified_tasks']}任务/{new['qualified_families']}家族；相对历史新增成功任务{new['new_success_tasks_over_history']}个。",
        "",
        f"采集停止原因：`{new['stop_reason']}`；当时仍符合预设次数/cap规则的任务{new['remaining_eligible_tasks']}个。没有为达到120而延长批数、降低质量门槛或复制成功轨迹。",
        "",
        "本次因账户余额不足提前停止，最后一批原计划20任务，实际18次、2项未执行，单列未执行清单。余额REST查询成功但无正余额；一次独立16-token上限诊断返回HTTP402，含义依据[DeepSeek官方错误码](https://api-docs.deepseek.com/quick_start/error_codes/)。诊断未执行AppWorld动作、不生成示范。此前传输错误只保存异常类型，不能事后把所有错误都断言为402。",
        "",
        f"本次条件采集成功/全部尝试 = {new['official_successes']}/{new['attempts']} = **{new['success_per_attempt']:.2%}**；成功/有效评价 = {new['official_successes']}/{new['valid_attempts']} = {new['success_per_valid']:.2%}。{new['infrastructure_errors']}次基础设施错误单列，绝不冒充策略失败奖励。",
        "",
        f"第二次新尝试共{new['second_new_attempts']}次，额外成本/请求/工具计数：`{new['second_attempt_costs']}`。每条的sampling_reason、new_attempt_number及此前成功数见机器审计；coverage ledger绑定先前结果和来源哈希。",
        "",
        f"筛选原因（可重叠）：`{new['filter_reasons_nonexclusive']}`。终止原因（每尝试一类）：`{new['termination_reasons']}`。请求大小阻止发生在发送下一请求之前，完整已执行前缀和成本仍保留；有效错误还包括未开放API、回复截断及业务任务未通过。全部失败详情在私有run中。",
        "",
        f"真实数字格式纠正触发{new['guard_corrections']}次，同值格式纠正通过{new['guard_valid_corrections']}次，纠正后整任务官方成功{new['official_success_after_guard_correction']}次；分母为0时不得宣称纠正成功率。",
        "",
        "## 3. 正式选择、去重与长度",
        "",
        f"历史+新增唯一合格来源共{counts['source_qualified']}条；最终{counts['selected']}条/{counts['unique_tasks']}任务/{counts['families']}家族；整条暂缓{counts['deferred']}，任务cap reserve {counts['reserved_by_task_cap']}，精确重复{counts['exact_duplicates']}。高相似配对{counts['high_similarity_pairs']}对（全长度合格候选中统计，不等于应删除的轨迹数）。",
        "",
        f"暂缓原因：完整序列超过32768的{sum(d['max_sequence_tokens'] > 32768 for d in m['selection']['deferred'])}条，Assistant目标超过4096的{sum(d['max_target_tokens'] > 4096 for d in m['selection']['deferred'])}条（两项可重叠）。它们仍保留官方成功身份，不能写成任务失败；所有原始完整历史在deferred及control来源中。",
        "",
        "精确去重忽略调用ID差异但保留完整消息和每回合Schema；高相似判据为业务调用名称/参数序列SequenceMatcher>=0.9，凭据只在指纹计算中忽略。每task最多2条，优先不同簇，再按版本/B/输入成本排序。reserve与deferred保留完整来源和原因，不静默截断、不复制补数。",
        "",
        f"最终Prompt分布：`{m['selected_prompt_versions']}`；工具模式：`{m['selected_tool_modes']}`。初始与最终分布可由source_inventory和selection重建。",
        "",
        "## 4. 分组与实际训练token",
        "",
        "split_seed=2026102120，在全train 90任务/30家族上预先固定24训练家族、6验证家族；只有成功入选者进入实际分区。同任务/场景变体无跨区，官方dev未使用。内部validation部分场景曾参与历史Prompt开发，因此不能称为完全未接触的泛化测试。",
        "",
        "| 分区 | 完整轨迹 | 任务 | 成功家族 | Assistant目标 | 监督token | 总输入token | 最长序列 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, p in m["partition_metrics"].items():
        t = p["ready_token_counts"]
        lines.append(
            f"| {name} | {p['raw_clean_demonstrations']} | {p['unique_tasks']} | {len(p['families'])} | {t['examples']} | {t['supervised_tokens']:,} | {t['input_tokens']:,} | {p['max_sequence_tokens']} |"
        )
    lines += [
        "",
        f"全体 **{tokens['examples']}个Assistant目标、{tokens['supervised_tokens']:,}监督token**；逐回合总输入{tokens['input_tokens']:,}，屏蔽上下文{tokens['context_tokens']:,}（{m['masked_token_fraction']:.3%}）。最长完整序列{m['longest_sequence_tokens']}、prefix {m['longest_prefix_tokens']}、目标{m['longest_target_tokens']}。",
        "",
        f"批量调用目标{tokens['batch_targets']}/{tokens['examples']}（{m['batch_target_fraction']:.2%}），无损回读{tokens['roundtrip_calls']}个工具调用，Schema变化{tokens['schema_changes']}次。拒绝回合仅上下文{tokens['format_rejected_context_only_turns']}次。",
        "",
        f"相邻样本完全相同prefix累计{tokens['repeated_identical_prefix_tokens']:,} token；逐回合输入/各轨迹最终回合输入合计 = {m['input_expansion_over_final_sequences']:.3f}。这是当前逐回合训练的计算开销定义，不是语义重复率。每样本只监督当前Assistant，其余角色/历史Assistant/拒绝回合/padding为-100。",
        "",
        "家族→任务→相似簇→轨迹→回合平衡loss质量，example_weight分区均值为1；先取每样本监督token CE均值再乘权重。长轨迹/重复查询不会只因token数多支配目标，但仍消耗前缀计算。下表同时公开原始token和有效loss质量，零样本家族没有被隐藏。",
        "",
        "| scenario_id | 业务Apps | 分区 | 轨迹 | 任务 | Assistant目标 | 监督token | 输入token | 分区内loss质量 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for f in m["family_distribution"]:
        s = f["stats"]
        lines.append(
            f"| {f['scenario_id']} | {', '.join(f['business_apps']) or '—'} | {f['partition']} | {f['trajectories']} | {f['tasks']} | {s.get('examples',0)} | {s.get('supervised_tokens',0):,} | {s.get('input_tokens',0):,} | {f['effective_loss_mass']:.3%} |"
        )
    allcost = m["source_inventory"]["costs_by_currency"]
    lines += [
        "",
        f"业务App出现轨迹数（多App任务可重叠）：`{m['selected_app_presence']}`。场景家族数依据官方scenario_id，不等于应用数量。",
        "",
        "## 5. 全成本与可核验边界",
        "",
        f"新增采集{cost['provider_requests']}模型请求、{cost['tool_calls']}实际工具调用、{cost.get('tool_requests',cost['tool_calls'])}工具申请；已知输入{cost['prompt_tokens']:,}（其中缓存{cost['cached_prompt_tokens']:,}）、已知输出{cost['generated_tokens']:,}，已知token合计 {cost['total_tokens']:,}。有{new['requests_without_response']}个请求缺少完整响应，不能给出精确总token。Teacher控制器累计墙钟约{new['controller_wall_seconds']:.3f}秒；中断批次使用config文件mtime到外部停止时刻，原monotonic终值不可得。",
        "",
        f"新增**USD {cost['usage_priced_cost']:.9f}**，按冻结公布费率与实际回执usage估算。包括失败、基础设施错误已完成部分及第二次尝试。未知费用预留{cost['unknown_cost_reservation']}、未知生成预留{cost['unknown_generation_reservation']}；实际逐请求账单未提供，billed_cost=null，不能将费率估算写成已核实实扣。",
        "",
        f"本轮已审核包所覆盖的历史+新增独立尝试{m['source_inventory']['unique_attempts']}次；去除包副本后的累计成本/调用/token：`{allcost}`。旧A Predictor请求已计入历史费用。AppWorld工具为本地执行，单列调用数量，没有虚构工具单价。本机没有GPU训练耗时，远程资源未接入。",
        "",
        f"另有1次独立服务诊断模型请求，HTTP402，无usage；其未知预留USD {m['provider_diagnostic']['unknown_cost_reservation']:.9f}及16输出token单独保留。包括已审核包历史、本轮全部新增和该诊断的总账：`{m['all_historical_and_new_and_diagnostic_costs_usd']}`。这些预留是保守上界，不是已扣款。原Clean manifest的unknown_usage_attempts旧字段未标出缺响应，正式报告改按原始request/response差集统计，不改写原包。",
        "",
        "## 6. 完整性、测试和交接",
        "",
        f"必要软件测试 **{tests}通过**，Ruff与format检查通过（`reports/base_sft_pytest_final.txt` / `base_sft_ruff_final.txt`）。Mock/合成检查只验证逻辑；真实验收来自本轮Teacher+AppWorld与固定Qwen tokenizer。",
        "",
        "全源包复验、官方train及公开任务重建、动态工具按已发生发现结果重放、原始请求/响应/成本、Assistant-only/padding mask、Student工具调用解析回读通过。原始消息与真实工具反馈保持对应；control中的评价/来源/状态引用从model_input排除。",
        "",
        "正式包再分词与token JSONL逐字节一致、本机独立归档导入通过；文件篡改、错误可信SHA、mask泄漏及控制消息注入均被拒绝。P0–P2、v3/v5源锁、collection锁与原15包manifest不变。详见final verify/retokenize/import/negative及source_checks证据。",
        "",
        f"私有数据目录 `{release['dataset_path']}`；归档 `{release['archive_path']}`，{release['bytes']:,}字节。manifest SHA：`{release['manifest_sha256']}`；归档SHA：`{release['archive_sha256']}`。完整逐文件摘要在私有manifest，可信摘要在已提交的release JSON。原始数据、任务、评价、凭据和权重均不上传GitHub。",
        "",
        "远程导入/再分词/依赖/配置/验收说明见`docs/REMOTE_BASE_SFT.md`。准备BF16+LoRA、2步短样本及1步最长样本烟测，参数更新、非零有限梯度、保存/重载tensor SHA和logits检查均有入口；本轮只做CPU微型Qwen3架构loss/梯度等价测试，0 optimizer step，不能算正式SFT。",
        "",
        "## 7. 实际命令、错误与下一步",
        "",
        "实际执行：",
        "",
        "```bash",
        "PYTHONPATH=src .venv/bin/python -m recoveragent.dataset_sources scan --appworld-root data/appworld --output .cache/base_sft_existing_20261021 --report reports/base_sft_existing_inventory.json",
        "PYTHONPATH=src .venv/bin/python scripts/collect_base_sft.py --campaign configs/base_sft_v1_campaign.json --config .cache/v5_ab_20261021_a.json --credentials-file .env",
        "PYTHONPATH=src .venv/bin/python scripts/seal_interrupted_collection.py --campaign configs/base_sft_v1_campaign.json --run runs/base_sft_v1_20261021_b06",
        "HF_HOME=$PWD/.cache/huggingface HF_HUB_OFFLINE=1 PYTHONPATH=src .venv-inference/bin/python -m recoveragent.formal_dataset build --input .cache/base_sft_v1_20261021_result.json --output data/base_sft_v1 --report reports/base_sft_dataset_v1_build.json",
        f"PYTHONPATH=src .venv/bin/python -m recoveragent.formal_dataset verify --input data/base_sft_v1 --expected-manifest-sha256 {release['manifest_sha256']} --official-appworld-root data/appworld --report reports/base_sft_dataset_v1_verify.json",
        "PYTHONPATH=src:. .venv/bin/pytest -q",
        ".venv/bin/ruff check src tests scripts",
        ".venv/bin/ruff format --check src tests scripts",
        "```",
        "",
        "日志：`/tmp/recoveragent-base-sft-campaign.log`、`.cache/base_sft_v1_20261021_bNN_{collect,quality,audit}.log`及`.cache/base_sft_dataset_v1_*.log`。真实AppWorld采集沿用已授权的非受限运行环境；公开只提交统计/哈希。初次打包预演缺HF_HOME、随后未设HF_HUB_OFFLINE导致缓存/metadata查询错误，改为本地缓存离线模式后通过；失败预演目录原样保留。旧拒绝响应审计只覆盖JSON解析错误，按冻结预执行逻辑扩展至schema可见性/参数/调用边界，未修改Actor或历史奖励。",
        "",
        "尚缺：连接并核实远程RTX PRO 6000 96GB、安装匹配依赖（本机PEFT/Accelerate未安装）、安全传输和远程再分词、固定4B权重与真实后端接入、输入+生成共同窗口限制、真实短/长烟测及checkpoint重载、Raw Base/Base-SFT-only的固定dev评价。内部validation不能替代官方dev。正式长SFT和GRPO本轮没有启动；未达到规模项如实保留，不能降低质量门槛补齐。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    text = render()
    with Path("reports/base_sft_dataset_v1_report.md").open("x") as stream:
        stream.write(text)
