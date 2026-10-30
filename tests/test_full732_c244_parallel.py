"""Independent lane transport, exactly-once adoption, and unknown accounting."""

import copy
from types import SimpleNamespace
import sys

import pytest

from recoveragent import full732_c244_parallel as p
from recoveragent.io import files_manifest, write_json
from test_full732_c244 import config


def test_second_lane_changes_only_transport(monkeypatch):
    monkeypatch.setattr(p.serial, "verify_lock", lambda: {})
    cfg = config()
    cfg["inference_url"] = "http://127.0.0.1:8769"
    assert p.validate_lane(cfg) == 1
    assert cfg["inference_url"].endswith("8769")
    cfg["student_limits"]["wall_seconds"] += 1
    with pytest.raises(ValueError, match="execution differs"):
        p.validate_lane(cfg)


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:8770", "https://example.com", "http://localhost:8769"]
)
def test_lane_cannot_send_private_requests_to_another_endpoint(url):
    cfg = config()
    cfg["inference_url"] = url
    with pytest.raises(ValueError, match="private local"):
        p.validate_lane(cfg)


def test_out_of_order_results_keep_true_unattempted_and_unknown():
    cfg = config()
    rows = [
        dict(cfg["cases"][2], status="valid", official_success=True),
        dict(cfg["cases"][0], status="evaluation_error", official_success=None),
    ]
    result = p.summarize(cfg, rows)
    assert [r["task_id"] for r in result["rows"]] == [
        cfg["cases"][0]["task_id"],
        cfg["cases"][2]["task_id"],
    ]
    assert cfg["cases"][1] in result["unattempted"]
    assert cfg["cases"][2] not in result["unattempted"]
    assert (result["success"], result["failure"], result["unknown"], result["TGC"]) == (
        1,
        0,
        1,
        None,
    )
    with pytest.raises(ValueError, match="Duplicate"):
        p.summarize(cfg, rows + rows[:1])


def test_handoff_never_retries_a_partial_attempt(tmp_path):
    cfg = config()
    write_json(tmp_path / "config.json", cfg)
    task = cfg["cases"][0]["task_id"]
    directory = tmp_path / "tasks" / task
    row = dict(
        cfg["cases"][0],
        status="valid",
        official_success=False,
        policy=cfg["expected_policy"],
    )
    write_json(directory / "full732_result.json", row)
    handoff = {
        "previous": str(tmp_path),
        "serial_processes_exited": True,
        "config_sha256": p.sha256(tmp_path / "config.json"),
        "user_requested_parallel": True,
        "completed_count": 1,
        "task_files": {task: files_manifest(directory)},
    }
    assert p.inherited_rows(tmp_path, cfg, handoff) == [row]
    (tmp_path / "tasks" / cfg["cases"][1]["task_id"]).mkdir()
    with pytest.raises(ValueError, match="Unresolved serial attempt"):
        p.inherited_rows(tmp_path, cfg, handoff)


def test_handoff_refuses_modified_receipt(tmp_path):
    cfg = config()
    write_json(tmp_path / "config.json", cfg)
    task = cfg["cases"][0]["task_id"]
    directory = tmp_path / "tasks" / task
    row = dict(
        cfg["cases"][0],
        status="valid",
        official_success=False,
        policy=cfg["expected_policy"],
    )
    write_json(directory / "full732_result.json", row)
    handoff = {
        "previous": str(tmp_path),
        "serial_processes_exited": True,
        "config_sha256": p.sha256(tmp_path / "config.json"),
        "user_requested_parallel": True,
        "completed_count": 1,
        "task_files": {task: files_manifest(directory)},
    }
    changed = copy.deepcopy(row)
    changed["official_success"] = True
    (directory / "full732_result.json").unlink()
    write_json(directory / "full732_result.json", changed)
    with pytest.raises(ValueError, match="evidence changed"):
        p.inherited_rows(tmp_path, cfg, handoff)


def test_backend_rebinds_socket_without_touching_original_module(tmp_path, monkeypatch):
    observed = {}

    class Handler:
        def respond(self, value, status=200):
            observed["response"] = (value, status)

    def server(address, handler):
        observed["address"] = address
        handler().respond({"error_type": "OutOfMemoryError"}, 500)

    module = SimpleNamespace(HTTPServer=server)
    module.main = lambda: module.HTTPServer(("127.0.0.1", 8768), Handler)
    original = p.original_backend.HTTPServer
    monkeypatch.setattr(p.serial, "isolated_module", lambda *args: module)
    monkeypatch.setattr(sys, "argv", ["test"])
    log = tmp_path / "errors.jsonl"
    p.backend(SimpleNamespace(port=8769, error_log=str(log), adapter="accepted"))
    assert observed["address"] == ("127.0.0.1", 8769)
    assert observed["response"] == ({"error_type": "OutOfMemoryError"}, 500)
    assert "OutOfMemoryError" in log.read_text()
    assert p.original_backend.HTTPServer is original


def test_stop_covers_worker_process_group(monkeypatch):
    calls = []
    process = SimpleNamespace(
        pid=123, poll=lambda: None, wait=lambda timeout: calls.append(("wait", timeout))
    )
    monkeypatch.setattr(p.os, "killpg", lambda *args: calls.append(args))
    p.stop_group(process)
    assert calls[0] == (123, p.signal.SIGTERM)
