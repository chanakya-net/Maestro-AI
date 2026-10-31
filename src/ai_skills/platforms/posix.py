"""Session ownership for cooperative CLI trees (not a security sandbox).

All process groups in the owned session are included. A descendant deliberately
calling setsid() can escape; hostile/daemonizing executables are unsupported.
"""
import os
import signal
import subprocess
import time

import psutil


def members(session):
    result = []
    for process in psutil.process_iter():
        try:
            if os.getsid(process.pid) == session and process.status() != psutil.STATUS_ZOMBIE:
                result.append({'pid': process.pid, 'created_at': process.create_time()})
        except (psutil.NoSuchProcess, ProcessLookupError):
            continue
        except (psutil.AccessDenied, PermissionError):
            raise PermissionError('session enumeration is inaccessible') from None
    return result


def _anchor_exists(anchors, session):
    for anchor in anchors:
        try:
            process = psutil.Process(anchor['pid'])
            if process.create_time() != anchor['created_at']:
                continue
            # macOS getsid(zombie) raises ESRCH, but an unreaped session leader
            # still reserves its PID, so that session number cannot be reused.
            if anchor['pid'] == session and process.status() == psutil.STATUS_ZOMBIE:
                return True
            if os.getsid(anchor['pid']) == session:
                return True
        except (psutil.NoSuchProcess, ProcessLookupError):
            pass
    return False


def terminate(session, grace_seconds, anchors):
    # Re-enumerate for new descendants; psutil signal methods protect against
    # PID reuse. Never signal a group number after its identity anchor is gone.
    for sig, delay in ((signal.SIGTERM, grace_seconds), (signal.SIGKILL, 3.0)):
        deadline = time.monotonic() + delay
        first = True
        while first or time.monotonic() < deadline:
            anchored = _anchor_exists(anchors, session)
            current = members(session)
            if not current:
                return []
            if not anchored:
                raise PermissionError('session has no verified creation-time anchor')
            anchors = current
            for member in current:
                try:
                    process = psutil.Process(member['pid'])
                    if process.create_time() != member['created_at']:
                        raise PermissionError('process identity changed before signal')
                    process.send_signal(sig)
                except psutil.NoSuchProcess:
                    pass
            first = False
            time.sleep(.03)
    return members(session)


class ProcessGroup:
    def __init__(self, argv, cwd, env, stdin, stdout, stderr):
        self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=stdin,
                                        stdout=stdout, stderr=stderr, start_new_session=True)
        self.pid = self.process.pid
        # Retain the leader (including its zombie) until the first membership
        # scan establishes anchors. Reaping first loses fast-exit descendants.
        self.created_at = psutil.Process(self.pid).create_time()
        self.anchors = [{'pid': self.pid, 'created_at': self.created_at}]

    def members(self):
        anchored = _anchor_exists(self.anchors, self.pid)
        current = members(self.pid)
        if current and not anchored:
            raise PermissionError('session has no verified creation-time anchor')
        self.anchors = current
        if not any(member['pid'] == self.pid for member in current):
            self.process.poll()
        return current

    def stop(self, grace_seconds):
        result = terminate(self.pid, grace_seconds, self.anchors)
        self.process.wait(timeout=3)
        return result

    def close(self):
        self.process.poll()

    def return_code(self):
        return self.process.poll()
