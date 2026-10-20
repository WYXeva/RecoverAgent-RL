"""Public API selection using the pinned upstream APIPredictor, without gold labels."""

import argparse
import copy
import hashlib
import importlib.util
import urllib.request
from pathlib import Path

from . import APPWORLD_REVISION
from .io import canonical, digest, read_json, write_json
from .teacher import normalize_message, response_usage, usage_cost

MODES = ("dynamic_discovery", "api_predictor")
PREDICTOR_PROMPTS = ("official_zero_shot", "dependency_aware_v1")
DEPENDENCY_CHECK = (
    "Dependency check before finalizing the API list: the execution agent starts "
    "without pre-obtained credentials or access tokens. Include the public APIs "
    "needed to obtain required inputs and satisfy dependencies of your selected "
    "APIs, including credential retrieval and login where authentication is "
    "required. Consider discovery of identifiers, pagination, and task completion. "
    "Do not assume prerequisites have already been performed by another agent. "
    "Only select APIs from the supplied catalogue; still output one app.api per line."
)
SOURCES = {
    "api_predictor.py": (
        "experiments/code/common/api_predictor.py",
        "10baddc59e28cb73e3685a03a31abfc38f5331a93822715cb2a7c6df36491b18",
    ),
    "api_predictor.txt": (
        "experiments/prompts/api_predictor.txt",
        "53d8e9698bfff783830cbdbe6fec9bf4c51557b9b8673a5367bec6ae7c0beda4",
    ),
}
DEFAULT_SOURCE = ".cache/appworld-reference"
DISCOVERY_INSTRUCTION = (
    "Use api_docs to discover APIs; requesting show_api_doc makes that function "
    "available on the next turn."
)


def prepare_source(directory):
    """Download only pinned public code/prompt; never patch the installed dependency."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, (remote, expected) in SOURCES.items():
        path = directory / name
        if path.exists():
            data = path.read_bytes()
        else:
            url = (
                "https://raw.githubusercontent.com/StonyBrookNLP/appworld/"
                + APPWORLD_REVISION
                + "/"
                + remote
            )
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Upstream source hash mismatch: " + name)
        if not path.exists():
            path.write_bytes(data)


def official_predictor(directory=DEFAULT_SOURCE):
    directory = Path(directory).resolve()
    for name, (_, expected) in SOURCES.items():
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Upstream source hash mismatch: " + name)
    spec = importlib.util.spec_from_file_location(
        "_recoveragent_official_api_predictor", directory / "api_predictor.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.APIPredictor(
        prompt_file_path=str(directory / "api_predictor.txt"),
        demo_task_ids=[],  # Upstream demos load hidden required_apis labels.
        max_predicted_apis=20,
        app_api_separator="__",
        mode="predicted",
    )


def select_tools(adapter, names):
    """Match upstream Function Calling Agent's catalogue-order schema selection."""
    catalogue = {doc["function"]["name"]: doc for doc in adapter.catalogue}
    unknown = set(names) - set(catalogue)
    if unknown or any(name.startswith("admin__") for name in names):
        raise ValueError("Predictor selected a non-public API")
    return [copy.deepcopy(doc) for name, doc in catalogue.items() if name in names]


def predictor_messages(predictor, task, variant="official_zero_shot"):
    messages = predictor.build_messages(task, include_cache_control=False)
    if variant not in PREDICTOR_PROMPTS:
        raise ValueError("Unknown Predictor prompt variant")
    if variant == "dependency_aware_v1":
        messages = copy.deepcopy(messages)
        messages[-1]["content"] += "\n\n" + DEPENDENCY_CHECK
    return messages


def initialize_tools(adapter, policy, config, output, usage, trajectory):
    mode = config.get("tool_mode", "dynamic_discovery")
    if mode == "dynamic_discovery":
        return adapter.initial_tools(), True
    predictor = official_predictor(config.get("predictor_source", DEFAULT_SOURCE))
    # With zero demos, upstream cache_control=True indexes an empty list.
    # This documented public option avoids the issue without modifying upstream.
    variant = config.get("predictor_prompt_variant", "official_zero_shot")
    messages = predictor_messages(predictor, adapter.world.task, variant)
    maximum = min(
        config["teacher"]["max_new_tokens"],
        config["limits"]["episode_generated_tokens"],
        config["remaining_generated_tokens"],
    )
    if maximum <= 0 or config["remaining_requests"] <= 0:
        raise ValueError("No predictor budget")
    payload = policy.build_text_request(messages, maximum)
    reserve = usage_cost(
        {
            "prompt_tokens": config["teacher"]["billing_context_tokens"],
            "generated_tokens": maximum,
            "cached_prompt_tokens": 0,
        },
        config["teacher"]["pricing"],
    )
    if reserve > config["remaining_cost"]:
        raise ValueError("No predictor cost reservation available")
    request_dir = Path(output) / "requests"
    write_json(request_dir / "000.request.json", payload)
    write_json(
        request_dir / "000.reservation.json",
        {
            "max_tokens": maximum,
            "max_cost": reserve,
            "dispatched": True,
            "purpose": "api_predictor",
        },
    )
    usage["provider_requests"] += 1
    usage["unknown_cost_reservation"] += reserve
    usage["unknown_generation_reservation"] += maximum
    response = policy.transport(payload)
    write_json(request_dir / "000.response.json", response)
    current = response_usage(response)
    for key, value in current.items():
        usage[key] += value
    cost = usage_cost(current, config["teacher"]["pricing"])
    usage["usage_priced_cost"] += cost
    usage["unknown_cost_reservation"] -= reserve
    usage["unknown_generation_reservation"] -= maximum
    backend = [response.get("model"), response.get("system_fingerprint")]
    record = {
        "request_id": "000",
        "request_hash": digest(payload),
        "response_hash": digest(response),
        "usage": current,
        "usage_priced_cost": cost,
        "source_revision": APPWORLD_REVISION,
        "source_sha256": {name: value[1] for name, value in SOURCES.items()},
        "demo_task_ids": [],
        "mode": "predicted",
        "max_predicted_apis": 20,
        "selected_names": [],
        "selected_tools": [],
        "prompt_variant": variant,
        "supplement_sha256": digest(DEPENDENCY_CHECK)
        if variant == "dependency_aware_v1"
        else None,
        "allowed_api_names": [
            f"{app}.{api}".lower()
            for app, docs in adapter.world.task.api_docs.items()
            for api in docs
        ],
    }
    trajectory["predictor"] = record
    if (
        backend[0] != config["teacher"]["model"]
        or not backend[1]
        or (config.get("expected_backend") and config["expected_backend"] != backend)
        or current["generated_tokens"] > maximum
        or current["prompt_tokens"] > config["teacher"]["billing_context_tokens"]
    ):
        raise ValueError("Unverifiable predictor identity or billing bound")
    trajectory["observed_backend"] = backend
    try:
        message = normalize_message(response)
        if message.get("tool_calls"):
            raise ValueError("Predictor must return API names as text")
        names = predictor.predicted_output_to_apis(
            adapter.world.task, message["content"]
        )
        tools = select_tools(adapter, names)
    except (ValueError, KeyError, TypeError) as exc:
        record["format_error"] = type(exc).__name__ + ": " + str(exc)
        return [], False
    record.update(selected_names=names, selected_tools=copy.deepcopy(tools))
    return tools, True


def execute_with_mode(adapter, name, arguments, tools, mode):
    result, expanded = adapter.execute(name, arguments, tools)
    return result, tools if mode == "api_predictor" else expanded


def validate_predictor_receipts(trajectory, directory):
    """Portable verification: selector receipts are evidence, never SFT messages."""
    record = trajectory.get("predictor")
    if trajectory.get("tool_mode", "dynamic_discovery") != "api_predictor":
        if record:
            raise ValueError("Unexpected predictor in dynamic discovery")
        return None
    if (
        not record
        or record.get("format_error")
        or record["demo_task_ids"] != []
        or record["mode"] != "predicted"
        or record["source_revision"] != APPWORLD_REVISION
        or record["source_sha256"] != {k: v[1] for k, v in SOURCES.items()}
    ):
        raise ValueError("Invalid public-only predictor provenance")
    request = read_json(Path(directory) / "requests" / "000.request.json")
    response = read_json(Path(directory) / "requests" / "000.response.json")
    if (
        digest(request) != record["request_hash"]
        or digest(response) != record["response_hash"]
        or request["model"] != trajectory["teacher"]["model"]
        or response["model"] != request["model"]
        or request.get("thinking") != {"type": "disabled"}
        or "tools" in request
        or response_usage(response) != record["usage"]
        or response_usage(response)["generated_tokens"] > request["max_tokens"]
    ):
        raise ValueError("Predictor receipt mismatch")
    variant = record.get("prompt_variant", "official_zero_shot")
    if (
        variant not in PREDICTOR_PROMPTS
        or trajectory.get("predictor_prompt_variant", "official_zero_shot") != variant
    ):
        raise ValueError("Predictor prompt variant mismatch")
    if variant == "dependency_aware_v1" and (
        record.get("supplement_sha256") != digest(DEPENDENCY_CHECK)
        or not request["messages"][-1]["content"].endswith("\n\n" + DEPENDENCY_CHECK)
    ):
        raise ValueError("Predictor supplement mismatch")
    message = normalize_message(response)
    if message.get("tool_calls") or len(record["selected_names"]) > 20:
        raise ValueError("Invalid predictor output")
    # Independent, portable audit of the pinned upstream parser's small contract.
    # Runtime selection above always calls the actual upstream implementation.
    allowed = set(record["allowed_api_names"])
    parsed = [
        line.strip().lower()
        for line in message["content"].strip().splitlines()
        if line.strip().lower() in allowed
    ]
    if "supervisor.complete_task" not in parsed:
        parsed.insert(0, "supervisor.complete_task")
    expected = [name.replace(".", "__") for name in list(dict.fromkeys(parsed))[:20]]
    if record["max_predicted_apis"] != 20 or record["selected_names"] != expected:
        raise ValueError("Selected APIs do not match upstream prediction parsing")
    tools = record["selected_tools"]
    if (
        {t["function"]["name"] for t in tools} != set(record["selected_names"])
        or trajectory["initial_tools"] != tools
        or any(t["tools"] != tools for t in trajectory["turns"])
    ):
        raise ValueError("Fixed predicted schemas changed")
    if (
        abs(
            usage_cost(record["usage"], trajectory["teacher"]["pricing"])
            - record["usage_priced_cost"]
        )
        > 1e-9
    ):
        raise ValueError("Predictor cost mismatch")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare"])
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    args = parser.parse_args()
    prepare_source(args.source)
    print(
        canonical(
            {"source": args.source, "sha256": {k: v[1] for k, v in SOURCES.items()}}
        )
    )


if __name__ == "__main__":
    main()
