import importlib.util
from pathlib import Path

import pytest

from recoveragent.io import write_json


spec = importlib.util.spec_from_file_location(
    "finish_v2_interrupted",
    Path(__file__).resolve().parents[1] / "scripts/finish_v2_interrupted.py",
)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


@pytest.fixture
def source(tmp_path):
    write_json(tmp_path / "round_summary.json", {"error_type": "OutOfMemoryError"})
    write_json(
        tmp_path / "S2_grpo/updates/000.json",
        {
            "batch": 0,
            "update": {
                "updated": True,
                "gradient_tensors": 2,
                "l2_norm": 1.0,
                "loss": -0.1,
            },
        },
    )
    checkpoint = tmp_path / "S2_grpo/update_01"
    checkpoint.mkdir()
    (checkpoint / "adapter_model.safetensors").write_bytes(b"synthetic")
    for group in (2, 3):
        for member in range(4):
            write_json(
                tmp_path
                / "rollouts"
                / f"group_{group:02d}_member_{member}/result.json",
                {"policy": {"checkpoint_id": "saved-policy"}},
            )
    return tmp_path


def test_only_completed_saved_update_is_selected(source):
    rows, steps, checkpoint, pending = entry.completed_updates(source)
    assert len(rows) == steps == 1
    assert checkpoint.name == "update_01"
    assert len(pending) == 8
    assert not (source / "S2_grpo/checkpoint_receipt.json").exists()


@pytest.mark.parametrize(
    "damage",
    [
        "not_oom",
        "missing_adapter",
        "extra_checkpoint",
        "missing_rollout",
        "wrong_policy",
        "nonfinite",
        "zero_gradient",
        "missing_batch",
    ],
)
def test_incomplete_or_inconsistent_sources_fail_closed(source, damage):
    if damage == "not_oom":
        (source / "round_summary.json").unlink()
        write_json(source / "round_summary.json", {"error_type": "TimeoutError"})
    elif damage == "missing_adapter":
        (source / "S2_grpo/update_01/adapter_model.safetensors").unlink()
    elif damage == "extra_checkpoint":
        (source / "S2_grpo/update_02").mkdir()
    elif damage == "missing_rollout":
        (source / "rollouts/group_03_member_3/result.json").unlink()
    elif damage == "wrong_policy":
        (source / "rollouts/group_03_member_3/result.json").unlink()
        write_json(
            source / "rollouts/group_03_member_3/result.json",
            {"policy": {"checkpoint_id": "different"}},
        )
    else:
        row = {
            "batch": 1 if damage == "missing_batch" else 0,
            "update": {
                "updated": True,
                "gradient_tensors": 2,
                "l2_norm": 0
                if damage == "zero_gradient"
                else float("nan")
                if damage == "nonfinite"
                else 1,
                "loss": 0.1,
            },
        }
        # JSON accepts NaN in this synthetic malformed receipt; admission must not.
        import json

        (source / "S2_grpo/updates/000.json").write_text(json.dumps(row))
    with pytest.raises(ValueError):
        entry.completed_updates(source)
