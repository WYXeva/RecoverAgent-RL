"""Software gate tests, never evidence of model training."""

import hashlib
import json
import sys
from types import SimpleNamespace

import pytest
from scripts import run_p3b_training as driver


@pytest.mark.parametrize("fail_reload", [True, False])
def test_formal_training_requires_both_verified_smokes(
    tmp_path, monkeypatch, fail_reload
):
    data = tmp_path / "data"
    data.mkdir()
    (data / "manifest.json").write_text("{}")
    sha = hashlib.sha256(b"{}").hexdigest()
    tokens = tmp_path / "tokens.json"
    tokens.write_text(
        json.dumps(
            {"status": "PASS", "byte_identical": True, "source_manifest_sha256": sha}
        )
    )
    output = tmp_path / "run"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "driver",
            "--source",
            str(tmp_path),
            "--python",
            sys.executable,
            "--dataset",
            str(data),
            "--model-cache",
            str(tmp_path),
            "--retokenization-report",
            str(tokens),
            "--expected-manifest-sha256",
            sha,
            "--output",
            str(output),
        ],
    )
    calls = []

    def launch(command, **kwargs):
        calls.append(command)
        report = command[command.index("--report") + 1]
        driver.write(
            report,
            {
                "status": "PASS",
                "parameter_update_and_reload_pass": not fail_reload,
                "checkpoint_reloaded": not fail_reload,
                "dataset_manifest_sha256": sha,
                "gradient_checks": [{}],
            },
        )
        return SimpleNamespace(poll=lambda: 0, returncode=0)

    monkeypatch.setattr(driver.subprocess, "Popen", launch)
    if fail_reload:
        with pytest.raises(ValueError, match="gate failed"):
            driver.main()
        assert len(calls) == 1
        assert driver.read(output / "summary.json")["status"] == "FAILED"
    else:
        driver.main()
        assert [
            driver.read(output / "summary.json")["stages"][i]["stage"] for i in range(3)
        ] == ["short_smoke", "length_smoke", "formal"]
        assert len(calls) == 3
    with pytest.raises(FileExistsError):
        driver.main()
