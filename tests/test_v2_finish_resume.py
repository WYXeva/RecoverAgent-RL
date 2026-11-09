"""Synthetic post-S1 handoff and reduced selection; no model or paid calls."""

import copy
import json

import pytest

from recoveragent.demonstrations import sha256
from recoveragent.io import files_manifest, read_json, write_json
from recoveragent.v2_finish_resume import carry_s1
from recoveragent.v2_round import config
from recoveragent.v2_tasks import ROOT, development_tasks, grpo_schedule, plan


def test_reduced_selection_keeps_three_scenarios_and_three_plus_one_groups():
    cfg = read_json(ROOT / "configs/v2_round1_finish4_20261109.json")
    tasks = development_tasks(cfg["dev_tasks"])
    frozen = plan()
    scenes = {r["task_id"]: r["scenario_id"] for r in frozen["rows"]}
    assert tasks == [frozen["dev24"][i] for i in (0, 3, 6)]
    assert len({scenes[t] for t in tasks}) == 3
    assert not set(tasks) & set(frozen["feedback20"])
    generated = {"source_task_id": frozen["feedback20"][0]}
    schedule = grpo_schedule(cfg, [generated])
    assert [t["source"] for t in schedule] == ["original"] * 3 + ["generated"]
    assert schedule[-1]["generated_task"] == generated
    assert (
        len(grpo_schedule(cfg, [])) == 3
    )  # No synthetic replacement of missing new task.
    assert cfg["student_limits"]["max_input_tokens"] == 32768


@pytest.fixture
def post_s1(tmp_path, monkeypatch):
    source, s0 = tmp_path / "source", tmp_path / "S0"
    cfg = config()
    old = read_json(ROOT / "configs/v2_round1_feedback15_20261109.json")
    identity = cfg["models"][cfg["selected_model"]]
    deployment = {"job_deadline_epoch": 1000}
    write_json(source / "run_config.json", old)
    write_json(
        source / "preflight.json",
        {
            "source": {"lock_sha256": sha256(ROOT / old["source_lock"])},
            "deployment": {
                "job_deadline_epoch": 1000
                - (cfg["gpu_wall_seconds"] - old["gpu_wall_seconds"])
            },
        },
    )
    frozen = plan()
    write_json(
        source / "task_lists.json",
        {
            "feedback20": frozen["feedback20"],
            "dev24": frozen["dev24"],
            "split_manifest_sha256": frozen["manifest_sha256"],
        },
    )
    s0.mkdir()
    (s0 / "parameters.txt").write_text("synthetic S0")
    write_json(
        s0 / "checkpoint_receipt.json",
        {
            "model": identity,
            "reload_passed": True,
            "adapter_sha256_after": "s0",
            "files": files_manifest(s0),
        },
    )
    (source / "recovery_mix.jsonl").write_text("{}\n" * 8)
    s1 = source / "S1_recovery"
    s1.mkdir()
    (s1 / "parameters.txt").write_text("synthetic S1")
    write_json(
        s1 / "checkpoint_receipt.json",
        {
            "model": identity,
            "reload_passed": True,
            "stage": "S1_recovery",
            "profile": "formal",
            "parent": str(s0),
            "parent_receipt_sha256": sha256(s0 / "checkpoint_receipt.json"),
            "adapter_sha256_before": "s0",
            "adapter_sha256_after": "s1",
            "data_sha256": sha256(source / "recovery_mix.jsonl"),
            "examples": 8,
            "optimizer_steps": 1,
            "files": files_manifest(s1),
        },
    )
    write_json(
        source / "post_s1_handoff.json",
        {
            "reason": "user_approved_grpo4_dev3_32k",
            "downstream_requests": 0,
            "checkpoint_receipt_sha256": sha256(s1 / "checkpoint_receipt.json"),
        },
    )
    feedback = [{"task_id": t} for t in frozen["feedback20"][:15]]
    proof = {"source_run": "synthetic_prior", "generation_reservation": 7}
    monkeypatch.setattr(
        "recoveragent.v2_finish_resume.carry_feedback",
        lambda *a: (feedback, 123, proof),
    )
    write_json(source / "continued_feedback.json", proof)
    recovery = source / "recovery" / "one"
    trajectory = {"status": "valid", "reward": 1, "usage": {"usage_priced_cost": 0.1}}
    write_json(recovery / "trajectory.json", trajectory)
    write_json(recovery / "suffix_acceptance.json", {"status": "eligible"})
    generation = source / "generation" / "one"
    write_json(generation / "trajectory.json", trajectory)
    write_json(source / "generated_pool.json", [])
    write_json(
        source / "round_summary.json",
        {
            "status": "stopped",
            "feedback": feedback,
            "recovery": [
                {
                    "attempted": True,
                    "directory": str(recovery),
                    "official_success": True,
                    "acceptance": {"status": "eligible"},
                }
            ],
            "generated": [{"accepted": False, "directory": str(generation)}],
            "training_generated_tokens": 123,
            "training_generated_token_reservation": 7,
            "teacher_usage_priced_cny": 0.2,
        },
    )
    return source, cfg, identity, s0, deployment


def test_post_s1_preserves_weights_and_all_cost_ledgers(post_s1):
    source, cfg, identity, s0, deployment = post_s1
    before = files_manifest(source)
    carried, pool, s1, proof = carry_s1(*post_s1)
    assert carried["training_generated_tokens"] == 123
    assert carried["training_generated_token_reservation"] == 7
    assert carried["teacher_usage_priced_cny"] == 0.2
    assert not pool and s1 == source / "S1_recovery" and proof["no_replay"]
    assert files_manifest(source) == before


@pytest.mark.parametrize(
    "damage",
    [
        "deadline",
        "model",
        "budget",
        "checkpoint",
        "parent",
        "data",
        "ledger",
        "reservation",
        "cost",
        "lock",
        "rollout",
        "dev",
        "handoff",
    ],
)
def test_post_s1_rejects_changed_lineage_budget_or_downstream(post_s1, damage):
    source, cfg, identity, s0, deployment = post_s1
    if damage == "deadline":
        deployment["job_deadline_epoch"] += 1
    elif damage == "model":
        identity = {"model": "wrong"}
    elif damage == "budget":
        cfg = copy.deepcopy(cfg)
        cfg["student_limits"]["max_requests"] += 1
    elif damage == "checkpoint":
        (source / "S1_recovery/parameters.txt").write_text("changed")
    elif damage == "parent":
        (s0 / "parameters.txt").write_text("different S0")
    elif damage == "data":
        (source / "recovery_mix.jsonl").write_text("changed")
    elif damage in {"ledger", "reservation", "cost"}:
        path = source / "round_summary.json"
        value = read_json(path)
        key = {
            "ledger": "training_generated_tokens",
            "reservation": "training_generated_token_reservation",
            "cost": "teacher_usage_priced_cny",
        }[damage]
        value[key] += 1
        path.write_text(json.dumps(value))
    elif damage == "lock":
        path = source / "preflight.json"
        value = read_json(path)
        value["source"]["lock_sha256"] = "wrong"
        path.write_text(json.dumps(value))
    elif damage == "handoff":
        path = source / "post_s1_handoff.json"
        value = read_json(path)
        value["checkpoint_receipt_sha256"] = "wrong"
        path.write_text(json.dumps(value))
    else:
        (source / ("rollouts" if damage == "rollout" else "evaluation")).mkdir()
    with pytest.raises(ValueError):
        carry_s1(source, cfg, identity, s0, deployment)


def test_existing_public_read_proposal_revalidated_without_new_teacher(tmp_path):
    from recoveragent.v2_finish_resume import revalidate_public_read_proposals
    from recoveragent.v2_generator import candidate

    source, output = tmp_path / "old", tmp_path / "new"
    task_id = plan()["feedback20"][0]
    messages = [
        {"role": "system", "content": "synthetic"},
        {"role": "user", "content": '{"instruction":"original goal"}'},
    ]
    for i, (name, args, result) in enumerate(
        [
            ("supervisor__show_active_task", {}, {"instruction": "public original"}),
            ("gmail__show_category_sizes", {"read": False}, {"inbox": 2, "outbox": 3}),
            (
                "supervisor__complete_task",
                {
                    "answer": json.dumps(
                        {
                            "read": False,
                            "categories": ["inbox", "outbox"],
                            "rationale": "observed counting weakness",
                        }
                    )
                },
                {},
            ),
        ]
    ):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": str(i),
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": str(i), "content": json.dumps(result)},
            ]
        )
    original = source / "generation" / "one"
    record = {"task_id": task_id, "messages": messages}
    write_json(original / "trajectory.json", record)
    feedback = source / "feedback" / "one"
    write_json(feedback / "history.json", {"messages": []})
    write_json(
        source / "round_summary.json",
        {
            "feedback": [
                {
                    "task_id": task_id,
                    "termination": "synthetic",
                    "directory": str(feedback),
                }
            ],
            "generated": [
                {
                    "accepted": False,
                    "reason": "Unsupported or mutating exploration action",
                    "directory": str(original),
                }
            ],
        },
    )
    calls = []

    def isolated(python, command, payload, directory, deadline):
        assert command == "generator-check"
        calls.append(payload["mode"])
        write_json(
            directory / "validation.json",
            {
                "mode": payload["mode"],
                "initial_state_hash": "same",
                "evaluation": {"success": payload["mode"] == "positive"},
            },
        )

    before = files_manifest(source)
    pool, audit = revalidate_public_read_proposals(
        source, output, [], "python", "synthetic_root", 100, isolated
    )
    assert len(pool) == 1 and pool[0]["expected_answer"] == 5
    assert calls == ["positive", "noop", "omit_completion", "wrong_answer"]
    assert audit[0]["new_teacher_requests"] == 0 and files_manifest(source) == before
    record["messages"][2]["tool_calls"][0]["function"]["name"] = "gmail__delete_email"
    with pytest.raises(ValueError, match="Unsupported or mutating"):
        candidate(record, {}, "synthetic")


def test_restored_scale_and_24h_keep_episode_budgets():
    cfg = config()
    old = read_json(ROOT / "configs/v2_round1_feedback15_20261109.json")
    assert cfg["gpu_wall_seconds"] == 86400
    assert cfg["feedback_tasks"] == 15
    assert cfg["grpo"] == old["grpo"]
    assert development_tasks(cfg["dev_tasks"]) == plan()["dev24"]
    assert cfg["student_limits"] == old["student_limits"]
    assert cfg["train_generated_tokens"] == 200000
    fake = {"source_task_id": plan()["feedback20"][0]}
    groups = grpo_schedule(cfg, [fake])
    assert len(groups) == 16
    assert sum(g["source"] == "generated" for g in groups) == 4
