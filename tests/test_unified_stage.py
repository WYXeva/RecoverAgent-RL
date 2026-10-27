"""Synthetic protocol tests; real AppWorld probe results are reported separately."""

from pathlib import Path
import sys
import types

import pytest

from recoveragent.io import digest, read_json, write_json
from recoveragent.shared_stage import compare, prepare, verify_lock, verify_release
from recoveragent.unified_split import (
    ROLES,
    audit_pool,
    make_split,
    sha,
    stage_tasks,
    validate_split,
    validate_training_sources,
)


def audit_fixture():
    return {
        "source": {"data_version": "fixture"},
        "rows": [
            {
                "task_id": f"{s}_{n}",
                "scenario_id": s,
                "official_split": split,
                "structural_status": "PASS",
                "initial_input_sha256": digest([s, n]),
            }
            for s, split in (
                ("family1", "train"),
                ("family2", "train"),
                ("family3", "dev"),
                ("family4", "test_normal"),
            )
            for n in range(1, 4)
        ],
    }


@pytest.fixture
def plan(monkeypatch):
    # Synthetic release/lineage fixtures do not claim an AppWorld runtime check.
    monkeypatch.setattr(
        "recoveragent.shared_stage.verify_runtime_data", lambda *_: True
    )
    return make_split(audit_fixture(), train_only=True)


def seal(plan):
    plan["manifest_sha256"] = digest(
        {k: v for k, v in plan.items() if k != "manifest_sha256"}
    )


def test_deterministic_disjoint_triplets_and_b_reuse(plan):
    audit = audit_fixture()
    audit["rows"].reverse()
    audit["historical_success"] = {r["task_id"]: False for r in audit["rows"]}
    assert make_split(audit, train_only=True)["rows"] == plan["rows"]
    assert plan["counts"] == dict.fromkeys(ROLES, 2)
    sets = [
        set(r["task_id"] for r in stage_tasks(plan, s))
        for s in ("base_sft", "recovery_sft", "validation")
    ]
    assert sum(map(len, sets)) == len(set.union(*sets)) == 6
    assert stage_tasks(plan, "recovery_sft") == stage_tasks(plan, "grpo")
    assert len(stage_tasks(plan, "validation")) == 2


@pytest.mark.parametrize("fault", ["missing_variant", "duplicate", "missing_files"])
def test_invalid_scenario_excluded_without_filling(fault):
    audit = audit_fixture()
    if fault == "missing_variant":
        audit["rows"].pop(0)
    elif fault == "duplicate":
        audit["rows"][1] = audit["rows"][0]
    else:
        audit["rows"][0]["structural_status"] = "BLOCKED"
    split = make_split(audit, train_only=True)
    assert split["counts"] == dict.fromkeys(ROLES, 1)
    assert len(split["excluded"]) == 1


@pytest.mark.parametrize(
    "fault", ["seed", "hash", "assignment", "duplicate", "official_split"]
)
def test_manifest_tampering_and_relabeling_rejected(plan, fault):
    if fault == "seed":
        plan["seed"] += 1
    elif fault == "hash":
        plan["manifest_sha256"] = "0" * 64
    elif fault == "assignment":
        plan["rows"][0]["role"], plan["rows"][1]["role"] = (
            plan["rows"][1]["role"],
            plan["rows"][0]["role"],
        )
        seal(plan)
    elif fault == "duplicate":
        plan["rows"].append(plan["rows"][0])
        seal(plan)
    else:
        plan["rows"][0]["official_split"] = "dev"
        seal(plan)
    with pytest.raises(ValueError):
        validate_split(plan)


def test_full_candidate_blocked_for_execution_and_training():
    candidate = make_split(audit_fixture(), train_only=False)
    assert candidate["counts"] == dict.fromkeys(ROLES, 4)
    for stage in ("base_sft", "recovery_sft", "grpo", "validation"):
        with pytest.raises(ValueError, match="blocked"):
            stage_tasks(candidate, stage)


@pytest.mark.parametrize("stage", ["base_sft", "recovery_sft", "grpo"])
def test_no_c_in_training_and_bound_sources(plan, stage):
    record = {
        "task_id": stage_tasks(plan, stage)[0]["task_id"],
        "official_split": "train",
        "split_manifest_sha256": plan["manifest_sha256"],
    }
    assert validate_training_sources(plan, stage, [record])
    record["task_id"] = stage_tasks(plan, "validation")[0]["task_id"]
    with pytest.raises(ValueError, match="violates"):
        validate_training_sources(plan, stage, [record])
    record["task_id"] = stage_tasks(plan, stage)[0]["task_id"]
    record["split_manifest_sha256"] = "unbound"
    with pytest.raises(ValueError, match="Unbound"):
        validate_training_sources(plan, stage, [record])


def protocol(plan):
    return {
        "split_manifest_sha256": plan["manifest_sha256"],
        "model_identity": {
            "model": "Qwen/Qwen3-4B",
            "revision": "fixed",
            "thinking": False,
            "chat_template_sha256": "fixed",
        },
        "execution": {
            "appworld_root": "synthetic_test_only",
            "memory": {"enabled": False},
            "tool_mode": "dynamic_discovery",
            "student_limits": {"seed": 42},
        },
    }


def release(tmp_path, plan, stage, parent=None):
    prefix = tmp_path / stage
    sources = prefix.with_suffix(".sources.json")
    s = {"M1": "base_sft", "M2": "recovery_sft", "M3": "grpo"}[stage]
    write_json(
        sources,
        [
            {
                "task_id": stage_tasks(plan, s)[0]["task_id"],
                "official_split": "train",
                "split_manifest_sha256": plan["manifest_sha256"],
            }
        ],
    )
    evidence = prefix.with_suffix(".synthetic.json")
    write_json(evidence, {"synthetic_test_only": True})
    value = {
        "stage": stage,
        "split_manifest_sha256": plan["manifest_sha256"],
        "policy": {
            **protocol(plan)["model_identity"],
            "checkpoint_id": "adapter:" + stage,
        },
        "parameter_update_verified": True,
        "save_reload_verified": True,
        "training_sources": {"path": str(sources), "sha256": sha(sources)},
        "training_evidence": {str(evidence): sha(evidence)},
        "initialization": "raw_Qwen3-4B"
        if parent is None
        else read_json(parent)["policy"]["checkpoint_id"],
        "parent_release": None
        if parent is None
        else {"path": str(parent), "sha256": sha(parent)},
    }
    path = prefix.with_suffix(".release.json")
    write_json(path, value)
    return path


def test_all_stages_use_same_clean_inputs_and_correct_lineage(tmp_path, plan):
    parent, configurations = None, []
    for stage in ("M1", "M2", "M3"):
        parent = release(tmp_path, plan, stage, parent)
        config = prepare(
            protocol(plan), plan, stage, parent, sha(parent), "http://localhost"
        )
        assert config["start_kind"] == "clean_original_task"
        assert config["resume_checkpoint"] is None
        worker = config["worker_config"]
        del worker["expected_policy"]
        configurations.append(worker)
    assert configurations[0] == configurations[1] == configurations[2]
    assert {c["task_id"] for c in configurations[0]["cases"]} == {
        r["task_id"] for r in stage_tasks(plan, "validation")
    }


@pytest.mark.parametrize(
    "fault", ["old_adapter", "no_update", "source_c", "wrong_policy", "source_hash"]
)
def test_release_gates(tmp_path, plan, fault):
    path = release(tmp_path, plan, "M1")
    value = read_json(path)
    if fault == "old_adapter":
        value["initialization"] = "historical_adapter"
    elif fault == "no_update":
        value["parameter_update_verified"] = False
    elif fault == "wrong_policy":
        value["policy"]["model"] = "Qwen-1.7B"
    else:
        source = Path(value["training_sources"]["path"])
        records = read_json(source)
        records[0]["task_id"] = stage_tasks(plan, "validation")[0]["task_id"]
        source.write_text(__import__("json").dumps(records))
        if fault == "source_c":
            value["training_sources"]["sha256"] = sha(source)
    path.write_text(__import__("json").dumps(value))
    with pytest.raises(ValueError):
        prepare(protocol(plan), plan, "M1", path, sha(path), "http://localhost")


def test_wrong_parent_chain_rejected(tmp_path, plan):
    m1 = release(tmp_path, plan, "M1")
    m2 = release(tmp_path, plan, "M2", m1)
    value = read_json(m2)
    value["initialization"] = "some_other_adapter"
    m2.write_text(__import__("json").dumps(value))
    with pytest.raises(ValueError, match="lineage"):
        verify_release(m2, sha(m2), plan, "M2")


def results(plan, success):
    return [
        {
            "task_id": c["task_id"],
            "status": "valid",
            "official_success": success,
            "outcome": "success" if success else "task_failure",
            "initial_state_hash": c["task_id"],
            "first_input_hash": c["task_id"],
            "first_messages_hash": c["task_id"],
            "first_tools_hash": c["task_id"],
        }
        for c in stage_tasks(plan, "validation")
    ]


def test_paired_metrics_complete_denominators_and_deltas(plan):
    result = compare(
        protocol(plan),
        plan,
        {
            "M1": results(plan, False),
            "M2": results(plan, True),
            "M3": results(plan, True),
        },
    )
    assert result["status"] == "COMPLETE"
    assert result["deltas"] == {"M2_minus_M1": 1, "M3_minus_M2": 0, "M3_minus_M1": 1}
    assert len(result["matrix"]) == 2


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "evaluation_error",
        "infrastructure_error",
        "initial_mismatch",
        "input_mismatch",
    ],
)
def test_unknown_missing_and_unpaired_never_complete(plan, fault):
    rows = {stage: results(plan, True) for stage in ("M1", "M2", "M3")}
    if fault == "missing":
        rows["M3"].pop()
    elif fault in {"evaluation_error", "infrastructure_error"}:
        rows["M3"][0].update(
            status=fault,
            official_success=None,
            outcome="evaluation_unknown" if fault == "evaluation_error" else fault,
        )
    else:
        rows["M3"][0][
            "initial_state_hash" if fault == "initial_mismatch" else "first_input_hash"
        ] = "different"
    result = compare(protocol(plan), plan, rows)
    assert result["status"] == "INCOMPLETE"
    assert all(v is None for v in result["deltas"].values())
    if fault in {"missing", "evaluation_error", "infrastructure_error"}:
        assert result["totals"]["M3"]["TGC"] is None
        assert result["totals"]["M3"]["failures"] == 0


def test_unknown_cannot_be_cast_to_failure(plan):
    rows = results(plan, False)
    rows[0]["status"] = "evaluation_error"
    with pytest.raises(ValueError, match="contradicts"):
        compare(protocol(plan), plan, {"M1": rows})


def test_empty_comparison_is_not_pass(plan):
    result = compare(protocol(plan), plan, {})
    assert result["status"] == "INCOMPLETE"
    assert all(
        t["not_run"] == 2 and t["TGC"] is None for t in result["totals"].values()
    )


def test_runtime_lock_detects_changes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("code.py").write_text("original")
    write_json("lock.json", {"files": {"code.py": sha("code.py")}})
    verify_lock("lock.json")
    Path("code.py").write_text("changed")
    with pytest.raises(ValueError, match="mismatch"):
        verify_lock("lock.json")


def test_metadata_audit_does_not_read_task_or_hidden_contents(tmp_path, monkeypatch):
    from recoveragent import APPWORLD_REVISION
    import recoveragent.unified_split as module

    monkeypatch.setattr(
        module.importlib.metadata,
        "distribution",
        lambda _: types.SimpleNamespace(read_text=lambda _: APPWORLD_REVISION),
    )
    root = tmp_path / "data"
    (root / "datasets").mkdir(parents=True)
    (root / "base_dbs").mkdir()
    (root / "api_docs/function_calling").mkdir(parents=True)
    (root / "version.txt").write_text("fixture")
    (root / "LICENSE").write_text("fixture")
    ids = {
        s: [f"{s}_{i}" for i in (1, 2, 3)]
        for s in ("train", "dev", "test_normal", "test_challenge")
    }
    # Use valid single-underscore task IDs despite split names containing underscores.
    ids = {s: [f"f{j}_{i}" for i in (1, 2, 3)] for j, s in enumerate(ids)}
    for split, tasks in ids.items():
        (root / "datasets" / (split + ".txt")).write_text("\n".join(tasks))
        for task in tasks:
            folder = root / "tasks" / task
            (folder / "dbs").mkdir(parents=True)
            (folder / "ground_truth").mkdir()
            (folder / "specs.json").write_text("do not inspect")
            (folder / "dbs/admin.jsonl").write_text("do not inspect")
            for name in (
                "evaluation.py",
                "metadata.json",
                "answer.json",
                "test_data.json",
            ):
                (folder / "ground_truth" / name).write_text("do not inspect")
    monkeypatch.setitem(
        sys.modules,
        "appworld",
        types.SimpleNamespace(
            load_task_ids=lambda s: ids[s], update_root=lambda _: None
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "appworld.task",
        types.SimpleNamespace(task_id_to_generator_id=lambda t: t.split("_")[0]),
    )
    original = Path.read_bytes

    def guarded(path):
        if "ground_truth" in path.parts or any(
            t in path.parts for s in ids if s != "train" for t in ids[s]
        ):
            raise AssertionError("Protected content was read")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    report = audit_pool(tmp_path)
    assert report["tasks"] == 12
    assert report["scenarios"] == 4
    assert report["anomalies"] == []


def test_probe_continuation_keeps_unknowns_and_runs_only_unattempted(
    tmp_path, plan, monkeypatch
):
    from scripts.audit_unified_pool import probe
    import scripts.audit_unified_pool as module

    ordered = sorted(plan["rows"], key=lambda r: r["task_id"])
    prior_rows = [
        {"task_id": r["task_id"], "status": "BLOCKED", "evaluation_category": "unknown"}
        for r in ordered[:2]
    ]
    previous = tmp_path / "previous"
    write_json(
        previous / "summary.json",
        {"split_manifest_sha256": plan["manifest_sha256"], "rows": prior_rows},
    )
    old_hash = sha(previous / "summary.json")
    plan_path = tmp_path / "plan.json"
    write_json(plan_path, plan)
    calls = []

    def fake_run(args, **kwargs):
        task = args[args.index("--task") + 1]
        out = Path(args[args.index("--output") + 1])
        calls.append(task)
        write_json(
            out / "probe.json",
            {"task_id": task, "status": "BLOCKED", "evaluation_category": "unknown"},
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    probe("unused", str(plan_path), tmp_path / "next", str(previous))
    assert calls == [r["task_id"] for r in ordered[2:]]
    assert sha(previous / "summary.json") == old_hash
    assert read_json(tmp_path / "next/summary.json")["unattempted"] == []


def test_probe_continuation_rejects_different_plan(tmp_path, plan):
    from scripts.audit_unified_pool import probe

    previous = tmp_path / "previous"
    write_json(
        previous / "summary.json", {"split_manifest_sha256": "changed", "rows": []}
    )
    path = tmp_path / "plan.json"
    write_json(path, plan)
    with pytest.raises(ValueError, match="same plan prefix"):
        probe("unused", str(path), tmp_path / "out", str(previous))


def test_type_breakdown_preserves_unknown_denominator(plan):
    rows = results(plan, True)
    rows[0].update(
        status="evaluation_error", official_success=None, outcome="evaluation_unknown"
    )
    coverage_rows = [{"task_id": r["task_id"], "text_labels": ["query"]} for r in rows]
    report = compare(protocol(plan), plan, {"M1": rows}, coverage_rows)
    assert report["by_public_text_type"]["query"]["M1"]["TGC"] is None
    assert report["by_public_text_type"]["query"]["M1"]["planned"] == 2


def test_empty_pool_is_auditable_but_not_executable():
    audit = audit_fixture()
    for row in audit["rows"]:
        row["structural_status"] = "BLOCKED"
    plan = make_split(audit, train_only=True)
    assert plan["counts"] == dict.fromkeys(ROLES, 0)
    assert len(plan["excluded"]) == 2
    with pytest.raises(ValueError, match="No valid tasks"):
        stage_tasks(plan, "base_sft")


def test_actual_historical_checkpoint_release_is_ineligible(plan):
    path = Path("reports/a100_checkpoint_release_20261026.json")
    with pytest.raises(ValueError, match="historical release"):
        verify_release(path, sha(path), plan, "M1")


def test_unresolved_scoring_gate_blocks_future_runs(tmp_path, plan):
    gate = tmp_path / "preflight.json"
    write_json(gate, {"scoring_status": "PARTIAL"})
    config = protocol(plan)
    config["scoring_preflight"] = {"path": str(gate), "sha256": sha(gate)}
    with pytest.raises(ValueError, match="scoring boundary"):
        prepare(config, plan, "M1", "not_loaded", "unused", "http://localhost")


@pytest.mark.parametrize("fault", ["task", "base", "schema"])
def test_runtime_input_manifest_rejects_changes(tmp_path, plan, fault):
    from recoveragent.unified_split import verify_runtime_data

    root = tmp_path / "data"
    for folder in ["datasets", "base_dbs", "api_docs/function_calling"]:
        (root / folder).mkdir(parents=True)
    (root / "version.txt").write_text("fixture")
    (root / "datasets/train.txt").write_text(
        "\n".join(r["task_id"] for r in plan["rows"])
    )
    base = root / "base_dbs/base.db"
    base.write_bytes(b"original base")
    schema = root / "api_docs/function_calling/public.json"
    schema.write_text("{}")
    for row in plan["rows"]:
        directory = root / "tasks" / row["task_id"]
        (directory / "dbs").mkdir(parents=True)
        (directory / "specs.json").write_text("{}")
        (directory / "dbs/public.jsonl").write_text("{}\n")
        row["initial_input_sha256"] = digest(
            {
                str(p.relative_to(directory)): sha(p)
                for p in [directory / "specs.json", directory / "dbs/public.jsonl"]
            }
        )
    plan["source"] = {
        "data_version": "fixture",
        "split_files": {"train": sha(root / "datasets/train.txt")},
        "base_db_files": {"base.db": sha(base)},
        "tool_schema_files": {"public.json": sha(schema)},
    }
    seal(plan)
    assert verify_runtime_data(plan, tmp_path)
    changed = (
        directory / "specs.json"
        if fault == "task"
        else base
        if fault == "base"
        else schema
    )
    changed.write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        verify_runtime_data(plan, tmp_path)
