"""Synthetic B10 takeover/deadline/encoding tests. No model or AppWorld tasks."""

import copy
import hashlib
import json
import shutil

import pytest

from recoveragent import b10_pilot as pilot, b10_deadline, p3a, p3c, student, state
from recoveragent import recovery_suffix as suffix, evaluation
from recoveragent.io import files_manifest, read_json
from recoveragent.full732_recovery_tasks import context_for
from recoveragent.schema_compatibility import SCHEMA_REVISION
from recoveragent.teacher import TeacherPolicy
import test_nullable_recovery as fixtures
from test_nullable_recovery import session_for

from test_student_batch import Tokenizer, block, receipt
from test_teacher_data import response


worlds = fixtures.worlds


@pytest.fixture(autouse=True)
def selected_budget(monkeypatch, tmp_path):
    # Synthetic source tree only: v2 intentionally invalidates released B10 locks.
    # Keep the historical files unchanged and exercise the old controller there.
    from recoveragent import protocol_lock
    from recoveragent.demonstrations import sha256
    from recoveragent.io import write_json

    root = tmp_path / "synthetic_b10_source_tree"
    locked = read_json(pilot.ROOT / pilot.BUDGET_LOCK)
    for name in locked["files"]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pilot.ROOT / name, target)
        locked["files"][name] = sha256(target)
    write_json(root / pilot.BUDGET_LOCK, locked)
    verify = protocol_lock.verify_lock
    monkeypatch.setattr(
        protocol_lock, "verify_lock", lambda c, root_override=None: verify(c, root=root)
    )
    monkeypatch.setattr(pilot, "ROOT", root)
    monkeypatch.setattr(pilot, "CONFIG", pilot.BUDGET_CONFIG)
    monkeypatch.setattr(pilot, "LOCK", pilot.BUDGET_LOCK)
    monkeypatch.setattr(pilot, "RUNTIME", pilot.BUDGET_REVISION)


@pytest.fixture
def prepared(worlds, tmp_path, monkeypatch):
    lock = read_json(pilot.ROOT / pilot.LOCK)
    monkeypatch.setattr(
        student,
        "verify_student_runtime",
        lambda r: {
            "session_contract": lock["session_contract"],
            "lock_sha256": pilot.verify_lock(),
        },
    )
    task = pilot.configuration()["tasks"][0]
    adapter = worlds(task, context_for(task))
    adapter.world.task.generator_id = task.rsplit("_", 1)[0]
    adapter.configure_schema_compatibility(SCHEMA_REVISION, tmp_path / "schema")
    session = session_for(adapter, tmp_path / "student")
    session.runtime_revision = "p3c_nullable_v2_20261104"
    # Real synthetic batch discovers a tool before the recovery boundary.
    session.step(
        receipt(
            block(
                "api_docs__show_api_doc",
                {"app_name": "gmail", "api_name": "show_category_sizes"},
            )
        )
    )
    adapter.value = 73  # Student side effect must survive takeover, not initial=1.
    source = tmp_path / "source"
    state.capture(
        adapter,
        source,
        session.messages,
        session.tools,
        "post_action",
        {"task_active": True, "resume_eligible": True},
        agent_runtime=p3c.agent_runtime(session),
    )
    return task, source, adapter, session


class Template(Tokenizer):
    def apply_chat_template(self, messages, **kwargs):
        return "".join(json.dumps(m) for m in messages)


def teacher_run(prepared, worlds, tmp_path, monkeypatch, *, fail=False):
    task, source, old, session = prepared
    config = copy.deepcopy(pilot.configuration()["teacher_config"])
    config.update(
        custom_research=context_for(task),
        remaining_requests=80,
        remaining_generated_tokens=16384,
        remaining_cost=171,
    )
    seen = []

    def adapter(root, task, *a, **kw):
        value = worlds(task, kw["research"])
        value.world.task.generator_id = task.rsplit("_", 1)[0]
        return value

    def transport(payload):
        current = worlds.created[-1]
        assert current.value == 73
        assert payload["messages"][: len(session.messages)] == session.messages
        assert payload["messages"][len(session.messages)] == suffix.HANDOFF
        assert payload["tools"] == session.tools
        assert "future_trajectory" not in json.dumps(payload)
        seen.append(payload)
        if fail:
            raise RuntimeError("synthetic transport failure")
        result = response()
        result["model"] = "deepseek-v4.1-flash"
        result["id"] = "synthetic-response-id"
        result["choices"][0]["message"]["reasoning_content"] = (
            "synthetic genuine Teacher reasoning"
        )
        result["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{}"
        return result

    monkeypatch.setattr(p3a, "AppWorldAdapter", adapter)
    monkeypatch.setattr(evaluation, "environment_check", lambda: {"synthetic": True})

    def score(task_id, experiment, root, out, *, research):
        assert task_id == task and research == context_for(task)
        return {"status": "valid", "success": True, "official": {"synthetic": True}}

    monkeypatch.setattr(evaluation, "evaluate_isolated", score)
    monkeypatch.setattr(
        p3a, "TeacherPolicy", lambda c, cr: TeacherPolicy(c, cr, transport=transport)
    )
    directory = tmp_path / "teacher"
    result = p3a.run_teacher_episode(
        config,
        task,
        directory,
        {"DEEPSEEK_API_KEY1": "synthetic-key"},
        recovery_source=source,
    )
    return directory, result, seen


def test_takeover_official_reference_and_suffix_only(
    prepared, worlds, tmp_path, monkeypatch
):
    original = files_manifest(prepared[1])
    directory, record, seen = teacher_run(prepared, worlds, tmp_path, monkeypatch)
    assert record["status"] == "valid", record
    assert record["reward"] == 1 and len(seen) == 1
    assert record["usage"]["provider_requests"] == 1
    assert record["recovery"]["student_prefix_usage"]["model_requests"] == 1
    accepted = suffix.process_attempt(directory, Template())
    assert accepted["eligible"], accepted
    examples = read_json(directory / "encoded_suffix.json")
    assert len(examples) == 1
    example = examples[0]
    assert example["assistant_index"] == len(prepared[3].messages) + 1
    assert set(example["labels"][: example["target_start"]]) == {-100}
    assert all(example["loss_mask"][example["target_start"] :])
    assert files_manifest(prepared[1]) == original
    assert read_json(directory / "evaluation.json")["reference"] == "original_task"


def test_failed_attempt_preserves_unknown_cost_no_targets(
    prepared, worlds, tmp_path, monkeypatch
):
    directory, record, seen = teacher_run(
        prepared, worlds, tmp_path, monkeypatch, fail=True
    )
    assert len(seen) == 1 and record["status"] == "infrastructure_error"
    assert record["usage"]["unknown_cost_reservation"] > 0
    assert not suffix.process_attempt(directory, Template())["eligible"]
    assert not (directory / "encoded_suffix.json").exists()


def test_whole_overlong_success_deferred(prepared, worlds, tmp_path, monkeypatch):
    directory, record, _ = teacher_run(prepared, worlds, tmp_path, monkeypatch)

    class Long(Template):
        def __call__(self, text, **kw):
            return {
                "input_ids": [1] * 33000 + super().__call__(text, **kw)["input_ids"]
            }

    result = suffix.process_attempt(directory, Long())
    assert result["deferred"] and not result["eligible"]
    assert (directory / "recovery_sample.json").exists()
    assert not (directory / "encoded_suffix.json").exists()


def test_restored_branches_are_independent_and_schema_exact(prepared, worlds, tmp_path):
    task, source, old, session = prepared
    for number in range(2):
        adapter = worlds(task, context_for(task))
        record = {}
        messages, tools, guard = suffix.restore_teacher_start(
            adapter, source, tmp_path / str(number), record
        )
        assert adapter.value == 73 and tools == session.tools
        assert messages[:-1] == session.messages
        adapter.value = 999
        assert old.value == 73
    bad = worlds(task, context_for(task))
    bad.versions = {**bad.versions, "revision": "wrong"}
    with pytest.raises(ValueError):
        suffix.restore_teacher_start(bad, source, tmp_path / "bad", {})
    assert bad.imports == 0


@pytest.mark.parametrize("mode", ["normal", "deadline", "wrong_echo", "timeout"])
def test_client_server_deadline_agreement_and_no_partial_execution(
    monkeypatch, mode, tmp_path
):
    from test_student_batch import Adapter, DISCOVER

    monkeypatch.setattr(
        student,
        "TEMPLATE_SHA256",
        hashlib.sha256(Tokenizer.chat_template.encode()).hexdigest(),
    )
    adapter = Adapter()
    cls = b10_deadline.session_class(student.StudentSession, "http://127.0.0.1:8768")
    session = cls(
        adapter,
        Tokenizer(),
        [{"role": "user", "content": "synthetic"}],
        [DISCOVER],
        output=tmp_path / "student",
        clock=lambda: 100,
        wall_seconds=600,
        tool_mode="dynamic_discovery",
    )
    monkeypatch.setattr(b10_deadline, "execution_clock", lambda: 150)

    def http(url, request, timeout):
        assert (
            request["deadline_monotonic"] == 700 and timeout == 555 and timeout != 180
        )
        if mode == "timeout":
            raise TimeoutError("unknown response")
        response = receipt(block("api_docs__show_api_doc", {}))(request)
        response.update(
            runtime_variant=b10_deadline.VARIANT,
            deadline_monotonic=699 if mode == "wrong_echo" else 700,
        )
        if mode == "deadline":
            response["finish_reason"] = "deadline"
        return response

    monkeypatch.setattr(b10_deadline, "request_json", http)
    if mode in {"wrong_echo", "timeout"}:
        with pytest.raises((ValueError, TimeoutError)):
            session.step(None)
        assert session.termination == "infrastructure_error"
        assert session.usage["unknown_generation_reservation"] > 0
        assert not adapter.calls
    else:
        event = session.step(None)
        assert session.usage["generated_tokens"] > 0
        if mode == "deadline":
            assert session.termination == "budget_exhausted"
            assert not adapter.calls and event["dispatched_count"] == 0
        else:
            assert len(adapter.calls) == 1


def test_new_lock_and_frozen_contract_old_locks_still_refuse():
    from recoveragent.nullable_recovery_runtime import verify_lock
    from recoveragent.m1_vgpu_reference_backend import verify_lock as old_backend

    config = pilot.configuration()
    assert len(config["tasks"]) == 10
    assert config["student_config"]["source_lock"] == pilot.LOCK
    assert pilot.verify_lock()
    for old in (verify_lock, old_backend):
        with pytest.raises(ValueError):
            old()


def test_actual_p3c_entry_uses_new_lock_deadline_and_schema(
    worlds, tmp_path, monkeypatch
):
    config = pilot.student_config("unused", "unused", "http://127.0.0.1:8768")
    engine = pilot.student_engine(config)
    # The isolated Student module keeps its own pinned template constant.
    engine.StudentSession.__mro__[1].__init__.__globals__["TEMPLATE_SHA256"] = (
        hashlib.sha256(Tokenizer.chat_template.encode()).hexdigest()
    )
    monkeypatch.setattr(
        engine,
        "AppWorldAdapter",
        lambda root, task, *a, **k: worlds(task, k["research"]),
    )
    monkeypatch.setattr(engine, "environment_check", lambda: None)
    monkeypatch.setattr(engine, "load_tokenizer", Tokenizer)
    identity = {"checkpoint_id": "synthetic"}
    monkeypatch.setattr(engine, "request_json", lambda *a, **k: identity)
    requests = []
    answers = iter(
        [
            block(
                "api_docs__show_api_doc",
                {"app_name": "gmail", "api_name": "show_category_sizes"},
            ),
            block("supervisor__complete_task", {}),
        ]
    )

    def http(url, request, timeout):
        requests.append(request)
        response = receipt(next(answers))(request)
        response.update(
            runtime_variant=pilot.BUDGET_VARIANT,
            deadline_monotonic=request["deadline_monotonic"],
        )
        return response

    monkeypatch.setattr(b10_deadline, "request_json", http)
    monkeypatch.setattr(
        engine,
        "evaluate_isolated",
        lambda *a, **k: {"status": "valid", "success": False},
    )
    result = engine.run_episode(
        config, pilot.b_tasks()[1][0], tmp_path / "episode", {}, identity
    )
    assert result["fault"] is None, result
    assert len(requests) == 2
    assert requests[0]["deadline_monotonic"] == requests[1]["deadline_monotonic"]
    assert result["schema_compatibility"]["revision"] == SCHEMA_REVISION
    assert (
        read_json(tmp_path / "episode/interaction/execution_protocol.json")[
            "lock_sha256"
        ]
        == pilot.verify_lock()
    )


def test_fifteen_corrections_then_stop_and_numeric_budget_unchanged():
    from recoveragent.harness import ProtocolHarness, BUDGET_REVISION

    new = ProtocolHarness("dynamic_discovery", budget_revision=BUDGET_REVISION)
    for index in range(1, 16):
        feedback = json.loads(new.reject("malformed_output")["content"])
        assert not new.stopped and feedback["corrections_remaining"] == 16 - index
    assert not json.loads(new.reject("malformed_output")["content"])["retry_allowed"]
    assert new.stopped and new.summary()["budget_revision"] == BUDGET_REVISION
    with pytest.raises(ValueError):
        new.reject("malformed_output")
    old = ProtocolHarness("dynamic_discovery")
    for _ in range(3):
        old.reject("malformed_output")
    assert old.stopped and "budget_revision" not in old.summary()
    numeric = ProtocolHarness("dynamic_discovery", budget_revision=BUDGET_REVISION)
    numeric.reject("malformed_output", answer_pending=True)
    assert numeric.stopped and numeric.rejections == 1


def test_versioned_1200_deadline_and_80_request_contract(monkeypatch, tmp_path):
    from test_student_batch import Adapter, DISCOVER
    from recoveragent import m1_vgpu_reference_backend as old

    contract = pilot.deadline_contract()
    assert contract.remaining(1300, 100) == 1200
    with pytest.raises(ValueError):
        old.remaining(1300, 100)
    for bad in (True, float("inf"), 1301):
        with pytest.raises(ValueError):
            contract.remaining(bad, 100)
    monkeypatch.setattr(
        student,
        "TEMPLATE_SHA256",
        hashlib.sha256(Tokenizer.chat_template.encode()).hexdigest(),
    )
    cls = b10_deadline.session_class(
        student.StudentSession, "http://127.0.0.1:8768", contract=contract
    )
    adapter = Adapter()
    session = cls(
        adapter,
        Tokenizer(),
        [{"role": "user", "content": "synthetic"}],
        [DISCOVER],
        output=tmp_path / "s",
        clock=lambda: 100,
        wall_seconds=1200,
        max_requests=80,
        tool_mode="dynamic_discovery",
    )
    monkeypatch.setattr(b10_deadline, "execution_clock", lambda: 150)

    def http(url, request, timeout):
        assert request["deadline_monotonic"] == 1300 and timeout == 1155
        value = receipt(block("api_docs__show_api_doc", {}))(request)
        value.update(
            runtime_variant=pilot.BUDGET_VARIANT,
            deadline_monotonic=1300,
            finish_reason="deadline",
        )
        return value

    monkeypatch.setattr(b10_deadline, "request_json", http)
    event = session.step(None)
    assert event["dispatched_count"] == 0 and not adapter.calls
    assert session.termination == "budget_exhausted"
    config = pilot.configuration()
    assert config["student_config"]["student_limits"]["max_requests"] == 80
    assert config["student_config"]["student_limits"]["max_generated_tokens"] == 16384
    assert config["teacher_config"]["limits"]["episode_wall_seconds"] == 1200
    assert config["teacher_config"]["limits"]["episode_requests"] == 80


def test_original_b10_execution_lock_is_not_resigned(monkeypatch):
    from recoveragent.demonstrations import sha256

    assert (
        sha256(pilot.ROOT / pilot.INITIAL_LOCK)
        == "2835f25c0d5f8241a4fa11caa1faeec1cb01621afdccfd34d22bd156d39d162f"
    )
    monkeypatch.setattr(pilot, "CONFIG", pilot.INITIAL_CONFIG)
    monkeypatch.setattr(pilot, "LOCK", pilot.INITIAL_LOCK)
    monkeypatch.setattr(pilot, "RUNTIME", pilot.INITIAL_RUNTIME)
    with pytest.raises(ValueError, match="source changed"):
        pilot.verify_lock()


def test_sealed_continuation_rejects_changed_and_unresolved_evidence(
    tmp_path, monkeypatch
):
    from recoveragent.io import write_json
    from recoveragent.demonstrations import sha256

    config = copy.deepcopy(pilot.configuration())
    root = tmp_path / "prior"
    write_json(
        root / "frozen_config.json", read_json(pilot.ROOT / pilot.INITIAL_CONFIG)
    )
    for task in config["tasks"][:7]:
        write_json(root / "student" / task / "result.json", {"task_id": task})
        write_json(root / "claims" / ("student_" + task + ".json"), {"task_id": task})
    for index in range(6):
        task = config["tasks"][index]
        write_json(
            root / "claims" / ("teacher_" + task + ".json"),
            {"task_id": task, "checkpoint_id": task},
        )
        write_json(root / "teacher" / task / "evidence_manifest.json", {})
    write_json(root / "pilot_manifest.json", {"files": files_manifest(root)})
    config["continuation"]["prior_manifest_sha256"] = sha256(
        root / "pilot_manifest.json"
    )
    monkeypatch.setattr(pilot, "configuration", lambda: config)
    episodes, completed = pilot.prior_evidence(root)
    assert len(episodes) == 7 and len(completed) == 6
    # A known dispatched request cannot be hidden by deleting its evidence.
    path = next((root / "teacher").glob("*/evidence_manifest.json"))
    path.unlink()
    with pytest.raises(ValueError, match="evidence changed"):
        pilot.prior_evidence(root)
    manifest = root / "pilot_manifest.json"
    manifest.unlink()
    write_json(manifest, {"files": files_manifest(root)})
    config["continuation"]["prior_manifest_sha256"] = sha256(manifest)
    with pytest.raises(ValueError, match="Unresolved Teacher"):
        pilot.prior_evidence(root)
