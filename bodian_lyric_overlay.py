#!/usr/bin/env python3

import json
import os
import queue
import math
import re
import subprocess
import sys
import threading
import time

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk
except Exception:
    Image = None
    ImageDraw = None
    ImageFilter = None
    ImageFont = None
    ImageTk = None


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TRANSPARENT_COLOR = "#010203"
BUTTON_BG = "#0b120f"
BUTTON_ACTIVE_BG = "#17231e"
DEFAULT_WIDTH = 980
DEFAULT_HEIGHT = 124
DEFAULT_MIN_WIDTH = 560
DEFAULT_HORIZONTAL_MARGIN = 40
LYRIC_OUTER_PAD_X = 32
LYRIC_OUTER_PAD_TOP = 16
LYRIC_OUTER_PAD_BOTTOM = 14
LYRIC_INNER_PAD_X = 22
LYRIC_LINE_GAP = 8
LYRIC_RENDER_FPS_MS = 40
LYRIC_SCROLL_FOCUS_RATIO = 0.46
CONTROL_FONT = ("Microsoft YaHei UI", 9, "bold")
LYRIC_FONT_MAIN = ("Microsoft YaHei UI", 28)
LYRIC_FONT_SUB = ("Microsoft YaHei UI", 20)
LYRIC_SECONDARY_COLOR = "#f4f4f4"
LYRIC_INACTIVE_COLOR = "#ffffff"
WINDOWS_FONT_CANDIDATES = [
    ("msyhbd.ttc", True),
    ("msyh.ttc", False),
    ("simsun.ttc", False),
    ("simhei.ttf", True),
    ("arial.ttf", False),
]


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
        self._topbar_visible = False
        self._controls_hovered = False
        self._geometry_update_pending = False
        self._drag_label = None
        self._topmost_button = None
        self._lock_button = None
        self._theme_button = None
        self._close_button = None
        self._drag_state = None
        self._user_positioned = bool(self.settings.get("lyric_overlay_geometry"))
        self._render_canvas = None
        self._render_image = None
        self._render_photo = None
        self._render_window = None
        self._render_image_id = None
        self._render_cached_key = None
        self._render_font_cache = {}
        self._render_size = (DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self._layout_geometry = None
        self._hover_last_at = 0.0
        self._render_loop_started = False
        self._render_after_id = None
        self._render_state = {
            "window": "",
            "primary": "",
            "secondary": "",
            "primary_is_translation": False,
            "secondary_is_translation": False,
            "current_ms": 0,
            "next_ms": 0,
            "progress": 0.0,
        }
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

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._closing.clear()
        self._close_notified = False
        self._ready.clear()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=0.2)

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

    def _parse_geometry(self, geometry):
        geometry = str(geometry or "").strip()
        match = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", geometry)
        if not match:
            return None
        return tuple(map(int, match.groups()))

    def _display_line_pair(self):
        info = self._line_pair_info()
        return info["primary"], info["secondary"]

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
        y = max(20, screen_h - height - 28)
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

    def _font_for_path(self, family, size, bold=False):
        if ImageFont is None:
            return None
        key = (family, int(size), bool(bold))
        cached = self._render_font_cache.get(key)
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
        self._render_font_cache[key] = font
        return font

    def _resolve_theme_colors(self):
        _index, theme = self._get_theme()
        active = theme.get("text") or "#63f0a3"
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

    def _build_layout(self, draw, state, width, height):
        primary = str(state.get("primary") or "").strip()
        secondary = str(state.get("secondary") or "").strip()
        progress = _clamp(state.get("progress", 0.0), 0.0, 1.0)

        primary_font_size = max(18, min(32, int(round(width * 0.024))))
        secondary_font_size = max(14, min(24, int(round(width * 0.017))))
        primary_font = self._font_for_path(LYRIC_FONT_MAIN[0], primary_font_size, False)
        secondary_font = self._font_for_path(LYRIC_FONT_SUB[0], secondary_font_size, False)
        if primary_font is None or secondary_font is None:
            return None

        primary_width = self._measure_text(draw, primary, primary_font)
        secondary_width = self._measure_text(draw, secondary, secondary_font) if secondary else 0
        line_gap = max(4, int(round(height * 0.04)))
        top_pad = max(12, int(round(height * 0.12)))
        bottom_pad = max(10, int(round(height * 0.08)))
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

    def _compose_lyric_image(self, width, height):
        if Image is None or ImageDraw is None or ImageTk is None:
            return None, None
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
        )
        if self._render_cached_key == key and self._render_image is not None:
            return self._render_image, self._render_photo

        image = Image.new("RGBA", (max(1, int(width)), max(1, int(height))), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        layout = self._build_layout(draw, state, width, height)
        if not layout:
            return None, None

        window_w = width
        window_h = max(height, layout["content_h"])
        if window_h != height:
            image = Image.new("RGBA", (max(1, int(window_w)), max(1, int(window_h))), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            layout = self._build_layout(draw, state, width, window_h)
            if not layout:
                return None, None

        primary = layout["primary"]
        secondary = layout["secondary"]
        primary_font = layout["primary_font"]
        secondary_font = layout["secondary_font"]
        inner_pad_x = layout["inner_pad_x"]
        progress = layout["progress"]

        if primary:
            if layout["long_line"]:
                overflow = max(0, layout["primary_width"] - width + inner_pad_x * 2)
                base_x = int(round((width - layout["primary_width"]) / 2))
                pan_x = int(round(overflow * 0.35 * progress))
                primary_x = _clamp(base_x + pan_x, -max(0, layout["primary_width"] - width), width)
            else:
                primary_x = int(round((width - layout["primary_width"]) / 2))
            self._draw_progress_text(image, draw, (primary_x, layout["primary_y"]), primary, primary_font, theme_inactive, theme_active, progress, width)

        if secondary:
            secondary_width = layout["secondary_width"]
            secondary_x = int(round((width - secondary_width) / 2))
            self._draw_text(draw, (secondary_x, layout["secondary_y"]), secondary, secondary_font, theme_inactive)

        self._render_cached_key = key
        self._render_image = image
        self._render_photo = ImageTk.PhotoImage(image)
        return self._render_image, self._render_photo

    def _draw_text(self, draw, xy, text, font, color):
        try:
            draw.text(xy, text, font=font, fill=color)
        except Exception:
            pass

    def _draw_progress_text(self, image, draw, xy, text, font, base_color, active_color, progress, width):
        if not text:
            return
        try:
            draw.text(xy, text, font=font, fill=base_color)
        except Exception:
            return
        reveal = int(round(self._measure_text(draw, text, font) * _clamp(progress, 0.0, 1.0)))
        if reveal <= 0:
            return
        clip_left = max(0, int(round(xy[0])))
        clip_right = min(width, clip_left + reveal)
        if clip_right <= clip_left:
            return
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.text(xy, text, font=font, fill=active_color)
        mask = Image.new("L", image.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        feather = 8
        solid_right = max(clip_left, clip_right - feather)
        if solid_right > clip_left:
            mask_draw.rectangle([clip_left, 0, solid_right, image.size[1]], fill=255)
        if clip_right > solid_right:
            edge_w = max(1, clip_right - solid_right)
            edge = Image.new("L", (edge_w, image.size[1]), 0)
            edge_draw = ImageDraw.Draw(edge)
            for x in range(edge_w):
                alpha = int(round(255 * ((x + 1) / edge_w)))
                edge_draw.line((x, 0, x, image.size[1]), fill=alpha)
            mask.paste(edge, (solid_right, 0))
        image.paste(overlay, (0, 0), mask)

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
        should_show = (not locked) or self._controls_hovered
        if should_show:
            self._show_controls()
        else:
            self._hide_controls()

    def _pack_button_right(self, button, padx=(10, 0)):
        if not button:
            return
        button.pack_forget()
        button.pack(side="right", padx=padx)

    def _layout_controls(self, locked):
        if self._drag_label:
            self._drag_label.pack_forget()
            self._drag_label.pack(side="left", padx=(0, 8))
        self._pack_button_right(self._close_button)
        self._pack_button_right(self._theme_button)
        self._pack_button_right(self._lock_button)
        self._pack_button_right(self._topmost_button)

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
            active_geometry = current_geometry or default_geometry or (DEFAULT_WIDTH, DEFAULT_HEIGHT, 120, 120)
            width = int(active_geometry[0])
            height = int(active_geometry[1])
            x = int(active_geometry[2])
            y = int(active_geometry[3])
            screen_w = max(DEFAULT_WIDTH + 80, root.winfo_screenwidth())
            screen_h = max(DEFAULT_HEIGHT + 120, root.winfo_screenheight())
        except Exception:
            return

        if self._user_positioned:
            width = min(max(width, DEFAULT_MIN_WIDTH), max(DEFAULT_MIN_WIDTH, screen_w - DEFAULT_HORIZONTAL_MARGIN))
        else:
            width = min(max(width, DEFAULT_WIDTH), max(DEFAULT_MIN_WIDTH, screen_w - DEFAULT_HORIZONTAL_MARGIN * 2))
        if self._line_label:
            self._line_label.configure(wraplength=0)
        if self._meta_label:
            self._meta_label.configure(wraplength=0)
        try:
            root.update_idletasks()
        except Exception:
            return

        try:
            req_width = int(root.winfo_reqwidth() or width)
            req_height = int(root.winfo_reqheight() or height or DEFAULT_HEIGHT)
        except Exception:
            return

        req_width = min(max(req_width, DEFAULT_MIN_WIDTH), max(DEFAULT_MIN_WIDTH, screen_w - DEFAULT_HORIZONTAL_MARGIN * 2))
        req_height = min(max(req_height, 72), max(72, screen_h - 40))
        if self._user_positioned:
            x = min(max(x, 10), max(10, screen_w - req_width - 10))
            y = min(max(y, 10), max(10, screen_h - req_height - 10))
        else:
            x = max(DEFAULT_HORIZONTAL_MARGIN, (screen_w - width) // 2)
            y = max(20, screen_h - req_height - 28)
        geometry = f"{width}x{req_height}+{x}+{y}"
        try:
            if root.geometry() != geometry:
                root.geometry(geometry)
        except Exception:
            pass

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
        self._refresh_controls_visibility()
        self._render()

    def _render(self):
        if not self._root:
            return
        root = self._root
        if not root or self._closing.is_set():
            return
        try:
            root.update_idletasks()
        except Exception:
            pass
        try:
            width = max(1, int(root.winfo_width() or root.winfo_reqwidth() or DEFAULT_WIDTH))
            height = max(1, int(root.winfo_height() or root.winfo_reqheight() or DEFAULT_HEIGHT))
        except Exception:
            width, height = DEFAULT_WIDTH, DEFAULT_HEIGHT
        self._render_size = (width, height)
        composed = self._compose_lyric_image(width, height)
        if not composed:
            return
        image, photo = composed
        if photo is None:
            return
        canvas = self._render_canvas
        if not canvas:
            return
        try:
            render_width, render_height = image.size
            if self._render_image_id is None:
                self._render_image_id = canvas.create_image(render_width // 2, render_height // 2, image=photo)
            else:
                canvas.itemconfigure(self._render_image_id, image=photo)
            canvas.coords(self._render_image_id, render_width // 2, render_height // 2)
            canvas.configure(width=render_width, height=render_height)
            canvas.configure(scrollregion=(0, 0, render_width, render_height))
            self._render_photo = photo
            self._render_image = image
        except Exception:
            pass
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
        root.minsize(560, 72)
        root.withdraw()
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

        self._drag_label = tk.Button(topbar, text="拖动", bg=BUTTON_BG, fg="#d8d8d8", activebackground=BUTTON_ACTIVE_BG, activeforeground="#ffffff", highlightthickness=0, bd=0, padx=5, pady=0, font=CONTROL_FONT, relief="flat", cursor="fleur")
        self._close_button = btn("×", lambda: self._queue.put(("close", None)))
        self._theme_button = btn("青绿", self.next_theme, accent=True)
        self._lock_button = btn("锁定", self.toggle_locked, accent=True)
        self._topmost_button = btn("置顶", self.toggle_topmost, accent=True)

        content = tk.Frame(root, bg=transparent_color)
        content.pack(fill="both", expand=True)

        self._render_canvas = tk.Canvas(
            content,
            bg=transparent_color,
            highlightthickness=0,
            bd=0,
            relief="flat",
        )
        self._render_canvas.pack(fill="both", expand=True, padx=24, pady=(12, 10))

        def on_hover_enter(_event=None):
            self._controls_hovered = True
            self._refresh_controls_visibility()

        def on_hover_leave(_event=None):
            self._controls_hovered = False
            self._refresh_controls_visibility()

        for widget in (root, content, self._render_canvas, topbar, self._drag_label):
            widget.bind("<Enter>", on_hover_enter)
        root.bind("<Leave>", on_hover_leave)

        drag_targets = [topbar, self._drag_label, self._render_canvas]

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
            self._user_positioned = True
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

        def watch_hover():
            if self._closing.is_set():
                return
            self._controls_hovered = self._pointer_inside_root()
            self._refresh_controls_visibility()
            root.after(80, watch_hover)

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
                elif action == "theme_next":
                    self.settings["lyric_overlay_theme"] = (int(self.settings.get("lyric_overlay_theme", 0) or 0) + 1) % len(THEMES)
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
        root.after(650, keep_visible)
        root.after(120, watch_hover)
        self._schedule_render_tick()
        try:
            root.mainloop()
        finally:
            if self._render_after_id is not None:
                try:
                    root.after_cancel(self._render_after_id)
                except Exception:
                    pass
            self._notify_closed()
            self._root = None
            self._topbar = None
            self._drag_label = None
            self._topmost_button = None
            self._lock_button = None
            self._theme_button = None
            self._close_button = None
            self._render_canvas = None
            self._render_image_id = None


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
