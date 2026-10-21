"""A v1: public authentication dependency checks, at most one selector correction.

This is a project variant, not the original official protocol. Checks cover known
token/password bootstrap paths only, not general task solvability. Never execute
tools or insert selected APIs on the model's behalf.
"""

import copy
from pathlib import Path

from . import APPWORLD_REVISION
from .io import canonical, digest, read_json, write_json
from .teacher import normalize_message, response_usage, usage_cost
from . import tool_modes as legacy

PROTOCOL = "api_predictor_validated_v1"
LEGACY_PROTOCOL = "frozen_v3"


def has_field(value, field):
    if isinstance(value, dict):
        return field in value or any(has_field(v, field) for v in value.values())
    return isinstance(value, list) and any(has_field(v, field) for v in value)


def dependency_catalogue(api_docs):
    """Only public parameter and success-response schemas; no environment values."""
    return {
        f"{app}__{api}": {
            "required": [p["name"] for p in doc["parameters"] if p["required"]],
            "returns": [
                field
                for field in ("access_token", "password")
                if has_field(doc["response_schemas"]["success"], field)
            ],
        }
        for app, docs in api_docs.items()
        for api, doc in docs.items()
    }


def dependency_errors(names, catalogue):
    """Acyclic bootstrap: selected same-app token producer + public password source.

    APIs requiring a token cannot bootstrap that token. Apps without a documented
    producer are explicitly unverified (no speculative missing-API error).
    """
    selected = set(names)
    errors = []
    for app in sorted({n.split("__")[0] for n in names}):
        consumers = sorted(
            n
            for n in names
            if n.startswith(app + "__") and "access_token" in catalogue[n]["required"]
        )
        if not consumers:
            continue
        producers = sorted(
            n
            for n, d in catalogue.items()
            if n.startswith(app + "__")
            and "access_token" in d["returns"]
            and "access_token" not in d["required"]
        )
        if not producers:
            continue
        available = selected.intersection(producers)
        if not available:
            errors.append(
                {
                    "app": app,
                    "required_input": "access_token",
                    "consumers": consumers,
                    "public_sources": producers,
                }
            )
            continue
        # Require a password source only when every selected token route needs it.
        if all("password" in catalogue[n]["required"] for n in available):
            sources = sorted(
                n
                for n, d in catalogue.items()
                if n.split("__")[0] in {app, "supervisor"}
                and "password" in d["returns"]
                and not d["required"]
            )
            if sources and not selected.intersection(sources):
                errors.append(
                    {
                        "app": app,
                        "required_input": "password",
                        "consumers": sorted(available),
                        "public_sources": sources,
                    }
                )
    return errors


def correction_message(errors):
    return {
        "role": "user",
        "content": (
            "PUBLIC SCHEMA DEPENDENCY CHECK FAILED. No tools have executed and no "
            "credentials are available yet. Select a corrected COMPLETE API list, "
            "including public prerequisite routes; do not just output additions. "
            "Return one app.api per line from the original catalogue (maximum 20). "
            "This is the only correction opportunity. Errors:\n" + canonical(errors)
        ),
    }


def initialize_tools(adapter, policy, config, output, usage, trajectory):
    if (
        config.get("selection_protocol", LEGACY_PROTOCOL) != PROTOCOL
        or config.get("tool_mode", "dynamic_discovery") != "api_predictor"
    ):
        return legacy.initialize_tools(
            adapter, policy, config, output, usage, trajectory
        )
    predictor = legacy.official_predictor(
        config.get("predictor_source", legacy.DEFAULT_SOURCE)
    )
    variant = config.get("predictor_prompt_variant", "dependency_examples_v2")
    messages = legacy.predictor_messages(predictor, adapter.world.task, variant)
    catalogue = dependency_catalogue(adapter.world.task.api_docs)
    record = {
        "protocol": PROTOCOL,
        "source_revision": APPWORLD_REVISION,
        "source_sha256": {k: v[1] for k, v in legacy.SOURCES.items()},
        "demo_task_ids": [],
        "mode": "predicted",
        "max_predicted_apis": 20,
        "prompt_variant": variant,
        "dependency_catalogue": catalogue,
        "catalogue_sha256": digest(catalogue),
        "attempts": [],
        "allowed_api_names": [n.replace("__", ".") for n in catalogue],
        "selected_names": [],
        "selected_tools": [],
        "usage": {
            k: 0 for k in ("prompt_tokens", "generated_tokens", "cached_prompt_tokens")
        },
        "usage_priced_cost": 0.0,
        "provider_requests": 0,
    }
    trajectory["predictor"] = record
    for attempt in range(2):
        maximum = min(
            config["teacher"]["max_new_tokens"],
            min(
                config["limits"]["episode_generated_tokens"],
                config["remaining_generated_tokens"],
            )
            - usage["generated_tokens"],
        )
        reserve = usage_cost(
            {
                "prompt_tokens": config["teacher"]["billing_context_tokens"],
                "generated_tokens": max(0, maximum),
                "cached_prompt_tokens": 0,
            },
            config["teacher"]["pricing"],
        )
        if (
            maximum <= 0
            or usage["provider_requests"] >= config["remaining_requests"]
            or usage["usage_priced_cost"] + reserve > config["remaining_cost"]
        ):
            record["format_error"] = "dependency_correction_budget_exhausted"
            return [], False
        request_id = "000" if attempt == 0 else "000-correction"
        payload = policy.build_text_request(messages, maximum)
        directory = Path(output) / "requests"
        write_json(directory / f"{request_id}.request.json", payload)
        write_json(
            directory / f"{request_id}.reservation.json",
            {
                "max_tokens": maximum,
                "max_cost": reserve,
                "dispatched": True,
                "purpose": PROTOCOL,
            },
        )
        usage["provider_requests"] += 1
        usage["unknown_cost_reservation"] += reserve
        usage["unknown_generation_reservation"] += maximum
        response = policy.transport(payload)
        write_json(directory / f"{request_id}.response.json", response)
        current = response_usage(response)
        cost = usage_cost(current, config["teacher"]["pricing"])
        for k, v in current.items():
            usage[k] += v
            record["usage"][k] += v
        usage["usage_priced_cost"] += cost
        usage["unknown_cost_reservation"] -= reserve
        usage["unknown_generation_reservation"] -= maximum
        record["usage_priced_cost"] += cost
        record["provider_requests"] += 1
        event = {
            "request_id": request_id,
            "request_hash": digest(payload),
            "response_hash": digest(response),
            "usage": current,
            "usage_priced_cost": cost,
            "selected_names": [],
            "selected_tools": [],
        }
        record["attempts"].append(event)
        backend = [response.get("model"), response.get("system_fingerprint")]
        if (
            backend[0] != config["teacher"]["model"]
            or not backend[1]
            or (
                config.get("expected_backend") and backend != config["expected_backend"]
            )
            or (
                trajectory.get("observed_backend")
                and backend != trajectory["observed_backend"]
            )
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
            selected_tools = legacy.select_tools(adapter, names)
            errors = dependency_errors(names, catalogue)
        except (ValueError, KeyError, TypeError) as exc:
            record["format_error"] = type(exc).__name__ + ": " + str(exc)
            return [], False
        event.update(
            selected_names=names,
            selected_tools=copy.deepcopy(selected_tools),
            dependency_errors=errors,
        )
        record.update(
            selected_names=names, selected_tools=copy.deepcopy(selected_tools)
        )
        if not errors:
            return selected_tools, True
        messages = [*messages, message, correction_message(errors)]
    record["format_error"] = "unresolved_public_dependencies"
    return [], False


def validate_predictor_receipts(trajectory, directory):
    record = trajectory.get("predictor")
    if not record or record.get("protocol") != PROTOCOL:
        if (
            trajectory.get("selection_protocol") == PROTOCOL
            and trajectory["tool_mode"] == "api_predictor"
        ):
            raise ValueError("Missing validated selection receipts")
        return legacy.validate_predictor_receipts(trajectory, directory)
    if (
        trajectory.get("selection_protocol") != PROTOCOL
        or trajectory["tool_mode"] != "api_predictor"
        or record.get("format_error")
        or record["demo_task_ids"] != []
        or record["mode"] != "predicted"
        or record["source_revision"] != APPWORLD_REVISION
        or record["source_sha256"] != {k: v[1] for k, v in legacy.SOURCES.items()}
        or record["max_predicted_apis"] != 20
        or not 1 <= len(record["attempts"]) <= 2
        or record["prompt_variant"] != "dependency_examples_v2"
        or trajectory.get("predictor_prompt_variant") != record["prompt_variant"]
    ):
        raise ValueError("Invalid validated selector provenance")
    catalogue = record["dependency_catalogue"]
    if digest(catalogue) != record["catalogue_sha256"] or record[
        "allowed_api_names"
    ] != [n.replace("__", ".") for n in catalogue]:
        raise ValueError("Dependency catalogue mismatch")
    totals = {k: 0 for k in record["usage"]}
    cost, previous_request, previous_message, previous_errors = 0.0, None, None, None
    for i, event in enumerate(record["attempts"]):
        request_id = "000" if i == 0 else "000-correction"
        if event["request_id"] != request_id:
            raise ValueError("Invalid selector request identity")
        request = read_json(Path(directory) / "requests" / f"{request_id}.request.json")
        response = read_json(
            Path(directory) / "requests" / f"{request_id}.response.json"
        )
        current = response_usage(response)
        if (
            digest(request) != event["request_hash"]
            or digest(response) != event["response_hash"]
            or request["model"] != trajectory["teacher"]["model"]
            or [response.get("model"), response.get("system_fingerprint")]
            != trajectory["observed_backend"]
            or request.get("thinking") != {"type": "disabled"}
            or "tools" in request
            or current != event["usage"]
            or current["generated_tokens"] > request["max_tokens"]
        ):
            raise ValueError("Selector receipt mismatch")
        if i == 0:
            if (
                not request["messages"][0]["content"].endswith(
                    "\n\n" + legacy.SELECTION_RULES
                )
                or request["messages"][-5:-1] != legacy.example_messages()
            ):
                raise ValueError("Selector public examples changed")
        elif not previous_errors or request["messages"] != [
            *previous_request["messages"],
            previous_message,
            correction_message(previous_errors),
        ]:
            raise ValueError("Selector correction context mismatch")
        message = normalize_message(response)
        parsed = [
            s.strip().lower()
            for s in message["content"].strip().splitlines()
            if s.strip().lower() in record["allowed_api_names"]
        ]
        if "supervisor.complete_task" not in parsed:
            parsed.insert(0, "supervisor.complete_task")
        names = [n.replace(".", "__") for n in list(dict.fromkeys(parsed))[:20]]
        if (
            message.get("tool_calls")
            or names != event["selected_names"]
            or {t["function"]["name"] for t in event["selected_tools"]} != set(names)
            or dependency_errors(names, catalogue) != event["dependency_errors"]
        ):
            raise ValueError("Selector parsing/dependency evidence mismatch")
        for k in totals:
            totals[k] += current[k]
        priced = usage_cost(current, trajectory["teacher"]["pricing"])
        if abs(priced - event["usage_priced_cost"]) > 1e-9:
            raise ValueError("Selector per-request cost mismatch")
        cost += priced
        previous_request, previous_message, previous_errors = (
            request,
            message,
            event["dependency_errors"],
        )
    tools = record["attempts"][-1]["selected_tools"]
    if (
        previous_errors
        or record["selected_tools"] != tools
        or record["selected_names"] != record["attempts"][-1]["selected_names"]
        or trajectory["initial_tools"] != tools
        or any(t["tools"] != tools for t in trajectory["turns"])
        or totals != record["usage"]
        or abs(cost - record["usage_priced_cost"]) > 1e-9
        or record["provider_requests"] != len(record["attempts"])
    ):
        raise ValueError("Selector final tools/aggregate accounting mismatch")
    return record
