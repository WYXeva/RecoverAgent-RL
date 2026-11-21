"""Render the requested three tables from measured, immutable episode metrics."""

import json
import math
import csv
from pathlib import Path
import argparse

parser = argparse.ArgumentParser(__doc__)
parser.add_argument("--output", default="reports/final_test111_s0_u060_u100")
args = parser.parse_args()
OUT = Path(args.output)
data = json.loads((OUT / "metrics.json").read_text())
rows = data["rows"]
models = data["models"]
manifest = json.loads((OUT / "evaluation_manifest.json").read_text())
complete = manifest.get("status") == "complete"
if complete:
    assert len(rows) == 333
    assert all(m["Episodes"] == 111 for m in models)
    assert all(row["rubric_status"] != "not_run" for row in rows)

# HTTP 422 alone does not distinguish malformed parameters from AppWorld
# business rejections (for example insufficient inventory). Preserve the
# measured events; make the frozen collector's coarse category explicit.
parameter_label = "parameter_or_http422_business_rejection"
for row in rows:
    for event in row["events"]:
        if event["channel"] in ("api", "visible_api"):
            event["types"] = [
                parameter_label if kind == "api_parameter_error" else kind
                for kind in event["types"]
            ]
breakdown_path = OUT / "harness_failure_breakdown.csv"
with breakdown_path.open(newline="") as stream:
    breakdown = list(csv.DictReader(stream))
for row in breakdown:
    if row["error_type"] in (
        "api:api_parameter_error",
        "visible_api:api_parameter_error",
    ):
        row["error_type"] = row["error_type"].split(":")[0] + ":" + parameter_label
with breakdown_path.open("w", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(breakdown[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(breakdown)

# Use the same observed Requester attempts for the cost and API-error columns.
# Preserve AppWorld's own tracked-request counter separately; it can omit calls
# that raise before its tracking hook. Neither counter is inferred from code.
for model in models:
    group = [row for row in rows if row["model"] == model["Model ID"]]
    for source, column in (
        ("business_calls", "Mean AppWorld Tracked Business Calls"),
        ("business_api_attempts", "Mean Business API Calls"),
    ):
        values = [row[source] for row in group if row[source] is not None]
        model[column] = sum(values) / len(values) if values else None
    model["Measured Business Call Episodes"] = sum(
        row["business_api_attempts"] is not None for row in group
    )
    model["Failure Rate"] = model["Official Failure"] / (
        model["Official Success"] + model["Official Failure"]
    )
    error_requests = [
        {event["request_index"] for event in row["events"]} for row in group
    ]
    corrected_requests = [
        {
            event["request_index"]
            for event in row["events"]
            if event["recovered"] is True
        }
        for row in group
    ]
    model["Mean Error-bearing Replies"] = sum(map(len, error_requests)) / len(group)
    model["Mean Replies With Later Correction Evidence"] = sum(
        map(len, corrected_requests)
    ) / len(group)
    model["Error Affected Episodes"] = sum(bool(v) for v in error_requests)
    model["Error Affected Episode Rate"] = model["Error Affected Episodes"] / len(group)
    model["Rubric Unavailable"] = len(group) - model["Rubric Episodes"]
with (OUT / "model_comparison.csv").open("w", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(models[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(models)
data["reporting_revision"] = "test111_measured_api_attempt_cost_v2"
(OUT / "metrics.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def percent(v):
    return "unavailable" if v is None else f"{100*v:.2f}%"


def number(v):
    return "unavailable" if v is None else f"{v:.2f}"


def wilson(s, n):
    if not n:
        return "unavailable"
    z = 1.959963984540054
    p = s / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    delta = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return f"{100*(mid-delta):.1f}–{100*(mid+delta):.1f}%"


def rate(m, a, b):
    x, y = m.get(a), m.get(b)
    return (
        "unavailable"
        if x is None or y is None or y == 0
        else f"{x}/{y} ({100*x/y:.2f}%)"
    )


lines = [
    "# S0 / U060 / U100：Test111 综合评测",
    "",
    (
        "**333/333 个选定模型－任务结果已完成，官方结果与原始证据已核验。** Rubric调用和有效评分数量分列，缺失评分不填0。"
        if complete
        else f"当前已导出 **{len(rows)}/333** 个模型－任务结果；本表随进度更新。只有333条及各项检查收尾后才是完整交付。"
    ),
    "",
    "Test111为37场景×3变体的项目自定义隔离集，不是官方leaderboard split。已用于最终评价，不回流训练、提示优化或checkpoint选择。三模型共用固定CodeAct、官方9B基座、non-thinking、temperature=0、top_p=1、64K、BF16、vLLM0.19.0及80请求/32768输出token/1200秒，重复错误3次提醒5次终止。",
    "",
    "## 能力表",
    "",
    "| 模型 | 完成/111 | 成功 | 失败 | Unknown | 官方成功率 | 95% Wilson区间 | 有效覆盖率 |",
    "|---|---:|---:|---:|---:|---:|---:|---:|",
]
for m in models:
    s, f = m["Official Success"], m["Official Failure"]
    lines.append(
        f"| {m['Model ID']} | {m['Episodes']} | {s} | {f} | {m['Unknown']} | {percent(m['Pass Rate'])} | {wilson(s,s+f)} | {(s+f)/111:.1%} |"
    )
lines += [
    "",
    "Pass Rate=Success/(Success+Failure)；Unknown不进入分母，未开始任务不计为Unknown。置信区间未额外校正同一场景3变体的相关性，应谨慎解读。",
    "",
    "## Harness 表",
    "",
    "| 模型 | 格式拒绝/模型回复 | 外层非法调用/模型回复 | 执行错误/代码块 | API错误/实际业务请求 | 无进展重复/代码块 | 预算终止/完成任务 |",
    "|---|---:|---:|---:|---:|---:|---:|",
]
for m in models:
    values = [
        rate(m, *pair)
        for pair in [
            ("format_rejections", "replies"),
            ("invalid_tool_calls", "replies"),
            ("code_errors", "code_blocks"),
            ("business_api_errors", "business_api_attempts"),
            ("guard_confirmed_no_progress_repeats", "code_blocks"),
            ("budget_exhausted", "Episodes"),
        ]
    ]
    lines.append("| " + m["Model ID"] + " | " + " | ".join(values) + " |")
lines += [
    "",
    "格式与外层非法调用存在包含关系，不相加当作总错误。执行错误与API错误也可能来自同一事件。API列及平均业务API调用次数来自实际Requester尝试，包含失败请求和Python捕获的异常；AppWorld自身tracker计数另留一列。不存在API等调用前错误另列visible_api类别，其精确调用次数无法从Requester取得，不用AST估算。",
    "这里的执行错误率覆盖返回失败的整个代码块，包括由API错误引起的异常，不能全部解释为纯Python语法/编程错误。分类型见后表及CSV。",
    "历史诊断器仅凭HTTP 422不能分清参数错误与库存等业务规则拒绝，故统一明确标为parameter_or_http422_business_rejection，不宣称全部是参数错误；可靠的认证/资源/缺参等分类单列，422内部进一步细分unavailable。",
    "",
    "无进展重复采用保守口径：相同代码、相同错误、可核验状态未变化；原始重复代码/文档另列原始计数，正常分页和复核不自动算错。错误纠正表示后续同代码/API不再抛错，不等于官方任务完成。client_budget原日志未细分上下文/客户端deadline时保留unavailable，不猜测。",
    "",
    "## 质量与成本表",
    "",
    "| 模型 | Rubric已评分 | 目标覆盖/2 | 工具使用/2 | 状态完整/2 | 验证/2 | 效率/2 | 总分/10 | 平均请求 | 平均业务API | 平均输入token | 平均输出token | 平均秒/题 |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
]
for m in models:
    vals = [
        number(m.get("Mean " + key))
        for key in [
            "goal_coverage",
            "tool_usage",
            "state_integrity",
            "verification",
            "efficiency",
        ]
    ]
    vals += [
        number(m.get(key))
        for key in [
            "Mean Rubric Score",
            "Mean Model Requests",
            "Mean Business API Calls",
            "Mean Input Tokens",
            "Mean Output Tokens",
            "Mean Execution Time",
        ]
    ]
    lines.append(
        "| "
        + m["Model ID"]
        + " | "
        + str(m["Rubric Episodes"])
        + " | "
        + " | ".join(vals)
        + " |"
    )
lines += [
    "",
    "Rubric是固定DeepSeek V4.1 Flash、thinking enabled/high/8192的公开轨迹诊断，不替代官方验收。输入不含隐藏答案、官方检查细节或其他模型分数；服务别名没有不可变权重证明，不能声称提供者位级确定性。模型用量以实际回执为准，未测字段不作0；上表为选定尝试成本，原故障/补跑额外成本在最终manifest单列。",
    "",
    "## 逐题配对",
    "",
    "| 比较 | 双方已有有效结果 | 后者新增成功 | 后者退步 | 都成功 | 都失败 |",
    "|---|---:|---:|---:|---:|---:|",
]
by = {
    m: {r["task_id"]: r for r in rows if r["model"] == m}
    for m in ["S0", "U060", "U100"]
}
for a, b in [("S0", "U060"), ("S0", "U100"), ("U060", "U100")]:
    pairs = [
        (by[a][t]["success"], by[b][t]["success"])
        for t in by[a].keys() & by[b].keys()
        if type(by[a][t]["success"]) is bool and type(by[b][t]["success"]) is bool
    ]
    counts = [
        sum(not x and y for x, y in pairs),
        sum(x and not y for x, y in pairs),
        sum(x and y for x, y in pairs),
        sum(not x and not y for x, y in pairs),
    ]
    lines.append(
        "| "
        + a
        + " → "
        + b
        + " | "
        + str(len(pairs))
        + " | "
        + " | ".join(map(str, counts))
        + " |"
    )
all_tasks = set.intersection(*(set(by[model]) for model in by))
lines += [
    "",
    f"三个模型都成功：{sum(all(by[m][task]['success'] is True for m in by) for task in all_tasks)}题；"
    f"三个模型都失败：{sum(all(by[m][task]['success'] is False for m in by) for task in all_tasks)}题。逐题变化保存在task_level_comparison.csv，配对新增/退步任务ID另见manifest。",
    "",
    "## 执行错误及恢复明细",
    "",
    "以下每格为次数/涉及任务数。错误可能同属代码与API通道，不能跨行相加；完整分类及后续恢复证据见harness_failure_breakdown.csv。",
    "",
    "| 事件 | S0 | U060 | U100 |",
    "|---|---:|---:|---:|",
]
event_kinds = sorted(
    {
        (event["channel"], kind)
        for row in rows
        for event in row["events"]
        for kind in event["types"]
    }
)
for channel, kind in event_kinds:
    values = []
    for model in ("S0", "U060", "U100"):
        counts = [
            sum(
                event["channel"] == channel and kind in event["types"]
                for event in row["events"]
            )
            for row in by[model].values()
        ]
        values.append(f"{sum(counts)}/{sum(v > 0 for v in counts)}")
    lines.append(f"| {channel}:{kind} | " + " | ".join(values) + " |")
lines += [
    "",
    "| 模型 | 平均含错回复/题（按请求去重） | 平均有后续纠正证据的回复/题 | 至少一次错误的任务 | 连续同错最长 | 无业务请求最长连续段 |",
    "|---|---:|---:|---:|---:|---:|",
]
for model in models:
    group = list(by[model["Model ID"]].values())
    lines.append(
        f"| {model['Model ID']} | {model['Mean Error-bearing Replies']:.2f} | "
        f"{model['Mean Replies With Later Correction Evidence']:.2f} | "
        f"{model['Error Affected Episodes']}/{model['Episodes']} | "
        f"{model['longest_same_error']} | {max(row['longest_no_business_api_run'] or 0 for row in group)} |"
    )
lines += [
    "",
    "纠正是后续相同代码/API不再报错的可观察代理，不证明全部原业务问题被修复。无业务请求段可能是在查文档、筛选已有变量或核对状态，仅报告长度，不全部认定无进展。",
    "严格同错/同状态指标为0也不代表没有无意义重复：向错误地址重复下单会改变状态，无法被该保守指标识别，下面的b08d5ef_2案例即为此类。原始重复数量单列，不全部算作错误。",
    "",
    "| 模型 | 重复文档查询/文档调用 | 涉及任务 | 重复代码/代码块 | 涉及任务 | 相同代码最长连续次数 |",
    "|---|---:|---:|---:|---:|---:|",
]
for model in models:
    group = list(by[model["Model ID"]].values())
    lines.append(
        f"| {model['Model ID']} | {rate(model, 'repeat_documentation', 'documentation_calls')} | "
        f"{sum((row['repeat_documentation'] or 0) > 0 for row in group)}/{len(group)} | "
        f"{rate(model, 'repeat_code', 'code_blocks')} | "
        f"{model['repeat_code_affected_episodes']}/{len(group)} | {model['longest_same_code']} |"
    )
lines += [
    "",
    "| 预算终止原因 | S0（任务/111） | U060（任务/111） | U100（任务/111） |",
    "|---|---:|---:|---:|",
]
for reason in (
    "request_limit",
    "token_limit",
    "wall_limit",
    "client_budget",
    "format_corrections_exhausted",
    "repeated_error_limit",
):
    values = []
    for model in ("S0", "U060", "U100"):
        n = sum(row["termination"] == reason for row in by[model].values())
        values.append(f"{n}/111 ({n/111:.1%})")
    lines.append(f"| {reason} | " + " | ".join(values) + " |")
lines += [
    "",
    "## Rubric覆盖与局限",
    "",
    "| 模型 | 有效评分 | 不可用 | 官方失败但目标覆盖获2分 | 官方失败中目标覆盖/工具/状态/验证/效率得0分 |",
    "|---|---:|---:|---:|---|",
]
for model in models:
    group = list(by[model["Model ID"]].values())
    failure = [
        row
        for row in group
        if row["success"] is False and row["rubric_status"] == "valid"
    ]
    good_goal = sum(row["rubric_scores"].get("goal_coverage") == 2 for row in failure)
    poor = [
        sum(row["rubric_scores"].get(dim) == 0 for row in failure)
        for dim in (
            "goal_coverage",
            "tool_usage",
            "state_integrity",
            "verification",
            "efficiency",
        )
    ]
    lines.append(
        f"| {model['Model ID']} | {model['Rubric Episodes']} | {model['Rubric Unavailable']} | {good_goal} | "
        + "/".join(map(str, poor))
        + " |"
    )
common = set.intersection(*(set(by[m]) for m in ("S0", "U060", "U100")))
common_rubric = sorted(
    task for task in common if all(by[m][task]["rubric_status"] == "valid" for m in by)
)
if common_rubric:
    means = {
        model: sum(by[model][task]["rubric_total"] for task in common_rubric)
        / len(common_rubric)
        for model in by
    }
    lines += [
        "",
        f"三模型均有有效Rubric的共同子集为{len(common_rubric)}题，平均总分："
        + "、".join(f"{model} {value:.3f}" for model, value in means.items())
        + "。这用于检查缺失评分的影响，不替换官方结果。",
    ]
lines += [
    "",
    "Rubric仅看公开可见证据，既可能遗漏业务约束，也可能因可见证据不足与官方结果不同；上表保留实际不一致，不人工改分。五维0分可重叠，不是互斥的失败根因分类。",
]
lines += [
    "",
    "## 修复、补跑及验证边界",
    "",
    "- 数值答案分类v4支持模型答案在比较的左或右侧；仅确认模型数值转换失败且参考可转有限数字时记有效失败，不改答案和官方断言。`998908e_1/3`旧终态真实复评的官方报告SHA均与原件一致，另存有效失败审计；不回写旧报告。",
    "- 用户固定授权S0六条Unknown补跑一次：`1b2bae9_1/2`、`3fcc458_1/3`、`998908e_1/3`。前四条为审查中断；原件和成本保留，补跑结果为最终选定结果，不从两次择优。旧02目录额外诊断尝试也保留但不计入正式选定结果。",
    "- 首次01目录诊断写盘违反AppWorld沙箱限制，整批标为无效并排除；修复内存收集、执行返回后写盘。正式02/03/04原件和源锁保留；三个模型的输入、权重及官方断言未变。",
    "- 66项当前针对性CPU测试通过；另73项回归通过，旧训练锁校验当前工作区按预期拒绝，未重签旧锁；新锁及旧锁拒绝的独立测试通过。23项Harness合成结果与真实模型统计分开。",
    (
        "- 本轮仅评测，无参数更新、提示改动或新增任务生成。观察到的最佳Test111结果不用于重新选择或训练模型；U060的预先选择依据仍是历史dev24。"
        if complete
        else "- 当前仍在正式运行，尚不下三个模型最终优劣结论。完整后核对配对任务、主要错误、成本及Rubric覆盖；不将阶段快照当作最终成绩。"
    ),
]
findings = OUT / "findings.md"
if complete and findings.exists():
    lines += ["", findings.read_text().strip()]
(OUT / "evaluation_report.md").write_text("\n".join(lines) + "\n")
# Normalize record separators for Git while preserving all parsed CSV fields.
for path in OUT.glob("*.csv"):
    with path.open(newline="") as stream:
        records = list(csv.reader(stream))
    with path.open("w", newline="") as stream:
        csv.writer(stream, lineterminator="\n").writerows(records)
print({"records": len(rows), "models": [m["Model ID"] for m in models]})
