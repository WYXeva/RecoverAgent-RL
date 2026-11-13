"""Synthetic collection contracts; no live API, model weights or AppWorld tasks."""

import copy
import io
import json
from types import SimpleNamespace

import pytest

from recoveragent import base_sft, p3a, teacher_collection as collection
from recoveragent.io import digest, read_json, write_json
from recoveragent.teacher import TeacherPolicy
from recoveragent.v2_tasks import context_for, plan


def test_train_selection_counts_unique_tasks_and_skips_existing_successes():
    rows = plan()["rows"]
    train = [r for r in rows if r["role"] == "train"]
    excluded = {r["task_id"] for r in train[::3]}
    selected = collection.selection(rows, excluded)
    assert len(selected) == len(set(selected)) == 340
    assert not set(selected) & excluded
    assert all(context_for(task, training=True)["role"] == "train" for task in selected)
    assert len({task.rsplit("_", 1)[0] for task in selected[:170]}) == 170


def test_worker_identity_session_and_limits_are_frozen(monkeypatch):
    monkeypatch.setattr(collection, "verify_lock", lambda: "synthetic")
    task = plan()["feedback20"][0]
    cfg = collection.worker_config(task, "synthetic-run")
    p3a.validate_config(cfg)
    assert cfg["collection_context"]["max_sequence_tokens"] == 65536
    assert (
        cfg["teacher"]["opencode_session_id"]
        == collection.worker_config(task, "synthetic-run")["teacher"][
            "opencode_session_id"
        ]
    )
    assert (
        cfg["teacher"]["opencode_session_id"]
        != collection.worker_config(task, "other-run")["teacher"]["opencode_session_id"]
    )
    for field in ["base_url", "model", "opencode_session_id"]:
        wrong = copy.deepcopy(cfg)
        wrong["teacher"][field] = "changed"
        with pytest.raises(ValueError, match="frozen"):
            p3a.validate_config(wrong)
    with pytest.raises(ValueError):
        collection.worker_config(plan()["dev24"][0], "synthetic")


@pytest.mark.parametrize("pooled", [False, True])
@pytest.mark.parametrize("explicit_session", [False, True])
def test_go_header_persists_across_tool_turns_without_changing_payload(
    monkeypatch, pooled, explicit_session
):
    cfg = collection.worker_config(plan()["feedback20"][0], "synthetic")
    if not explicit_session:
        cfg["teacher"].pop("opencode_session_id")
    if not pooled:
        cfg["teacher"].pop("http_transport")
    payload = {
        "model": cfg["teacher"]["model"],
        "messages": [{"role": "user", "content": "synthetic"}],
    }
    seen = []

    class Response(io.BytesIO):
        status_code = 200

        def json(self):
            return {"synthetic": True}

    def inspect(headers, data):
        h = {k.lower(): v for k, v in headers.items()}
        assert h["authorization"] == "Bearer synthetic-secret"
        assert h["user-agent"] == cfg["teacher"]["http_user_agent"]
        seen.append(h["x-opencode-session"])
        assert json.loads(data) == payload
        return Response(b'{"synthetic":true}')

    if pooled:
        import requests

        class Session:
            def mount(self, prefix, adapter):
                assert adapter.max_retries.total == 0

            def post(self, url, **kw):
                assert kw["allow_redirects"] is False
                return inspect(kw["headers"], kw["data"])

            def close(self):
                pass

        monkeypatch.setattr(requests, "Session", Session)
    else:
        import urllib.request

        monkeypatch.setattr(
            urllib.request,
            "build_opener",
            lambda *a: SimpleNamespace(
                open=lambda request, **kw: inspect(
                    dict(request.header_items()), request.data
                )
            ),
        )
    policy = TeacherPolicy(cfg, {"DEEPSEEK_API_KEY": "synthetic-secret"})
    for _ in range(2):
        assert policy.transport(payload) == {"synthetic": True}
    assert seen == [policy.session_id] * 2
    other = TeacherPolicy(cfg, {"DEEPSEEK_API_KEY": "synthetic-secret"})
    if explicit_session:
        assert policy.session_id == cfg["teacher"]["opencode_session_id"]
    else:
        assert other.session_id != policy.session_id
        assert "opencode_session_id" not in cfg["teacher"]


class CharacterTokenizer:
    def apply_chat_template(self, messages, **kwargs):
        return "".join(m["content"] for m in messages)

    def __call__(self, text, **kwargs):
        return {"input_ids": [ord(c) for c in text]}


def test_shared_64k_window_reserves_response_without_mutating_history():
    tokenizer = CharacterTokenizer()
    messages = [{"role": "user", "content": "x" * 61440}]
    original = copy.deepcopy(messages)
    cfg = collection.configuration()
    exact = collection.context_receipt(
        tokenizer, messages, [], 4096, cfg["context"], cfg["student"]
    )
    assert exact["within_budget"] and exact["input_tokens"] == 61440
    messages[0]["content"] += "x"
    over = collection.context_receipt(
        tokenizer, messages, [], 4096, cfg["context"], cfg["student"]
    )
    assert not over["within_budget"] and not over["truncated"]
    assert messages[0]["content"] == original[0]["content"] + "x"


def test_64k_encoding_keeps_long_prefix_masked_and_defers_whole_sample(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(base_sft, "validate_demo", lambda record: record)
    task = plan()["feedback20"][0]
    history = [
        {"role": "system", "content": "x" * 40000},
        {"role": "user", "content": "y"},
    ]
    sample = {
        "task_id": task,
        "scenario_id": task.rsplit("_", 1)[0],
        "trajectory_id": "synthetic",
        "messages": history + [{"role": "assistant", "content": "ok"}],
        "turns": [{"assistant_index": 2, "history_hash": digest(history), "tools": []}],
    }
    with pytest.raises(ValueError, match="too long"):
        collection.encode_sample(sample, CharacterTokenizer(), 32768)
    rows = collection.encode_sample(sample, CharacterTokenizer(), 65536)
    assert len(rows) == 1 and sum(rows[0]["loss_mask"]) == 2
    assert set(rows[0]["labels"][:40001]) == {-100}
    long = copy.deepcopy(sample)
    long["messages"][0]["content"] *= 2
    result = collection.save_sample(
        tmp_path / "deferred", long, CharacterTokenizer(), 65536
    )
    assert result["status"] == "deferred" and not result["eligible"]
    assert read_json(tmp_path / "deferred/sample.json") == long
    assert not (tmp_path / "deferred/examples.jsonl.gz").exists()


def test_resume_does_not_reissue_unresolved_paid_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(collection, "verify_lock", lambda: "synthetic")
    monkeypatch.setattr(collection, "sha256", lambda _: "synthetic")
    cfg = collection.configuration()
    write_json(tmp_path / "config.json", cfg)
    write_json(
        tmp_path / "campaign.json",
        {
            "source_lock_sha256": "synthetic",
            "reused_eligible": 100,
            "tasks": ["synthetic"],
        },
    )
    write_json(tmp_path / "claims/synthetic.json", {"task_id": "synthetic"})
    monkeypatch.setattr(
        collection, "run_worker", lambda *a: pytest.fail("Must not retry")
    )
    collection.collect(tmp_path, tmp_path / "unused.env")
    assert (
        read_json(tmp_path / "progress.json")["status"]
        == "blocked_unresolved_prior_claim"
    )


def test_collector_stops_at_cumulative_target_without_duplicate_tasks(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(collection, "verify_lock", lambda: "synthetic")
    monkeypatch.setattr(collection, "sha256", lambda _: "synthetic")
    cfg = {**collection.configuration(), "target_successes": 3}
    monkeypatch.setattr(collection, "configuration", lambda: cfg)
    write_json(tmp_path / "config.json", cfg)
    write_json(
        tmp_path / "campaign.json",
        {
            "source_lock_sha256": "synthetic",
            "reused_eligible": 2,
            "tasks": ["first", "second"],
        },
    )
    seen = []

    def run(output, task, credentials):
        seen.append(task)
        return {
            "task_id": task,
            "eligible": True,
            "official_success": True,
            "status": "eligible",
        }

    monkeypatch.setattr(collection, "run_worker", run)
    monkeypatch.setattr(collection, "export", lambda _: None)
    collection.collect(tmp_path, tmp_path / "unused.env")
    progress = read_json(tmp_path / "progress.json")
    assert seen == ["first"]
    assert progress["status"] == "target_reached" and progress["total_eligible"] == 3


def test_historical_locks_are_not_resigned():
    from recoveragent.protocol_lock import verify_lock

    cfg = read_json("configs/full732_a244_teacher_parallel_v2_20261028.json")
    with pytest.raises(ValueError, match="Frozen prompt/tool protocol changed"):
        verify_lock(cfg)


@pytest.mark.parametrize("runtime", ["collection", "learning"])
def test_v2_clean_teacher_export_replays_guards_and_durable_receipts(
    tmp_path, monkeypatch, runtime, current_v2_unit_lock
):
    from recoveragent import APPWORLD_REVISION, evaluation, v2_model
    from recoveragent.demonstrations import as_demo, validate_demo, validate_receipts
    from test_runner import Adapter
    from test_teacher_data import response

    monkeypatch.setattr(collection, "verify_lock", lambda: "synthetic")
    task = plan()["feedback20"][0]
    cfg = collection.worker_config(task, "synthetic-export")
    if runtime == "learning":
        from recoveragent.v2_teacher import configuration

        cfg = configuration(task, cfg["student"], "synthetic", 3, recovery=False)

    class World(Adapter):
        versions = {"revision": APPWORLD_REVISION, "data_version": "synthetic"}

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.world.task.generator_id = context_for(task)["scenario_id"]

        def configure_schema_compatibility(self, revision, output):
            pass  # No AppWorld schemas in this synthetic fixture.

    reply = response()
    reply.update(model=cfg["teacher"]["model"], id="synthetic-response")
    reply["choices"][0]["message"]["reasoning_content"] = "synthetic private reasoning"
    invoked = []

    def transport(payload):
        invoked.append(payload)
        return copy.deepcopy(reply)

    monkeypatch.setattr(p3a, "AppWorldAdapter", World)
    monkeypatch.setattr(v2_model, "load_tokenizer", lambda _: CharacterTokenizer())
    monkeypatch.setattr(evaluation, "environment_check", lambda: {"synthetic": True})
    monkeypatch.setattr(
        evaluation,
        "evaluate_isolated",
        lambda *a, **kw: {
            "status": "valid",
            "success": True,
            "official": {"synthetic": True},
        },
    )
    monkeypatch.setattr(
        p3a,
        "TeacherPolicy",
        lambda c, creds: TeacherPolicy(c, creds, transport=transport),
    )
    directory = tmp_path / "synthetic-attempt"
    trajectory = p3a.run_teacher_episode(
        cfg,
        task,
        directory,
        {
            "DEEPSEEK_API_KEY": "synthetic-secret",
            "DEEPSEEK_API_KEY1": "synthetic-secret",
        },
    )
    assert trajectory["status"] == "valid", trajectory
    assert trajectory["reward"] == 1 and len(invoked) == 1
    context = read_json(directory / "context/001.json")
    assert context["max_sequence_tokens"] == 65536 and context["within_budget"]
    validate_demo(as_demo(trajectory))
    validate_receipts(trajectory, directory)
    assert (
        collection.filter_attempt(
            trajectory,
            read_json(directory / "evaluation.json"),
            {task},
            cfg["limits"],
            directory,
        )
        == []
    )
    for field in ("answer_guard", "harness"):
        bad = copy.deepcopy(trajectory)
        bad[field]["stopped"] = True
        with pytest.raises(ValueError):
            validate_demo(as_demo(bad))
    bad = copy.deepcopy(trajectory)
    bad["custom_research"]["role"] = "dev"
    with pytest.raises(ValueError):
        validate_demo(as_demo(bad))
    request_path = directory / "requests/001.request.json"
    request = read_json(request_path)
    request["messages"][0]["content"] = "tampered"
    request_path.write_text(json.dumps(request))
    with pytest.raises(ValueError, match="Provider receipt changed"):
        validate_receipts(trajectory, directory)


def test_control_stop_drains_without_starting_new_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(collection, "verify_lock", lambda: "synthetic")
    monkeypatch.setattr(collection, "sha256", lambda _: "synthetic")
    write_json(tmp_path / "config.json", collection.configuration())
    write_json(
        tmp_path / "campaign.json",
        {"source_lock_sha256": "synthetic", "reused_eligible": 0, "tasks": ["unused"]},
    )
    (tmp_path / "STOP").touch()
    monkeypatch.setattr(collection, "run_worker", lambda *a: pytest.fail("Must drain"))
    monkeypatch.setattr(collection, "export", lambda _: None)
    collection.collect(tmp_path, tmp_path / "unused.env")
    assert read_json(tmp_path / "progress.json")["status"] == "stopped_by_control_file"


@pytest.mark.parametrize(
    "trace",
    [
        "test.case(expected, '==', predicted_answer, normalize=float)",
        "predicted_answer = float(predicted_answer)",
    ],
)
def test_numeric_evaluation_quarantine_keeps_unknown_and_excludes_data(tmp_path, trace):
    record = {
        "status": "infrastructure_error",
        "task_id": "synthetic",
        "termination_reason": "evaluation_error",
        "failure_category": "evaluation_runtime",
        "usage": {},
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "supervisor__complete_task",
                            "arguments": '{"answer":"not numeric"}',
                        }
                    }
                ],
            }
        ],
    }
    evaluated = {
        "status": "evaluation_error",
        "category": "unknown",
        "success": None,
        "official_success": False,
        "official_result_complete": True,
        "environment": {"uri_roundtrip": True},
        "observations": [
            {"exception_type": "ValueError", "category": "unknown_exception"}
        ],
        "official": {
            "success": False,
            "num_tests": 2,
            "passes": [{}],
            "failures": [{"requirement": "assert answers match.", "trace": trace}],
        },
    }
    write_json(tmp_path / "trajectory.json", record)
    path = tmp_path / "isolated_evaluation.json"
    write_json(path, evaluated)
    before = collection.sha256(path)
    result = collection.process_attempt(tmp_path, tmp_path / "encoded", {}, None)
    assert result["status"] == "infrastructure_error"
    assert result["official_success"] is None and result["eligible"] is False
    assert result["task_error_quarantine"]["evaluation_sha256"] == before
    assert not (tmp_path / "encoded").exists() and collection.sha256(path) == before
    assert not collection.blocks_campaign(result)
    bad = copy.deepcopy(record)
    bad["messages"][0]["tool_calls"][0]["function"]["arguments"] = '{"answer":"12.5"}'
    assert collection.numeric_answer_quarantine(bad, tmp_path) is None
    bad = {**record, "provider_error": {"status_code": 429}}
    assert collection.numeric_answer_quarantine(bad, tmp_path) is None
    for field, value in [
        ("official_result_complete", False),
        ("category", "environment_error"),
        (
            "observations",
            [{"exception_type": "OSError", "category": "environment_error"}],
        ),
    ]:
        path.write_text(json.dumps({**evaluated, field: value}))
        assert collection.numeric_answer_quarantine(record, tmp_path) is None
    for trace in ["float(database_config)", "raise ValueError('unknown')"]:
        bad = copy.deepcopy(evaluated)
        bad["official"]["failures"][0]["trace"] = trace
        path.write_text(json.dumps(bad))
        assert collection.numeric_answer_quarantine(record, tmp_path) is None


@pytest.mark.parametrize("quarantined", [False, True])
def test_collector_continues_only_past_quarantined_task_error(
    tmp_path, monkeypatch, quarantined
):
    monkeypatch.setattr(collection, "verify_lock", lambda: "synthetic")
    monkeypatch.setattr(collection, "sha256", lambda _: "synthetic")
    cfg = {**collection.configuration(), "target_successes": 1, "concurrency": 1}
    monkeypatch.setattr(collection, "configuration", lambda: cfg)
    write_json(tmp_path / "config.json", cfg)
    write_json(
        tmp_path / "campaign.json",
        {
            "source_lock_sha256": "synthetic",
            "reused_eligible": 0,
            "tasks": ["bad", "good"],
        },
    )
    seen = []

    def worker(output, task, credentials):
        seen.append(task)
        r = {
            "task_id": task,
            "eligible": task == "good",
            "official_success": True if task == "good" else None,
            "status": "eligible" if task == "good" else "infrastructure_error",
            "termination": "evaluation_error",
        }
        if task == "bad" and quarantined:
            r["task_error_quarantine"] = {"protocol": collection.QUARANTINE}
        return r

    monkeypatch.setattr(collection, "run_worker", worker)
    monkeypatch.setattr(collection, "export", lambda _: None)
    collection.collect(tmp_path, tmp_path / "unused.env")
    assert seen == (["bad", "good"] if quarantined else ["bad"])
    p = read_json(tmp_path / "progress.json")
    assert p["total_eligible"] == int(quarantined)
    assert p["status"] == (
        "target_reached" if quarantined else "blocked_infrastructure"
    )
