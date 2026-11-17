"""Single candidate repair across structure, Solver scoring, coverage and resume."""

import copy
from types import SimpleNamespace

import pytest

from recoveragent.coevolve_goal import ScoringGap, objective, actions
from recoveragent.coevolve_goal_schema import related_metadata
from recoveragent.coevolve_contract_repair import repair, reuse_reference
from recoveragent.io import digest, read_json, write_json
from test_public_goal_reward import fixture
from test_s2_evolution import engine
from test_s2_experiment import cfg
from test_coevolve import history


def metadata():
    return {
        "tables": {
            "demo.Transfer": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "owner_id": {"type": "integer"},
                    "amount": {"type": "number"},
                },
            }
        }
    }


def setup_candidate(tmp_path, monkeypatch):
    e = engine(tmp_path)
    e.public_goals = True
    e.settings = {**e.settings, "quality_filter": False}
    source = cfg()["feedback_task_ids"][0]
    explorations = [
        {"task_id": source, "trajectory_id": "explore", "messages": history()}
    ]
    task, observed = fixture()
    proposal = {
        "query": "Transfer exactly 21.5; report amount and new IDs. Do not change other transfers.",
        "action_sequence": "Three real actions",
        "completion_criterion": "Transfer and report",
        "evidence_refs": [
            {"rollout_id": "explore", "action_index": i} for i in range(3)
        ],
        "success_conditions": copy.deepcopy(task["success_conditions"]),
    }
    # Same real candidate builder and admission paths; model transport is synthetic.
    calls = []

    def text(source, directory, prompt, content):
        calls.append(directory.name)
        if directory.name == "contract_repair":
            assert content["model_metadata"] == metadata()
            assert content["proposal"]["query"] == proposal["query"]
            return {"success_conditions": task["success_conditions"]}
        return {
            "success": True,
            "reason": "synthetic coverage",
            "contract_complete": True,
            "conditions": [],
        }

    monkeypatch.setattr(e, "text", text)
    return e, proposal, explorations, observed, calls


@pytest.mark.parametrize("gap", ["runtime", "coverage", "structure"])
def test_first_gap_repairs_once_and_keeps_public_goal(tmp_path, monkeypatch, gap):
    e, proposal, explorations, observed, calls = setup_candidate(tmp_path, monkeypatch)
    target = tmp_path / "candidate"
    if gap == "structure":
        proposal["success_conditions"]["checks"][0]["op"] = "unsupported"
    admitted = []

    def admit(task, directory, *, reference_path=None, judgment=None):
        admitted.append((task, directory, reference_path))
        if len(admitted) == 1 and gap != "structure":
            observed["instruction"] = task["instruction"]
            observed["actions"] = actions(history())
            write_json(
                directory / (directory.name + "_solver") / "trajectory.json",
                {
                    "status": "valid",
                    "initial_state_hash": "real-in-fixture",
                    "messages": history(),
                    "goal_evidence": observed,
                    "coevolve_terminal": {
                        k: observed[k] for k in ("instruction", "status", "answer")
                    },
                },
            )
            raise ScoringGap(
                "Unavailable runtime field"
                if gap == "runtime"
                else "Goal coverage incomplete"
            )
        assert objective(task, {**observed, "instruction": task["instruction"]})[
            "success"
        ]
        return task

    monkeypatch.setattr(e, "admit", admit)
    result = e.admit_proposal(proposal, explorations, {}, target, metadata())
    assert result["instruction"].startswith(proposal["query"])
    assert calls == ["contract_repair"]
    assert read_json(target / "contract_repair_claim.json")["count"] == 1
    if gap != "structure":
        assert admitted[-1][2] is not None
        assert (
            read_json(target / "repaired_admission/reference_reuse.json")[
                "original_trajectory_modified"
            ]
            is False
        )


def test_true_solver_failure_does_not_trigger_repair(tmp_path, monkeypatch):
    e, proposal, explorations, _, calls = setup_candidate(tmp_path, monkeypatch)
    monkeypatch.setattr(
        e,
        "admit",
        lambda *a, **kw: (_ for _ in ()).throw(
            ValueError("Solver failed objective conditions")
        ),
    )
    with pytest.raises(ValueError, match="Solver failed"):
        e.admit_proposal(proposal, explorations, {}, tmp_path / "candidate", metadata())
    assert (
        not calls and not (tmp_path / "candidate/contract_repair_claim.json").exists()
    )


def test_one_repair_across_stages_and_resume(tmp_path, monkeypatch):
    e, proposal, explorations, _, calls = setup_candidate(tmp_path, monkeypatch)
    proposal["success_conditions"]["checks"][0]["op"] = "unsupported"
    monkeypatch.setattr(
        e,
        "admit",
        lambda *a, **kw: (_ for _ in ()).throw(
            ScoringGap("runtime scoring still unsupported")
        ),
    )
    target = tmp_path / "candidate"
    for _ in range(2):
        with pytest.raises(ScoringGap):
            e.admit_proposal(proposal, explorations, {}, target, metadata())
    assert calls == ["contract_repair"]
    assert read_json(target / "contract_repair_claim.json")["count"] == 1


def test_interrupted_repair_consumes_allowance_before_call(tmp_path):
    calls = []

    def interrupted(*a):
        calls.append(True)
        raise KeyboardInterrupt()

    e = SimpleNamespace(text=interrupted)
    with pytest.raises(KeyboardInterrupt):
        repair(e, "task", {"query": "goal"}, tmp_path, metadata(), "gap")
    with pytest.raises(ScoringGap, match="already consumed"):
        repair(e, "task", {"query": "goal"}, tmp_path, metadata(), "resume")
    assert calls == [True]


@pytest.mark.parametrize("change", ["output", "missing_state", "missing_actions"])
def test_inadequate_reuse_keeps_pending_without_solver(tmp_path, change):
    task, observed = fixture()
    old, new = copy.deepcopy(task), copy.deepcopy(task)
    ref = {
        "status": "valid",
        "initial_state_hash": "initial",
        "messages": history(),
        "goal_evidence": observed,
    }
    if change == "output":
        new["success_conditions"]["output"]["required"] = False
    if change == "missing_state":
        observed["before"] = {}
    if change == "missing_actions":
        observed.pop("actions")
    source = tmp_path / "trajectory.json"
    write_json(source, ref)
    before = source.read_bytes()
    with pytest.raises(ScoringGap, match="pending"):
        reuse_reference(old, new, source, metadata(), tmp_path)
    assert source.read_bytes() == before


def test_metadata_is_pinned_related_and_contains_no_rows(monkeypatch):
    # AppWorld imports dotenv at module load; keep this metadata test isolated.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: False)
    # Reads installed model declarations only; never constructs/runs AppWorld.
    value = related_metadata([{"action": {"name": "amazon__show_cart"}}])
    assert value["appworld_revision"]
    assert all(name.startswith("amazon.") for name in value["tables"])
    assert (
        value["tables"]["amazon.CartEntry"]["properties"]["quantity"]["type"]
        == "integer"
    )
    assert "default" not in str(value) and "record_hash" in str(value)
    assert digest(value) == digest(
        related_metadata([{"action": {"name": "amazon__show_cart"}}])
    )


def test_generate_coverage_gap_reuses_solver_and_publishes_to_existing_pool(
    tmp_path, monkeypatch
):
    from recoveragent import coevolve_goal_schema
    from recoveragent.coevolve_tasks import validate_task
    from recoveragent.coevolve_signals import interaction_triplets

    e, proposal, _, observed, calls = setup_candidate(tmp_path, monkeypatch)
    monkeypatch.setattr(coevolve_goal_schema, "related_metadata", lambda _: metadata())
    executed = []

    def execute(source, directory, payload):
        executed.append(payload["mode"])
        record = {
            "task_id": source,
            "trajectory_id": directory.name,
            "initial_state_hash": "same",
            "messages": history(),
            "status": "valid",
        }
        if payload["mode"] == "validation":
            observed["instruction"] = payload["task"]["instruction"]
            observed["actions"] = actions(history())
            record.update(
                goal_evidence=copy.deepcopy(observed),
                coevolve_terminal={
                    "instruction": observed["instruction"],
                    "status": "success",
                    "answer": observed["answer"],
                    "business_sha256": "diagnostic",
                },
            )
        write_json(directory / "trajectory.json", record)
        return record

    def text(source, directory, prompt, content):
        calls.append(directory.name)
        if directory.name == "abstraction":
            assert content["model_metadata"] == metadata()
            offered = copy.deepcopy(proposal)
            offered["evidence_refs"] = [
                {k: t[k] for k in ("rollout_id", "action_index")}
                for t in content["interaction_triplets"][:3]
            ]
            return {"tasks": [offered]}
        if directory.name == "contract_repair":
            assert content["model_metadata"] == metadata()
            return {"success_conditions": proposal["success_conditions"]}
        assert directory.name == "semantic_judge"
        fixed = "repaired_admission" in directory.parts
        return {
            "success": fixed,
            "reason": "synthetic",
            "contract_complete": fixed,
            "contract_reason": "missing coverage"
            if not fixed
            else "all requirements covered",
            "conditions": [],
        }

    monkeypatch.setattr(e, "execute", execute)
    monkeypatch.setattr(e, "text", text)
    monkeypatch.setattr(
        e,
        "checks",
        lambda *a: [
            {
                "mode": mode,
                "initial_state_hash": "same",
                "evaluation": {"success": mode == "positive"},
            }
            for mode in ("positive", "noop", "omit_completion", "wrong_answer")
        ],
    )
    directory = tmp_path / "context"
    e.generate(cfg()["feedback_task_ids"][0], {}, directory)
    assert executed == ["exploration", "exploration", "validation"]
    assert calls == [
        "abstraction",
        "semantic_judge",
        "contract_repair",
        "semantic_judge",
    ]
    assert len(e.pool) == 1 and e.attempts[0]["accepted"]
    assert e.attempts[0]["contract_repair_count"] == 1
    published = list(directory.glob("*/accepted_task.json"))
    assert len(published) == 1 and read_json(published[0]) == e.pool[0]
    validate_task(e.pool[0], e.pool[0]["source_task_id"])
    reference = read_json(
        published[0].parent / (published[0].parent.name + "_solver") / "trajectory.json"
    )
    assert interaction_triplets(reference["messages"], reference["trajectory_id"])


def test_scoring_fault_evidence_reused_without_rewriting_old_status(tmp_path):
    from recoveragent.coevolve_tasks import attach_reference

    task, observed = fixture()
    observed["actions"] = actions(history())
    ref = {
        "status": "invalid",
        "error_type": "ScoringGap",
        "scoring_gap": "old path",
        "initial_state_hash": "initial",
        "messages": history(),
        "goal_evidence": observed,
        "coevolve_terminal": {
            k: observed[k] for k in ("instruction", "status", "answer")
        },
    }
    ref["coevolve_terminal"]["business_sha256"] = "diagnostic"
    path = tmp_path / "trajectory.json"
    write_json(path, ref)
    before = path.read_bytes()
    reuse_reference(task, task, path, metadata(), tmp_path)
    judgment = {
        "success": True,
        "reason": "synthetic coverage",
        "contract_complete": True,
    }
    with pytest.raises(ValueError, match="actually finish"):
        attach_reference(task, ref, judgment)
    accepted = attach_reference(task, ref, judgment, scoring_repair=True)
    assert accepted["execution_contract"]["reference_goal_evaluation"]["success"]
    assert path.read_bytes() == before and read_json(path)["status"] == "invalid"


def test_compact_metadata_keeps_columns_named_like_schema_annotations():
    from recoveragent.coevolve_goal_schema import model_schema, validate_fields

    model = SimpleNamespace(
        model_json_schema=lambda: {
            "title": "Entity",
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "public title column"},
                "description": {"type": "string"},
                "default": {"type": "boolean", "default": False},
            },
        }
    )
    schema = model_schema(model)
    assert set(schema["properties"]) == {"title", "description", "default"}
    assert "title" not in schema and "description" not in schema["properties"]["title"]
    task, _ = fixture()
    spec = {
        **task["success_conditions"],
        "bindings": {},
        "checks": [
            {
                "left": {
                    "values": {
                        "from": {"rows": {"table": "demo.Entity"}},
                        "path": ["title"],
                    }
                },
                "right": {"literal": []},
            }
        ],
    }
    validate_fields(spec, {"tables": {"demo.Entity": schema}})
