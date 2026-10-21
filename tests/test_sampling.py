"""Train membership, reproducibility and frozen prompt boundaries (software only)."""

import copy
import sys
from collections import Counter
from types import SimpleNamespace

import pytest

from recoveragent.io import digest, read_json, write_json
from recoveragent.protocol_lock import verify_lock
from recoveragent.sampling import make_plan, load_plan
from recoveragent import sampling, p3a


POOL = {f"family{i}_{j}": f"family{i}" for i in range(10) for j in range(3)}
SETTINGS = {
    "strategy": "scenario_balanced",
    "seed": 42,
    "num_tasks": 12,
    "num_scenarios": 6,
    "exclude_scenarios": ["family0"],
}


def test_scenario_balance_order_invariance_and_seed():
    plan = make_plan(POOL, SETTINGS)
    assert plan == make_plan(dict(reversed(list(POOL.items()))), SETTINGS)
    assert len(set(plan["tasks"])) == 12
    counts = Counter(r["scenario_id"] for r in plan["rows"])
    assert len(counts) == 6 and set(counts.values()) == {2}
    assert "family0" not in counts
    assert plan != make_plan(POOL, {**SETTINGS, "seed": 43})
    assert plan["hidden_labels_or_results_used"] is False


@pytest.mark.parametrize(
    "tasks", [["dev_1"], ["family0_1"], ["family1_1", "family1_1"]]
)
def test_explicit_rejects_nontrain_excluded_or_duplicate(tasks):
    with pytest.raises(ValueError):
        make_plan(
            POOL,
            {
                **SETTINGS,
                "strategy": "explicit",
                "num_tasks": len(tasks),
                "task_ids": tasks,
            },
        )


def test_fixed_manifest_reproduces_and_rejects_substitution(tmp_path, monkeypatch):
    settings = {
        **SETTINGS,
        "strategy": "explicit",
        "num_tasks": 2,
        "task_ids": ["family9_2", "family1_0"],
    }
    plan = make_plan(POOL, settings)
    path = tmp_path / "plan.json"
    write_json(path, plan)
    config = {
        "task_manifest": str(path),
        "task_manifest_hash": digest(plan),
        "appworld_root": "unused",
        "sampling": settings,
        "limits": {"max_tasks": 2},
    }
    monkeypatch.setattr(sampling, "official_pool", lambda _: POOL)
    assert load_plan(config)["tasks"] == settings["task_ids"]
    config["sampling"] = {**settings, "task_ids": list(reversed(settings["task_ids"]))}
    with pytest.raises(ValueError, match="reproduced"):
        load_plan(config)


def test_expansion_requires_frozen_contract_and_legacy_caps(tmp_path):
    config = read_json("configs/p3a_expanded.json")
    p3a.validate_config(config)
    changed = copy.deepcopy(config)
    changed["actor_prompt_variant"] = "budget_batched_v2"
    with pytest.raises(ValueError, match="frozen protocol"):
        p3a.validate_config(changed)
    legacy = read_json("configs/p3a_teacher.json")
    legacy["limits"]["max_tasks"] = 12
    with pytest.raises(ValueError, match="max_tasks"):
        p3a.validate_config(legacy)
    # An actual source mutation must fail before model dispatch.
    write_json(tmp_path / "lock.json", {"files": {"prompt.txt": digest("original")}})
    (tmp_path / "prompt.txt").write_text("tampered")
    with pytest.raises(ValueError, match="protocol changed"):
        verify_lock({"protocol_lock": "lock.json"}, root=tmp_path)


@pytest.mark.parametrize(
    "proceed,reserve,expected", [(True, 0, 12), (False, 0, 1), (True, 196608, 1)]
)
def test_interrupted_worker_preserved_and_unknown_budget_not_reused(
    tmp_path, monkeypatch, proceed, reserve, expected
):
    # Synthetic controller fault injection only; not a Teacher trajectory.
    config = read_json("configs/p3a_expanded.json")
    config["authorization"]["max_cost"] = 12
    config["continue_after_infrastructure_error"] = proceed
    plan = make_plan(POOL, SETTINGS)
    monkeypatch.setitem(
        sys.modules,
        "appworld",
        SimpleNamespace(load_task_ids=lambda _: list(POOL), update_root=lambda _: None),
    )
    monkeypatch.setattr(sampling, "load_plan", lambda _: plan)
    monkeypatch.setattr(p3a, "preflight", lambda *a: {"status": "PASS"})
    monkeypatch.setattr(p3a, "read_credentials", lambda _: {})
    monkeypatch.setattr(
        p3a.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1)
    )
    empty = p3a.interrupted_usage(tmp_path / "empty", config["teacher"])
    empty["unknown_generation_reservation"] = reserve
    monkeypatch.setattr(p3a, "interrupted_usage", lambda *a: copy.deepcopy(empty))
    output = tmp_path / "run"
    result = p3a.collect(config, output)
    assert result["attempts"] == expected
    assert result["counts"]["infrastructure_errors"] == expected
    assert result["costs"]["unknown_generation_reservation"] == reserve * expected
    assert len(list(output.glob("*.controller_error.json"))) == expected
    selected = read_json(output / "task_selection.json")["tasks"]
    assert selected == plan["tasks"]  # No replacement or implicit retry.
