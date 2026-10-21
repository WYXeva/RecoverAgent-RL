"""Software checks for prompt identity, context separation, and selector provenance."""

import copy
import json
from types import SimpleNamespace

import pytest

from recoveragent import p3a
from recoveragent.demonstrations import export_dataset, validate_receipts
from recoveragent.io import digest, read_json, write_json
from recoveragent.p3a_prompts import EXECUTION_RULES, MODE_RULES, actor_system
from recoveragent.predictor_prompts import STRUCTURED_VARIANT, example_messages
from recoveragent.tool_modes import PREDICTOR_PROMPTS, predictor_messages
from test_teacher_data import config as teacher_config
from test_tool_modes import predictor_fixture


@pytest.fixture
def config():
    return teacher_config.__wrapped__()


def test_shared_actor_contract_differs_only_in_tool_access(config):
    systems = []
    for mode in MODE_RULES:
        config["tool_mode"] = mode
        prompt = actor_system(config)
        assert prompt.startswith(EXECUTION_RULES)
        systems.append(prompt.replace(MODE_RULES[mode], "MODE"))
    assert systems[0] == systems[1]


def test_two_examples_precede_actual_task_and_do_not_mutate_upstream():
    base = [
        {"role": "system", "content": "public catalogue"},
        {"role": "assistant", "content": "Understood."},
        {"role": "user", "content": "actual public task"},
    ]
    predictor = SimpleNamespace(build_messages=lambda *a, **kw: base)
    result = predictor_messages(predictor, None, STRUCTURED_VARIANT)
    assert result[-5:-1] == example_messages()
    assert result[-1] == base[-1]
    assert [x["role"] for x in result[-5:]] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert base[0]["content"] == "public catalogue" and len(base) == 3
    for message in example_messages()[1::2]:
        names = message["content"].splitlines()
        assert len(set(names)) == len(names) <= 20
        assert "supervisor.complete_task" in names


@pytest.mark.parametrize("variant", PREDICTOR_PROMPTS)
def test_all_selector_variants_export_only_real_actor_targets(
    tmp_path, monkeypatch, config, variant
):
    config["predictor_prompt_variant"] = variant
    requests = predictor_fixture(monkeypatch, config)
    run = tmp_path / "run"
    write_json(run / "config.json", config)
    output = run / "teacher_synthetic"
    trajectory = p3a.run_teacher_episode(
        config, "fake", output, {"DEEPSEEK_API_KEY": "synthetic"}
    )
    assert trajectory["status"] == "valid"
    validate_receipts(trajectory, output)
    assert trajectory["usage"]["provider_requests"] == 2
    assert trajectory["usage"]["model_turns"] == 1
    assert all(
        "SYNTHETIC SELECTION EXAMPLE" not in m["content"]
        for m in requests[1]["messages"]
    )
    dataset = tmp_path / "dataset"
    manifest = export_dataset(run, dataset, {"fake"}, config["limits"])
    assert manifest["counts"]["accepted"] == 1
    record = json.loads((dataset / "demonstrations.jsonl").read_text())
    assert len(record["turns"]) == 1
    assert "SYNTHETIC SELECTION EXAMPLE" not in json.dumps(record)
    if variant == STRUCTURED_VARIANT:
        assert requests[0]["messages"][-5:-1] == example_messages()
        assert trajectory["predictor"]["demo_task_ids"] == []
        assert trajectory["predictor"]["example_count"] == 2
        # Even with an updated receipt hash, altered examples cannot keep the
        # original version/provenance label. This is separate from file integrity.
        request_path = output / "requests/000.request.json"
        tampered = copy.deepcopy(read_json(request_path))
        tampered["messages"][-2]["content"] += "\nadmin.secret"
        request_path.write_text(json.dumps(tampered))
        trajectory["predictor"]["request_hash"] = digest(tampered)
        with pytest.raises(ValueError, match="examples or selection contract"):
            validate_receipts(trajectory, output)
