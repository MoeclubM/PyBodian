#!/usr/bin/env python3

import atexit
import ctypes
import os
import shutil
import signal
import subprocess
import sys
import time
import weakref


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class BoDianPlayer:
    _instances = weakref.WeakSet()
    _signal_handlers = {}
    _console_ctrl_handler = None

    def __init__(self):
        self.ffplay = shutil.which("ffplay")
        self.process = None
        self._job_handle = None
        self.state = "stopped"
        self.current_url = None
        self.duration_ms = 0
        self.base_position_ms = 0
        self.started_at = None
        self.audio_driver = "wasapi" if sys.platform == "win32" else ""
        self.just_finished = False
        self._instances.add(self)
        self._install_signal_handlers()
        atexit.register(self.close)

    def ensure_ready(self):
        if not self.ffplay:
            raise RuntimeError("未找到 ffplay，请先安装 FFmpeg 并确保 ffplay 可执行")

    def play(self, url, duration_ms=0, start_ms=0, audio_format=""):
        self.ensure_ready()
        audio_format = (audio_format or "").lower()
        if audio_format in ("mflac", "mgg"):
            raise RuntimeError(f"当前本地播放器 ffplay 无法解码 {audio_format.upper()} 码流")
        self.stop(reset_position=False)
        self.kill_all_ffplay()
        self.current_url = url
        self.duration_ms = max(0, int(duration_ms or 0))
        self.base_position_ms = max(0, int(start_ms or 0))
        self.just_finished = False
        self._spawn(self.base_position_ms)

    def pause(self):
        if self.state != "playing":
            return
        self.base_position_ms = self.get_position_ms()
        self._terminate_process()
        self.kill_all_ffplay()
        self.state = "paused"
        self.started_at = None

    def resume(self):
        if self.state != "paused" or not self.current_url:
            return
        self._spawn(self.base_position_ms)

    def seek(self, position_ms):
        if not self.current_url:
            return
        target_ms = max(0, int(position_ms or 0))
        if self.duration_ms:
            target_ms = min(target_ms, self.duration_ms)
        self.base_position_ms = target_ms
        if self.state == "playing":
            self._spawn(target_ms)

    def stop(self, reset_position=True):
        self._terminate_process()
        self.kill_all_ffplay()
        self.state = "stopped"
        self.started_at = None
        self.just_finished = False
        if reset_position:
            self.base_position_ms = 0

    def close(self):
        self.stop()

    def poll_finished(self):
        if not self.process:
            self._close_job_handle()
            return False
        if self.process.poll() is None:
            return False
        self.process = None
        self._close_job_handle()
        self.started_at = None
        self.state = "stopped"
        if self.duration_ms:
            self.base_position_ms = self.duration_ms
            self.just_finished = True
        return True

    def get_position_ms(self):
        if self.state != "playing" or not self.started_at:
            return self.base_position_ms
        elapsed = int((time.monotonic() - self.started_at) * 1000)
        position = self.base_position_ms + elapsed
        if self.duration_ms:
            return min(position, self.duration_ms)
        return position

    @classmethod
    def _install_signal_handlers(cls):
        if cls._signal_handlers:
            return
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                cls._signal_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, cls._handle_signal)
            except (ValueError, OSError):
                continue
        cls._install_console_ctrl_handler()

    @classmethod
    def _handle_signal(cls, signum, frame):
        cls._cleanup_all_players()
        previous = cls._signal_handlers.get(signum, signal.SIG_DFL)
        if callable(previous):
            previous(signum, frame)
            return
        if previous == signal.SIG_IGN:
            return
        if signum == getattr(signal, "SIGINT", None):
            raise KeyboardInterrupt()
        raise SystemExit(128 + int(signum))

    @classmethod
    def _install_console_ctrl_handler(cls):
        if sys.platform != "win32" or cls._console_ctrl_handler is not None:
            return
        try:
            handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

            def handler(ctrl_type):
                cls._cleanup_all_players()
                return ctrl_type in (2, 5, 6)

            cls._console_ctrl_handler = handler_type(handler)
            ctypes.windll.kernel32.SetConsoleCtrlHandler(cls._console_ctrl_handler, True)
        except Exception:
            cls._console_ctrl_handler = None

    @classmethod
    def _cleanup_all_players(cls):
        for player in list(cls._instances):
            try:
                player.close()
            except Exception:
                pass
        cls.kill_all_ffplay()

    def _spawn(self, start_ms):
        self._terminate_process()
        self.kill_all_ffplay()
        args = [
            self.ffplay,
            "-nodisp",
            "-autoexit",
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        if start_ms > 0:
            args.extend(["-ss", f"{start_ms / 1000:.3f}"])
        args.append(self.current_url)
        env = os.environ.copy()
        if self.audio_driver:
            env["SDL_AUDIODRIVER"] = self.audio_driver
        self.process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            env=env,
        )
        self._attach_to_kill_job(self.process)
        self.started_at = time.monotonic()
        self.state = "playing"
        self.just_finished = False

    def _terminate_process(self):
        if not self.process:
            self._close_job_handle()
            return
        process = self.process
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                self._kill_process_tree(process.pid)
                try:
                    process.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            except Exception:
                self._kill_process_tree(process.pid)
        self.process = None
        self._close_job_handle()

    def _attach_to_kill_job(self, process):
        if sys.platform != "win32" or not process:
            return
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
            kernel32.CreateJobObjectW.restype = ctypes.c_void_p
            kernel32.SetInformationJobObject.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_uint32,
            ]
            kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return
            info = _JobObjectExtendedLimitInformation()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = kernel32.SetInformationJobObject(
                job,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.cast(ctypes.byref(info), ctypes.c_void_p),
                ctypes.sizeof(info),
            )
            process_handle = ctypes.c_void_p(int(process._handle))
            if not ok or not kernel32.AssignProcessToJobObject(job, process_handle):
                kernel32.CloseHandle(job)
                return
            self._job_handle = job
        except Exception:
            self._close_job_handle()

    def _close_job_handle(self):
        if not self._job_handle or sys.platform != "win32":
            self._job_handle = None
            return
        try:
            ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            ctypes.windll.kernel32.CloseHandle(self._job_handle)
        except Exception:
            pass
        self._job_handle = None

    def _kill_process_tree(self, pid):
        if not pid:
            return
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW,
                    timeout=3,
                    check=False,
                )
            except Exception:
                pass
            return
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass

    @classmethod
    def kill_all_ffplay(cls):
        if sys.platform != "win32":
            return
        try:
            subprocess.run(
                ["taskkill", "/IM", "ffplay.exe", "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
                timeout=3,
                check=False,
            )
        except Exception:
            pass
