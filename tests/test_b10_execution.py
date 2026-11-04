"""Synthetic B10 takeover/deadline/encoding tests. No model or AppWorld tasks."""

import copy
import hashlib
import json

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
            runtime_variant=b10_deadline.VARIANT,
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
