"""Per-attempt Job Object ownership; only imported on native Windows.

Create suspended, assign, then resume. Incompatible enclosing jobs fail launch;
there is deliberately no recursive-PID fallback. Console-less workers have no
portable graceful console signal: stop immediately terminates their Job Object.
"""
import msvcrt
import subprocess

import psutil
import win32api
import win32con
import win32job
import win32process


class ProcessGroup:
    def __init__(self, argv, cwd, env, stdin, stdout, stderr):
        if argv[0].lower().endswith(('.cmd', '.bat')):
            raise ValueError('batch launchers require a separate quoting adapter')
        self.job = win32job.CreateJobObject(None, None)
        self.process = None
        thread = None
        try:
            limits = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
            limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, limits)
            startup = win32process.STARTUPINFO()
            startup.dwFlags |= win32con.STARTF_USESTDHANDLES
            handles = [msvcrt.get_osfhandle(stream.fileno()) for stream in (stdin, stdout, stderr)]
            for handle in handles:
                win32api.SetHandleInformation(handle, win32con.HANDLE_FLAG_INHERIT, win32con.HANDLE_FLAG_INHERIT)
            startup.hStdInput, startup.hStdOutput, startup.hStdError = handles
            flags = win32con.CREATE_SUSPENDED | win32con.CREATE_NO_WINDOW | win32con.CREATE_UNICODE_ENVIRONMENT
            try:
                self.process, thread, self.pid, _ = win32process.CreateProcess(
                    argv[0], subprocess.list2cmdline(argv), None, None, True,
                    flags, env, str(cwd), startup)
            finally:
                for handle in handles:
                    win32api.SetHandleInformation(handle, win32con.HANDLE_FLAG_INHERIT, 0)
            win32job.AssignProcessToJobObject(self.job, self.process)
            self.created_at = psutil.Process(self.pid).create_time()
            win32process.ResumeThread(thread)
        except BaseException:
            if self.process is not None:
                win32process.TerminateProcess(self.process, 1)
                self.process.Close()
            self.job.Close()
            raise
        finally:
            if thread is not None:
                thread.Close()

    def members(self):
        result = []
        for pid in win32job.QueryInformationJobObject(self.job, win32job.JobObjectBasicProcessIdList):
            try:
                result.append({'pid': pid, 'created_at': psutil.Process(pid).create_time()})
            except psutil.NoSuchProcess:
                pass
        return result

    def stop(self, grace_seconds):
        import time
        win32job.TerminateJobObject(self.job, 1)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            remaining = self.members()
            if not remaining:
                return []
            time.sleep(.03)
        return self.members()

    def close(self):
        self.process.Close()
        self.job.Close()
