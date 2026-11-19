"""Small before-update check using the exact normal rollout actor and tokens."""

from .v2_grpo import segments


def audit_behavior(trainer, groups, settings, *, segment_loader=None):
    import torch

    model = trainer.model
    model.eval()
    checks = []
    for episode in groups[0]:
        for row in (segment_loader or segments)(
            episode["directory"], episode["policy"], max_length=65536
        ):
            if (
                row["temperature"] != trainer.temperature
                or row["top_p"] != 1.0
                or row["logprob_contract"]
                != "vllm_processed_logprobs_temperature_top_p1_top_k_all"
            ):
                raise ValueError(
                    "Behavior and trainer sampling probability definitions differ"
                )
            n = min(len(row["completion_ids"]), settings["tokens_per_segment"])
            if not n:
                continue
            ids = torch.tensor(
                [row["prompt_ids"] + row["completion_ids"][:n]], device=model.device
            )
            with torch.no_grad():
                actual, _, _ = trainer._get_per_token_logps_and_entropies(
                    model, ids, torch.ones_like(ids), n
                )
            recorded = torch.tensor([row["logprobs"][:n]], device=model.device)
            delta = actual.float() - recorded
            ratio = delta.exp()
            finite = bool(torch.isfinite(delta).all() and torch.isfinite(ratio).all())
            checks.append(
                {
                    "policy": episode["policy"],
                    "directory": episode["directory"],
                    "request_sha256": row["request_sha256"],
                    "response_sha256": row["response_sha256"],
                    "prompt_tokens": len(row["prompt_ids"]),
                    "completion_token_ids": row["completion_ids"][:n],
                    "behavior_logprobs": row["logprobs"][:n],
                    "trainer_logprobs": actual[0].tolist(),
                    "finite": finite,
                    "mean_abs_logprob_error": float(delta.abs().mean())
                    if finite
                    else None,
                    "max_abs_logprob_error": float(delta.abs().max())
                    if finite
                    else None,
                    "ratio_mean": float(ratio.mean()) if finite else None,
                    "ratio_min": float(ratio.min()) if finite else None,
                    "ratio_max": float(ratio.max()) if finite else None,
                }
            )
            if len(checks) >= settings["segments"]:
                break
        if len(checks) >= settings["segments"]:
            break
    passed = bool(checks) and all(
        c["finite"]
        and c["mean_abs_logprob_error"] <= settings["max_mean_abs_error"]
        and c["max_abs_logprob_error"] <= settings["max_abs_error"]
        for c in checks
    )
    return {
        "passed": passed,
        "settings": settings,
        "temperature": trainer.temperature,
        "top_p": 1.0,
        "top_k": "all",
        "same_actor_before_update": True,
        "checks": checks,
    }
