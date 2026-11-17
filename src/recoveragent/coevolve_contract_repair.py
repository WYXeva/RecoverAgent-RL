"""One durable contract-repair allowance per candidate, shared across admission."""

from pathlib import Path

from .coevolve_goal import ABSTRACTION_CONTRACT, ScoringGap, actions, tables, validate
from .coevolve_goal_schema import validate_fields
from .io import digest, read_json, write_json
from .s2_state import evidence


def checked(spec, metadata):
    try:
        validate(spec)
        validate_fields(spec, metadata)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ScoringGap(str(exc)) from exc


def repair(engine, source, proposal, target, metadata, gap):
    target = Path(target)
    claim = target / "contract_repair_claim.json"
    repaired = target / "repaired_proposal.json"
    identity = {
        "proposal_sha256": digest(proposal),
        "metadata_sha256": digest(metadata),
    }
    if claim.exists():
        prior = read_json(claim)
        if any(prior.get(k) != v for k, v in identity.items()):
            raise ScoringGap("Contract repair candidate identity changed")
        if repaired.exists():
            return read_json(repaired)
        raise ScoringGap(
            "Contract repair allowance already consumed; response unavailable"
        )
    # Persist BEFORE calling a model. A crash/unknown response does not grant another call.
    write_json(claim, {**identity, "count": 1, "first_gap": str(gap)})
    try:
        result = engine.text(
            source,
            target / "contract_repair",
            ABSTRACTION_CONTRACT
            + " Return JSON {success_conditions: ...}. Preserve the supplied public goal and ALL "
            "requirements exactly. Repair scoring, never weaken conditions to fit the Solver. "
            "Use only the supplied model metadata; it contains no records or hidden answers.",
            {"proposal": proposal, "scoring_gap": str(gap), "model_metadata": metadata},
        )
        if not isinstance(result, dict):
            raise ScoringGap("Contract repair response must be an object")
        fixed = {**proposal, "success_conditions": result.get("success_conditions")}
        # Retain even an invalid repair; it must not acquire a second allowance.
        write_json(repaired, fixed)
        checked(fixed["success_conditions"], metadata)
        write_json(
            target / "contract_repair_result.json",
            {"count": 1, "status": "contract_valid", "proposal_sha256": digest(fixed)},
        )
        return fixed
    except (ValueError, KeyError, TypeError, RuntimeError) as exc:
        write_json(
            target / "contract_repair_result.json",
            {"count": 1, "status": "deferred_scoring", "reason": str(exc)},
        )
        raise ScoringGap(str(exc)) from exc


def reuse_reference(old_task, new_task, reference_path, metadata, output):
    """Rescore immutable real evidence only; otherwise leave validation pending."""
    if (
        old_task["instruction"] != new_task["instruction"]
        or old_task["success_conditions"]["output"]
        != new_task["success_conditions"]["output"]
    ):
        raise ScoringGap(
            "Repaired public output changed; pending fresh Solver validation"
        )
    path = Path(reference_path)
    if not path.exists():
        raise ScoringGap(
            "Repaired contract pending validation: no complete Solver evidence"
        )
    reference = read_json(path)
    observed = reference.get("goal_evidence") or {}
    needed = set(tables(new_task["success_conditions"]))
    if (
        not (
            reference.get("status") == "valid"
            or (
                reference.get("error_type") == "ScoringGap"
                and reference.get("scoring_gap")
                and not reference.get("interruption")
                and not reference.get("provider_error")
            )
        )
        or not reference.get("initial_state_hash")
        or not isinstance(reference.get("messages"), list)
        or not isinstance(observed.get("actions"), list)
        or not needed <= observed.get("before", {}).keys()
        or not needed <= observed.get("after", {}).keys()
        or observed.get("instruction") != new_task["instruction"]
        or not isinstance(reference.get("coevolve_terminal"), dict)
        or any(
            reference["coevolve_terminal"].get(k) != observed.get(k)
            for k in ("instruction", "status", "answer")
        )
    ):
        raise ScoringGap(
            "Repaired contract pending validation: insufficient initial/final/action evidence"
        )
    if actions(reference["messages"]) != observed["actions"]:
        raise ScoringGap(
            "Repaired contract pending validation: action evidence differs from actual history"
        )
    checked(new_task["success_conditions"], metadata)
    evidence(
        Path(output) / "reference_reuse.json",
        {
            "reference_path": str(path),
            "reference_sha256": digest(reference),
            "prior_task_sha256": digest(old_task),
            "repaired_task_sha256": digest(new_task),
            "public_input_unchanged": True,
            "original_trajectory_modified": False,
        },
    )
    return path
