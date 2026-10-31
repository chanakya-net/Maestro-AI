"""Strict authoritative JSON and durable same-directory replacement primitives."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import socket
import tempfile
import time
from typing import Callable, TypeVar
import uuid

import psutil

REQUIRED = object()


def load_json(path: Path, *, missing: object = REQUIRED) -> object:
    """Default only for an absent file; malformed or unreadable state is an error."""
    try:
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        if missing is REQUIRED:
            raise
        return missing


def write_json_atomic(path: Path, value: object) -> None:
    """Flush and fsync a unique adjacent temporary before atomic replacement."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


T = TypeVar("T")


class LockTimeoutError(TimeoutError):
    """The lock remained busy or its owner could not safely be identified."""


class DirectoryLock:
    """A local-filesystem directory lock; unknown owners require manual recovery.

    Owner identity uses psutil's PID creation time, hostname and a random token.
    This deliberately replaces the old dependency-free helpers' PID-only checks;
    psutil is a required cross-platform runtime dependency. Shared/network filesystems
    with unreliable mkdir/rename semantics are outside this lock's contract.
    """

    def __init__(self, path: Path, timeout_seconds: float = 10.0, poll_interval_seconds: float = 0.02) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be finite and nonnegative")
        if not math.isfinite(poll_interval_seconds) or poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive and finite")
        self.path = Path(f"{path}.lock")
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.token = uuid.uuid4().hex
        self._held = False

    def __enter__(self) -> DirectoryLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        attempted = False
        while True:
            if attempted and time.monotonic() >= deadline:
                raise LockTimeoutError(f"timed out waiting for lock {self.path}; owner busy or unknown")
            attempted = True
            try:
                self.path.mkdir()
            except FileExistsError:
                if self._reclaim_dead_owner():
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockTimeoutError(f"timed out waiting for lock {self.path}; owner busy or unknown")
                time.sleep(min(remaining, self.poll_interval_seconds))
                continue
            try:
                write_json_atomic(self.path / "owner.json", {
                    "host": socket.gethostname(), "pid": os.getpid(),
                    "create_time": psutil.Process().create_time(), "token": self.token,
                })
            except BaseException:
                self.path.rmdir()
                raise
            self._held = True
            return self

    @staticmethod
    def _proven_dead(owner: object) -> bool:
        if not isinstance(owner, dict) or owner.get("host") != socket.gethostname():
            return False
        pid, created, token = owner.get("pid"), owner.get("create_time"), owner.get("token")
        if type(pid) is not int or pid <= 0 or isinstance(created, bool) or not isinstance(created, (int, float)) or not math.isfinite(created) or created <= 0 or not isinstance(token, str) or not token:
            return False
        try:
            process = psutil.Process(pid)
            return process.create_time() != created or process.status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return True
        except (psutil.AccessDenied, OSError):
            return False

    def _reclaim_dead_owner(self) -> bool:
        # A claim INSIDE the observed directory serializes reapers. A second
        # reaper with an old observation can acquire a claim in a new lock, but
        # must then re-read its identity and will leave the new owner untouched.
        claim = self.path / ".reaping"
        claimed = False
        moved = False
        claim_stat = None
        try:
            observed_stat = self.path.stat()
            observed = load_json(self.path / "owner.json")
            if not self._proven_dead(observed):
                return False
            claim.mkdir()
            claimed = True
            claim_stat = claim.stat()
            current_stat = self.path.stat()
            if (current_stat.st_dev, current_stat.st_ino) != (observed_stat.st_dev, observed_stat.st_ino):
                return False
            if load_json(self.path / "owner.json") != observed or not self._proven_dead(observed):
                return False
            quarantine = self.path.with_name(f".{self.path.name}.reaped.{uuid.uuid4().hex}")
            self.path.rename(quarantine)
            moved = True
            shutil.rmtree(quarantine)
            return True
        except (OSError, ValueError):
            return False
        finally:
            if claimed and not moved and claim_stat is not None:
                try:
                    current_claim = claim.stat()
                    if (current_claim.st_dev, current_claim.st_ino) == (claim_stat.st_dev, claim_stat.st_ino):
                        claim.rmdir()
                except OSError:
                    pass

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._held:
            return
        self._held = False
        try:
            owner = load_json(self.path / "owner.json")
            if not isinstance(owner, dict) or owner.get("token") != self.token:
                return
            (self.path / "owner.json").unlink()
            self.path.rmdir()
        except (OSError, ValueError):
            return


class StateStore:
    """Reload, validate, mutate and replace one JSON object under the same lock.

    Missing state starts empty. Domain schemas are preserved, including legacy
    main-state version 4; caller-supplied validators own schema normalization.
    Malformed JSON and nonobjects fail closed.
    Updates must be short local mutations: do not wait on processes or networks.
    """

    def __init__(self, path: Path, *, timeout_seconds: float = 10.0, validator: Callable[[dict], None] | None = None) -> None:
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds
        self.validator = validator

    def _validate(self, state: object) -> dict:
        if not isinstance(state, dict):
            raise ValueError("state must be a JSON object")
        if self.validator is not None:
            self.validator(state)
        return state

    def transaction(self, update: Callable[[dict], T]) -> T:
        with DirectoryLock(self.path, timeout_seconds=self.timeout_seconds):
            state = self._validate(load_json(self.path, missing={}))
            result = update(state)
            self._validate(state)
            write_json_atomic(self.path, state)
            return result
