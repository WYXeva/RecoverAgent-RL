"""Synthetic CPU integration; no paid models or AppWorld task execution."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from recoveragent.codeact import evolution, experiment, online, protocol
from recoveragent.coevolve_llm import candidate, admit
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
    from test_v2_training_cpu import tiny, tokenizer
    from test_grpo_efficiency import row
    from recoveragent.v2_grpo import make_trainer, update_batch
    from recoveragent.grpo_compare import gradient_comparison

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
    y = update_batch(
        second,
        [episodes],
        max_length=128,
        segment_loader=loader,
        observe_gradients=observe,
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


def test_controller_effective_accumulation_new_pool_and_resume(
    config, tmp_path, monkeypatch
):
    config["online"].update(max_groups=10, s0_sha256="s0")
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
        else:
            assert len(req["contexts"]) <= 4
            result = {"accepted": [task], "attempts": [{"accepted": True}]}
        write_json(Path(output) / "result.json", result)
        return SimpleNamespace(wait=lambda **kw: 0, poll=lambda: 0, returncode=0)

    monkeypatch.setattr(experiment, "launch", launch)
    root = tmp_path / "run"
    experiment.experiment(config, dep, "unused", root)
    state = read_json(root / "state.json")
    assert state["status"] == "complete" and len(state["groups"]) == 10
    assert state["optimizer_steps"] == 4 and len(state["consumed"]) == 7
    assert not state["pending"] and len(set(state["consumed"])) == 7
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
    from recoveragent.codeact import episode
    from test_codeact_reboot import response

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
    assert experiment.verify_source({"source_root": str(ROOT)})
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
