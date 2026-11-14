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
def current_v2_unit_lock(tmp_path, monkeypatch):
    """Test historical Teacher behavior without re-signing its release lock."""
    from recoveragent import v2_runtime

    lock = read_json(v2_runtime.LOCK)
    names = set(lock["files"]) | {
        str(p.relative_to(v2_runtime.ROOT))
        for p in (v2_runtime.ROOT / "src/recoveragent").glob("*.py")
    }
    lock["files"] = {name: sha256(v2_runtime.ROOT / name) for name in names}
    path = tmp_path / "synthetic_v2_unit_lock.json"
    write_json(path, lock)
    monkeypatch.setattr(v2_runtime, "LOCK", path)


@pytest.fixture
def current_s1_unit_lock(tmp_path, monkeypatch):
    """Exercise frozen S1 semantics in a temporary tree after Teacher-only changes.

    This never updates a production lock or the separately running S1 checkout.
    """
    from recoveragent import v2_runtime

    original = v2_runtime.ROOT
    root = tmp_path / "synthetic_s1_source_tree"
    lock_name = f"configs/{v2_runtime.S1_RUNTIME}_lock.json"
    lock = read_json(original / lock_name)
    names = set(lock["files"]) | {
        str(p.relative_to(original))
        for p in (original / "src/recoveragent").glob("*.py")
    }
    for name in names | {
        str(p.relative_to(original)) for p in (original / "configs").glob("*_lock.json")
    }:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original / name, target)
    lock["files"] = {name: sha256(root / name) for name in names}
    (root / lock_name).unlink()
    write_json(root / lock_name, lock)
    monkeypatch.setattr(v2_runtime, "ROOT", root)
    monkeypatch.setattr(
        v2_runtime, "CONFIG", root / v2_runtime.CONFIG.relative_to(original)
    )


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
            # Test-only manifests for transport diagnostics and P0.1/P0.2's explicit
            # schema opt-in wiring. Production source locks remain untouched;
            # separate tests require those historical locks to reject this tree.
            changed = {
                name
                for name, expected in lock["files"].items()
                if sha256(name) != expected
            }
            assert changed <= {
                "src/recoveragent/teacher.py",
                "src/recoveragent/adapter.py",
                "src/recoveragent/p3a.py",
                "src/recoveragent/evaluation.py",
                "src/recoveragent/research_access.py",
                "src/recoveragent/state.py",
                "src/recoveragent/student.py",
                "src/recoveragent/demonstrations.py",
                "src/recoveragent/harness.py",
                "src/recoveragent/answer_guard.py",
            }
            lock["files"] = {name: sha256(name) for name in lock["files"]}
            lock["revision"] = "SYNTHETIC_UNIT_TEST_ONLY"
            home = tmp_path / "synthetic_campaign_locks" / str(len(bound))
            write_json(home / "lock.json", lock)
            config["protocol_lock"] = str(home / "lock.json")
            write_json(home / "config.json", config)
            bound[key] = home / "config.json"
        return original(bound[key])

    monkeypatch.setattr(fresh, "campaign_config", current)


@pytest.fixture
def current_nullable_unit_lock(tmp_path, monkeypatch):
    """Temporary test tree only; never re-sign a released P0.2 runtime lock."""
    from recoveragent import nullable_recovery_runtime as runtime

    root = tmp_path / "synthetic_nullable_source_tree"
    original = runtime.ROOT
    lock = read_json(original / runtime.LOCK)
    for name in lock["files"]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original / name, target)
        lock["files"][name] = sha256(target)
    write_json(root / runtime.LOCK, lock)
    monkeypatch.setattr(runtime, "ROOT", root)
    return root


@pytest.fixture
def current_b10_preparation_unit_lock(current_nullable_unit_lock, monkeypatch):
    from scripts import prepare_b10_recovery_pilot as pilot

    root = current_nullable_unit_lock
    lock = read_json(pilot.ROOT / pilot.LOCK)
    for name in lock["files"]:
        target = root / name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pilot.ROOT / name, target)
        lock["files"][name] = sha256(target)
    write_json(root / pilot.LOCK, lock)
    monkeypatch.setattr(pilot, "ROOT", root)
