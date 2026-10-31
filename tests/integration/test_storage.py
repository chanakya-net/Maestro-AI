import json
from pathlib import Path

import pytest

from ai_skills.storage import load_json, write_json_atomic


def test_missing_is_distinct_from_malformed_authoritative_json(tmp_path):
    path = tmp_path / "state.json"
    assert load_json(path, missing=None) is None
    with pytest.raises(FileNotFoundError):
        load_json(path)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_json(path, missing={})


def test_atomic_json_roundtrip(tmp_path):
    path = tmp_path / "nested" / "state.json"
    write_json_atomic(path, {"count": 1})
    assert load_json(path) == {"count": 1}
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("failure", ["replace", "fsync", "serialization"])
def test_failed_replace_preserves_original_and_cleans_temporary(tmp_path, monkeypatch, failure):
    import ai_skills.storage as storage
    path = tmp_path / "state.json"
    write_json_atomic(path, {"count": 1})
    if failure != "serialization":
        def fail(*args):
            raise OSError("injected disk error")
        monkeypatch.setattr(storage.os, failure, fail)
    with pytest.raises((OSError, TypeError)):
        write_json_atomic(path, {"count": object() if failure == "serialization" else 2})
    assert load_json(path) == {"count": 1}
    assert list(tmp_path.iterdir()) == [path]


def test_transaction_commits_mutation_and_returns_result(tmp_path):
    from ai_skills.storage import StateStore
    path = tmp_path / "state.json"
    def update(state):
        state["count"] = state.get("count", 0) + 1
        return "result"
    assert StateStore(path).transaction(update) == "result"
    assert load_json(path) == {"count": 1}


import os
import socket
import subprocess
import sys
import time
import psutil
from ai_skills.storage import DirectoryLock, LockTimeoutError, StateStore


def test_concurrent_transactions_preserve_both_updates(tmp_path):
    path = tmp_path / "state.json"
    script = '''
import sys, time
from pathlib import Path
from ai_skills.storage import StateStore
store = StateStore(Path(sys.argv[1]))
for _ in range(25):
    def update(state):
        count = state.get('count', 0)
        time.sleep(.002)
        state['count'] = count + 1
    store.transaction(update)
'''
    writers = [subprocess.Popen([sys.executable, "-c", script, str(path)]) for _ in range(3)]
    for writer in writers:
        assert writer.wait(timeout=20) == 0
    assert load_json(path)["count"] == 75


def test_failed_mutation_does_not_commit_and_releases_lock(tmp_path):
    path = tmp_path / "state.json"
    write_json_atomic(path, {"count": 1})
    def fail(state):
        state["count"] = 2
        raise RuntimeError("update failed")
    with pytest.raises(RuntimeError):
        StateStore(path).transaction(fail)
    assert load_json(path) == {"count": 1}
    StateStore(path).transaction(lambda state: state.update(count=3))
    assert load_json(path)["count"] == 3


@pytest.mark.parametrize("value", [[], None])
def test_invalid_authoritative_state_not_replaced(tmp_path, value):
    path = tmp_path / "state.json"
    write_json_atomic(path, value)
    with pytest.raises(ValueError):
        StateStore(path).transaction(lambda state: state.update(count=2))
    assert load_json(path) == value


def test_crash_left_lock_reclaimed_only_after_identity_check(tmp_path):
    path = tmp_path / "state.json"
    script = '''
import os, sys
from pathlib import Path
from ai_skills.storage import DirectoryLock
lock = DirectoryLock(Path(sys.argv[1]))
lock.__enter__()
os._exit(0)
'''
    subprocess.run([sys.executable, "-c", script, str(path)], check=True, timeout=5)
    StateStore(path, timeout_seconds=.2).transaction(lambda state: state.update(recovered=True))
    assert load_json(path)["recovered"] is True
    assert not Path(f"{path}.lock").exists()


def test_legacy_domain_schema_is_preserved_and_validator_controls_versions(tmp_path):
    path = tmp_path / "state.json"
    legacy = load_json(Path(__file__).parents[1] / "fixtures" / "legacy_states" / "main-state-v4.json")
    write_json_atomic(path, legacy)
    StateStore(path).transaction(lambda state: state.update(count=1))
    assert load_json(path) == {**legacy, "count": 1}
    def require_new_schema(state):
        if state.get("schema_version") != 1:
            raise ValueError("unsupported domain schema")
    with pytest.raises(ValueError, match="domain schema"):
        StateStore(path, validator=require_new_schema).transaction(lambda state: state.clear())
    assert load_json(path) == {**legacy, "count": 1}


def _owner(**overrides):
    return {"host": socket.gethostname(), "pid": os.getpid(), "create_time": psutil.Process().create_time(), "token": "original", **overrides}


@pytest.mark.parametrize("owner", [
    _owner(), _owner(host="foreign-host"), _owner(create_time=None),
    _owner(pid=999_999_999, create_time=None), _owner(pid=True),
    {"pid": 999_999_999}, None, "{broken",
])
def test_foreign_or_unknown_lock_owner_is_not_reaped(tmp_path, owner):
    path = tmp_path / "state.json"
    lock_path = Path(f"{path}.lock")
    lock_path.mkdir()
    if owner == "{broken":
        (lock_path / "owner.json").write_text(owner)
    elif owner is not None:
        write_json_atomic(lock_path / "owner.json", owner)
    started = time.monotonic()
    with pytest.raises(LockTimeoutError):
        with DirectoryLock(path, timeout_seconds=.05):
            pytest.fail("acquired an owned or unknown lock")
    assert time.monotonic() - started < 1
    assert lock_path.exists()
    if owner is not None and owner != "{broken":
        assert load_json(lock_path / "owner.json") == owner


def test_release_never_deletes_replacement_lock(tmp_path):
    path = tmp_path / "state.json"
    original = DirectoryLock(path)
    original.__enter__()
    moved = tmp_path / "old-lock"
    original.path.rename(moved)
    with DirectoryLock(path) as replacement:
        original.__exit__(None, None, None)
        assert load_json(replacement.path / "owner.json")["token"] == replacement.token
    assert not replacement.path.exists()


def test_pid_reuse_is_a_proven_stale_owner(tmp_path):
    path = tmp_path / "state.json"
    lock_path = Path(f"{path}.lock")
    lock_path.mkdir()
    write_json_atomic(lock_path / "owner.json", _owner(create_time=psutil.Process().create_time() - 1000))
    with DirectoryLock(path, timeout_seconds=.2):
        assert load_json(lock_path / "owner.json")["token"] != "original"


def test_simultaneous_reapers_do_not_delete_newly_acquired_lock(tmp_path):
    path = tmp_path / "state.json"
    lock_path = Path(f"{path}.lock")
    lock_path.mkdir()
    write_json_atomic(lock_path / "owner.json", _owner(create_time=psutil.Process().create_time() - 1000))
    script = '''
import sys, time
from pathlib import Path
from ai_skills.storage import StateStore
for _ in range(10):
    def update(state):
        count = state.get('count', 0)
        time.sleep(.005)
        state['count'] = count + 1
    StateStore(Path(sys.argv[1])).transaction(update)
'''
    writers = [subprocess.Popen([sys.executable, "-c", script, str(path)]) for _ in range(6)]
    for writer in writers:
        assert writer.wait(timeout=20) == 0
    assert load_json(path)["count"] == 60
    assert not lock_path.exists()


def test_lock_metadata_failure_cleans_created_directory(tmp_path, monkeypatch):
    import ai_skills.storage as storage
    path = tmp_path / "state.json"
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(storage.os, "fsync", fail)
    with pytest.raises(OSError):
        with DirectoryLock(path):
            pytest.fail("metadata was not persisted")
    assert list(tmp_path.iterdir()) == []


def test_transaction_preserves_malformed_authoritative_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        StateStore(path).transaction(lambda state: state.update(count=1))
    assert path.read_text() == "not-json"
    assert not Path(f"{path}.lock").exists()


def test_domain_validation_rejects_invalid_mutation_before_commit(tmp_path):
    path = tmp_path / "state.json"
    write_json_atomic(path, {"schema_version": 1})
    def validator(state):
        if state.get("schema_version") != 1:
            raise ValueError("unsupported domain schema")
    with pytest.raises(ValueError):
        StateStore(path, validator=validator).transaction(lambda state: state.update(schema_version=2))
    assert load_json(path) == {"schema_version": 1}


@pytest.mark.skipif(os.name != "posix", reason="Unreaped zombies are a POSIX lifecycle state")
def test_unreaped_dead_owner_does_not_block_recovery(tmp_path):
    path = tmp_path / "state.json"
    script = '''
import os, sys
from pathlib import Path
from ai_skills.storage import DirectoryLock
DirectoryLock(Path(sys.argv[1])).__enter__()
os._exit(0)
'''
    owner = subprocess.Popen([sys.executable, "-c", script, str(path)])
    try:
        deadline = time.monotonic() + 5
        while psutil.Process(owner.pid).status() != psutil.STATUS_ZOMBIE:
            assert time.monotonic() < deadline, "fixture owner did not exit"
            time.sleep(.01)
        # Deliberately do not reap until after recovery: the parent can outlive
        # a crashed worker and leave its PID present even though it cannot write.
        StateStore(path, timeout_seconds=.2).transaction(lambda state: state.update(recovered=True))
        assert load_json(path) == {"recovered": True}
    finally:
        owner.wait(timeout=5)
