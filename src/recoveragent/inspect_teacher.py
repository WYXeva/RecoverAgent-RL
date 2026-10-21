"""Offline, read-only inspection of the actual Teacher API input and output."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from .io import digest, read_json


class Redactor:
    """Mask known credential values, including echoes in text and JSON strings."""

    sensitive = re.compile(
        r"(?:^|_)(?:password|passwd|api_key|access_token|refresh_token|"
        r"authorization|client_secret|secret|token)$",
        re.I,
    )
    obvious = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}|\bBearer\s+[A-Za-z0-9._~+/-]+=*")

    def __init__(self, values):
        self.secrets = {}
        self.discover(values)

    def discover(self, value):
        if isinstance(value, dict):
            for key, item in value.items():
                # Schema properties remain intact: their values are dictionaries.
                if self.sensitive.search(key) and isinstance(item, str) and item:
                    self.secrets.setdefault(item, f"<REDACTED_{len(self.secrets) + 1}>")
                self.discover(item)
        elif isinstance(value, list):
            for item in value:
                self.discover(item)
        elif isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (ValueError, RecursionError):
                return
            if isinstance(parsed, (dict, list)):
                self.discover(parsed)

    def text(self, value):
        for secret in sorted(self.secrets, key=len, reverse=True):
            replacement = self.secrets[secret]
            # Preserve the exact serialization except for credential substitutions.
            for form in {
                secret,
                json.dumps(secret, ensure_ascii=False)[1:-1],
                json.dumps(secret, ensure_ascii=True)[1:-1],
            }:
                value = value.replace(form, replacement)
        return self.obvious.sub("<REDACTED_CREDENTIAL>", value)

    def apply(self, value):
        if isinstance(value, dict):
            return {key: self.apply(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.apply(item) for item in value]
        return self.text(value) if isinstance(value, str) else value


def _names(tools):
    return [tool["function"]["name"] for tool in tools]


def _feedback(directory, request_id, event, trajectory):
    dispatches = sorted((directory / "dispatches").glob(f"{request_id}_*.json"))
    if dispatches:
        return [read_json(path) for path in dispatches], "dispatches"
    if "tool_results" in event:
        return event["tool_results"], "recorded_event"
    # Early P3-A single-call receipts predate the dispatch journal.
    index = event.get("assistant_index", -1)
    messages = trajectory.get("messages", [])
    if event.get("dispatched") and 0 <= index < len(messages) - 1:
        result = messages[index + 1]
        if result.get("role") == "tool":
            return [result], "recorded_trajectory"
    return [], "no_recorded_feedback"


def build_view(path, turn=None, all_turns=False):
    """No evaluator, model, environment, network, or current tool catalogue is used."""
    directory = Path(path).resolve()
    if directory.is_file():
        if directory.name != "trajectory.json":
            raise ValueError("Pass a worker directory or its trajectory.json")
        directory = directory.parent
    trajectory_path = directory / "trajectory.json"
    trajectory = read_json(trajectory_path) if trajectory_path.exists() else {}
    requests = sorted(
        (
            p
            for p in (directory / "requests").glob("*.request.json")
            if p.name.split(".")[0].isdecimal() and p.name.split(".")[0] != "000"
        ),
        key=lambda p: int(p.name.split(".")[0]),
    )
    predictor_path = directory / "requests" / "000.request.json"
    if not requests and not predictor_path.exists():
        raise ValueError("No saved Teacher requests in this directory")
    config_path = directory.parent / (directory.name + ".config.json")
    config = read_json(config_path) if config_path.exists() else {}
    events = {t["request_id"]: t for t in trajectory.get("turns", [])}
    details, overview, previous_names, raw_values = [], [], [], []
    source_hashes = {}
    predictor_view = None
    if predictor_path.exists():
        prediction_request = read_json(predictor_path)
        response_path = predictor_path.with_name("000.response.json")
        prediction_response = (
            read_json(response_path) if response_path.exists() else None
        )
        aggregate = trajectory.get("predictor", {})
        record = (aggregate.get("attempts") or [aggregate])[0]
        warnings = []
        for label, value, path in (
            ("request_hash", prediction_request, predictor_path),
            ("response_hash", prediction_response, response_path),
        ):
            if value is not None:
                source_hashes[str(path.relative_to(directory))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                if record.get(label) and digest(value) != record[label]:
                    warnings.append(label + "_mismatch")
        predictor_view = {
            "purpose": "Pre-execution API selection; separate from Actor history and SFT targets.",
            "requested_max_tokens": prediction_request.get("max_tokens"),
            "selected_names": record.get("selected_names"),
            "usage": record.get("usage"),
            "format_error": aggregate.get("format_error"),
            "receipt_warnings": warnings,
        }
        if all_turns:
            predictor_view.update(
                llm_input=prediction_request, response_receipt=prediction_response
            )
        raw_values.extend([prediction_request, prediction_response])
        if aggregate.get("protocol") == "api_predictor_validated_v1":
            predictor_view["protocol"] = aggregate["protocol"]
            predictor_view["aggregate_usage"] = aggregate["usage"]
            predictor_view["final_selected_names"] = aggregate["selected_names"]
            predictor_view["dependency_errors"] = record.get("dependency_errors")
            predictor_view["corrections"] = []
            correction_path = directory / "requests/000-correction.request.json"
            correction_events = aggregate["attempts"][1:]
            if correction_path.exists() and not correction_events:
                correction_events = [{"request_id": "000-correction"}]
            for event in correction_events:
                receipt = {
                    "request_id": event["request_id"],
                    "selected_names": event.get("selected_names"),
                    "dependency_errors": event.get("dependency_errors"),
                    "usage": event.get("usage"),
                    "receipt_warnings": [],
                }
                for kind, label in (
                    ("request", "llm_input"),
                    ("response", "response_receipt"),
                ):
                    path = directory / "requests" / f"{event['request_id']}.{kind}.json"
                    if not path.exists():
                        receipt["receipt_warnings"].append(kind + "_missing")
                        continue
                    value = read_json(path)
                    source_hashes[str(path.relative_to(directory))] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                    if (
                        event.get(kind + "_hash")
                        and digest(value) != event[kind + "_hash"]
                    ):
                        receipt["receipt_warnings"].append(kind + "_hash_mismatch")
                    if all_turns:
                        receipt[label] = value
                    raw_values.append(value)
                predictor_view["corrections"].append(receipt)
    known_generated = 0
    for request_path in requests:
        request_id = request_path.name.split(".")[0]
        request = read_json(request_path)
        response_path = request_path.with_name(f"{request_id}.response.json")
        response = read_json(response_path) if response_path.exists() else None
        event = events.get(request_id, {})
        event_path = directory / "turns" / f"{request_id}.json"
        if not event and event_path.exists():
            event = read_json(event_path)["event"]
        feedback, feedback_source = _feedback(directory, request_id, event, trajectory)
        names = _names(request["tools"])
        added = [name for name in names if name not in previous_names]
        removed = [name for name in previous_names if name not in names]
        previous_names = names
        warnings = []
        for label, value in (
            ("request_hash", request),
            ("history_hash", request["messages"]),
            ("response_hash", response),
        ):
            if label in event and value is not None and digest(value) != event[label]:
                warnings.append(label + "_mismatch")
        if event.get("tools") is not None and event["tools"] != request["tools"]:
            warnings.append("recorded_schema_differs_from_request")
        index = event.get("assistant_index")
        if (
            index is not None
            and trajectory.get("messages")
            and trajectory["messages"][:index] != request["messages"]
        ):
            warnings.append("recorded_history_differs_from_request")
        choices = response.get("choices", []) if response else []
        choice = choices[0] if choices else {}
        calls = choice.get("message", {}).get("tool_calls") or []
        generated = (
            response.get("usage", {}).get("completion_tokens") if response else None
        )
        if generated is not None:
            known_generated += generated
        diagnostics = {
            key: event[key]
            for key in ("format_error", "validation_error")
            if key in event
        }
        summary = {
            "turn": int(request_id),
            "message_count": len(request["messages"]),
            "visible_tools": len(names),
            "tools_added": added,
            "tools_removed": removed,
            "requested_max_tokens": request.get("max_tokens"),
            "generated_tokens": generated,
            "known_cumulative_generated_tokens": known_generated,
            "finish_reason": choice.get("finish_reason", "NO_RESPONSE_RECEIPT"),
            "requested_calls": len(calls),
            "recorded_tool_results": len(feedback),
            "receipt_warnings": warnings,
            "controller_diagnostics": diagnostics,
        }
        overview.append(summary)
        details.append(
            {
                "turn": int(request_id),
                # These objects come directly from this request, never from the final history.
                "llm_input": {
                    "messages": request["messages"],
                    "tools": request["tools"],
                },
                "request_parameters": {
                    k: v for k, v in request.items() if k not in {"messages", "tools"}
                },
                "visible_tool_names": names,
                "response_receipt": response,
                "tool_feedback_after_response": feedback,
                "feedback_source": feedback_source,
                "calls_visible_at_request_time": [
                    {
                        "id": call.get("id"),
                        "name": call.get("function", {}).get("name"),
                        "visible": call.get("function", {}).get("name") in names,
                    }
                    for call in calls
                ],
                "controller_diagnostics": diagnostics,
                "receipt_warnings": warnings,
            }
        )
        raw_values.extend([request, response, feedback, diagnostics])
        for file in [request_path, response_path]:
            if file.exists():
                source_hashes[str(file.relative_to(directory))] = hashlib.sha256(
                    file.read_bytes()
                ).hexdigest()
    if turn is not None and turn not in {row["turn"] for row in overview}:
        raise ValueError("Requested turn has no saved request")
    selected = details if all_turns else [row for row in details if row["turn"] == turn]
    view = {
        "format": "teacher-request-view-v1",
        "scope": "saved API requests, not provider internal prompt serialization or hidden reasoning",
        "source": str(directory),
        "redaction": "credential values masked consistently; underlying receipts unchanged",
        "controller_only": {
            "note": "Controller metadata is not LLM input. No evaluation file is read.",
            "trajectory": {
                key: trajectory.get(key)
                for key in (
                    "trajectory_id",
                    "task_id",
                    "status",
                    "reward",
                    "termination_reason",
                    "error_type",
                    "tool_mode",
                )
            },
            "limits": config.get("limits"),
            "worker_generated_token_allowance": config.get(
                "remaining_generated_tokens"
            ),
            "teacher_request_limits": {
                key: config.get("teacher", {}).get(key)
                for key in (
                    "max_new_tokens",
                    "max_tool_calls_per_turn",
                    "max_request_bytes",
                )
            },
            "config_source": str(config_path) if config else None,
        },
        "overview": overview,
        "predictor": predictor_view,
        "turns": selected,
        "source_receipt_sha256": source_hashes,
    }
    return Redactor(raw_values).apply(view)


def _json_block(value):
    text = json.dumps(value, indent=2, ensure_ascii=False)
    # Logged content must not terminate its Markdown code block.
    longest = max((len(m[0]) for m in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}json\n{text}\n{fence}\n"


def render_markdown(view):
    lines = [
        "# Teacher 的实际请求视图\n",
        "离线读取已保存的请求；凭据已隐藏，原始记录未修改。"
        "这里展示 API messages/tools，不代表供应商内部模板、token IDs 或未记录的思考过程。\n",
        "## 控制侧信息（LLM 没有收到）\n",
        _json_block(view["controller_only"]),
        "## 回合概览（全轨迹诊断信息）\n",
        "| 回合 | 输入消息 | 可见工具 | 请求输出上限 | 实际输出 | 请求调用/结果 | finish_reason |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n",
    ]
    for row in view["overview"]:
        cells = [
            row["turn"],
            row["message_count"],
            row["visible_tools"],
            row["requested_max_tokens"],
            row["generated_tokens"],
            f"{row['requested_calls']}/{row['recorded_tool_results']}",
            row["finish_reason"],
        ]
        lines.append(
            "| "
            + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in cells)
            + " |\n"
        )
    issues = [
        {
            "turn": row["turn"],
            "diagnostics": row["controller_diagnostics"],
            "receipt_warnings": row["receipt_warnings"],
        }
        for row in view["overview"]
        if row["controller_diagnostics"] or row["receipt_warnings"]
    ]
    if issues:
        lines.extend(["\n控制侧异常摘要（不是 LLM 输入）：\n", _json_block(issues)])
    if view.get("predictor"):
        lines.extend(
            [
                "\n## 执行前的 API Predictor（独立请求，不属于 Actor 对话）\n",
                "使用 `--all` 显示 Predictor 的完整目录输入和原始预测响应。\n",
                _json_block(view["predictor"]),
            ]
        )
    for row in view["turns"]:
        overview = next(v for v in view["overview"] if v["turn"] == row["turn"])
        lines.extend(
            [
                f"\n## 第 {row['turn']} 回合\n",
                "### 请求参数与工具变化\n",
                _json_block(
                    {
                        "request_parameters": row["request_parameters"],
                        "visible_tool_names": row["visible_tool_names"],
                        "tools_added_since_previous_request": overview["tools_added"],
                        "tools_removed_since_previous_request": overview[
                            "tools_removed"
                        ],
                    }
                ),
                "### 发出请求时：完整 messages（含此前工具反馈）\n",
                _json_block(row["llm_input"]["messages"]),
                "### 发出请求时：完整工具 schema\n",
                _json_block(row["llm_input"]["tools"]),
                "### 模型原始响应（保留截断和非法参数，不做修复）\n",
                _json_block(row["response_receipt"]),
                "### 响应之后的真实工具反馈（不是本轮输入）\n",
                _json_block(
                    {
                        "source": row["feedback_source"],
                        "results": row["tool_feedback_after_response"],
                    }
                ),
                "### 控制侧诊断（不是 LLM 输入）\n",
                _json_block(
                    {
                        "calls_visible_at_request_time": row[
                            "calls_visible_at_request_time"
                        ],
                        "diagnostics": row["controller_diagnostics"],
                        "receipt_warnings": row["receipt_warnings"],
                    }
                ),
            ]
        )
    if not view["turns"]:
        lines.append("\n用 `--turn N` 查看某轮完整请求，或用 `--all` 导出全部回合。\n")
    lines.extend(
        ["\n## 原始请求/响应 SHA-256\n", _json_block(view["source_receipt_sha256"])]
    )
    return "\n".join(lines)


def save_view(output, content, source):
    output, source = Path(output).resolve(), Path(source).resolve()
    if output == source or source in output.parents:
        raise ValueError("Write views outside the immutable trajectory directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trajectory", help="worker directory or trajectory.json")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--turn", type=int, help="show one request in full")
    selection.add_argument(
        "--all", action="store_true", help="show every request in full"
    )
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument(
        "--output", help="new local output file; default prints to terminal"
    )
    args = parser.parse_args()
    try:
        view = build_view(args.trajectory, args.turn, args.all)
        content = (
            json.dumps(view, ensure_ascii=False, indent=2) + "\n"
            if args.format == "json"
            else render_markdown(view)
        )
        if args.output:
            save_view(args.output, content, view["source"])
            print(f"已写入 {args.output}（凭据已隐藏，无模型调用）")
        else:
            print(content)
    except (OSError, ValueError, KeyError) as exc:
        # Never echo malformed input text, which might contain credentials.
        parser.exit(
            2,
            f"{type(exc).__name__}: 无法读取或写入视图；请检查输入、回合和输出路径。\n",
        )


if __name__ == "__main__":
    main()
