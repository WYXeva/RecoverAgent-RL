import pytest

from recoveragent import m1_inference_probe, m1_vgpu_inference, m1_vgpu_resume
from recoveragent.io import read_json, write_json
from recoveragent.m1_vgpu_runtime_v2 import child_command, modules, publish_json


@pytest.mark.parametrize(
    "name", ["progress.json", "active.json", "artifact_index.json", "summary.json"]
)
def test_live_status_preserves_each_snapshot(tmp_path, name):
    path = tmp_path / name
    for index in range(3):
        publish_json(path, {"index": index})
        assert read_json(path) == {"index": index}
    history = list((tmp_path / "status_history" / path.stem).glob("*.json"))
    assert sorted(read_json(p)["index"] for p in history) == [0, 1, 2]


def test_task_evidence_remains_create_once(tmp_path):
    path = tmp_path / "full732_result.json"
    publish_json(path, {"value": 1})
    with pytest.raises(FileExistsError):
        publish_json(path, {"value": 2})
    assert read_json(path) == {"value": 1}


def test_child_dispatch_and_frozen_modules_unchanged():
    probe, inference, resume = modules()
    assert probe.write_json is publish_json
    assert resume.write_json is publish_json
    assert inference.probe is probe
    assert resume.inference is inference
    assert m1_inference_probe.write_json is write_json
    assert m1_vgpu_resume.write_json is write_json
    assert m1_vgpu_inference.probe is m1_inference_probe
    prefix = ["python", "-m"]
    assert child_command(prefix + ["recoveragent.m1_inference_probe", "--worker"])[
        2:
    ] == ["recoveragent.m1_vgpu_runtime_v2", "probe", "--worker"]
    assert child_command(prefix + ["recoveragent.m1_vgpu_inference", "serve"])[2:] == [
        "recoveragent.m1_vgpu_runtime_v2",
        "serve",
    ]
    assert child_command(["/usr/bin/shutdown"]) == ["/usr/bin/shutdown"]
