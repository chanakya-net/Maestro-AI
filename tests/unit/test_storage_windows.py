"""Deterministic Windows sharing races at the real filesystem boundary."""
import errno
import json
import sys
from pathlib import Path

import pytest

from ai_skills import storage


def sharing_error(winerror=None):
    error = PermissionError(errno.EACCES, "injected sharing/access denial")
    if winerror is not None:
        error.winerror = winerror
    return error


@pytest.fixture
def windows_io(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    sleeps = []
    monkeypatch.setattr(storage.time, "sleep", sleeps.append)
    return sleeps


@pytest.mark.parametrize("winerror", [None, 5, 32, 33, 303])
def test_json_read_recovers_from_windows_sharing_race(tmp_path, monkeypatch, windows_io, winerror):
    path = tmp_path / "state.json"
    path.write_text('{"count": 7}', encoding="utf-8")
    original_open = Path.open
    failures = [sharing_error(winerror), sharing_error(winerror)]

    def briefly_denied(candidate, *args, **kwargs):
        if candidate == path and failures:
            raise failures.pop()
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", briefly_denied)
    assert storage.load_json(path, missing={}) == {"count": 7}


@pytest.mark.parametrize("writer", ["json", "text"])
def test_atomic_writer_recovers_from_windows_sharing_race(tmp_path, monkeypatch, windows_io, writer):
    path = tmp_path / "state.json"
    path.write_text("previous content", encoding="utf-8")
    original_replace = storage.os.replace
    failures = [sharing_error(32), sharing_error(5)]

    def briefly_denied(source, target):
        if failures:
            assert path.read_text(encoding="utf-8") == "previous content"
            raise failures.pop()
        return original_replace(source, target)

    monkeypatch.setattr(storage.os, "replace", briefly_denied)
    if writer == "json":
        storage.write_json_atomic(path, {"count": 7})
        assert storage.load_json(path) == {"count": 7}
    else:
        from ai_skills.pair_documents import write_text_atomic
        write_text_atomic(path, "new content\n")
        assert path.read_text(encoding="utf-8") == "new content\n"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("operation", ["read", "replace"])
def test_persistent_windows_access_denial_is_bounded_and_never_missing(tmp_path, monkeypatch, windows_io, operation):
    path = tmp_path / "state.json"
    path.write_text('{"count": 1}', encoding="utf-8")
    error = sharing_error()

    def denied(*args, **kwargs):
        raise error

    with monkeypatch.context() as injected:
        if operation == "read":
            injected.setattr(Path, "open", denied)
            call = lambda: storage.load_json(path, missing={})
        else:
            injected.setattr(storage.os, "replace", denied)
            call = lambda: storage.write_json_atomic(path, {"count": 2})
        with pytest.raises(PermissionError) as caught:
            call()
    assert caught.value is error
    assert 0 < sum(windows_io) <= 1
    assert 1 <= len(windows_io) <= 10
    assert storage.load_json(path) == {"count": 1}
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("platform,error", [
    ("linux", sharing_error(32)),
    ("darwin", sharing_error()),
    ("win32", PermissionError(errno.EPERM, "permanent denial")),
    ("win32", sharing_error(65)),
    ("win32", OSError(errno.EIO, "disk failure")),
])
@pytest.mark.parametrize("operation", ["read", "replace"])
def test_unrelated_io_errors_are_not_retried(tmp_path, monkeypatch, platform, error, operation):
    path = tmp_path / "state.json"
    path.write_text('{"count": 1}', encoding="utf-8")
    monkeypatch.setattr(sys, "platform", platform)

    def unexpected_sleep(delay):
        pytest.fail("unrelated I/O error was retried")

    def denied(*args, **kwargs):
        raise error

    monkeypatch.setattr(storage.time, "sleep", unexpected_sleep)
    if operation == "read":
        monkeypatch.setattr(Path, "open", denied)
        call = lambda: storage.load_json(path, missing={})
    else:
        monkeypatch.setattr(storage.os, "replace", denied)
        call = lambda: storage.write_json_atomic(path, {"count": 2})
    with pytest.raises(OSError) as caught:
        call()
    assert caught.value is error


def test_retried_json_read_still_distinguishes_missing_and_malformed(tmp_path, monkeypatch, windows_io):
    path = tmp_path / "state.json"
    assert storage.load_json(path, missing=None) is None
    with pytest.raises(FileNotFoundError):
        storage.load_json(path)
    path.write_text("{broken", encoding="utf-8")
    original_open = Path.open
    failures = [sharing_error(32)]

    def briefly_denied(candidate, *args, **kwargs):
        if candidate == path and failures:
            raise failures.pop()
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", briefly_denied)
    with pytest.raises(json.JSONDecodeError):
        storage.load_json(path, missing={})
    assert len(windows_io) == 1
