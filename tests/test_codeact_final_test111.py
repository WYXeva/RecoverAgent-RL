"""Final-test access protection and immutable primary-only continuation."""

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from recoveragent.codeact import assessment as a, cli, inference
from recoveragent.io import digest, read_json, write_json
from recoveragent.v2_tasks import CODEACT_TEST, context_for, plan

ROOT = Path(__file__).resolve().parents[1]


def config():
    return read_json(ROOT / "configs/codeact_final_test111_20261122.json")


def test_frozen_task_and_scenario_isolation(monkeypatch):
    tasks = inference.evaluation_tasks("test", "test111")
    assert len(tasks) == 111 and len(set(tasks)) == 111
    for task in tasks:
        assert context_for(task, final_test=CODEACT_TEST)["role"] == "test"
        with pytest.raises(ValueError):
            context_for(task, training=True)
    invalid = copy.deepcopy(plan())
    testrows = [r for r in invalid["rows"] if r["role"] == "test"]
    testrows[0]["task_id"] = testrows[1]["task_id"]
    monkeypatch.setattr(inference, "plan", lambda: invalid)
    with pytest.raises(ValueError, match="isolation"):
        inference.evaluation_tasks("test", "test111")


@pytest.mark.parametrize("command", ["collect", "episode", "export", "train"])
def test_cli_test_is_evaluate_only(command, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sys.argv",
        [
            "codeact",
            command,
            "--split",
            "test",
            "--task-set",
            "test111",
            "--output",
            str(tmp_path),
        ],
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


def test_evaluate_passes_final_protocol_binds_sha_and_resumes(tmp_path, monkeypatch):
    cfg = config()
    calls = []
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_model.safetensors").write_bytes(b"synthetic")
    server = tmp_path / "server.json"
    write_json(server, {"synthetic": True})
    monkeypatch.setattr(
        inference, "verify_checkpoint", lambda *args: {"formal_s0": True}
    )
    monkeypatch.setattr(
        inference,
        "VLLMStudent",
        lambda *args: SimpleNamespace(reset_usage=lambda: None),
    )

    def run(
        task, attempt, target, root, configuration, client, *, training, final_test
    ):
        assert training is False and final_test == CODEACT_TEST and attempt == 1
        assert (
            context_for(task, training=False, final_test=final_test)["role"] == "test"
        )
        calls.append(task)
        result = {
            "task_id": task,
            "success": False,
            "valid": True,
            "termination": "task_completed",
            "usage": {},
            "seconds": 1,
        }
        write_json(target / "result.json", result)
        return result

    monkeypatch.setattr(inference, "run", run)
    output = tmp_path / "evaluation"
    kwargs = {
        "task_set": "test111",
        "vllm_url": "http://synthetic",
        "server_receipt": server,
    }
    inference.evaluate(cfg, output, tmp_path, adapter, "test", **kwargs)
    assert calls == inference.evaluation_tasks("test", "test111")
    binding = read_json(output / "binding.json")
    assert binding["final_test_protocol"] == CODEACT_TEST
    assert binding["split_manifest_sha256"] == plan()["manifest_sha256"]
    assert binding["appworld_revision"] == cfg["appworld_revision"]
    assert binding["adapter_file_sha256"]
    inference.evaluate(cfg, output, tmp_path, adapter, "test", **kwargs)
    assert len(calls) == 111
    with pytest.raises(ValueError, match="explicit"):
        inference.evaluate(
            cfg, tmp_path / "wrong", tmp_path, adapter, "test", task_set="all"
        )


def test_primary_import_never_uses_successful_retry(tmp_path):
    old = read_json(ROOT / "configs/codeact_test111_20261122.json")
    new = config()
    model = old["evaluation"]["models"][0]
    task = old["evaluation"]["tasks"][0]
    origin = tmp_path / "old"
    target = tmp_path / "final"
    target.mkdir()
    write_json(
        origin / "binding.json",
        {
            "config": old,
            "protocol": a.identity(),
            "student_execution": a.execution_identity(old),
            "source_lock_sha256": "original-frozen-source",
        },
    )
    primary = origin / "S0/tasks" / task / "attempt_1"
    write_json(
        primary / "intent.json", {"binding": a.attempt_binding(old, model, task)}
    )
    record = {
        "binding": a.attempt_binding(old, model, task),
        "task_id": task,
        "directory": str(primary),
        "success": None,
        "reward_valid": False,
        "failure_stage": "sampling",
        "fault": "review interruption",
        "termination": "interrupted_evaluation",
        "usage": {},
        "rubric": {},
        "initial_state_hash": None,
    }
    write_json(primary / "evaluation_row.json", record)
    write_json(
        primary.parent / "attempt_2/evaluation_row.json",
        {**record, "success": True, "reward_valid": True},
    )
    dep = {"import_primary_run": str(origin)}
    a.import_primary_attempts(new, dep, target)
    rows, discarded = a.selected_rows(target / "S0", [task])
    assert rows[0]["success"] is None and not discarded
    assert rows[0]["imported_primary"]["row_sha256"] == digest(record)
    a.import_primary_attempts(new, dep, target)
    changed = copy.deepcopy(new)
    changed["limits"]["max_requests"] = 81
    with pytest.raises(ValueError, match="execution conditions"):
        a.import_primary_attempts(changed, dep, target)
