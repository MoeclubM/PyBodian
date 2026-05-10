#!/usr/bin/env python3

import json
import os
import queue
import re
import subprocess
import sys
import threading


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TRANSPARENT_COLOR = "#010203"
BUTTON_BG = "#0b120f"
BUTTON_ACTIVE_BG = "#17231e"
DEFAULT_WIDTH = 980
DEFAULT_HEIGHT = 96
CONTROL_FONT = ("Microsoft YaHei UI", 9, "bold")
LYRIC_FONT = ("Microsoft YaHei UI", 18, "bold")


THEMES = [
    {"name": "青绿", "text": "#63f0a3", "muted": "#dff9ea", "key": "#5ee69c"},
    {"name": "琥珀", "text": "#ffbe66", "muted": "#fff2dc", "key": "#f0b35f"},
    {"name": "靛蓝", "text": "#7d9cff", "muted": "#edf2ff", "key": "#7895f2"},
    {"name": "玫红", "text": "#ff78ae", "muted": "#ffe7f1", "key": "#ef70a4"},
    {"name": "银灰", "text": "#ffffff", "muted": "#f4f4f4", "key": "#f0f0f0"},
]


class LyricOverlay:
    def __init__(self, settings=None, on_settings_change=None, on_closed=None):
        self.settings = dict(settings or {})
        self.on_settings_change = on_settings_change
        self.on_closed = on_closed
        self._process = None
        self._reader_thread = None
        self._ready = threading.Event()
        self._stdin_lock = threading.Lock()
        self._close_notified = False

    def start(self):
        if self._process and self._process.poll() is None:
            return
        self._ready.clear()
        self._close_notified = False
        self._process = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--overlay-child"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        self._send("init", settings=self.settings)
        self._ready.wait(timeout=3)

    def is_alive(self):
        return bool(self._process and self._process.poll() is None)

    def update(self, **state):
        self._send("state", payload=state)

    def set_topmost(self, value):
        self._send("topmost", payload=bool(value))

    def set_locked(self, value):
        self._send("locked", payload=bool(value))

    def next_theme(self):
        self._send("theme_next")

    def toggle_topmost(self):
        self.set_topmost(not bool(self.settings.get("lyric_overlay_topmost", True)))

    def toggle_locked(self):
        self.set_locked(not bool(self.settings.get("lyric_overlay_locked", False)))

    def close(self):
        process = self._process
        if not process:
            return
        if process.poll() is None:
            self._send("close")
            try:
                if process.stdin:
                    process.stdin.close()
            except Exception:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
        self._process = None
        self._notify_closed()

    def _send(self, action, **payload):
        process = self._process
        if not process or process.poll() is not None or not process.stdin:
            return
        message = {"action": action, **payload}
        try:
            with self._stdin_lock:
                process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                process.stdin.flush()
        except Exception:
            self._notify_closed()

    def _reader_loop(self):
        process = self._process
        if not process or not process.stdout:
            return
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except Exception:
                    continue
                event = message.get("event")
                if event == "ready":
                    self._ready.set()
                elif event == "settings":
                    settings = dict(message.get("settings") or {})
                    self.settings.update(settings)
                    if callable(self.on_settings_change):
                        try:
                            self.on_settings_change(dict(self.settings))
                        except Exception:
                            pass
                elif event == "closed":
                    self._notify_closed()
                    break
        finally:
            self._ready.set()
            if self._process is process and process.poll() is not None:
                self._process = None

    def _notify_closed(self):
        if self._close_notified:
            return
        self._close_notified = True
        if callable(self.on_closed):
            try:
                self.on_closed()
            except Exception:
                pass


class _TkLyricOverlay:
    def __init__(self, settings=None, on_settings_change=None, on_closed=None):
        self.settings = dict(settings or {})
        self.on_settings_change = on_settings_change
        self.on_closed = on_closed
        self._queue = queue.Queue()
        self._thread = None
        self._ready = threading.Event()
        self._closing = threading.Event()
        self._close_notified = False
        self._root = None
        self._line_label = None
        self._meta_label = None
        self._topbar = None
        self._drag_label = None
        self._topmost_button = None
        self._lock_button = None
        self._theme_button = None
        self._close_button = None
        self._drag_state = None
        self._state = {
            "song_title": "",
            "artist": "",
            "text": "开始播放后加载歌词",
            "lines": [],
            "active_index": -1,
        }

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._closing.clear()
        self._close_notified = False
        self._ready.clear()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)

    def update(self, **state):
        self._queue.put(("state", state))

    def set_topmost(self, value):
        self._queue.put(("topmost", bool(value)))

    def set_locked(self, value):
        self._queue.put(("locked", bool(value)))

    def next_theme(self):
        self._queue.put(("theme_next", None))

    def toggle_topmost(self):
        self.set_topmost(not bool(self.settings.get("lyric_overlay_topmost", True)))

    def toggle_locked(self):
        self.set_locked(not bool(self.settings.get("lyric_overlay_locked", False)))

    def close(self):
        if self._thread and self._thread.is_alive():
            self._closing.set()
            self._queue.put(("close", None))
            if threading.current_thread() is not self._thread:
                self._thread.join(timeout=2)

    def _persist(self):
        if callable(self.on_settings_change):
            try:
                self.on_settings_change(dict(self.settings))
            except Exception:
                pass

    def _notify_closed(self):
        if self._close_notified:
            return
        self._close_notified = True
        if callable(self.on_closed):
            try:
                self.on_closed()
            except Exception:
                pass

    def _get_theme(self):
        index = int(self.settings.get("lyric_overlay_theme", 0) or 0) % len(THEMES)
        return index, THEMES[index]

    def _transparent_color(self, theme=None):
        return (theme or {}).get("key") or TRANSPARENT_COLOR

    def _current_line(self):
        lines = list(self._state.get("lines") or [])
        active_value = self._state.get("active_index", -1)
        try:
            active_index = int(active_value) if active_value is not None else -1
        except (TypeError, ValueError):
            active_index = -1
        if lines and 0 <= active_index < len(lines):
            active = lines[active_index]
            line = str(active.get("text", "") or "").strip()
            if line:
                return line
        text = str(self._state.get("text") or "暂无歌词").strip()
        return text.splitlines()[0] if text else "暂无歌词"

    def _meta_text(self):
        song_title = str(self._state.get("song_title") or "").strip()
        artist = str(self._state.get("artist") or "").strip()
        return song_title if not artist else f"{song_title} - {artist}"

    def _enable_dpi_awareness(self):
        if sys.platform != "win32":
            return
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    def _default_geometry(self):
        if not self._root:
            return f"{DEFAULT_WIDTH}x{DEFAULT_HEIGHT}+120+120"
        screen_w = max(DEFAULT_WIDTH + 80, self._root.winfo_screenwidth())
        screen_h = max(DEFAULT_HEIGHT + 120, self._root.winfo_screenheight())
        width = min(DEFAULT_WIDTH, max(560, screen_w - 80))
        height = DEFAULT_HEIGHT
        x = max(20, (screen_w - width) // 2)
        y = max(40, screen_h - height - 104)
        return f"{width}x{height}+{x}+{y}"

    def _normalize_geometry(self, geometry):
        geometry = str(geometry or "").strip()
        match = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", geometry)
        if not match:
            return self._default_geometry()
        width, height, x, y = map(int, match.groups())
        screen_w = max(width + 80, self._root.winfo_screenwidth()) if self._root else 1280
        screen_h = max(height + 120, self._root.winfo_screenheight()) if self._root else 720
        if height > 180 or y < int(screen_h * 0.42):
            return self._default_geometry()
        width = min(max(width, 560), max(560, screen_w - 40))
        height = min(max(height, 76), DEFAULT_HEIGHT)
        x = min(max(x, 10), max(10, screen_w - width - 10))
        y = min(max(y, 30), max(30, screen_h - height - 24))
        return f"{width}x{height}+{x}+{y}"

    def _configure_button(self, button, text, theme, accent=False):
        if not button:
            return
        button.configure(
            text=text,
            bg=BUTTON_BG,
            fg=theme["text"] if accent else theme["muted"],
            activebackground=BUTTON_ACTIVE_BG,
            activeforeground=theme["muted"],
            highlightthickness=0,
            bd=0,
        )

    def _pack_button_right(self, button, padx=(10, 0)):
        if not button:
            return
        button.pack_forget()
        button.pack(side="right", padx=padx)

    def _layout_controls(self, locked):
        for button in (
            self._drag_label,
            self._topmost_button,
            self._lock_button,
            self._theme_button,
            self._close_button,
        ):
            if button:
                button.pack_forget()
        if locked:
            self._pack_button_right(self._lock_button, padx=(8, 0))
            self._pack_button_right(self._drag_label, padx=(8, 0))
            return
        if self._drag_label:
            self._drag_label.pack(side="left", padx=(0, 8))
        self._pack_button_right(self._close_button)
        self._pack_button_right(self._theme_button)
        self._pack_button_right(self._lock_button)
        self._pack_button_right(self._topmost_button)

    def _apply_state(self):
        if not self._root:
            return
        _index, theme = self._get_theme()
        topmost = bool(self.settings.get("lyric_overlay_topmost", True))
        locked = bool(self.settings.get("lyric_overlay_locked", False))
        transparent_color = self._transparent_color(theme)
        geometry = self._normalize_geometry(self.settings.get("lyric_overlay_geometry"))
        self._root.attributes("-topmost", topmost)
        self._root.geometry(geometry)
        try:
            self._root.attributes("-alpha", float(self.settings.get("lyric_overlay_opacity", 1.0)))
        except Exception:
            pass
        try:
            self._root.wm_attributes("-transparentcolor", transparent_color)
        except Exception:
            pass
        self._root.configure(bg=transparent_color)
        if self._line_label:
            self._line_label.configure(bg=transparent_color, fg=theme["text"])
        if self._meta_label:
            self._meta_label.configure(bg=transparent_color, fg=theme["muted"])
        if self._topbar:
            self._topbar.configure(bg=transparent_color)
        if self._drag_label:
            self._drag_label.configure(
                text="拖动" if not locked else "已锁",
                bg=BUTTON_BG,
                fg=theme["muted"],
                activebackground=BUTTON_ACTIVE_BG,
            )
        self._configure_button(self._topmost_button, "置顶" if topmost else "浮动", theme, accent=True)
        self._configure_button(self._lock_button, "解锁" if locked else "锁定", theme, accent=True)
        self._configure_button(self._theme_button, theme["name"], theme, accent=True)
        self._configure_button(self._close_button, "×", theme, accent=False)
        self._layout_controls(locked)
        self._render()

    def _render(self):
        if not self._line_label:
            return
        self._line_label.configure(text=self._current_line())
        if self._meta_label:
            self._meta_label.configure(text="")

    def _thread_main(self):
        try:
            import tkinter as tk
        except Exception:
            self._ready.set()
            return

        self._enable_dpi_awareness()

        root = tk.Tk()
        self._root = root
        root.title("波点歌词")
        root.overrideredirect(True)
        root.minsize(560, 76)
        _index, theme = self._get_theme()
        transparent_color = self._transparent_color(theme)
        root.configure(bg=transparent_color)
        try:
            root.wm_attributes("-transparentcolor", transparent_color)
            root.wm_attributes("-toolwindow", True)
        except Exception:
            pass
        root.protocol("WM_DELETE_WINDOW", lambda: self._queue.put(("close", None)))

        topbar = tk.Frame(root, bg=transparent_color, height=24)
        topbar.pack(fill="x", side="top", padx=10, pady=(6, 0))
        topbar.pack_propagate(False)
        self._topbar = topbar

        def btn(text, command, accent=False):
            return tk.Button(
                topbar,
                text=text,
                command=command,
                bg=BUTTON_BG,
                fg="#ffffff" if accent else "#d8d8d8",
                activebackground=BUTTON_ACTIVE_BG,
                activeforeground="#ffffff",
                highlightthickness=0,
                bd=0,
                padx=4,
                pady=0,
                font=CONTROL_FONT,
                relief="flat",
                cursor="hand2",
            )

        self._drag_label = tk.Button(
            topbar,
            text="拖动",
            bg=BUTTON_BG,
            fg="#d8d8d8",
            activebackground=BUTTON_ACTIVE_BG,
            activeforeground="#ffffff",
            highlightthickness=0,
            bd=0,
            padx=5,
            pady=0,
            font=CONTROL_FONT,
            relief="flat",
            cursor="fleur",
        )
        self._close_button = btn("×", lambda: self._queue.put(("close", None)))
        self._theme_button = btn("青绿", self.next_theme, accent=True)
        self._lock_button = btn("锁定", self.toggle_locked, accent=True)
        self._topmost_button = btn("置顶", self.toggle_topmost, accent=True)

        line_label = tk.Label(
            root,
            text="开始播放后加载歌词",
            bg=transparent_color,
            fg="#63f0a3",
            anchor="center",
            justify="center",
            font=LYRIC_FONT,
        )
        line_label.pack(fill="both", expand=True, padx=24, pady=(0, 8))
        self._line_label = line_label

        drag_targets = [topbar, self._drag_label, line_label]

        def start_drag(event):
            if bool(self.settings.get("lyric_overlay_locked", False)):
                return
            self._drag_state = (event.x_root, event.y_root, root.winfo_x(), root.winfo_y())

        def do_drag(event):
            if bool(self.settings.get("lyric_overlay_locked", False)) or not self._drag_state:
                return
            start_x, start_y, origin_x, origin_y = self._drag_state
            dx = event.x_root - start_x
            dy = event.y_root - start_y
            root.geometry(f"+{origin_x + dx}+{origin_y + dy}")

        def stop_drag(_event):
            self._drag_state = None
            self.settings["lyric_overlay_geometry"] = root.geometry()
            self._persist()

        for widget in drag_targets:
            widget.bind("<ButtonPress-1>", start_drag)
            widget.bind("<B1-Motion>", do_drag)
            widget.bind("<ButtonRelease-1>", stop_drag)

        def keep_visible():
            if self._closing.is_set():
                return
            if bool(self.settings.get("lyric_overlay_topmost", True)):
                try:
                    root.deiconify()
                    root.lift()
                    root.attributes("-topmost", False)
                    root.attributes("-topmost", True)
                except Exception:
                    pass
            root.after(650, keep_visible)

        def poll_queue():
            while True:
                try:
                    action, payload = self._queue.get_nowait()
                except queue.Empty:
                    break
                if action == "close":
                    self._closing.set()
                    self._notify_closed()
                    root.destroy()
                    return
                if action == "state":
                    self._state.update(payload or {})
                    self._render()
                elif action == "topmost":
                    self.settings["lyric_overlay_topmost"] = bool(payload)
                    self._apply_state()
                    self._persist()
                elif action == "locked":
                    self.settings["lyric_overlay_locked"] = bool(payload)
                    self._apply_state()
                    self._persist()
                elif action == "theme_next":
                    self.settings["lyric_overlay_theme"] = (int(self.settings.get("lyric_overlay_theme", 0) or 0) + 1) % len(THEMES)
                    self._apply_state()
                    self._persist()
            if not self._closing.is_set():
                root.after(80, poll_queue)

        self._apply_state()
        self._ready.set()
        root.after(80, poll_queue)
        root.after(650, keep_visible)
        try:
            root.mainloop()
        finally:
            self._notify_closed()
            self._root = None
            self._line_label = None
            self._meta_label = None
            self._topbar = None
            self._drag_label = None
            self._topmost_button = None
            self._lock_button = None
            self._theme_button = None
            self._close_button = None


def _child_emit(event, **payload):
    try:
        sys.stdout.write(json.dumps({"event": event, **payload}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def _run_overlay_child():
    try:
        first_line = sys.stdin.readline()
        first_message = json.loads(first_line) if first_line else {}
        settings = dict(first_message.get("settings") or {})
    except Exception:
        settings = {}

    overlay = _TkLyricOverlay(
        settings=settings,
        on_settings_change=lambda changed: _child_emit("settings", settings=changed),
        on_closed=lambda: _child_emit("closed"),
    )

    def reader():
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except Exception:
                continue
            action = message.get("action")
            if action == "state":
                overlay._queue.put(("state", message.get("payload") or {}))
            elif action == "topmost":
                overlay._queue.put(("topmost", bool(message.get("payload"))))
            elif action == "locked":
                overlay._queue.put(("locked", bool(message.get("payload"))))
            elif action == "theme_next":
                overlay._queue.put(("theme_next", None))
            elif action == "close":
                overlay._queue.put(("close", None))
                break
        overlay._queue.put(("close", None))

    def ready_notifier():
        overlay._ready.wait(timeout=3)
        _child_emit("ready")

    threading.Thread(target=reader, daemon=True).start()
    threading.Thread(target=ready_notifier, daemon=True).start()
    overlay._thread_main()


if __name__ == "__main__" and "--overlay-child" in sys.argv:
    _run_overlay_child()
