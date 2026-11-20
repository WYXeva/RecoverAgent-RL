"""Synthetic CPU integration; no paid models or AppWorld task execution."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from recoveragent.codeact import evolution, experiment, online, protocol
from recoveragent.coevolve_llm import admit, candidate
from recoveragent.coevolve_tasks import identify
from recoveragent.io import read_json, write_json
from recoveragent.s2_schedule import GroupFeedback

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config(monkeypatch):
    monkeypatch.delenv("RECOVERAGENT_S2_EXECUTION", raising=False)
    return read_json(ROOT / "configs/codeact_grpo_coevolve_20261119.json")


def messages(query="Read the count."):
    return [
        {"role": "user", "content": json.dumps({"instruction": query})},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call",
                    "type": "function",
                    "function": {
                        "name": "appworld",
                        "arguments": json.dumps(
                            {"code": "print(apis.gmail.show_category_sizes())"}
                        ),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call", "content": '{"inbox": 2}'},
    ]


def accepted(config, tmp_path):
    source = config["online"]["train_tasks"][0]
    task = candidate(
        {
            "query": "Report the inbox count.",
            "action_sequence": "Read and report.",
            "completion_criterion": "The count is reported.",
        },
        [{"task_id": source, "trajectory_id": "synthetic", "messages": messages()}],
        {},
    )
    task["execution_protocol"] = protocol.identity()
    task["task_id"] = identify(task)
    trace = {
        "task_id": source,
        "status": "valid",
        "fault": None,
        "initial_state_hash": "initial",
        "messages": messages(task["instruction"]),
        "coevolve_terminal": {
            "instruction": task["instruction"],
            "status": "success",
            "answer": 2,
        },
    }
    write_json(tmp_path / "solver.json", trace)
    task = admit(
        SimpleNamespace(
            text=lambda *a, **kw: {
                "reason": "Synthetic matching observation",
                "evidence_steps": [0],
                "success": True,
            }
        ),
        task,
        tmp_path / "admission",
        reference_path=tmp_path / "solver.json",
    )
    return task, trace


@pytest.mark.parametrize("outcome", [True, False, None, "bad"])
def test_same_judge_reference_and_invalid_group(config, tmp_path, outcome):
    task, trace = accepted(config, tmp_path)
    calls = []

    def text(source, directory, system, content, *, role):
        calls.append(content)
        assert role == "judge" and content["reference"]["execution"]
        assert content["execution"][0]["action"]["name"] == "appworld"
        return {
            "reason": "Synthetic judgment",
            "evidence_steps": [0],
            "success": outcome,
        }

    result = evolution.reward(
        SimpleNamespace(text=text, judge_identity={"revision": "test"}),
        task,
        trace,
        tmp_path / "reward",
    )
    assert len(calls) == 1
    assert result["success"] == (outcome if outcome != "bad" else None)
    assert result["reward_valid"] is (type(outcome) is bool)
    rows = []
    for i in range(4):
        directory = tmp_path / f"member_{i}"
        write_json(directory / "history.json", {"messages": trace["messages"]})
        rows.append(
            {
                "task_id": task["source_task_id"],
                "generated_task_id": task["task_id"],
                "initial_state_hash": "initial",
                "policy": "actor",
                "directory": str(directory),
                "success": bool(i),
                "reward_valid": True,
            }
        )
    rows[0].update(success=result["success"], reward_valid=result["reward_valid"])
    event = GroupFeedback(config["online"]["signals"]).observe("test", rows)
    assert event["complete"] is (type(outcome) is bool)
    if type(outcome) is not bool:
        assert not experiment.effective(rows) and not event["signals"]


def test_actual_text_transport_thinking_and_shared_budget(
    config, tmp_path, monkeypatch
):
    from recoveragent.teacher import TeacherPolicy

    sent = []

    def post(self, payload):
        sent.append(payload)
        return {
            "model": config["teacher"]["model"],
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "reasoning_content": "Synthetic private reasoning",
                        "content": '{"reason":"synthetic","evidence_steps":[0],"success":true}',
                    },
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 25},
        }

    monkeypatch.setattr(TeacherPolicy, "_post", post)
    monkeypatch.setattr(
        evolution,
        "receipt",
        lambda *a: {"within_budget": True, "synthetic_count": True},
    )
    engine = evolution.Engine(
        config,
        {
            "teacher_ledger": str(tmp_path / "budget"),
            "deadline": evolution.epoch() + 100,
        },
        {config["teacher"]["api_key_env"]: "synthetic-key"},
    )
    value = engine.text(
        config["online"]["train_tasks"][0],
        tmp_path / "judge",
        evolution.JUDGE,
        {"execution": []},
        role="judge",
    )
    assert value["success"] is True
    assert (
        sent[0]["thinking"] == {"type": "enabled"}
        and sent[0]["reasoning_effort"] == "high"
    )
    assert sent[0]["max_tokens"] == 8192 and "temperature" not in sent[0]
    assert "Synthetic private reasoning" in json.dumps(
        read_json(tmp_path / "judge/response.json")
    )
    assert "reasoning_content" not in value
    entries = list((tmp_path / "budget").glob("*.json"))
    assert (
        len(entries) == 1 and read_json(entries[0])["usage"]["generated_tokens"] == 25
    )
    config["online"]["teacher_budget"]["requests"] = 1
    with pytest.raises(ValueError, match="budget"):
        engine.text(
            config["online"]["train_tasks"][0],
            tmp_path / "another",
            evolution.JUDGE,
            {},
            role="judge",
        )


def test_body_chunk_and_merged_grpo_match_reference(tmp_path):
    torch = pytest.importorskip("torch")
    peft = pytest.importorskip("peft")
    from test_grpo_efficiency import row
    from test_v2_training_cpu import tiny, tokenizer

    from recoveragent.grpo_compare import gradient_comparison
    from recoveragent.v2_grpo import make_trainer, update_batch

    def model():
        return peft.get_peft_model(
            tiny(),
            peft.LoraConfig(
                r=2, lora_alpha=4, target_modules=["q_proj", "v_proj"], lora_dropout=0
            ),
        )

    a, b = model(), model()
    b.load_state_dict(a.state_dict())
    cfg = {"learning_rate": 1e-5, "temperature": 0.8, "beta": 0.0}
    first = make_trainer(a, tokenizer(), tmp_path / "a", cfg)
    second = make_trainer(
        b,
        tokenizer(),
        tmp_path / "b",
        {**cfg, "chunk_lm_head": True, "coalesce_exact_prefixes": True},
    )
    rows, episodes = [], []
    for i in range(4):
        rs = [
            row([2, 3], [4 + i, 8], "a"),
            row([2, 3, 4 + i, 8, 11, 12], [13, 14], "b"),
        ]
        for r in rs:
            ids = torch.tensor([r["prompt_ids"] + r["completion_ids"]])
            with torch.no_grad():
                lp, _, _ = first._get_per_token_logps_and_entropies(
                    a, ids, torch.ones_like(ids), len(r["completion_ids"])
                )
            r["logprobs"] = lp[0].tolist()
        rows.append(rs)
        episodes.append(
            {
                "directory": str(i),
                "task_id": "synthetic",
                "generated_task_id": None,
                "policy": "same",
                "initial_state_hash": "same",
                "success": i == 0,
            }
        )
    gradients = []

    def observe(m):
        gradients.append(
            {n: p.grad.clone() for n, p in m.named_parameters() if p.requires_grad}
        )

    def loader(d, *a, **kw):
        return copy.deepcopy(rows[int(d)])

    x = update_batch(
        first,
        [episodes],
        max_length=128,
        segment_loader=loader,
        observe_gradients=observe,
    )
    memory_events = []
    y = update_batch(
        second,
        [episodes],
        max_length=128,
        segment_loader=loader,
        observe_gradients=observe,
        observe_memory=memory_events.append,
    )
    assert [e["stage"] for e in memory_events] == [
        "before_forward",
        "after_forward",
        "before_backward",
        "after_backward",
    ] * 4
    assert (
        sum(e["target_tokens"] for e in memory_events if e["stage"] == "after_backward")
        == 16
    )
    numeric = gradient_comparison(*gradients)
    assert x["loss"] == pytest.approx(y["loss"], abs=2e-6)
    assert x["ratio_token_count"] == y["ratio_token_count"] == 16
    assert (
        x["sequence_work"]["training_segments"] == 8
        and y["sequence_work"]["training_segments"] == 4
    )
    assert numeric["max_absolute_error"] < 2e-6
    for n in gradients[0]:
        torch.testing.assert_close(
            gradients[0][n], gradients[1][n], atol=2e-6, rtol=2e-4
        )
    print(
        json.dumps(
            {
                "synthetic_cpu": True,
                "loss_error": abs(x["loss"] - y["loss"]),
                "gradient_error": numeric,
                "before": x["sequence_work"],
                "after": y["sequence_work"],
            }
        )
    )


@pytest.mark.parametrize("rounds", [1, 5, "updates", "updates_oom"])
def test_controller_effective_accumulation_new_pool_and_resume(
    config, tmp_path, monkeypatch, rounds
):
    update_rounds = isinstance(rounds, str)
    config["online"].update(max_groups=20 if rounds == 5 else 10, s0_sha256="s0")
    if rounds == 5:
        config["grpo"]["groups_per_round"] = 4
        config["online"]["round_evaluation"] = {"task_set": "dev24"}
    if update_rounds:
        config["grpo"]["groups_per_round"] = 2
        config["online"]["round_evaluation"] = {
            "task_set": "dev24",
            "every_optimizer_updates": 2,
        }
    dep = {
        "s0": "s0",
        "deadline": evolution.epoch() + 100,
        "teacher_ledger": str(tmp_path / "ledger"),
    }
    task, _ = accepted(config, tmp_path / "new_task")
    observed = []

    def checkpoint(path, identity):
        return {
            "formal_s0": path == "s0",
            "protocol": protocol.identity(),
            "adapter_sha256_after": path,
        }

    monkeypatch.setattr(experiment, "verify_checkpoint", checkpoint)

    class Server:
        def __init__(self, cfg, deploy, actor, directory):
            self.proof = {"url": "synthetic", "actor": actor}
            self.receipt = str(directory / "receipt")

        def close(self):
            pass

    monkeypatch.setattr(experiment, "Server", Server)

    def launch(deploy, kind, config_path, request_path, output):
        req = read_json(request_path)
        observed.append((kind, req))
        if kind == "evaluate":
            if update_rounds:
                assert req["evaluation_basis"] == "optimizer_updates"
                assert req["target_updates"] == req["round"] * 2
                assert req["actor"] == f"updated_{req['target_updates']}"
                assert Path(output).name == f"updates_{req['target_updates']:06d}"
            else:
                assert req["round"] * 4 == len(
                    [k for k, _ in observed if k == "sample"]
                )
            write_json(
                Path(output) / "summary.json",
                {
                    "status": "complete",
                    "tasks": 24,
                    "success": 8,
                    "failure": 16,
                    "unknown": 0,
                },
            )
            return SimpleNamespace(wait=lambda **kw: 0, poll=lambda: 0, returncode=0)
        if kind == "sample":
            result = {
                "index": req["index"],
                "selection": req["selection"],
                "episodes": [],
            }
            for member in range(4):
                target = Path(output) / str(member)
                write_json(target / "history.json", {"messages": messages()})
                result["episodes"].append(
                    {
                        "task_id": req["selection"]["task_id"],
                        "generated_task_id": req["selection"]
                        .get("generated_task", {})
                        .get("task_id"),
                        "directory": str(target),
                        "success": (
                            False
                            if req["index"] in (0, 4)
                            else True
                            if req["index"] == 2
                            else member == 0
                        ),
                        "policy": online.policy(checkpoint(req["actor"], {}), config),
                        "initial_state_hash": "initial",
                    }
                )
        elif kind == "update":
            assert 1 <= len(req["groups"]) <= 2 and all(
                experiment.effective(g) for g in req["groups"]
            )
            assert all(
                e["policy"] == online.policy(checkpoint(req["actor"], {}), config)
                for g in req["groups"]
                for e in g
            )
            result = {
                "updated": True,
                "group_ids": req["group_ids"],
                "optimizer_steps": req["optimizer_steps"] + 1,
                "actor": "updated_" + str(req["optimizer_steps"] + 1),
                "optimizer": "saved",
                "optimizer_sha256": "sha",
            }
            if (
                rounds == "updates_oom"
                and len([k for k, _ in observed if k == "update"]) == 1
            ):
                result.update(
                    updated=False,
                    **{
                        k: req[k]
                        for k in (
                            "actor",
                            "optimizer",
                            "optimizer_sha256",
                            "optimizer_steps",
                        )
                    },
                )
        else:
            assert len(req["contexts"]) <= 4
            result = {"accepted": [task], "attempts": [{"accepted": True}]}
        write_json(Path(output) / "result.json", result)
        return SimpleNamespace(wait=lambda **kw: 0, poll=lambda: 0, returncode=0)

    monkeypatch.setattr(experiment, "launch", launch)
    root = tmp_path / "run"
    experiment.experiment(config, dep, "unused", root)
    state = read_json(root / "state.json")
    assert (
        state["status"] == "complete"
        and len(state["groups"]) == config["online"]["max_groups"]
    )
    assert state["optimizer_steps"] == (
        9 if rounds == 5 else 3 if rounds == "updates_oom" else 4
    )
    assert len(state["consumed"]) == (
        17 if rounds == 5 else 5 if rounds == "updates_oom" else 7
    )
    assert not state["pending"] and len(set(state["consumed"])) == len(
        state["consumed"]
    )
    assert len(state["round_evaluations"]) == (
        5
        if rounds == 5
        else 2
        if rounds == "updates"
        else 1
        if rounds == "updates_oom"
        else 0
    )
    if update_rounds:
        assert [len(req["groups"]) for kind, req in observed if kind == "update"] == [
            2,
            2,
            2,
            1,
        ]
        progress = read_json(root / "summary.json")["learning_round_progress"]
        assert progress["completed_rounds"] == state["optimizer_steps"] // 2
        assert progress["updates_in_round"] == state["optimizer_steps"] % 2
    for item in state["round_evaluations"]:
        assert item["actor"] == "updated_" + str(item["optimizer_steps"])
    assert not {0, 2, 4} & set(state["consumed"])
    assert (
        read_json(state["groups"][9])["selection"]["generated_task"]["task_id"]
        == task["task_id"]
    )
    count = len(observed)
    experiment.experiment(config, dep, "unused", root)
    assert len(observed) == count
    config["grpo"]["learning_rate"] *= 2
    with pytest.raises(ValueError, match="binding changed"):
        experiment.experiment(config, dep, "unused", root)


@pytest.mark.parametrize("generated", [False, True])
def test_shared_episode_official_vs_derived_goal(
    config, tmp_path, monkeypatch, generated
):
    import sys

    from test_codeact_reboot import response

    from recoveragent.codeact import episode

    task, _ = accepted(config, tmp_path / "candidate")
    official = []
    active = SimpleNamespace(
        status=None, instruction="Original public instruction", save=lambda: None
    )

    class Models(dict):
        def model_hashes(self):
            return {"supervisor.Task": "initial"}

    class World:
        def __init__(self, **kw):
            assert kw["load_ground_truth"] is False
            self.task = SimpleNamespace(
                instruction=active.instruction, supervisor={}, datetime="2023-01-02"
            )
            self.models = Models(
                supervisor={"Task": SimpleNamespace(find_all=lambda: [active])}
            )
            self.requester = SimpleNamespace(
                request_tracker=SimpleNamespace(requests=[]),
                request=lambda *a, **kw: {
                    "instruction": active.instruction,
                    "status": active.status,
                    "answer": None,
                },
            )

        def execute(self, code):
            assert code == "apis.supervisor.complete_task()"
            active.status = "success"
            return "Task completed."

        def task_completed(self):
            return active.status == "success"

        def save(self):
            pass

        def close(self):
            pass

    monkeypatch.setitem(
        sys.modules,
        "appworld",
        SimpleNamespace(AppWorld=World, update_root=lambda _: None),
    )
    monkeypatch.setattr(episode, "verify_environment", lambda _: {})
    monkeypatch.setattr(episode, "authorize_task", lambda *a: None)
    monkeypatch.setattr(
        episode.importlib.metadata,
        "distribution",
        lambda _: SimpleNamespace(
            read_text=lambda _: json.dumps({"revision": config["appworld_revision"]})
        ),
    )
    monkeypatch.setattr(
        episode,
        "evaluate_isolated",
        lambda *a, **kw: official.append(a[0]) or {"status": "valid", "success": False},
    )
    client = SimpleNamespace(
        usage={"generated_tokens": 0},
        remember=lambda *a: None,
        generate=lambda *a: response("apis.supervisor.complete_task()"),
    )
    target = tmp_path / "execution"
    result = episode.run(
        task["source_task_id"],
        1,
        target,
        tmp_path,
        config,
        client,
        generated_task=task if generated else None,
    )
    trace = read_json(target / "trajectory.json")
    assert (
        trace["initial_state_hash"]
        and trace["original_instruction"] == "Original public instruction"
    )
    assert len(official) == int(not generated)
    if generated:
        assert result["evaluation"]["status"] == "pending_judge"
        assert trace["coevolve_terminal"]["instruction"] == task["instruction"]
        assert task["action_sequence"] not in trace["messages"][1]["content"]
    else:
        assert result["valid"] and result["success"] is False


def test_new_source_lock_and_tamper_rejection(tmp_path):
    assert experiment.verify_source(
        {
            "source_root": str(ROOT),
            "source_lock": "configs/codeact_online_compact_20261120.lock.json",
        }
    )
    with pytest.raises(ValueError, match="source changed"):
        experiment.verify_source({"source_root": str(ROOT)})
    with pytest.raises(ValueError, match="source changed"):
        experiment.verify_source(
            {
                "source_root": str(ROOT),
                "source_lock": "configs/codeact_online_eval_v3_20261119.lock.json",
            }
        )
    with pytest.raises(ValueError, match="source changed"):
        experiment.verify_source(
            {
                "source_root": str(ROOT),
                "source_lock": "configs/codeact_online_updates20_20261120.lock.json",
            }
        )
    from recoveragent.demonstrations import sha256

    p = tmp_path / "module.py"
    p.write_text("original")
    write_json(
        tmp_path / "configs/codeact_online_source_20261119.lock.json",
        {"files": {"module.py": sha256(p)}},
    )
    assert experiment.verify_source({"source_root": str(tmp_path)})
    p.write_text("changed")
    with pytest.raises(ValueError, match="source changed"):
        experiment.verify_source({"source_root": str(tmp_path)})


def test_dev24_resume_keeps_interrupted_attempt_unknown(config, tmp_path, monkeypatch):
    from recoveragent.codeact import inference

    monkeypatch.setattr(inference, "verify_checkpoint", lambda *a: {"sha": "fixed"})
    monkeypatch.setattr(
        inference, "Student", lambda *a: SimpleNamespace(reset_usage=lambda: None)
    )
    config["evaluation_resume_interrupted_as_unknown"] = True
    tasks = inference.evaluation_tasks("dev", "dev24")
    calls = []

    def run(task, attempt, target, root, cfg, client, *, training):
        assert not training
        calls.append(task)
        if task == tasks[0]:
            write_json(
                target / "live.json", {"usage": {"requests": 1}, "agent_seconds": 2}
            )
            raise TimeoutError("Synthetic job deadline")
        result = {
            "task_id": task,
            "success": True,
            "termination": "task_completed",
            "usage": {"requests": 1},
        }
        write_json(target / "result.json", result)
        return result

    monkeypatch.setattr(inference, "run", run)
    args = (config, tmp_path / "dev", "root", "actor", "dev")
    with pytest.raises(TimeoutError):
        inference.evaluate(*args, task_set="dev24")
    inference.evaluate(*args, task_set="dev24")
    assert calls == tasks
    result = read_json(tmp_path / "dev/summary.json")
    assert result["tasks"] == 24 and result["success"] == 23 and result["unknown"] == 1
    assert result["usage"]["requests"] == 24
    assert (
        read_json(tmp_path / "dev" / tasks[0] / "result.json")["termination"]
        == "interrupted_evaluation"
    )


def test_five_round_plan_keeps_runtime_cost_and_inference_budgets():
    previous = read_json(ROOT / "configs/codeact_grpo_coevolve_20261119.json")
    current = read_json(
        ROOT / "configs/codeact_grpo_coevolve_five_rounds_20261119.json"
    )
    assert current["grpo"]["rounds"] == 5 and current["grpo"]["groups_per_round"] == 40
    assert (
        current["online"]["max_groups"] == 200
        and current["online"]["max_updates"] == 100
    )
    assert (
        current["online"]["wall_seconds"] == previous["online"]["wall_seconds"] == 86400
    )
    assert current["online"]["teacher_budget"] == previous["online"]["teacher_budget"]
    assert current["online"]["max_generation_batches"] == 24
    assert current["student_inference"] == previous["student_inference"]
    assert current["limits"] == previous["limits"]
    assert (
        current["online"]["round_evaluation"]["student_inference"]["temperature"] == 0
    )
    assert (
        current["online"]["round_evaluation"]["student_inference"]["record_behavior"]
        is False
    )


def test_allocator_is_set_on_update_launch_only(config, tmp_path, monkeypatch):
    config["grpo"]["cuda_allocator"] = "expandable_segments:True"
    path = tmp_path / "config.json"
    write_json(path, config)
    monkeypatch.setenv("PYTHONPATH", "source")
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "max_split_size_mb:128")
    monkeypatch.delenv("PYTORCH_ALLOC_CONF", raising=False)
    launched = []
    monkeypatch.setattr(
        experiment.subprocess, "Popen", lambda argv, **kw: launched.append(kw["env"])
    )
    dep = {
        "training_python": "train",
        "worker_python": "worker",
        "kernel_overlay": "kernels",
        "deployment_file": "deployment.json",
    }
    for kind in ["update", "sample", "evolve", "evaluate"]:
        experiment.launch(dep, kind, path, "request.json", tmp_path / kind)
    assert launched[0]["PYTORCH_ALLOC_CONF"] == "expandable_segments:True"
    assert "PYTORCH_CUDA_ALLOC_CONF" not in launched[0]
    for env in launched[1:]:
        assert env["PYTORCH_CUDA_ALLOC_CONF"] == "max_split_size_mb:128"
        assert "PYTORCH_ALLOC_CONF" not in env
    # Direct CLI update uses the same idempotent setup before importing torch.
    env = experiment.update_environment(config, {})
    assert experiment.update_environment(config, env) == env


@pytest.mark.parametrize("failure_stage", ["forward", "backward"])
def test_memory_trace_identifies_failure_without_optimizer_update(
    tmp_path, failure_stage
):
    torch = pytest.importorskip("torch")
    from recoveragent.v2_grpo import update_batch

    model = torch.nn.Linear(2, 1)
    optimizer = torch.optim.SGD(model.parameters(), lr=1e-5)
    steps, events = [], []
    optimizer.step = lambda: steps.append(True)

    def compute_loss(*args):
        if failure_stage == "forward":
            raise RuntimeError("simulated allocation failure")
        return model.weight.sum()

    def backward(loss):
        raise RuntimeError("simulated allocation failure")

    trainer = SimpleNamespace(
        model=model,
        optimizer=optimizer,
        beta=0,
        recoveragent_coalesce=False,
        recoveragent_activation_offload=None,
        compute_loss=compute_loss,
        accelerator=SimpleNamespace(backward=backward),
    )
    model.device = torch.device("cpu")
    episodes = [
        {
            "directory": str(tmp_path),
            "task_id": "task",
            "generated_task_id": None,
            "policy": "same",
            "initial_state_hash": "same",
            "success": i == 0,
        }
        for i in range(4)
    ]
    rows = [{"prompt_ids": [1, 2], "completion_ids": [3], "logprobs": [-1.0]}]
    with pytest.raises(RuntimeError, match="simulated allocation"):
        update_batch(
            trainer,
            [episodes],
            segment_loader=lambda *a, **kw: rows,
            observe_memory=events.append,
        )
    assert events[-1]["stage"] == "before_" + failure_stage
    assert events[-1]["sequence_tokens"] == 3 and events[-1]["target_tokens"] == 1
    assert not steps


def test_memory_config_changes_only_allocator_and_measurement():
    old = read_json(ROOT / "configs/codeact_grpo_coevolve_five_rounds_20261119.json")
    new = read_json(ROOT / "configs/codeact_grpo_coevolve_memory_20261120.json")
    assert new["grpo"].pop("cuda_allocator") == "expandable_segments:True"
    assert new["grpo"].pop("record_memory") is True
    assert new == old


def test_update_milestones_keep_legacy_evaluations_separate(config):
    config["online"]["round_evaluation"] = {"every_optimizer_updates": 20}
    state = {
        "groups": ["synthetic"] * 80,
        "optimizer_steps": 18,
        "round_evaluations": [
            {"round": 1, "optimizer_steps": 7},
            {"round": 2, "optimizer_steps": 18},
        ],
    }
    assert experiment.evaluation_due(config, state) is None
    state["optimizer_steps"] = 20
    expected = {
        "evaluation_basis": "optimizer_updates",
        "round": 1,
        "target_updates": 20,
    }
    assert experiment.evaluation_due(config, state) == expected
    state["round_evaluations"].append({**expected, "optimizer_steps": 20})
    assert experiment.evaluation_due(config, state) is None
    state["optimizer_steps"] = 21
    assert experiment.evaluation_due(config, state) is None
    state["optimizer_steps"] = 40
    assert experiment.evaluation_due(config, state)["round"] == 2


def test_update_round_config_keeps_existing_resource_limits():
    old = read_json(ROOT / "configs/codeact_grpo_coevolve_memory_20261120.json")
    new = read_json(ROOT / "configs/codeact_grpo_coevolve_updates20_20261120.json")
    assert new["online"]["round_evaluation"].pop("every_optimizer_updates") == 20
    assert new == old
