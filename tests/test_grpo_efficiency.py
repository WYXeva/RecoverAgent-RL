"""CPU token accounting and actual tiny-model loss/gradient equivalence."""

import copy

import pytest

from recoveragent.grpo_segments import coalesce, offload_row


def row(prompt, completion, tag):
    return {
        "prompt_ids": prompt,
        "completion_ids": completion,
        "logprobs": [-2.0] * len(completion),
        "request_sha256": tag,
        "response_sha256": tag + "r",
        "temperature": 0.8,
        "top_p": 1.0,
        "logprob_contract": "processed",
    }


def test_exact_prefix_only_and_all_targets_preserved():
    rows = [
        row([2, 3], [4, 5], "a"),
        row([2, 3, 4, 5, 6, 7], [8], "b"),
        row([9, 3, 4, 5, 6, 7, 8], [10], "changed_schema"),
    ]
    saved = copy.deepcopy(rows)
    merged = coalesce(rows)
    assert rows == saved and len(merged) == 2
    assert merged[0]["completion_ids"] == [4, 5, 6, 7, 8]
    assert merged[0]["tool_mask"] == [1, 1, 0, 0, 1]
    assert merged[0]["logprobs"] == [-2, -2, 0, 0, -2]
    assert [r["request_sha256"] for r in merged[0]["request_refs"]] == ["a", "b"]
    changed = copy.deepcopy(rows[:2])
    changed[1]["temperature"] = 0.5
    assert len(coalesce(changed)) == 2
    assert len(coalesce(rows, max_target_tokens=2)) == 3


def test_explicit_offload_threshold():
    assert offload_row("cpu", 1)
    assert not offload_row(None, 65536)
    assert not offload_row({"cpu_above_tokens": 4096}, 4096)
    assert offload_row({"cpu_above_tokens": 4096}, 4097)
    with pytest.raises(ValueError):
        offload_row({"cpu_above_tokens": -1}, 5)


def test_kernel_check_detects_resolved_fallback_not_only_package_name():
    from recoveragent.grpo_kernels import implementation

    def dispatch(implementation, is_new_implementation):
        def wrapped(*args):
            return implementation(*args) if is_new_implementation else None

        return wrapped

    def backend():
        return None

    assert implementation(dispatch(backend, True))["optimized"] is True
    assert implementation(dispatch(backend, False))["optimized"] is False


@pytest.mark.parametrize("offload", [None, "cpu", {"cpu_above_tokens": 5}])
def test_real_tiny_model_coalescing_preserves_loss_gradients_and_update(
    tmp_path, monkeypatch, offload
):
    torch = pytest.importorskip("torch")
    peft = pytest.importorskip("peft")
    pytest.importorskip("trl")
    from test_v2_training_cpu import tiny, tokenizer

    from recoveragent import v2_grpo
    from recoveragent.v2_grpo import make_trainer, update_batch

    base = peft.get_peft_model(
        tiny(),
        peft.LoraConfig(
            r=2, lora_alpha=4, target_modules=["q_proj", "v_proj"], lora_dropout=0.0
        ),
    )
    settings = {"learning_rate": 1e-5, "temperature": 0.8, "beta": 0.0}
    first = make_trainer(base, tokenizer(), tmp_path / "first", settings)
    originals = []
    for member in range(4):
        rows = [
            row([2, 3], [4 + member, 8], "a"),
            row([2, 3, 4 + member, 8, 11, 12], [13, 14], "b"),
            row([15, 16, 17], [18, 19], "rewrite"),
        ]
        base.eval()
        for r in rows:
            ids = torch.tensor([r["prompt_ids"] + r["completion_ids"]])
            with torch.no_grad():
                lp, _, _ = first._get_per_token_logps_and_entropies(
                    base, ids, torch.ones_like(ids), len(r["completion_ids"])
                )
            r["logprobs"] = lp[0].tolist()
        originals.append(rows)
    episodes = [
        {
            "task_id": "t",
            "generated_task_id": None,
            "initial_state_hash": "initial",
            "policy": {"same": "actor"},
            "success": i == 0,
            "directory": str(i),
        }
        for i in range(4)
    ]
    monkeypatch.setattr(
        v2_grpo,
        "segments",
        lambda directory, *a, **k: copy.deepcopy(originals[int(directory)]),
    )
    independent = peft.get_peft_model(
        tiny(),
        peft.LoraConfig(
            r=2, lora_alpha=4, target_modules=["q_proj", "v_proj"], lora_dropout=0.0
        ),
    )
    independent.load_state_dict(base.state_dict())
    second = make_trainer(
        independent,
        tokenizer(),
        tmp_path / "second",
        {**settings, "coalesce_exact_prefixes": True, "activation_offload": offload},
    )
    gradients = []

    def observe(model):
        gradients.append(
            {n: p.grad.clone() for n, p in model.named_parameters() if p.requires_grad}
        )

    a = update_batch(first, [episodes], max_length=128, observe_gradients=observe)
    b = update_batch(second, [episodes], max_length=128, observe_gradients=observe)
    from recoveragent.grpo_compare import gradient_comparison

    comparison = gradient_comparison(gradients[0], gradients[1])
    # Optional private numerical receipt; no test fixtures are published as real runs.
    import os

    from recoveragent.io import write_json

    if os.environ.get("RECOVERAGENT_CPU_NUMERIC_OUTPUT"):
        write_json(
            os.environ["RECOVERAGENT_CPU_NUMERIC_OUTPUT"]
            + "."
            + str(offload)
            + ".json",
            {
                "synthetic_random_cpu_model": True,
                "offload": offload,
                "loss_absolute_error": abs(a["loss"] - b["loss"]),
                "unmerged": a,
                "merged": b,
                "preclip_gradients": comparison,
            },
        )
    assert a["loss"] == pytest.approx(b["loss"], abs=2e-6)
    assert a["ratio_token_count"] == b["ratio_token_count"] == 24
    assert a["sequence_work"]["training_segments"] == 12
    assert b["sequence_work"]["training_segments"] == 8
    assert b["sequence_work"]["cpu_offloaded_segments"] == (
        8 if offload == "cpu" else 4 if offload else 0
    )
    for name in gradients[0]:
        torch.testing.assert_close(
            gradients[0][name], gradients[1][name], atol=2e-6, rtol=2e-4
        )
    for a, b in zip(first.model.parameters(), second.model.parameters(), strict=True):
        torch.testing.assert_close(a, b, atol=2e-6, rtol=2e-4)


def test_diagnostic_constant_group_backward_without_optimizer_step(
    tmp_path, monkeypatch
):
    pytest.importorskip("torch")
    peft = pytest.importorskip("peft")
    from test_v2_training_cpu import tiny, tokenizer

    from recoveragent import v2_grpo
    from recoveragent.sft_recipe import tensor_hash
    from recoveragent.v2_grpo import make_trainer, update_batch

    model = peft.get_peft_model(
        tiny(),
        peft.LoraConfig(
            r=2, lora_alpha=4, target_modules=["q_proj", "v_proj"], lora_dropout=0
        ),
    )
    trainer = make_trainer(
        model,
        tokenizer(),
        tmp_path,
        {
            "learning_rate": 1e-5,
            "temperature": 0.8,
            "beta": 0,
            "coalesce_exact_prefixes": True,
        },
    )
    episodes = [
        {
            "task_id": "synthetic",
            "generated_task_id": None,
            "policy": "fixed",
            "initial_state_hash": "same",
            "success": False,
            "directory": str(i),
        }
        for i in range(4)
    ]
    monkeypatch.setattr(
        v2_grpo,
        "segments",
        lambda *a, **k: [row([2, 3], [4, 5], "one"), row([2, 3, 4, 5, 6], [7], "two")],
    )
    before = tensor_hash(peft.get_peft_model_state_dict(model))
    observed = []
    result = update_batch(
        trainer,
        [episodes],
        max_length=128,
        optimizer_step=False,
        measure_constant=True,
        observe_gradients=lambda m: observed.append(True),
    )
    assert result["backward_executed"] and not result["updated"]
    assert result["loss"] == 0 and result["l2_norm"] == 0 and observed == [True]
    assert trainer.state.global_step == 0 and not trainer.optimizer.state
    assert tensor_hash(peft.get_peft_model_state_dict(model)) == before
    with pytest.raises(ValueError, match="diagnostic only"):
        update_batch(trainer, [episodes], measure_constant=True)
