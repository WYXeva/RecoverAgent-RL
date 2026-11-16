"""Actual tiny random-model TRL updates; no pretrained model or AppWorld run."""

import copy

import pytest

from test_v2_training_cpu import tiny, tokenizer
from recoveragent import s2_update, v2_training, v2_model
from recoveragent.io import digest, files_manifest, read_json, write_json
from recoveragent.sft_recipe import tensor_hash
from recoveragent.v2_grpo import make_trainer


torch = pytest.importorskip("torch")
peft = pytest.importorskip("peft")
pytest.importorskip("trl")
transformers = pytest.importorskip("transformers")
if not hasattr(transformers, "Qwen3_5ForConditionalGeneration"):
    pytest.skip("Requires the pinned v2 training environment", allow_module_level=True)


def test_fixed_s0_reference_optimizer_resume_and_reloaded_actor(tmp_path, monkeypatch):
    config = read_json("configs/v2_s2_static_evolve_20261116.json")
    identity = config["models"][config["selected_model"]]
    monkeypatch.setattr(v2_training, "load_base", lambda *a, **k: tiny())
    monkeypatch.setattr(v2_model, "load_tokenizer", lambda *a, **k: tokenizer())
    config["lora"].update(r=2, lora_alpha=4, target_modules=["q_proj", "v_proj"])
    model = v2_training.load_trainable(identity, config["lora"], device="cpu")
    parent = tmp_path / "synthetic_S0"
    model.save_pretrained(parent)
    tokenizer().save_pretrained(parent)
    s0_hash = tensor_hash(peft.get_peft_model_state_dict(model))
    write_json(
        parent / "checkpoint_receipt.json",
        {
            "model": identity,
            "adapter_sha256_after": s0_hash,
            "reload_passed": True,
            "files": files_manifest(parent),
            "synthetic_random_model_only": True,
        },
    )
    config["s0"]["adapter_sha256"] = s0_hash

    def groups(actor, prefix):
        current = v2_training.load_trainable(
            identity, config["lora"], actor, device="cpu"
        )
        trainer = make_trainer(
            current, tokenizer(), tmp_path / (prefix + "_trainer"), config["grpo"]
        )
        policy = {
            **identity,
            "checkpoint_id": digest(
                {"adapter_sha256": tensor_hash(peft.get_peft_model_state_dict(current))}
            ),
        }
        result = []
        for g in range(2):
            rows = []
            for i in range(4):
                prompt, completion = [2, 3], [4 + i, 8 + i]
                ids = torch.tensor([prompt + completion])
                current.eval()
                with torch.no_grad():
                    logps, _, _ = trainer._get_per_token_logps_and_entropies(
                        current, ids, torch.ones_like(ids), 2
                    )
                directory = tmp_path / prefix / f"{g}_{i}"
                request = {
                    "policy": policy,
                    "input_token_ids": prompt,
                    "max_input_tokens": 65536,
                    "max_new_tokens": 2,
                }
                response = {
                    "policy": policy,
                    "input_token_ids": prompt,
                    "token_ids": completion,
                    "logprobs": logps[0].tolist(),
                }
                write_json(directory / "session/requests/001.request.json", request)
                write_json(directory / "session/requests/001.response.json", response)
                rows.append(
                    {
                        "task_id": "synthetic",
                        "generated_task_id": "generated" if g else None,
                        "policy": policy,
                        "initial_state_hash": "clean",
                        "success": i == 0,
                        "classification": "success" if i == 0 else "natural_failure",
                        "directory": str(directory),
                    }
                )
            result.append(rows)
        return result

    request = {
        "actor": str(parent),
        "s0": str(parent),
        "groups": groups(parent, "first"),
        "group_ids": [0, 1],
        "optimizer": None,
        "optimizer_steps": 0,
    }
    first = s2_update.update(config, request, tmp_path / "update1", device="cpu")
    assert (
        first["updated"]
        and first["optimizer_steps"] == 1
        and first["actor_sha256"] != s0_hash
    )
    request.update(
        actor=first["actor"],
        optimizer=first["optimizer"],
        optimizer_sha256=first["optimizer_sha256"],
        optimizer_steps=1,
        groups=groups(first["actor"], "second"),
        group_ids=[2, 3],
    )
    second = s2_update.update(config, request, tmp_path / "update2", device="cpu")
    assert (
        second["reference_adapter_sha256"]
        == first["reference_adapter_sha256"]
        == s0_hash
    )
    assert (
        second["actor_sha256"] != first["actor_sha256"]
        and second["optimizer_steps"] == 2
    )
    assert second["contributing_generated_task_ids"] == ["generated"]
    saved = torch.load(second["optimizer"], weights_only=False)
    assert {int(x["step"]) for x in saved["optimizer"]["state"].values()} == {2}
    assert v2_training.verify_checkpoint(second["actor"], identity)["reload_passed"]
    stale = copy.deepcopy(request)
    stale["optimizer_sha256"] = "changed"
    with pytest.raises(ValueError, match="Optimizer file"):
        s2_update.update(config, stale, tmp_path / "bad", device="cpu")
