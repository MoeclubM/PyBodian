#!/usr/bin/env python3

import atexit
import os
import shutil
import signal
import subprocess
import sys
import time
import weakref


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


class BoDianPlayer:
    _instances = weakref.WeakSet()
    _signal_handlers = {}

    def __init__(self):
        self.ffplay = shutil.which("ffplay")
        self.process = None
        self.state = "stopped"
        self.current_url = None
        self.duration_ms = 0
        self.base_position_ms = 0
        self.started_at = None
        self.audio_driver = "wasapi" if sys.platform == "win32" else ""
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
        self.current_url = url
        self.duration_ms = max(0, int(duration_ms or 0))
        self.base_position_ms = max(0, int(start_ms or 0))
        self._spawn(self.base_position_ms)

    def pause(self):
        if self.state != "playing":
            return
        self.base_position_ms = self.get_position_ms()
        self._terminate_process()
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
        self.state = "stopped"
        self.started_at = None
        if reset_position:
            self.base_position_ms = 0

    def close(self):
        self.stop()

    def poll_finished(self):
        if not self.process:
            return False
        if self.process.poll() is None:
            return False
        self.process = None
        self.started_at = None
        self.state = "stopped"
        if self.duration_ms:
            self.base_position_ms = self.duration_ms
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

    @classmethod
    def _handle_signal(cls, signum, frame):
        for player in list(cls._instances):
            try:
                player.close()
            except Exception:
                pass
        previous = cls._signal_handlers.get(signum, signal.SIG_DFL)
        if callable(previous):
            previous(signum, frame)
            return
        if previous == signal.SIG_IGN:
            return
        if signum == getattr(signal, "SIGINT", None):
            raise KeyboardInterrupt()
        raise SystemExit(128 + int(signum))

    def _spawn(self, start_ms):
        self._terminate_process()
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
            creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            env=env,
        )
        self.started_at = time.monotonic()
        self.state = "playing"

    def _terminate_process(self):
        if not self.process:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.process = None
