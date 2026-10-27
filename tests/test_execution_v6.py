"""Exact user prompt and actual request wiring; all transports/worlds are synthetic."""

import copy
import hashlib

import pytest

from recoveragent.execution_v5 import actor_system as legacy_system
from recoveragent.execution_v6 import (
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_SHA256,
    VARIANT,
    actor_system,
)
from recoveragent.harness import RULES
from recoveragent.io import read_json
from recoveragent.student_evaluation import evaluation_prompt


def test_exact_text_has_no_implicit_budget_or_harness_appendix():
    assert hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest() == (
        "5ef2bb774a258ba0a0e00d94284a29720bad3d2578c9a2a54ec94a594272dbba"
    )
    config = {"actor_prompt_variant": VARIANT, "tool_mode": "dynamic_discovery"}
    assert actor_system(config, harness=True) == SYSTEM_PROMPT
    assert actor_system(config, harness=False) == SYSTEM_PROMPT
    assert RULES not in SYSTEM_PROMPT
    config["system_prompt_sha256"] = "wrong"
    with pytest.raises(ValueError, match="checksum"):
        actor_system(config)


def test_reject_fixed_tool_mode_instead_of_misleading_discovery_prompt():
    with pytest.raises(ValueError, match="dynamic_discovery"):
        actor_system({"actor_prompt_variant": VARIANT, "tool_mode": "api_predictor"})


def test_new_shared_stage_changes_only_prompt_identity_and_runtime():
    old = read_json("configs/shared_stage_validation.json")
    new = read_json("configs/shared_stage_validation_v2.json")
    assert evaluation_prompt(new["execution"]) == SYSTEM_PROMPT
    assert new["execution"]["system_prompt_sha256"] == SYSTEM_PROMPT_SHA256
    normalized = copy.deepcopy(new)
    normalized["format"] = old["format"]
    for field in ("actor_prompt_variant", "runtime_revision"):
        normalized["execution"][field] = old["execution"][field]
    del normalized["execution"]["system_prompt_sha256"]
    assert normalized == old  # Same split, budget, Memory and scoring blocker.


@pytest.mark.parametrize("mode", ["dynamic_discovery", "api_predictor"])
def test_legacy_v5_prompt_bytes_are_unchanged(mode):
    config = read_json("configs/p3a_harness_refactor.json")
    config["tool_mode"] = mode
    config.update(remaining_generated_tokens=16384, remaining_requests=40)
    assert actor_system(config) == legacy_system(config)
    assert actor_system(config, harness=True) == legacy_system(config) + "\n" + RULES


def test_teacher_actual_synthetic_request_uses_exact_student_system(
    tmp_path, monkeypatch
):
    from recoveragent import evaluation, p3a
    from recoveragent.teacher import TeacherPolicy
    from test_harness import config as harness_config
    from test_teacher_data import install_fixture, response

    config = harness_config()
    config["actor_prompt_variant"] = VARIANT
    config["system_prompt_sha256"] = SYSTEM_PROMPT_SHA256
    install_fixture(monkeypatch, config)
    monkeypatch.setattr(evaluation, "environment_check", lambda: {"synthetic": True})
    monkeypatch.setattr(
        evaluation,
        "evaluate_isolated",
        lambda *args: {
            "status": "valid",
            "success": True,
            "official": {"synthetic": True},
        },
    )
    requests = []

    def transport(payload):
        requests.append(copy.deepcopy(payload))
        return response()

    monkeypatch.setattr(
        p3a,
        "TeacherPolicy",
        lambda c, creds: TeacherPolicy(c, creds, transport=transport),
    )
    trajectory = p3a.run_teacher_episode(
        config, "unit_task", tmp_path / "synthetic", {"DEEPSEEK_API_KEY": "synthetic"}
    )
    assert trajectory["status"] == "valid"
    assert len(requests) == 1
    assert requests[0]["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert trajectory["messages"][0]["content"] == evaluation_prompt(
        read_json("configs/shared_stage_validation_v2.json")["execution"]
    )
    paths = list((tmp_path / "synthetic/requests").glob("*.request.json"))
    assert len(paths) == 1
    saved = read_json(paths[0])
    assert saved["messages"][0]["content"] == SYSTEM_PROMPT
