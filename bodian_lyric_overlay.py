#!/usr/bin/env python3
"""桌面歌词浮窗。

Windows 优先使用 UpdateLayeredWindow 实现 per-pixel alpha 分层窗口：
文字边缘按真实 alpha 与桌面混合，无色键毛边，配合柔和阴影保证任意背景下的清晰度；
置顶通过周期性 SetWindowPos(HWND_TOPMOST) 无闪烁维持，并设置 WS_EX_NOACTIVATE
避免浮窗抢焦点。分层窗口不可用时回退到 Tk 透明色方案（渲染时把文字压到不透明
键色底上，消除旧版抗锯齿混入黑色的暗边）。
"""

import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageTk
except Exception:
    Image = None
    ImageChops = None
    ImageDraw = None
    ImageFilter = None
    ImageFont = None
    ImageTk = None


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TRANSPARENT_COLOR = "#010203"
BUTTON_BG = "#0b120f"
BUTTON_ACTIVE_BG = "#17231e"
BASE_WIDTH = 980
BASE_HEIGHT = 124
BASE_MIN_WIDTH = 560
DEFAULT_HORIZONTAL_MARGIN = 40
LYRIC_RENDER_FPS_MS = 40
TOPMOST_KEEPALIVE_MS = 700
SHADOW_BLUR = 3.0
SHADOW_STRENGTH = 0.72
SHADOW_OFFSET_Y = 3
REVEAL_FEATHER_PX = 10
CONTROL_FONT = ("Microsoft YaHei UI", 9, "bold")
LYRIC_FONT_MAIN = ("Microsoft YaHei UI", 28)
LYRIC_FONT_SUB = ("Microsoft YaHei UI", 20)
WINDOWS_FONT_CANDIDATES = [
    ("msyhbd.ttc", True),
    ("msyh.ttc", False),
    ("simsun.ttc", False),
    ("simhei.ttf", True),
    ("arial.ttf", False),
]

IS_WIN32 = sys.platform == "win32"


def _clamp(value, low, high):
    return max(low, min(high, value))


def _smoothstep(value):
    value = _clamp(float(value or 0.0), 0.0, 1.0)
    return value * value * (3.0 - 2.0 * value)


def _font_search_paths():
    roots = []
    windir = os.environ.get("WINDIR")
    if windir:
        roots.append(os.path.join(windir, "Fonts"))
    roots.extend(
        [
            "/usr/share/fonts",
            "/usr/local/share/fonts",
        ]
    )
    return roots


def _find_font_path(names):
    for root in _font_search_paths():
        if not os.path.isdir(root):
            continue
        for name in names:
            path = os.path.join(root, name)
            if os.path.isfile(path):
                return path
    for name in names:
        if os.path.isfile(name):
            return name
    return None


def _dpi_scale():
    """进程 DPI 感知生效后的系统缩放比例（1.0 = 96 DPI）。"""
    if not IS_WIN32:
        return 1.0
    try:
        import ctypes
        dpi = int(ctypes.windll.user32.GetDpiForSystem())
        if dpi > 0:
            return dpi / 96.0
    except Exception:
        pass
    try:
        import ctypes
        dc = ctypes.windll.user32.GetDC(None)
        dpi = int(ctypes.windll.gdi32.GetDeviceCaps(dc, 88))
        ctypes.windll.user32.ReleaseDC(None, dc)
        if dpi > 0:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


class _WinLayered:
    """ctypes 封装：per-pixel alpha 分层窗口与置顶维护。"""

    WS_EX_LAYERED = 0x00080000
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    GWL_EXSTYLE = -20
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_FRAMECHANGED = 0x0020
    ULW_ALPHA = 0x00000002
    DIB_RGB_COLORS = 0
    LOGPIXELSX = 88

    def __init__(self):
        import ctypes
        self.ctypes = ctypes
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        self.user32 = user32
        self.gdi32 = gdi32

        LONG = ctypes.c_long
        DWORD = ctypes.c_uint32
        WORD = ctypes.c_uint16
        BYTE = ctypes.c_ubyte

        class POINT(ctypes.Structure):
            _fields_ = [("x", LONG), ("y", LONG)]

        class SIZE(ctypes.Structure):
            _fields_ = [("cx", LONG), ("cy", LONG)]

        class RECT(ctypes.Structure):
            _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [
                ("BlendOp", BYTE),
                ("BlendFlags", BYTE),
                ("SourceConstantAlpha", BYTE),
                ("AlphaFormat", BYTE),
            ]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", DWORD),
                ("biWidth", LONG),
                ("biHeight", LONG),
                ("biPlanes", WORD),
                ("biBitCount", WORD),
                ("biCompression", DWORD),
                ("biSizeImage", DWORD),
                ("biXPelsPerMeter", LONG),
                ("biYPelsPerMeter", LONG),
                ("biClrUsed", DWORD),
                ("biClrImportant", DWORD),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", DWORD * 1)]

        self._POINT = POINT
        self._SIZE = SIZE
        self._RECT = RECT
        self._BLENDFUNCTION = BLENDFUNCTION
        self._BITMAPINFOHEADER = BITMAPINFOHEADER
        self._BITMAPINFO = BITMAPINFO

        user32.GetDC.argtypes = [ctypes.c_void_p]
        user32.GetDC.restype = ctypes.c_void_p
        user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.ReleaseDC.restype = ctypes.c_int
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.SetWindowPos.restype = ctypes.c_bool
        user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
        user32.GetWindowRect.restype = ctypes.c_bool
        user32.UpdateLayeredWindow.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(POINT),
            ctypes.POINTER(SIZE),
            ctypes.c_void_p,
            ctypes.POINTER(POINT),
            DWORD,
            ctypes.POINTER(BLENDFUNCTION),
            DWORD,
        ]
        user32.UpdateLayeredWindow.restype = ctypes.c_bool
        gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
        gdi32.DeleteDC.restype = ctypes.c_bool
        gdi32.CreateDIBSection.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(BITMAPINFO),
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            DWORD,
        ]
        gdi32.CreateDIBSection.restype = ctypes.c_void_p
        gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.SelectObject.restype = ctypes.c_void_p
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        gdi32.DeleteObject.restype = ctypes.c_bool

    def get_toplevel_hwnd(self, root):
        """Tk 根窗口对应的 Win32 顶层 HWND。"""
        hwnd = self.user32.GetParent(ctypes.c_void_p(root.winfo_id()))
        if not hwnd:
            hwnd = ctypes.c_void_p(root.winfo_id())
        return hwnd

    def enable(self, hwnd):
        """加 WS_EX_LAYERED/TOOLWINDOW/NOACTIVATE 样式并置顶。返回是否成功。"""
        try:
            exstyle = self.user32.GetWindowLongW(hwnd, self.GWL_EXSTYLE)
            exstyle |= self.WS_EX_LAYERED | self.WS_EX_TOOLWINDOW | self.WS_EX_NOACTIVATE
            self.user32.SetWindowLongW(hwnd, self.GWL_EXSTYLE, exstyle)
            self.set_topmost(hwnd, True)
            self.user32.SetWindowPos(
                hwnd,
                ctypes.c_void_p(self.HWND_TOPMOST),
                0,
                0,
                0,
                0,
                self.SWP_NOMOVE | self.SWP_NOSIZE | self.SWP_NOACTIVATE | self.SWP_FRAMECHANGED,
            )
            return True
        except Exception:
            return False

    def set_topmost(self, hwnd, topmost):
        after = self.HWND_TOPMOST if topmost else self.HWND_NOTOPMOST
        try:
            return bool(
                self.user32.SetWindowPos(
                    hwnd,
                    ctypes.c_void_p(after),
                    0,
                    0,
                    0,
                    0,
                    self.SWP_NOMOVE | self.SWP_NOSIZE | self.SWP_NOACTIVATE,
                )
            )
        except Exception:
            return False

    @staticmethod
    def _premultiply_bgra(image):
        """straight RGBA → 预乘 BGRA 字节（自上而下行序）。"""
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        r, g, b, a = image.split()
        premult = Image.merge(
            "RGBA",
            (
                ImageChops.multiply(r, a),
                ImageChops.multiply(g, a),
                ImageChops.multiply(b, a),
                a,
            ),
        )
        pr, pg, pb, pa = premult.split()
        return Image.merge("RGBA", (pb, pg, pr, pa)).tobytes()

    def update(self, hwnd, image, opacity=1.0):
        """用 RGBA 图像刷新分层窗口表面（保持当前窗口位置与大小）。"""
        try:
            rect = self._RECT()
            if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return False
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            if width <= 0 or height <= 0:
                return False
            if image.size != (width, height):
                surface = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                surface.paste(image.crop((0, 0, min(image.size[0], width), min(image.size[1], height))), (0, 0))
                image = surface
            bgra = self._premultiply_bgra(image)

            screen_dc = self.user32.GetDC(None)
            if not screen_dc:
                return False
            mem_dc = self.gdi32.CreateCompatibleDC(screen_dc)
            if not mem_dc:
                self.user32.ReleaseDC(None, screen_dc)
                return False
            bits = self.ctypes.c_void_p()
            bmi = self._BITMAPINFO()
            bmi.bmiHeader.biSize = self.ctypes.sizeof(self._BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = width
            bmi.bmiHeader.biHeight = -height
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0
            dib = self.gdi32.CreateDIBSection(screen_dc, ctypes.byref(bmi), self.DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
            ok = False
            if dib and bits:
                old = self.gdi32.SelectObject(mem_dc, dib)
                self.ctypes.memmove(bits, bgra, len(bgra))
                pt_dst = self._POINT(rect.left, rect.top)
                size = self._SIZE(width, height)
                pt_src = self._POINT(0, 0)
                alpha_value = int(_clamp(float(opacity or 1.0), 0.05, 1.0) * 255)
                blend = self._BLENDFUNCTION(0, 0, alpha_value, 1)
                ok = bool(
                    self.user32.UpdateLayeredWindow(
                        hwnd,
                        screen_dc,
                        ctypes.byref(pt_dst),
                        ctypes.byref(size),
                        mem_dc,
                        ctypes.byref(pt_src),
                        0,
                        ctypes.byref(blend),
                        self.ULW_ALPHA,
                    )
                )
                self.gdi32.SelectObject(mem_dc, old)
            self.gdi32.DeleteObject(dib)
            self.gdi32.DeleteDC(mem_dc)
            self.user32.ReleaseDC(None, screen_dc)
            return ok
        except Exception:
            return False


THEMES = [
    {"name": "青绿", "text": "#63f0a3", "muted": "#dff9ea", "key": "#5ee69c"},
    {"name": "琥珀", "text": "#ffbe66", "muted": "#fff2dc", "key": "#f0b35f"},
    {"name": "靛蓝", "text": "#7d9cff", "muted": "#edf2ff", "key": "#7895f2"},
    {"name": "玫红", "text": "#ff78ae", "muted": "#ffe7f1", "key": "#ef70a4"},
    {"name": "银灰", "text": "#ffffff", "muted": "#f4f4f4", "key": "#f0f0f0"},
]


class LyricOverlay:
    """歌词浮窗代理：优先子进程隔离运行，打包成 exe 后退化为同进程线程运行。"""

    def __init__(self, settings=None, on_settings_change=None, on_closed=None):
        self.settings = dict(settings or {})
        self.on_settings_change = on_settings_change
        self.on_closed = on_closed
        self._process = None
        self._reader_thread = None
        self._ready = threading.Event()
        self._stdin_lock = threading.Lock()
        self._close_notified = False
        self._inprocess = None

    def start(self):
        if self._inprocess is not None:
            return
        if self._process and self._process.poll() is None:
            return
        if getattr(sys, "frozen", False):
            self._start_inprocess()
            return
        self._ready.clear()
        self._close_notified = False
        try:
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
        except Exception:
            self._process = None
            self._start_inprocess()
            return
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        self._send("init", settings=self.settings)

    def _start_inprocess(self):
        if self._inprocess is not None:
            return
        self._ready.clear()
        self._close_notified = False
        self._inprocess = _TkLyricOverlay(
            settings=self.settings,
            on_settings_change=self._on_inprocess_settings,
            on_closed=self._on_inprocess_closed,
        )
        self._inprocess.start()

    def _on_inprocess_settings(self, changed):
        self.settings.update(dict(changed or {}))
        if callable(self.on_settings_change):
            try:
                self.on_settings_change(dict(self.settings))
            except Exception:
                pass

    def _on_inprocess_closed(self):
        self._inprocess = None
        self._notify_closed()

    def is_alive(self):
        if self._inprocess is not None:
            thread = getattr(self._inprocess, "_thread", None)
            return bool(thread and thread.is_alive())
        return bool(self._process and self._process.poll() is None)

    def update(self, **state):
        if self._inprocess is not None:
            self._inprocess.update(**state)
        else:
            self._send("state", payload=state)

    def set_topmost(self, value):
        if self._inprocess is not None:
            self._inprocess.set_topmost(bool(value))
        else:
            self._send("topmost", payload=bool(value))

    def set_locked(self, value):
        if self._inprocess is not None:
            self._inprocess.set_locked(bool(value))
        else:
            self._send("locked", payload=bool(value))

    def set_opacity(self, value):
        if self._inprocess is not None:
            self._inprocess.set_opacity(float(value))
        else:
            self._send("opacity", payload=float(value))

    def set_font_scale(self, value):
        if self._inprocess is not None:
            self._inprocess.set_font_scale(float(value))
        else:
            self._send("font_scale", payload=float(value))

    def set_primary_color(self, value):
        if self._inprocess is not None:
            self._inprocess.set_primary_color(str(value or ""))
        else:
            self._send("primary_color", payload=str(value or ""))

    def set_line_gap(self, value):
        if self._inprocess is not None:
            self._inprocess.set_line_gap(int(value))
        else:
            self._send("line_gap", payload=int(value))

    def next_theme(self):
        if self._inprocess is not None:
            self._inprocess.next_theme()
        else:
            self._send("theme_next")

    def set_theme(self, index):
        if self._inprocess is not None:
            self._inprocess.set_theme(int(index))
        else:
            self._send("set_theme", payload=int(index))

    def toggle_topmost(self):
        self.set_topmost(not bool(self.settings.get("lyric_overlay_topmost", True)))

    def toggle_locked(self):
        self.set_locked(not bool(self.settings.get("lyric_overlay_locked", False)))

    def close(self):
        if self._inprocess is not None:
            self._inprocess.close()
            self._inprocess = None
            self._notify_closed()
            return
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


def _enable_dpi_awareness():
    if not IS_WIN32:
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
        self._hwnd = None
        self._layered = None
        self._layered_ok = False
        self._force_present = True
        self._dpi_scale = 1.0
        self._topbar = None
        self._topbar_visible = False
        self._controls_hovered = False
        self._geometry_update_pending = False
        self._drag_label = None
        self._topmost_button = None
        self._lock_button = None
        self._theme_button = None
        self._font_up_button = None
        self._font_down_button = None
        self._close_button = None
        self._drag_state = None
        self._user_positioned = bool(self.settings.get("lyric_overlay_geometry"))
        self._canvas = None
        self._canvas_image_id = None
        self._canvas_photo = None
        self._compose_cached_key = None
        self._compose_cache_image = None
        self._font_cache = {}
        self._render_loop_started = False
        self._render_after_id = None
        self._topmost_after_id = None
        self._last_content_resize_at = 0.0
        self._state = {
            "song_title": "",
            "artist": "",
            "text": "开始播放后加载歌词",
            "lines": [],
            "active_index": -1,
            "position_ms": 0,
            "duration_ms": 0,
            "playback_state": "stopped",
            "_received_at": 0.0,
        }

    # ── 生命周期 ──────────────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._closing.clear()
        self._close_notified = False
        self._ready.clear()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=0.5)

    def update(self, **state):
        self._queue.put(("state", state))

    def set_topmost(self, value):
        self._queue.put(("topmost", bool(value)))

    def set_locked(self, value):
        self._queue.put(("locked", bool(value)))

    def set_opacity(self, value):
        self._queue.put(("opacity", float(value)))

    def set_font_scale(self, value):
        self._queue.put(("font_scale", float(value)))

    def set_primary_color(self, value):
        self._queue.put(("primary_color", str(value or "")))

    def set_line_gap(self, value):
        self._queue.put(("line_gap", int(value)))

    def next_theme(self):
        self._queue.put(("theme_next", None))

    def set_theme(self, index):
        self._queue.put(("set_theme", int(index)))

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

    # ── 主题 / 几何 ───────────────────────────────────────────────

    def _get_theme(self):
        index = int(self.settings.get("lyric_overlay_theme", 0) or 0) % len(THEMES)
        return index, THEMES[index]

    def _transparent_color(self, theme=None):
        return (theme or {}).get("key") or TRANSPARENT_COLOR

    def _opacity(self):
        try:
            return _clamp(float(self.settings.get("lyric_overlay_opacity", 1.0) or 1.0), 0.1, 1.0)
        except (TypeError, ValueError):
            return 1.0

    def _font_scale(self):
        try:
            return _clamp(float(self.settings.get("lyric_overlay_font_scale", 1.0) or 1.0), 0.6, 2.0)
        except (TypeError, ValueError):
            return 1.0

    def _line_gap_px(self):
        try:
            return _clamp(int(float(self.settings.get("lyric_overlay_line_gap", 0) or 0)), 0, 60)
        except (TypeError, ValueError):
            return 0

    def _primary_color_override(self):
        value = str(self.settings.get("lyric_overlay_primary_color", "") or "").strip()
        if value and re.match(r"^#[0-9a-fA-F]{6}$", value):
            return value
        return ""

    def _parse_geometry(self, geometry):
        geometry = str(geometry or "").strip()
        match = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", geometry)
        if not match:
            return None
        return tuple(map(int, match.groups()))

    def _default_size(self):
        scale = self._dpi_scale or 1.0
        return max(1, int(BASE_WIDTH * scale)), max(1, int(BASE_HEIGHT * scale))

    def _min_size(self):
        scale = self._dpi_scale or 1.0
        return max(1, int(BASE_MIN_WIDTH * scale)), max(1, int(BASE_HEIGHT * 0.58 * scale))

    def _current_progress_state(self):
        return self._line_pair_info()

    def _line_pair_info(self):
        fallback = str(self._state.get("text") or "暂无歌词").replace("\r", " ").replace("\n", " ").strip()
        fallback = fallback.splitlines()[0] if fallback else "暂无歌词"
        lines = list(self._state.get("lines") or [])
        if not lines:
            return {
                "window": fallback,
                "primary": fallback,
                "secondary": "",
                "primary_index": -1,
                "secondary_index": -1,
                "primary_is_translation": False,
                "secondary_is_translation": False,
                "current_ms": 0,
                "next_ms": 0,
                "progress": 0.0,
            }

        active_value = self._state.get("active_index", -1)
        try:
            active_index = int(active_value) if active_value is not None else -1
        except (TypeError, ValueError):
            active_index = -1

        visible = [index for index, line in enumerate(lines) if not line.get("translation")]
        if not visible:
            visible = list(range(len(lines)))
        visible_set = set(visible)

        primary_index = None
        if 0 <= active_index < len(lines):
            if active_index in visible_set:
                primary_index = active_index
            else:
                for index in range(active_index - 1, -1, -1):
                    if index in visible_set:
                        primary_index = index
                        break
                if primary_index is None:
                    for index in range(active_index + 1, len(lines)):
                        if index in visible_set:
                            primary_index = index
                            break
        if primary_index is None:
            primary_index = visible[0]

        primary_line = lines[primary_index]
        primary_text = str(primary_line.get("text", "") or "").replace("\r", " ").replace("\n", " ").strip() or fallback
        primary_time = max(0, int(primary_line.get("time_ms") or 0))

        same_group = []
        group_time = primary_line.get("time_ms")
        for index in range(primary_index + 1, len(lines)):
            if lines[index].get("time_ms") != group_time:
                break
            same_group.append(index)

        secondary_index = -1
        for index in same_group:
            if lines[index].get("translation"):
                secondary_index = index
                break

        if secondary_index < 0:
            for index in range(primary_index + 1, len(lines)):
                if index in visible_set:
                    secondary_index = index
                    break
                if lines[index].get("translation") and secondary_index < 0:
                    secondary_index = index

        secondary_text = ""
        if secondary_index >= 0:
            secondary_text = str(lines[secondary_index].get("text", "") or "").replace("\r", " ").replace("\n", " ").strip()

        next_index = -1
        for index in range(primary_index + 1, len(lines)):
            if index in visible_set:
                next_index = index
                break
        duration_ms = max(0, int(self._state.get("duration_ms") or 0))
        next_time = int(lines[next_index].get("time_ms") or primary_time + 2500) if next_index >= 0 else (duration_ms or primary_time + 2500)
        next_time = max(primary_time + 1, next_time)

        playback_state = str(self._state.get("playback_state") or "").lower()
        position_raw = self._state.get("position_ms")
        position_ms = max(0, int(primary_time if position_raw is None else position_raw))
        received_at = self._state.get("_received_at")
        try:
            received_at = float(received_at) if received_at is not None else 0.0
        except (TypeError, ValueError):
            received_at = 0.0
        if playback_state == "playing" and received_at > 0.0:
            position_ms += int(max(0.0, (time.monotonic() - received_at)) * 1000.0)

        progress = _clamp((position_ms - primary_time) / max(1, next_time - primary_time), 0.0, 1.0)
        if active_index > primary_index:
            progress = max(progress, 1.0)

        window = primary_text if not secondary_text else f"{primary_text} {secondary_text}"
        return {
            "window": window,
            "primary": primary_text,
            "secondary": secondary_text,
            "primary_index": primary_index,
            "secondary_index": secondary_index,
            "primary_is_translation": bool(primary_line.get("translation")),
            "secondary_is_translation": bool(secondary_index >= 0 and lines[secondary_index].get("translation")),
            "current_ms": primary_time,
            "next_ms": next_time,
            "progress": progress,
        }

    def _default_geometry(self):
        width, height = self._default_size()
        if not self._root:
            return f"{width}x{height}+120+120"
        screen_w = max(width + 80, self._root.winfo_screenwidth())
        screen_h = max(height + 120, self._root.winfo_screenheight())
        width = min(width, max(self._min_size()[0], screen_w - 80))
        x = max(20, (screen_w - width) // 2)
        y = max(20, screen_h - height - 28)
        return f"{width}x{height}+{x}+{y}"

    def _normalize_geometry(self, geometry):
        geometry = str(geometry or "").strip()
        match = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", geometry)
        if not match:
            return self._default_geometry()
        width, height, x, y = map(int, match.groups())
        default_w, default_h = self._default_size()
        min_w, min_h = self._min_size()
        screen_w = max(width + 80, self._root.winfo_screenwidth()) if self._root else 1280
        screen_h = max(height + 120, self._root.winfo_screenheight()) if self._root else 720
        max_h = max(min_h, int(default_h * 2.5))
        if height > max_h or y < int(screen_h * 0.42):
            return self._default_geometry()
        width = min(max(width, min_w), max(min_w, screen_w - 40))
        height = min(max(height, min_h), default_h)
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

    def _font_for_path(self, family, size, bold=False):
        if ImageFont is None:
            return None
        key = (family, int(size), bool(bold))
        cached = self._font_cache.get(key)
        if cached:
            return cached
        candidates = []
        normalized = str(family or "").strip().lower()
        if "yahei" in normalized or "雅黑" in normalized:
            candidates.extend(["msyhbd.ttc", "msyh.ttc"])
        if "simhei" in normalized or "黑体" in normalized:
            candidates.append("simhei.ttf")
        if "arial" in normalized:
            candidates.append("arial.ttf")
        if bold:
            candidates = [name for name in candidates if "bd" in name or "bold" in name] + candidates
        path = _find_font_path(candidates or [name for name, _ in WINDOWS_FONT_CANDIDATES])
        font = None
        if path:
            try:
                if path.lower().endswith(".ttc"):
                    font = ImageFont.truetype(path, int(size), index=0)
                else:
                    font = ImageFont.truetype(path, int(size))
            except Exception:
                font = None
        if font is None:
            try:
                font = ImageFont.load_default()
            except Exception:
                font = None
        self._font_cache[key] = font
        return font

    def _resolve_theme_colors(self):
        _index, theme = self._get_theme()
        active = self._primary_color_override() or theme.get("text") or "#63f0a3"
        inactive = theme.get("muted") or "#f4f4f4"
        return active, inactive

    def _measure_text(self, draw, text, font):
        if not text:
            return 0
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            return max(0, int(bbox[2] - bbox[0]))
        except Exception:
            try:
                return int(draw.textlength(text, font=font))
            except Exception:
                return len(text) * 12

    def _line_height(self, font):
        if not font:
            return 18
        try:
            bbox = font.getbbox("国")
            return max(1, int(bbox[3] - bbox[1]))
        except Exception:
            try:
                ascent, descent = font.getmetrics()
                return int(ascent + descent)
            except Exception:
                return 18

    def _build_layout(self, draw, state, width, height):
        primary = str(state.get("primary") or "").strip()
        secondary = str(state.get("secondary") or "").strip()
        progress = _clamp(state.get("progress", 0.0), 0.0, 1.0)

        font_scale = self._font_scale()
        primary_font_size = int(round(_clamp(width * 0.030 * font_scale, 12, 64)))
        secondary_font_size = int(round(_clamp(width * 0.021 * font_scale, 9, 46)))

        primary_font = self._font_for_path(LYRIC_FONT_MAIN[0], primary_font_size, bold=True)
        secondary_font = self._font_for_path(LYRIC_FONT_SUB[0], secondary_font_size, bold=False)
        if primary_font is None or secondary_font is None:
            return None

        primary_width = self._measure_text(draw, primary, primary_font)
        secondary_width = self._measure_text(draw, secondary, secondary_font) if secondary else 0
        gap_setting = self._line_gap_px()
        if gap_setting > 0:
            line_gap = int(round(gap_setting * (self._dpi_scale or 1.0)))
        else:
            line_gap = max(4, int(round(height * 0.04)))
        top_pad = max(8, int(round(height * 0.12)))
        bottom_pad = max(6, int(round(height * 0.08)))
        inner_pad_x = max(16, int(round(width * 0.02)))
        long_line = primary_width > max(0, width - inner_pad_x * 2)
        eased = _smoothstep(progress)
        primary_y = top_pad
        secondary_y = primary_y + self._line_height(primary_font) + (line_gap if secondary else 0)
        content_h = secondary_y + (self._line_height(secondary_font) if secondary else 0) + bottom_pad
        return {
            "primary": primary,
            "secondary": secondary,
            "primary_font": primary_font,
            "secondary_font": secondary_font,
            "primary_width": primary_width,
            "secondary_width": secondary_width,
            "primary_height": self._line_height(primary_font),
            "secondary_height": self._line_height(secondary_font),
            "primary_y": primary_y,
            "secondary_y": secondary_y,
            "long_line": long_line,
            "content_h": content_h,
            "inner_pad_x": inner_pad_x,
            "progress": eased,
        }

    # ── 渲染 ─────────────────────────────────────────────────────

    def _compose_lyric_image(self, width, height):
        """合成 RGBA 歌词图（带柔和阴影），带缓存。返回 (image, is_new)。"""
        if Image is None or ImageDraw is None:
            return None, False
        theme_active, theme_inactive = self._resolve_theme_colors()
        state = self._current_progress_state()
        key = (
            width,
            height,
            state["primary"],
            state["secondary"],
            round(float(state["progress"]), 3),
            theme_active,
            theme_inactive,
            self._font_scale(),
            self._line_gap_px(),
        )
        if self._compose_cached_key == key and self._compose_cache_image is not None:
            return self._compose_cache_image, False

        image = Image.new("RGBA", (max(1, int(width)), max(1, int(height))), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        layout = self._build_layout(draw, state, width, height)
        if not layout:
            return None, False

        window_h = max(height, layout["content_h"])
        if window_h != height:
            image = Image.new("RGBA", (max(1, int(width)), max(1, int(window_h))), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            layout = self._build_layout(draw, state, width, window_h)
            if not layout:
                return None, False

        progress = layout["progress"]
        if layout["primary"]:
            if layout["long_line"]:
                overflow = max(0, layout["primary_width"] - width + layout["inner_pad_x"] * 2)
                base_x = int(round((width - layout["primary_width"]) / 2))
                pan_x = int(round(overflow * 0.35 * progress))
                primary_x = _clamp(base_x + pan_x, -max(0, layout["primary_width"] - width), width)
            else:
                primary_x = int(round((width - layout["primary_width"]) / 2))
            self._draw_lyric_line(
                image,
                (primary_x, layout["primary_y"]),
                layout["primary"],
                layout["primary_font"],
                theme_inactive,
                theme_active,
                progress,
                width,
            )

        if layout["secondary"]:
            secondary_x = int(round((width - layout["secondary_width"]) / 2))
            self._draw_lyric_line(
                image,
                (secondary_x, layout["secondary_y"]),
                layout["secondary"],
                layout["secondary_font"],
                theme_inactive,
                None,
                0.0,
                width,
            )

        self._compose_cached_key = key
        self._compose_cache_image = image
        return image, True

    def _draw_lyric_line(self, image, xy, text, font, base_color, active_color=None, progress=0.0, width=0):
        if not text:
            return
        measure_draw = ImageDraw.Draw(image)
        text_width = self._measure_text(measure_draw, text, font)

        mask = Image.new("L", image.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        try:
            mask_draw.text(xy, text, font=font, fill=255)
        except Exception:
            return
        scale = self._dpi_scale or 1.0
        blur = max(1.0, SHADOW_BLUR * scale)
        offset_y = max(2, int(round(SHADOW_OFFSET_Y * scale)))
        shadow_alpha = mask.filter(ImageFilter.GaussianBlur(blur))
        shadow_alpha = shadow_alpha.point(lambda value: int(value * SHADOW_STRENGTH))
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shadow.putalpha(shadow_alpha)
        image.alpha_composite(shadow, (0, offset_y))

        draw = ImageDraw.Draw(image)
        try:
            draw.text(xy, text, font=font, fill=base_color)
        except Exception:
            return
        if active_color is None:
            return

        reveal = int(round(text_width * _clamp(progress, 0.0, 1.0)))
        if reveal <= 0:
            return
        clip_left = max(0, int(round(xy[0])))
        clip_right = min(width, clip_left + reveal)
        if clip_right <= clip_left:
            return
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        try:
            overlay_draw.text(xy, text, font=font, fill=active_color)
        except Exception:
            return
        feather = max(2, int(round(REVEAL_FEATHER_PX * scale)))
        solid_right = max(clip_left, clip_right - feather)
        if solid_right <= clip_left and clip_right > clip_left:
            solid_right = clip_left + 1
        band = Image.new("L", image.size, 0)
        band_draw = ImageDraw.Draw(band)
        if solid_right > clip_left:
            band_draw.rectangle([clip_left, 0, solid_right - 1, image.size[1]], fill=255)
        if clip_right > solid_right:
            edge_w = max(1, clip_right - solid_right)
            edge = Image.new("L", (edge_w, image.size[1]), 0)
            edge_draw = ImageDraw.Draw(edge)
            for x in range(edge_w):
                edge_draw.line((x, 0, x, image.size[1]), fill=int(round(255 * ((x + 1) / edge_w))))
            band.paste(edge, (solid_right, 0))
        overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), band))
        image.alpha_composite(overlay)

    # ── 呈现：分层窗口 / 画布回退 ────────────────────────────────

    def _present(self, image):
        if self._layered_ok and self._hwnd and self._layered is not None:
            try:
                self._layered.update(self._hwnd, image, self._opacity())
            except Exception:
                pass
        else:
            self._update_canvas(image)

    def _update_canvas(self, image):
        if ImageTk is None or not self._canvas:
            return
        _index, theme = self._get_theme()
        flat = Image.new("RGB", image.size, self._transparent_color(theme))
        flat.paste(image, (0, 0), image)
        photo = ImageTk.PhotoImage(flat)
        self._canvas_photo = photo
        try:
            if self._canvas_image_id is None:
                self._canvas_image_id = self._canvas.create_image(image.size[0] // 2, image.size[1] // 2, image=photo)
            else:
                self._canvas.itemconfigure(self._canvas_image_id, image=photo)
            self._canvas.coords(self._canvas_image_id, image.size[0] // 2, image.size[1] // 2)
            self._canvas.configure(width=image.size[0], height=image.size[1])
            self._canvas.configure(scrollregion=(0, 0, image.size[0], image.size[1]))
        except Exception:
            pass

    def _pointer_inside_root(self):
        root = self._root
        if not root:
            return False
        try:
            root_x = int(root.winfo_rootx())
            root_y = int(root.winfo_rooty())
            root_w = int(root.winfo_width())
            root_h = int(root.winfo_height())
            pointer_x = int(root.winfo_pointerx())
            pointer_y = int(root.winfo_pointery())
        except Exception:
            return False
        if root_w <= 0 or root_h <= 0:
            return False
        return root_x <= pointer_x < root_x + root_w and root_y <= pointer_y < root_y + root_h

    def _show_controls(self):
        if not self._topbar or self._topbar_visible:
            return
        self._topbar.place(relx=1.0, x=-10, y=6, anchor="ne")
        try:
            self._topbar.lift()
        except Exception:
            pass
        self._topbar_visible = True

    def _hide_controls(self):
        if not self._topbar or not self._topbar_visible:
            return
        self._topbar.place_forget()
        self._topbar_visible = False

    def _refresh_controls_visibility(self):
        if not self._root or not self._topbar:
            return
        locked = bool(self.settings.get("lyric_overlay_locked", False))
        if locked:
            # 锁定时只在指针真正悬停到浮窗不透明区域（Enter 事件）时显示控件
            should_show = self._controls_hovered
        else:
            should_show = True
        if should_show:
            self._show_controls()
        else:
            self._hide_controls()

    def _pack_button_right(self, button, padx=(10, 0)):
        if not button:
            return
        button.pack_forget()
        button.pack(side="right", padx=padx)

    def _layout_controls(self, locked=None):
        if self._drag_label:
            self._drag_label.pack_forget()
            self._drag_label.pack(side="left", padx=(0, 8))
        self._pack_button_right(self._close_button)
        self._pack_button_right(self._theme_button)
        self._pack_button_right(self._font_up_button)
        self._pack_button_right(self._font_down_button)
        self._pack_button_right(self._lock_button)
        self._pack_button_right(self._topmost_button)

    def _adjust_font_scale(self, delta):
        scale = _clamp(self._font_scale() + delta, 0.6, 2.0)
        self.settings["lyric_overlay_font_scale"] = round(scale, 2)
        self._apply_state()
        self._persist()

    def _schedule_geometry_update(self):
        root = self._root
        if not root or self._closing.is_set() or self._geometry_update_pending:
            return
        self._geometry_update_pending = True

        def run():
            self._geometry_update_pending = False
            self._sync_geometry()

        try:
            root.after_idle(run)
        except Exception:
            self._geometry_update_pending = False

    def _sync_geometry(self):
        root = self._root
        if not root or self._closing.is_set():
            return
        try:
            root.update_idletasks()
        except Exception:
            return
        try:
            current_geometry = self._parse_geometry(root.geometry())
            default_geometry = self._parse_geometry(self._default_geometry())
            active_geometry = current_geometry or default_geometry or (self._default_size()[0], self._default_size()[1], 120, 120)
            width = int(active_geometry[0])
            height = int(active_geometry[1])
            x = int(active_geometry[2])
            y = int(active_geometry[3])
            screen_w = max(self._default_size()[0] + 80, root.winfo_screenwidth())
            screen_h = max(self._default_size()[1] + 120, root.winfo_screenheight())
        except Exception:
            return

        min_w, min_h = self._min_size()
        default_w, default_h = self._default_size()
        if self._user_positioned:
            width = min(max(width, min_w), max(min_w, screen_w - DEFAULT_HORIZONTAL_MARGIN))
        else:
            width = min(max(width, default_w), max(min_w, screen_w - DEFAULT_HORIZONTAL_MARGIN * 2))
        height = min(max(height, min_h), max(min_h, screen_h - 40))
        if self._user_positioned:
            x = min(max(x, 10), max(10, screen_w - width - 10))
            y = min(max(y, 10), max(10, screen_h - height - 10))
        else:
            x = max(DEFAULT_HORIZONTAL_MARGIN, (screen_w - width) // 2)
            y = max(20, screen_h - height - 28)
        geometry = f"{width}x{height}+{x}+{y}"
        try:
            if root.geometry() != geometry:
                root.geometry(geometry)
                self._force_present = True
                if self._layered_ok and self._hwnd and self._layered is not None:
                    try:
                        self._layered.set_topmost(self._hwnd, True)
                    except Exception:
                        pass
        except Exception:
            pass

    def _apply_state(self):
        if not self._root:
            return
        _index, theme = self._get_theme()
        topmost = bool(self.settings.get("lyric_overlay_topmost", True))
        locked = bool(self.settings.get("lyric_overlay_locked", False))
        if self._layered_ok:
            if self._hwnd and self._layered is not None:
                self._layered.set_topmost(self._hwnd, topmost)
        else:
            self._root.attributes("-topmost", topmost)
            transparent_color = self._transparent_color(theme)
            try:
                self._root.wm_attributes("-transparentcolor", transparent_color)
            except Exception:
                pass
            self._root.configure(bg=transparent_color)
            if self._canvas:
                self._canvas.configure(bg=transparent_color)
            try:
                self._root.attributes("-alpha", self._opacity())
            except Exception:
                pass
        geometry = self._normalize_geometry(self.settings.get("lyric_overlay_geometry"))
        try:
            self._root.geometry(geometry)
        except Exception:
            pass
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
        self._refresh_controls_visibility()
        self._force_present = True
        self._render()

    def _render(self):
        root = self._root
        if not root or self._closing.is_set():
            return
        try:
            root.update_idletasks()
        except Exception:
            pass
        default_w, default_h = self._default_size()
        try:
            width = max(1, int(root.winfo_width() or root.winfo_reqwidth() or default_w))
            height = max(1, int(root.winfo_height() or root.winfo_reqheight() or default_h))
        except Exception:
            width, height = default_w, default_h
        image, is_new = self._compose_lyric_image(width, height)
        if image is None:
            return
        if self._layered_ok and image.size[1] != height and abs(image.size[1] - height) > 8:
            # 字号/行距变化导致内容高度变化时，让窗口高度自适应
            now = time.monotonic()
            if now - self._last_content_resize_at > 0.5:
                self._last_content_resize_at = now
                try:
                    root.geometry(f"{width}x{image.size[1]}+{root.winfo_x()}+{root.winfo_y()}")
                    self._force_present = True
                    if self._hwnd and self._layered is not None:
                        self._layered.set_topmost(self._hwnd, True)
                except Exception:
                    pass
        if is_new or self._force_present:
            self._force_present = False
            self._present(image)
        self._schedule_geometry_update()

    def _schedule_render_tick(self):
        root = self._root
        if not root or self._closing.is_set():
            return
        if self._render_loop_started and self._render_after_id is not None:
            return
        self._render_loop_started = True

        def tick():
            if self._closing.is_set() or not self._root:
                self._render_after_id = None
                return
            try:
                self._render()
            finally:
                try:
                    self._render_after_id = self._root.after(LYRIC_RENDER_FPS_MS, tick)
                except Exception:
                    self._render_after_id = None

        try:
            self._render_after_id = root.after(LYRIC_RENDER_FPS_MS, tick)
        except Exception:
            self._render_after_id = None

    def _topmost_loop(self):
        """周期性无闪烁地维持置顶。"""
        root = self._root
        if not root or self._closing.is_set():
            return
        if bool(self.settings.get("lyric_overlay_topmost", True)):
            if self._layered_ok and self._hwnd and self._layered is not None:
                try:
                    self._layered.set_topmost(self._hwnd, True)
                except Exception:
                    pass
            else:
                try:
                    root.attributes("-topmost", True)
                except Exception:
                    pass
        try:
            self._topmost_after_id = root.after(TOPMOST_KEEPALIVE_MS, self._topmost_loop)
        except Exception:
            self._topmost_after_id = None

    # ── Tk 线程主循环 ────────────────────────────────────────────

    def _thread_main(self):
        try:
            import tkinter as tk
        except Exception:
            self._ready.set()
            return

        _enable_dpi_awareness()
        self._dpi_scale = _dpi_scale()

        root = tk.Tk()
        self._root = root
        root.title("波点歌词")
        root.overrideredirect(True)
        root.withdraw()

        layered = None
        if IS_WIN32 and Image is not None:
            try:
                layered = _WinLayered()
            except Exception:
                layered = None
        self._layered = layered

        default_w, default_h = self._default_size()
        min_w, min_h = self._min_size()
        root.minsize(min_w, min_h)

        if layered is not None:
            try:
                root.update_idletasks()
                hwnd = layered.get_toplevel_hwnd(root)
                self._hwnd = hwnd
                self._layered_ok = bool(hwnd) and layered.enable(hwnd)
            except Exception:
                self._layered_ok = False
        if not self._layered_ok:
            self._layered_ok = False
            self._hwnd = None
            layered = None
            self._layered = None

        _index, theme = self._get_theme()
        transparent_color = self._transparent_color(theme)
        if not self._layered_ok:
            root.configure(bg=transparent_color)
            try:
                root.wm_attributes("-transparentcolor", transparent_color)
                root.wm_attributes("-toolwindow", True)
            except Exception:
                pass
        root.protocol("WM_DELETE_WINDOW", lambda: self._queue.put(("close", None)))

        topbar_bg = transparent_color if not self._layered_ok else BUTTON_BG
        topbar = tk.Frame(root, bg=topbar_bg, height=24)
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
        self._theme_button = btn(theme["name"], self.next_theme, accent=True)
        self._font_up_button = btn("A＋", lambda: self._queue.put(("font_delta", 0.1)), accent=True)
        self._font_down_button = btn("A－", lambda: self._queue.put(("font_delta", -0.1)), accent=True)
        self._lock_button = btn("锁定", self.toggle_locked, accent=True)
        self._topmost_button = btn("置顶", self.toggle_topmost, accent=True)

        content = None
        if not self._layered_ok:
            content = tk.Frame(root, bg=transparent_color)
            content.pack(fill="both", expand=True)
            self._canvas = tk.Canvas(
                content,
                bg=transparent_color,
                highlightthickness=0,
                bd=0,
                relief="flat",
            )
            self._canvas.pack(fill="both", expand=True, padx=24, pady=(12, 10))

        def on_hover_enter(_event=None):
            self._controls_hovered = True
            self._refresh_controls_visibility()

        def on_hover_leave(_event=None):
            self._controls_hovered = False
            self._refresh_controls_visibility()

        hover_widgets = [root, topbar, self._drag_label]
        if content:
            hover_widgets.append(content)
        if self._canvas:
            hover_widgets.append(self._canvas)
        for widget in hover_widgets:
            widget.bind("<Enter>", on_hover_enter)
        root.bind("<Leave>", on_hover_leave)

        drag_targets = [root, topbar, self._drag_label]
        if self._canvas:
            drag_targets.append(self._canvas)

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
            if self._layered_ok and self._hwnd and self._layered is not None:
                # Tk 的 geometry 移动可能不带 SWP_NOZORDER，拖动后立即回到置顶层
                try:
                    self._layered.set_topmost(self._hwnd, True)
                except Exception:
                    pass

        def stop_drag(_event):
            if not self._drag_state:
                return
            self._drag_state = None
            self._user_positioned = True
            self.settings["lyric_overlay_geometry"] = root.geometry()
            self._persist()

        for widget in drag_targets:
            widget.bind("<ButtonPress-1>", start_drag)
            widget.bind("<B1-Motion>", do_drag)
            widget.bind("<ButtonRelease-1>", stop_drag)

        def on_wheel(event):
            delta = 1 if getattr(event, "delta", 0) > 0 else -1
            self._queue.put(("font_delta", delta * 0.1))

        try:
            root.bind("<MouseWheel>", on_wheel)
        except Exception:
            pass

        def watch_hover():
            if self._closing.is_set():
                return
            if not bool(self.settings.get("lyric_overlay_locked", False)):
                # 仅未锁定时按矩形范围常显控件；锁定时依赖真实 Enter/Leave 悬停事件
                self._controls_hovered = self._pointer_inside_root()
            self._refresh_controls_visibility()
            root.after(150, watch_hover)

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
                    self._state["_received_at"] = time.monotonic()
                    self._render()
                elif action == "topmost":
                    self.settings["lyric_overlay_topmost"] = bool(payload)
                    self._apply_state()
                    self._persist()
                elif action == "locked":
                    self.settings["lyric_overlay_locked"] = bool(payload)
                    self._apply_state()
                    self._persist()
                elif action == "opacity":
                    self.settings["lyric_overlay_opacity"] = _clamp(float(payload or 1.0), 0.1, 1.0)
                    self._apply_state()
                    self._persist()
                elif action == "theme_next":
                    self.settings["lyric_overlay_theme"] = (int(self.settings.get("lyric_overlay_theme", 0) or 0) + 1) % len(THEMES)
                    self.settings["lyric_overlay_primary_color"] = ""
                    self._apply_state()
                    self._persist()
                elif action == "set_theme":
                    try:
                        self.settings["lyric_overlay_theme"] = int(payload) % len(THEMES)
                    except (TypeError, ValueError):
                        continue
                    self.settings["lyric_overlay_primary_color"] = ""
                    self._apply_state()
                    self._persist()
                elif action == "font_delta":
                    self._adjust_font_scale(float(payload or 0.0))
                elif action == "font_scale":
                    try:
                        self.settings["lyric_overlay_font_scale"] = round(_clamp(float(payload), 0.6, 2.0), 2)
                    except (TypeError, ValueError):
                        continue
                    self._apply_state()
                    self._persist()
                elif action == "primary_color":
                    value = str(payload or "").strip()
                    if value and not re.match(r"^#[0-9a-fA-F]{6}$", value):
                        continue
                    self.settings["lyric_overlay_primary_color"] = value
                    self._apply_state()
                    self._persist()
                elif action == "line_gap":
                    try:
                        self.settings["lyric_overlay_line_gap"] = _clamp(int(float(payload)), 0, 60)
                    except (TypeError, ValueError):
                        continue
                    self._apply_state()
                    self._persist()
            if not self._closing.is_set():
                root.after(80, poll_queue)

        self._apply_state()
        try:
            root.deiconify()
        except Exception:
            pass
        self._ready.set()
        root.after(80, poll_queue)
        root.after(TOPMOST_KEEPALIVE_MS, self._topmost_loop)
        root.after(120, watch_hover)
        self._schedule_render_tick()
        try:
            root.mainloop()
        finally:
            for after_id in (self._render_after_id, self._topmost_after_id):
                if after_id is not None:
                    try:
                        root.after_cancel(after_id)
                    except Exception:
                        pass
            self._notify_closed()
            # 解除 tkinter 模块级默认根引用，避免进程退出时在错误的线程里
            # 销毁 Tcl 解释器（Tcl_AsyncDelete 崩溃）
            try:
                import tkinter as _tk_module
                if getattr(_tk_module, "_default_root", None) is root:
                    _tk_module._default_root = None
            except Exception:
                pass
            try:
                root.destroy()
            except Exception:
                pass
            del root
            self._root = None
            self._hwnd = None
            self._topbar = None
            self._drag_label = None
            self._topmost_button = None
            self._lock_button = None
            self._theme_button = None
            self._font_up_button = None
            self._font_down_button = None
            self._close_button = None
            self._canvas = None
            self._canvas_image_id = None
            self._canvas_photo = None
            self._compose_cache_image = None
            self._compose_cached_key = None
            self._font_cache.clear()
            # 在创建 Tk 的线程内触发一轮回收，确保 Tcl 解释器（tkapp）在
            # 正确的线程里销毁，避免进程退出时报 Tcl_AsyncDelete 崩溃
            import gc as _gc
            _gc.collect()


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
            elif action == "opacity":
                try:
                    overlay._queue.put(("opacity", float(message.get("payload"))))
                except (TypeError, ValueError):
                    pass
            elif action == "font_scale":
                try:
                    overlay._queue.put(("font_scale", float(message.get("payload"))))
                except (TypeError, ValueError):
                    pass
            elif action == "primary_color":
                overlay._queue.put(("primary_color", str(message.get("payload") or "")))
            elif action == "line_gap":
                try:
                    overlay._queue.put(("line_gap", int(float(message.get("payload") or 0))))
                except (TypeError, ValueError):
                    pass
            elif action == "theme_next":
                overlay._queue.put(("theme_next", None))
            elif action == "set_theme":
                try:
                    overlay._queue.put(("set_theme", int(message.get("payload") or 0)))
                except (TypeError, ValueError):
                    pass
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
