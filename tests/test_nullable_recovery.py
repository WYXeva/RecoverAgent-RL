"""P0.2 synthetic worlds/receipts only; no model, GPU or real AppWorld task."""

import copy
import hashlib
import json
import shutil
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from recoveragent import p3c, runner, state, student
from recoveragent import nullable_recovery_runtime as runtime
from recoveragent import schema_compatibility as compat
from recoveragent.adapter import AppWorldAdapter
from recoveragent.execution_v6 import SYSTEM_PROMPT
from recoveragent.full732_recovery_tasks import b_tasks, context_for, validate_context
from recoveragent.io import digest, files_manifest, read_json
from recoveragent.schema_snapshot import prepare_snapshot_adapter
from test_contracts import FakeAdapter
import test_runner
from test_schema_compatibility import FIXTURE, SyntheticAdapter, function
from test_student_batch import Tokenizer, block, receipt


original_legacy_config = test_runner.config


@pytest.fixture
def legacy_config(original_legacy_config):
    return {
        **original_legacy_config,
        "recovery": original_legacy_config["normal"],
        "continuation_prompt": "Continue synthetic task",
    }


class SnapshotAdapter(SyntheticAdapter):
    export_state = FakeAdapter.export_state
    fingerprint = FakeAdapter.fingerprint
    runtime = FakeAdapter.runtime
    restore_runtime = FakeAdapter.restore_runtime

    def __init__(self, tools, task_id="synthetic", research=None):
        super().__init__(tools)
        self.task_id, self.value, self.imports, self.completed = task_id, 1, 0, False
        self.world.task = SimpleNamespace(ground_truth=None, generator_id="synthetic")
        self.world.task_completed = lambda: self.completed
        if research is not None:
            self.research_context = research

    def import_state(self, source, state_id):
        self.imports += 1
        FakeAdapter.import_state(self, source, state_id)

    def _execute(self, code):
        self.completed = "supervisor__complete_task" in code
        return super()._execute(code)

    def public_task(self):
        return {"instruction": "Synthetic task; no hidden data"}

    def save_terminal(self):
        pass

    def close(self):
        pass


@pytest.fixture
def worlds(monkeypatch):
    contract = read_json(FIXTURE)
    monkeypatch.setattr(
        compat,
        "load_public_openapi",
        lambda catalogue: (copy.deepcopy(contract["openapi"]), {"synthetic": True}),
    )
    monkeypatch.setattr(
        student,
        "TEMPLATE_SHA256",
        hashlib.sha256(Tokenizer.chat_template.encode()).hexdigest(),
    )
    created = []

    def make(task_id="synthetic", research=None):
        adapter = SnapshotAdapter(contract["upstream_catalogue"], task_id, research)
        created.append(adapter)
        return adapter

    make.created = created
    return make


def session_for(adapter, output, *, tools=None, messages=None):
    return student.StudentSession(
        adapter,
        Tokenizer(),
        messages
        or [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": '{"instruction":"synthetic"}'},
        ],
        adapter.initial_tools() if tools is None else tools,
        output=output,
        tool_mode="dynamic_discovery",
        protocol_harness=True,
        answer_guard=True,
        execution_protocol=runtime.RUNTIME,
    )


def snapshot(worlds, tmp_path, *, research=False, legacy=False):
    row = b_tasks()[1][0]
    adapter = (
        worlds(row["task_id"], context_for(row["task_id"])) if research else worlds()
    )
    adapter.configure_schema_compatibility(
        compat.SCHEMA_REVISION, tmp_path / "original"
    )
    session = session_for(adapter, tmp_path / "session")
    session.runtime_revision = runtime.RUNTIME
    source = tmp_path / "source"
    state.capture(
        adapter,
        source,
        session.messages,
        session.tools,
        "pre_action",
        {"task_active": True},
        agent_runtime=None if legacy else p3c.agent_runtime(session),
    )
    return source, adapter


def test_collection_entry_constructs_v2_before_initial_tools_and_session(
    worlds, monkeypatch, tmp_path
):
    config = read_json(runtime.CONFIG)
    row = b_tasks()[1][0]
    identity = {"checkpoint_id": "synthetic"}
    monkeypatch.setattr(
        p3c, "AppWorldAdapter", lambda root, task, *a, **k: worlds(task, k["research"])
    )
    monkeypatch.setattr(p3c, "environment_check", lambda: None)
    monkeypatch.setattr(p3c, "load_tokenizer", Tokenizer)
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

    def backend(url, request=None, **kwargs):
        if request is None:
            return identity
        requests.append(copy.deepcopy(request))
        return receipt(next(answers))(request)

    monkeypatch.setattr(p3c, "request_json", backend)
    monkeypatch.setattr(
        p3c, "evaluate_isolated", lambda *a, **k: {"status": "valid", "success": False}
    )
    original_initial = AppWorldAdapter.initial_tools

    def initial(adapter):
        assert (
            adapter.versions["schema_compatibility"]["revision"]
            == compat.SCHEMA_REVISION
        )
        assert (tmp_path / "episode/schema_compatibility.json").exists()
        return original_initial(adapter)

    monkeypatch.setattr(SnapshotAdapter, "initial_tools", initial)
    result = p3c.run_episode(config, row, tmp_path / "episode", {}, identity)
    assert result["fault"] is None
    assert result["split"] == row["official_split"]
    assert result["schema_compatibility"]["revision"] == compat.SCHEMA_REVISION
    assert len(requests) == 2
    assert requests[0]["messages"][0]["content"] == SYSTEM_PROMPT
    assert function(requests[1]["tools"], "gmail__show_category_sizes") == function(
        worlds.created[0].catalogue, "gmail__show_category_sizes"
    )
    for boundary in result["boundaries"]:
        manifest, _, _ = state.inspect_snapshot(boundary["snapshot_ref"])
        assert manifest["format_version"] == 5
        assert (
            manifest["versions"]["schema_compatibility"]
            == result["schema_compatibility"]
        )


@pytest.mark.parametrize("research", [False, True])
def test_restore_probe_rebuilds_and_dynamic_discovery_stays_v2(
    worlds, monkeypatch, tmp_path, research
):
    source, old = snapshot(worlds, tmp_path, research=research)
    before = files_manifest(source)
    monkeypatch.setattr(
        p3c,
        "AppWorldAdapter",
        lambda root, task, *a, **k: worlds(task, k.get("research")),
    )
    result = p3c.restore_probe("unused", source, "new", tmp_path / "restore.json")
    new = worlds.created[-1]
    assert result["status"] == "PASS" and result["task_actions_executed"] == 0
    assert new.imports == 1 and new.calls == []
    assert result["schema_compatibility"] == old.versions["schema_compatibility"]
    assert files_manifest(source) == before
    _, context, _ = state.inspect_snapshot(source)
    # A synthetic session verifies the restored schema boundary, not Agent resume.
    session = session_for(new, tmp_path / "restored_session", **context)
    session.step(
        receipt(
            block(
                "api_docs__show_api_doc",
                {"app_name": "gmail", "api_name": "show_category_sizes"},
            )
        )
    )
    shown = session.build_request()["tools"]
    assert function(shown, "gmail__show_category_sizes") == function(
        new.catalogue, "gmail__show_category_sizes"
    )
    event = session.step(
        receipt(
            block(
                "gmail__show_category_sizes",
                {"access_token": "synthetic", "read": None},
            )
        )
    )
    assert event["dispatched_count"] == 1


@pytest.mark.parametrize(
    "arguments",
    [
        {"access_token": None, "read": None},
        {"read": None},
        {"access_token": "synthetic", "read": "wrong"},
        {"access_token": "synthetic", "read": 1},
        {"access_token": "synthetic", "extra": True},
    ],
)
def test_restored_schema_and_validator_reject_entire_invalid_batch(
    worlds, tmp_path, arguments
):
    source, _ = snapshot(worlds, tmp_path)
    manifest, context, _ = state.inspect_snapshot(source)
    adapter = worlds()
    prepare_snapshot_adapter(adapter, manifest, context, tmp_path / "reconstructed")
    state.restore(adapter, source)
    session = session_for(adapter, tmp_path / "after", **context)
    session.step(
        receipt(
            block(
                "api_docs__show_api_doc",
                {"app_name": "gmail", "api_name": "show_category_sizes"},
            )
        )
    )
    shown = function(session.build_request()["tools"], "gmail__show_category_sizes")[
        "function"
    ]["parameters"]
    assert not Draft202012Validator(shown).is_valid(arguments)
    before = list(adapter.calls)
    event = session.step(
        receipt(
            block(
                "gmail__show_category_sizes",
                {"access_token": "synthetic", "read": None},
            )
            + block("gmail__show_category_sizes", arguments)
        )
    )
    assert event["protocol_rejection"] == "invalid_arguments"
    assert adapter.calls == before


@pytest.mark.parametrize(
    "damage",
    [
        "revision",
        "compatible_sha256",
        "upstream_sha256",
        "audit_sha256",
        "visible",
        "undiscovered",
    ],
)
def test_mismatch_fails_before_state_import_or_tool_execution(worlds, tmp_path, damage):
    source, _ = snapshot(worlds, tmp_path)
    manifest, context, _ = state.inspect_snapshot(source)
    adapter = worlds()
    if damage in {"revision", "compatible_sha256", "upstream_sha256", "audit_sha256"}:
        manifest["versions"]["schema_compatibility"][damage] = (
            "incompatible" if damage == "revision" else "0" * 64
        )
    elif damage == "visible":
        context["tools"][0]["function"]["description"] = "altered visible schema"
    else:
        function(adapter.catalogue, "gmail__show_category_sizes")["function"][
            "description"
        ] = "changed"
    with pytest.raises(ValueError):
        prepare_snapshot_adapter(adapter, manifest, context, tmp_path / "bad")
    assert adapter.imports == 0 and adapter.calls == []


def test_direct_restore_checks_live_catalogue_before_import(worlds, tmp_path):
    source, _ = snapshot(worlds, tmp_path)
    manifest, context, _ = state.inspect_snapshot(source)
    adapter = worlds()
    prepare_snapshot_adapter(adapter, manifest, context, tmp_path / "prepared")
    adapter.catalogue[-1]["function"]["description"] = "mutated"
    with pytest.raises(ValueError, match="catalogue"):
        state.restore(adapter, source)
    assert adapter.imports == 0 and adapter.calls == []


def test_explicit_restore_config_cannot_downgrade_snapshot(
    worlds, monkeypatch, tmp_path
):
    source, _ = snapshot(worlds, tmp_path, research=True)
    config = read_json(runtime.CONFIG)
    config[compat.CONFIG_KEY] = None
    monkeypatch.setattr(
        p3c,
        "AppWorldAdapter",
        lambda *a, **k: pytest.fail("constructed before rejection"),
    )
    with pytest.raises(ValueError, match="revisions differ"):
        p3c.restore_probe(
            "unused", source, "restore", tmp_path / "result.json", config=config
        )


def test_new_student_contract_requires_configured_v2(worlds, tmp_path):
    with pytest.raises(ValueError, match="requires Schema v2"):
        session_for(worlds(), tmp_path / "session")


@pytest.mark.parametrize("role", ["base_sft", "shared_validation"])
def test_frozen_b_membership_never_accepts_a_or_c(role):
    plan, _ = b_tasks()
    task = next(r["task_id"] for r in plan["rows"] if r["role"] == role)
    with pytest.raises(ValueError, match="B244"):
        context_for(task)


def test_new_config_and_lock_are_selected_without_reading_old_dataset():
    config = read_json(runtime.CONFIG)
    result = p3c.validate_config(config)
    assert result["rows"] == b_tasks()[1] and len(result["tasks"]) == 244
    assert "dataset" not in config and config["model"] == "Qwen/Qwen3-4B"
    assert p3c.verify_source(runtime.RUNTIME) == runtime.verify_lock()["lock_sha256"]
    assert (
        student.verify_student_runtime(runtime.RUNTIME)["revision"] == runtime.RUNTIME
    )
    for key, value in [
        ("model", "Qwen/Qwen3-9B"),
        ("task_role", "base_sft"),
        (compat.CONFIG_KEY, None),
        ("source_lock", student.V6_LOCK),
    ]:
        with pytest.raises(ValueError):
            p3c.validate_config({**config, key: value})
    with pytest.raises(ValueError, match="source changed"):
        student.verify_student_runtime(student.V6_RUNTIME)
    with pytest.raises(ValueError, match="source changed"):
        p3c.verify_source(p3c.RUNTIME)


@pytest.mark.parametrize("damage", ["source", "omission", "config", "contract"])
def test_new_lock_rejects_source_config_or_manifest_damage(
    tmp_path, monkeypatch, damage
):
    root = tmp_path / "code"
    lock = read_json(runtime.LOCK)
    for name in [*lock["files"], runtime.LOCK]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(runtime.ROOT / name, target)
    if damage == "source":
        (root / "src/recoveragent/schema_snapshot.py").write_text("# changed")
    elif damage == "config":
        (root / runtime.CONFIG).write_text("{}")
    else:
        if damage == "omission":
            del lock["files"]["src/recoveragent/schema_snapshot.py"]
        else:
            lock["session_contract"]["answer_guard"] = False
        (root / runtime.LOCK).write_text(json.dumps(lock))
    monkeypatch.setattr(runtime, "ROOT", root)
    with pytest.raises(ValueError):
        runtime.verify_lock()


def test_b_snapshot_cannot_relabel_official_split(worlds, tmp_path):
    source, _ = snapshot(worlds, tmp_path, research=True)
    manifest = read_json(source / "manifest.json")
    manifest["split"] = "invented"
    manifest.pop("checkpoint_id")
    manifest["checkpoint_id"] = digest(manifest)
    (source / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="split mismatch"):
        state.inspect_snapshot(source)
    with pytest.raises(ValueError, match="identity/source"):
        validate_context(
            manifest["task_id"],
            {**manifest["custom_research"], "official_split": "train"},
        )


def test_legacy_runner_refuses_batch_snapshot_before_constructing_world(
    worlds, monkeypatch, tmp_path, legacy_config
):
    source, _ = snapshot(worlds, tmp_path)
    monkeypatch.setattr(
        runner,
        "AppWorldAdapter",
        lambda *a, **k: pytest.fail("legacy world should not start"),
    )
    result = runner.run_episode(
        legacy_config, "synthetic", tmp_path / "legacy", 42, source
    )
    assert result["status"] == "infrastructure_error"
    assert "cannot resume a batch" in result["error"]


def test_legacy_single_call_recovery_also_reconstructs_recorded_v2(
    worlds, monkeypatch, tmp_path, legacy_config
):
    source, _ = snapshot(worlds, tmp_path, legacy=True)
    monkeypatch.setattr(
        runner, "AppWorldAdapter", lambda root, task, *a, **k: worlds(task)
    )

    def stop_after_restore(*a, **k):
        adapter = worlds.created[-1]
        assert adapter.imports == 1
        assert (
            adapter.versions["schema_compatibility"]["revision"]
            == compat.SCHEMA_REVISION
        )
        raise RuntimeError("synthetic stop before model")

    monkeypatch.setattr(runner, "LocalPolicy", stop_after_restore)
    result = runner.run_episode(
        legacy_config, "synthetic", tmp_path / "legacy", 42, source
    )
    assert result["error"] == "synthetic stop before model"


@pytest.mark.parametrize("damage", ["revision", "compatible_sha256", "visible"])
def test_restore_entry_rejects_internally_signed_incompatible_snapshot(
    worlds, monkeypatch, tmp_path, damage
):
    source, _ = snapshot(worlds, tmp_path)
    manifest = read_json(source / "manifest.json")
    if damage == "visible":
        context = read_json(source / "context.json")
        context["tools"][0]["function"]["description"] = "different contract"
        (source / "context.json").write_text(json.dumps(context))
        manifest["tool_schema_hash"] = digest(context["tools"])
        manifest["files"] = {
            k: v for k, v in files_manifest(source).items() if k != "manifest.json"
        }
    else:
        manifest["versions"]["schema_compatibility"][damage] = (
            "incompatible" if damage == "revision" else "0" * 64
        )
    manifest.pop("checkpoint_id")
    manifest["checkpoint_id"] = digest(manifest)
    (source / "manifest.json").write_text(json.dumps(manifest))
    before = files_manifest(source)
    monkeypatch.setattr(
        p3c, "AppWorldAdapter", lambda root, task, *a, **k: worlds(task)
    )
    with pytest.raises(ValueError):
        p3c.restore_probe("unused", source, "restore", tmp_path / "restore.json")
    assert all(a.imports == 0 and a.calls == [] for a in worlds.created)
    assert files_manifest(source) == before


def test_restore_cli_passes_explicit_config_to_guard(monkeypatch, tmp_path, capsys):
    import sys

    config = read_json(runtime.CONFIG)
    seen = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "p3c",
            "restore",
            "--config",
            runtime.CONFIG,
            "--source",
            "synthetic",
            "--experiment",
            "restore",
            "--output",
            str(tmp_path / "out.json"),
        ],
    )

    def probe(*args, **kwargs):
        seen.append(kwargs["config"])
        return {"synthetic": True}

    monkeypatch.setattr(p3c, "restore_probe", probe)
    p3c.main()
    assert seen == [config]
    assert json.loads(capsys.readouterr().out) == {"synthetic": True}


@pytest.mark.parametrize("binding", [None, {}, {"revision": compat.SCHEMA_REVISION}])
def test_malformed_binding_never_becomes_legacy_schema(worlds, tmp_path, binding):
    source, _ = snapshot(worlds, tmp_path)
    manifest, context, _ = state.inspect_snapshot(source)
    manifest["versions"]["schema_compatibility"] = binding
    adapter = worlds()
    with pytest.raises(ValueError, match="Schema"):
        prepare_snapshot_adapter(adapter, manifest, context, tmp_path / "bad")
    assert adapter.imports == 0 and adapter.calls == []


def test_checkpoint_reference_only_adapts_release_format_without_changing_m1():
    from recoveragent.demonstrations import sha256

    adapted = read_json(runtime.RELEASE)
    original = read_json(adapted["source_release"])
    assert sha256(adapted["source_release"]) == adapted["source_release_sha256"]
    assert adapted["adapter_tensor_sha256"] == original["adapter_parameter_hash"]
    assert adapted["model"] == original["model"] == "Qwen/Qwen3-4B"
    assert adapted["revision"] == original["revision"]
    assert (
        adapted["files"]["recoveragent_checkpoint.json"]
        == original["checkpoint_receipt_sha256"]
    )


def test_isolated_evaluator_receives_same_frozen_b_identity(monkeypatch, tmp_path):
    from recoveragent import evaluation
    from recoveragent.io import write_json

    task = b_tasks()[1][0]["task_id"]
    context = context_for(task)
    commands = []

    def worker(command, **kwargs):
        commands.append(command)
        assert read_json(command[command.index("--research") + 1]) == context
        write_json(command[command.index("--output") + 1], {"synthetic": True})

    monkeypatch.setattr(evaluation.subprocess, "run", worker)
    result = evaluation.evaluate_isolated(
        task, "synthetic", "unused", tmp_path, research=context
    )
    assert result == {"synthetic": True} and len(commands) == 1


def test_non_b_evaluation_context_rejected_before_starting_worker(
    monkeypatch, tmp_path
):
    from recoveragent import evaluation

    plan, _ = b_tasks()
    task = next(r["task_id"] for r in plan["rows"] if r["role"] == "shared_validation")
    monkeypatch.setattr(
        evaluation.subprocess, "run", lambda *a, **k: pytest.fail("worker started")
    )
    with pytest.raises(ValueError, match="B244"):
        evaluation.evaluate_isolated(
            task, "synthetic", "unused", tmp_path, research={"task_id": task}
        )
