"""Preparation/selection only. All episodes here are synthetic, never trial results."""

import copy
import json
import shutil

import pytest

from scripts import prepare_b10_recovery_pilot as pilot
from recoveragent import p3c, schema_compatibility as compat
from recoveragent.full732_recovery_tasks import b_tasks, context_for
from recoveragent.io import files_manifest, read_json, write_json
from recoveragent.nullable_recovery_runtime import RUNTIME
import test_nullable_recovery as fixtures

worlds = fixtures.worlds


def episode(worlds, tmp_path, *, error=True, distinct=False, **changes):
    row = b_tasks()[1][0]
    home = tmp_path / "episode"
    home.mkdir()
    adapter = worlds(row["task_id"], context_for(row["task_id"]))
    adapter.configure_schema_compatibility(compat.SCHEMA_REVISION, home / "schema")
    session = fixtures.session_for(adapter, home / "session")
    release = read_json("configs/p3c_nullable_v2_m1_release_20261104.json")
    session.identity = {
        k: release[k] for k in ("model", "revision", "thinking", "chat_template_sha256")
    }
    session.identity["checkpoint_id"] = "adapter:" + release["adapter_tensor_sha256"]
    session.runtime_revision = RUNTIME
    session.messages.extend(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "synthetic_call",
                        "type": "function",
                        "function": {
                            "name": "api_docs__show_api_doc",
                            "arguments": '{"app_name":"gmail","api_name":"show_category_sizes"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "synthetic_call",
                "name": "api_docs__show_api_doc",
                "content": "Execution failed: synthetic error"
                if error
                else "synthetic success",
            },
        ]
    )
    post = p3c.save_boundary(session, home / "checkpoints/post", "post_action", {})
    post["error_type"] = "public_tool_error"
    if distinct:
        session.messages.append({"role": "user", "content": "Synthetic later boundary"})
    late = p3c.save_boundary(session, home / "checkpoints/late", "pre_action", {})
    result = {
        "task_id": row["task_id"],
        "policy": session.identity,
        "outcome": "model_failure",
        "natural_failure": True,
        "evaluation": {"status": "valid", "success": False},
        "fault": None,
        "recovery_candidates": {
            "pre_error": late,
            "post_error": post,
            "late_failure": late,
        },
        **changes,
    }
    write_json(home / "result.json", result)
    write_json(home / "manifest.json", {"files": files_manifest(home)})
    return home


def test_preparation_uses_exact_frozen_first_ten_without_execution(tmp_path):
    jobs = pilot.prepare(tmp_path / "plan")
    assert [j["task_id"] for j in jobs] == [r["task_id"] for r in b_tasks()[1][:10]]
    assert all(j["attempt_limit"] == 1 for j in jobs)
    assert all(j["command"][2:4] == ["recoveragent.p3c", "episode"] for j in jobs)
    record = read_json(tmp_path / "plan/preparation.json")
    assert record["actual_student_tasks"] == record["actual_teacher_attempts"] == 0
    assert record["status"] == "PREPARED_NOT_EXECUTED"
    with pytest.raises(FileExistsError):
        pilot.prepare(tmp_path / "plan")


def test_checkpoint_selection_ignores_pre_error_and_deduplicates_boundary(
    worlds, tmp_path
):
    home = episode(worlds, tmp_path)
    before = files_manifest(home)
    result = pilot.select_checkpoints(home)
    assert [c["kind"] for c in result["selected"]] == ["post_error"]
    assert result["skipped"] == [
        {"kind": "late_failure", "reason": "duplicate_boundary"}
    ]
    assert files_manifest(home) == before
    assert all(c["teacher_attempt_limit"] == 1 for c in result["selected"])


def test_two_distinct_legal_boundaries_are_selected_in_frozen_order(worlds, tmp_path):
    result = pilot.select_checkpoints(episode(worlds, tmp_path, distinct=True))
    assert [c["kind"] for c in result["selected"]] == ["post_error", "late_failure"]


def test_post_error_requires_observed_error_in_snapshot_history(worlds, tmp_path):
    result = pilot.select_checkpoints(episode(worlds, tmp_path, error=False))
    assert [c["kind"] for c in result["selected"]] == ["late_failure"]
    assert result["skipped"][0]["reason"] == "not_post_observable_tool_error"


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": "success", "natural_failure": False},
        {
            "outcome": "infrastructure_error",
            "fault": {"category": "worker_environment"},
        },
        {"evaluation": {"status": "evaluation_error", "success": None}},
    ],
)
def test_success_and_unknown_never_become_recovery_sources(worlds, tmp_path, changes):
    result = pilot.select_checkpoints(episode(worlds, tmp_path, **changes))
    assert result == {
        "selected": [],
        "skipped": [{"reason": "not_valid_natural_failure"}],
    }


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"task_id": "not_in_frozen_b10"}, "outside frozen B10"),
        (
            {"policy": {"model": "raw base", "checkpoint_id": "raw_base:any"}},
            "did not come from M1",
        ),
    ],
)
def test_other_tasks_and_raw_model_rejected(worlds, tmp_path, changes, reason):
    with pytest.raises(ValueError, match=reason):
        pilot.select_checkpoints(episode(worlds, tmp_path, **changes))


def test_modified_snapshot_is_rejected_without_resealing(worlds, tmp_path):
    home = episode(worlds, tmp_path)
    (home / "checkpoints/post/context.json").write_text("{}")
    with pytest.raises(ValueError, match="evidence changed"):
        pilot.select_checkpoints(home)


def test_preparation_rejects_modified_config_or_lock(tmp_path, monkeypatch):
    root = tmp_path / "code"
    lock = read_json(pilot.ROOT / pilot.LOCK)
    for name in [*lock["files"], pilot.LOCK]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pilot.ROOT / name, target)
    config = copy.deepcopy(read_json(root / pilot.CONFIG))
    config["teacher_limits"]["max_attempts"] = 21
    (root / pilot.CONFIG).write_text(json.dumps(config))
    monkeypatch.setattr(pilot, "ROOT", root)
    with pytest.raises(ValueError, match="source changed"):
        pilot.checked_plan()
