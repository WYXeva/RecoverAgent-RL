"""Synthetic fixtures for split, provenance identities, weighting and coverage only."""

import copy
import json

import pytest

from recoveragent.coverage_sampling import coverage_plan
from recoveragent.dataset_design import design, exact_key, example_weights, family_split
from recoveragent.io import digest
from recoveragent.formal_dataset import training_record, visible_schema_replay
from recoveragent.sft_recipe import validate_config
from recoveragent.io import read_json
from scripts.audit_base_sft_collection import validate_batch


def sample(identity, task="f0_1", answer=1):
    return {
        "trajectory_id": identity,
        "task_id": task,
        "scenario_id": task.split("_")[0],
        "actor_prompt_variant": "execution_v5",
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": identity,
                        "type": "function",
                        "function": {
                            "name": "supervisor__complete_task",
                            "arguments": json.dumps({"answer": answer}),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": identity, "content": "ok"},
        ],
        "turns": [{"tools": []}],
    }


def quality(s, turns=2, ready=True):
    return {
        "trajectory_id": s["trajectory_id"],
        "task_id": s["task_id"],
        "scenario_id": s["scenario_id"],
        "ready_for_context_limit": ready,
        "max_sequence_tokens": 10 if ready else 40000,
        "max_target_tokens": 5,
        "stats": {"input_tokens": 20, "examples": turns},
    }


def test_dedup_ignores_call_ids_but_keeps_distinct_real_outputs_and_defers():
    a = sample("a")
    b = sample("b")
    c = sample("c", answer=2)
    d = sample("d", answer=3)
    e = sample("e", "g_1")
    assert exact_key(a) == exact_key(b)
    ss = [a, b, c, d, e]
    q = [quality(s, ready=s != e) for s in ss]
    out = design(ss, q)
    assert len(out["selected_ids"]) == 2 and len(out["duplicates"]) == 1
    assert len(out["reserve"]) == len(out["deferred"]) == 1
    assert out == design(list(reversed(ss)), list(reversed(q)))


def test_family_split_and_balanced_loss_mass():
    pool = {f"f{i}_{j}": f"f{i}" for i in range(8) for j in range(3)}
    split = family_split(pool, 42, 2)
    assert (
        split == family_split(pool, 42, 2)
        and list(split.values()).count("validation") == 2
    )
    ss = [sample("a", "f0_1"), sample("b", "f0_2"), sample("c", "f1_1")]
    qs = [quality(s, turns=i + 1) for i, s in enumerate(ss)]
    w = example_weights(ss, qs, {s["trajectory_id"]: s["trajectory_id"] for s in ss})
    mass = {
        f: sum(
            w[s["trajectory_id"]] * q["stats"]["examples"]
            for s, q in zip(ss, qs)
            if s["scenario_id"] == f
        )
        for f in ["f0", "f1"]
    }
    assert mass["f0"] == pytest.approx(mass["f1"])


def test_coverage_is_bounded_reproducible_and_declares_outcome_selection():
    pool = {f"f{i}_{j}": f"f{i}" for i in range(4) for j in range(3)}
    ledger = {
        "pool_hash": digest(sorted(pool.items())),
        "selected_success_counts": {"f0_0": 2, "f1_0": 1},
        "new_attempt_counts": {"f2_0": 2},
    }
    settings = {
        "seed": 12,
        "num_tasks": 4,
        "coverage_ledger_hash": digest(ledger),
        "max_per_task": 2,
        "max_new_attempts_per_task": 2,
    }
    p = coverage_plan(pool, settings, ledger)
    assert "f0_0" not in p["tasks"] and "f2_0" not in p["tasks"]
    assert p["hidden_labels_or_results_used"] and not p["hidden_answers_used"]
    assert p == coverage_plan(pool, settings, ledger)
    assert p["rows"][0]["reason"] == "missing_family"
    bad = copy.deepcopy(ledger)
    bad["selected_success_counts"]["f0_0"] = 0
    with pytest.raises(ValueError, match="changed"):
        coverage_plan(pool, settings, bad)


def test_control_metadata_is_not_exported_into_training_records():
    s = sample("a")
    s.update(
        appworld={"evaluator": "private"},
        provenance={"answer": "private"},
        initial_state_hash="private",
    )
    r = training_record(s)
    assert not {"appworld", "provenance", "initial_state_hash"} & r.keys()
    assert r["messages"] == s["messages"] and r["turns"] == s["turns"]
    assert "appworld" in s  # Original stays untouched.


def test_discovered_schema_only_appears_after_successful_tool_result():
    docs = {"function": {"name": "api_docs__show_api_doc"}}
    api = {"function": {"name": "example__read"}}
    catalogue = {t["function"]["name"]: t for t in [docs, api]}
    call = {
        "id": "one",
        "function": {
            "name": "api_docs__show_api_doc",
            "arguments": '{"app_name":"example","api_name":"read"}',
        },
    }
    s = {
        "messages": [
            {"role": "assistant", "tool_calls": [call]},
            {"role": "tool", "content": "public schema"},
            {"role": "assistant", "content": "done"},
        ],
        "turns": [
            {"assistant_index": 0, "tools": [docs]},
            {"assistant_index": 2, "tools": [docs, api]},
        ],
    }
    assert visible_schema_replay(s, catalogue) == [docs, api]
    bad = copy.deepcopy(s)
    bad["turns"][0]["tools"].append(api)
    with pytest.raises(ValueError, match="future or undiscovered"):
        visible_schema_replay(bad, catalogue)
    bad = copy.deepcopy(s)
    bad["messages"][1]["content"] = "Execution failed"
    with pytest.raises(ValueError, match="future or undiscovered"):
        visible_schema_replay(bad, catalogue)


def test_recipe_freezes_student_budget_and_mask_weight_contract():
    c = read_json("configs/base_sft_qwen3_4b_smoke.json")
    assert validate_config(c)["training_started"] is False
    for path, value in [
        ("model", "wrong"),
        ("thinking", True),
        ("max_sequence_tokens", 65536),
        ("loss_contract", "token_mean"),
    ]:
        bad = copy.deepcopy(c)
        bad[path] = value
        with pytest.raises(ValueError):
            validate_config(bad)
    c["training_args"]["max_steps"] = 100
    with pytest.raises(ValueError, match="bounded"):
        validate_config(c)


def test_training_runtime_must_match_packaged_source_lock(tmp_path):
    from recoveragent.formal_dataset import verify_runtime_protocol
    from recoveragent.io import write_json
    from recoveragent.demonstrations import sha256

    source = tmp_path / "code.py"
    source.write_text("# synthetic source\n")
    write_json(tmp_path / "control/protocol_lock.json", {"code.py": sha256(source)})
    assert verify_runtime_protocol(tmp_path, tmp_path)["runtime_source_match"]
    source.write_text("# changed source\n")
    with pytest.raises(ValueError, match="Runtime protocol source mismatch"):
        verify_runtime_protocol(tmp_path, tmp_path)


def test_interrupted_batch_keeps_actual_prefix_and_unattempted_tasks_separate():
    from scripts.seal_interrupted_collection import stopped_prefix

    plan = {"tasks": ["a", "b", "c"]}
    assert stopped_prefix(plan, [{"task_id": "b"}, {"task_id": "a"}]) == (
        ["a", "b"],
        ["c"],
    )
    for bad in (["a", "a"], ["a", "c"], ["unexpected"]):
        with pytest.raises(ValueError, match="planned task prefix"):
            stopped_prefix(plan, [{"task_id": t} for t in bad])


def test_rejected_batch_audit_checks_current_schema_not_just_json_format():
    message = sample("a")["messages"][0]
    request = {"messages": [], "tools": []}
    with pytest.raises(ValueError, match="visible public schema"):
        validate_batch(message, request, None, 0)
    with pytest.raises(ValueError, match="remaining tool budget"):
        validate_batch(message, request, 0, 0)
    duplicated = copy.deepcopy(message)
    duplicated["tool_calls"].append(copy.deepcopy(duplicated["tool_calls"][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        validate_batch(duplicated, request, None, 0)


@pytest.mark.parametrize(
    "name,symlink", [("../outside", False), ("/outside", False), ("link", True)]
)
def test_transfer_rejects_path_escape_and_links_before_extraction(
    tmp_path, name, symlink
):
    import io
    import tarfile
    from recoveragent.dataset_transfer import import_archive
    from recoveragent.demonstrations import sha256

    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(name)
        if symlink:
            info.type = tarfile.SYMTYPE
            info.linkname = "../outside"
        else:
            info.size = 1
        tar.addfile(info, None if symlink else io.BytesIO(b"x"))
    output = tmp_path / "imported"
    with pytest.raises(ValueError, match="Unsafe archive"):
        import_archive(archive, output, sha256(archive), "0" * 64)
    assert not output.exists()
