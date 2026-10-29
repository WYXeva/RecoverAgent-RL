"""Test-only manifests for protocol behavior after intentional source revisions.

These temporary fixtures are NOT released runtime locks or execution permission.
Separate tests assert immutable production locks reject the changed checkout.
"""

import copy
from pathlib import Path
import shutil

import pytest

from recoveragent.demonstrations import sha256
from recoveragent.io import read_json, write_json


@pytest.fixture
def current_v6_unit_lock(tmp_path, monkeypatch):
    from recoveragent import student

    root = tmp_path / "synthetic_student_source_tree"
    lock = read_json(student.ROOT / student.V6_LOCK)
    for name in lock["files"]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(student.ROOT / name, target)
        lock["files"][name] = sha256(target)
    write_json(root / student.V6_LOCK, lock)
    monkeypatch.setattr(student, "ROOT", root)
    return root


@pytest.fixture
def current_teacher_unit_lock(tmp_path):
    def bind(config):
        config = copy.deepcopy(config)
        lock = read_json(config["protocol_lock"])
        lock["files"] = {name: sha256(name) for name in lock["files"]}
        if "frozen_parameters" in lock:
            lock["frozen_parameters"] = {
                section: copy.deepcopy(config[section])
                for section in ("teacher", "limits")
            }
        path = tmp_path / "synthetic_teacher_lock.json"
        write_json(path, lock)
        config["protocol_lock"] = str(Path(path).resolve())
        return config

    return bind


@pytest.fixture
def current_fresh_campaign_unit_locks(tmp_path, monkeypatch):
    """Exercise legacy scheduling with synthetic locks, not historical runtime claims."""
    from recoveragent import fresh_teacher as fresh

    original = fresh.campaign_config
    bound = {}

    def current(path=fresh.DEFAULT):
        key = str(Path(path).resolve())
        if key not in bound:
            config = read_json(path)
            lock = read_json(config["protocol_lock"])
            # Only the deliberately revised transport diagnostics may differ.
            changed = {
                name
                for name, expected in lock["files"].items()
                if sha256(name) != expected
            }
            assert changed <= {"src/recoveragent/teacher.py"}
            lock["files"] = {name: sha256(name) for name in lock["files"]}
            lock["revision"] = "SYNTHETIC_UNIT_TEST_ONLY"
            home = tmp_path / "synthetic_campaign_locks" / str(len(bound))
            write_json(home / "lock.json", lock)
            config["protocol_lock"] = str(home / "lock.json")
            write_json(home / "config.json", config)
            bound[key] = home / "config.json"
        return original(bound[key])

    monkeypatch.setattr(fresh, "campaign_config", current)
