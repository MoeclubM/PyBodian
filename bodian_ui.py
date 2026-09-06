#!/usr/bin/env python3
"""
波点音乐终端界面
CTF Competition - Authorized Security Research
依赖: urwid
"""

import os
import queue
import re
import sys
import threading
import time
import unicodedata
import urwid

from bodian_terminal_images import CoverImageWidget, NativeImageScreen
from bodian_lyric_overlay import LyricOverlay, THEMES
from bodian_player import BoDianPlayer
from bodian_toolkit import (
    AUTH_FILE,
    DEFAULT_DOWNLOAD_DIR,
    QUALITY_OPTIONS,
    BoDianClient,
    _detect_ext,
    _fmt_dur,
    _generate_qr_png,
    _make_qr_url,
    _sanitize,
)
from bodian_media import mark_translation_lines


if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")


PALETTE = [
    ("bg", "light gray", "black"),
    ("title", "light green,bold", "black"),
    ("muted", "dark green", "black"),
    ("accent", "black", "dark green"),
    ("panel", "light gray", "black"),
    ("focus", "black", "light green"),
    ("play", "light green,bold", "black"),
    ("warn", "light red", "black"),
    ("lyric", "light green", "black"),
    ("lyric_active", "black", "light green"),
    ("lyric_translation", "light gray", "black"),
    ("lyric_translation_active", "black", "dark green"),
    ("bar_fill", "black", "dark green"),
    ("bar_empty", "light gray", "dark gray"),
    ("bar_handle", "black", "light green"),
]


SONG_NAME_COL = 24
SONG_DURATION_COL = 5
SONG_ARTIST_COL = 16
SONG_ALBUM_COL = 18
ALBUM_NAME_COL = 28
ALBUM_DATE_COL = 10
ALBUM_COUNT_COL = 6
RECOMMEND_VISIBLE_COUNT = 10


def _display_width(text):
    width = 0
    for ch in str(text or ""):
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _fit_display(text, width, offset=0):
    remaining_offset = max(0, int(offset or 0))
    out = []
    out_width = 0
    for ch in str(text or ""):
        char_width = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if remaining_offset:
            if remaining_offset >= char_width:
                remaining_offset -= char_width
                continue
            remaining_offset = 0
        if out_width + char_width > width:
            break
        out.append(ch)
        out_width += char_width
    return "".join(out) + " " * max(0, width - out_width)


def _scroll_offset(text, width, now_value):
    extra = max(0, _display_width(text) - width)
    if not extra:
        return 0
    pause = 4
    cycle = extra * 2 + pause * 2
    step = int(now_value * 3) % cycle
    if step < pause:
        return 0
    if step < pause + extra:
        return step - pause
    if step < pause + extra + pause:
        return extra
    return extra - (step - pause - extra - pause)


class SeekBar(urwid.WidgetWrap):

    def __init__(self, on_seek):
        self.on_seek = on_seek
        self.position_ms = 0
        self.duration_ms = 0
        self.dragging = False
        self.preview_ms = None
        self.text = urwid.Text("")
        super().__init__(self.text)

    def selectable(self):
        return True

    def set_state(self, position_ms, duration_ms):
        position_ms = max(0, int(position_ms or 0))
        duration_ms = max(0, int(duration_ms or 0))
        changed = position_ms != self.position_ms or duration_ms != self.duration_ms
        self.position_ms = position_ms
        self.duration_ms = duration_ms
        if not self.dragging:
            changed = changed or self.preview_ms is not None
            self.preview_ms = None
        if changed:
            self._invalidate()

    def render(self, size, focus=False):
        maxcol = size[0]
        display_ms = self.preview_ms if self.preview_ms is not None else self.position_ms
        time_text = f"{_fmt_dur(display_ms // 1000)} / {_fmt_dur(self.duration_ms // 1000)}"
        bar_width = max(12, maxcol - len(time_text) - 1)
        marker_index = 0
        if self.duration_ms > 0 and bar_width > 1:
            marker_index = min(bar_width - 1, max(0, int(round(display_ms / self.duration_ms * (bar_width - 1)))))
        self.text.set_text(
            [
                ("bar_fill", " " * marker_index),
                ("bar_handle", " "),
                ("bar_empty", " " * max(0, bar_width - marker_index - 1)),
                ("muted", " "),
                time_text,
            ]
        )
        return super().render(size, focus=focus)

    def mouse_event(self, size, event, button, col, row, focus):
        if button != 1 or self.duration_ms <= 0:
            return False
        if event == "mouse press":
            preview_ms, inside_bar = self._position_from_col(size[0], col)
            if not inside_bar:
                return False
            self.dragging = True
            self.preview_ms = preview_ms
            self._invalidate()
            return True
        if event == "mouse drag" and self.dragging:
            self.preview_ms, _inside_bar = self._position_from_col(size[0], col)
            self._invalidate()
            return True
        if event == "mouse release" and self.dragging:
            self.dragging = False
            self.preview_ms, _inside_bar = self._position_from_col(size[0], col)
            self.on_seek(self.preview_ms)
            self.preview_ms = None
            self._invalidate()
            return True
        return False

    def _position_from_col(self, maxcol, col):
        display_ms = self.preview_ms if self.preview_ms is not None else self.position_ms
        time_text = f"{_fmt_dur(display_ms // 1000)} / {_fmt_dur(self.duration_ms // 1000)}"
        bar_width = max(12, maxcol - len(time_text) - 1)
        if bar_width <= 1:
            return 0, False
        inside_bar = 0 <= col < bar_width
        ratio = min(1.0, max(0.0, min(max(col, 0), bar_width - 1) / (bar_width - 1)))
        return int(self.duration_ms * ratio), inside_bar


class ScrollListBox(urwid.ListBox):

    def set_scrollpos(self, position):
        if not self.body:
            return
        size = getattr(self, "_rendered_size", None)
        if not size:
            return
        maxcol, maxrow = size
        total_rows = self.rows_max(size)
        target_position = max(0, min(int(position), max(0, total_rows - maxrow)))
        current_focus = getattr(self, "focus_position", 0)
        row = 0
        target = len(self.body) - 1
        offset_inset = 0
        for index, widget in enumerate(self.body):
            row_count = widget.rows((maxcol,), index == current_focus)
            if row + row_count > target_position:
                target = index
                offset_inset = row - target_position
                break
            row += row_count
        self.change_focus(size, target, offset_inset=offset_inset)


class DragScrollBar(urwid.ScrollBar):

    def __init__(self, widget, thumb_char="█", trough_char="░", side="right", width=2, on_drag_state=None):
        self.dragging = False
        self.on_drag_state = on_drag_state
        super().__init__(widget, thumb_char=thumb_char, trough_char=trough_char, side=side, width=width)

    def mouse_event(self, size, event, button, col, row, focus):
        ow = self._original_widget
        ow_size = self._original_widget_size
        if self.scrollbar_side == "left":
            on_scrollbar = col < self.scrollbar_width
            content_col = col - self.scrollbar_width
        else:
            on_scrollbar = col >= ow_size[0]
            content_col = col
        handled = False
        if not on_scrollbar and hasattr(ow, "mouse_event"):
            handled = ow.mouse_event(ow_size, event, button, content_col, row, focus)
        if handled:
            return handled
        if button == 1 and on_scrollbar and hasattr(ow, "set_scrollpos"):
            if event == "mouse press":
                self.dragging = True
                if self.on_drag_state:
                    self.on_drag_state(True)
                return self._set_scroll_from_row(ow, ow_size, size[1], row, focus)
            if event == "mouse drag" and self.dragging:
                return self._set_scroll_from_row(ow, ow_size, size[1], row, focus)
            if event == "mouse release" and self.dragging:
                self.dragging = False
                if self.on_drag_state:
                    self.on_drag_state(False)
                return self._set_scroll_from_row(ow, ow_size, size[1], row, focus)
        elif event == "mouse release":
            self.dragging = False
            if self.on_drag_state:
                self.on_drag_state(False)
        if hasattr(ow, "set_scrollpos"):
            if button == 4:
                pos = ow.get_scrollpos(ow_size)
                ow.set_scrollpos(max(pos - 1, 0))
                return True
            if button == 5:
                pos = ow.get_scrollpos(ow_size)
                ow.set_scrollpos(pos + 1)
                return True
        return handled

    def _set_scroll_from_row(self, ow, ow_size, maxrow, row, focus):
        total_rows = ow.rows_max(ow_size, focus)
        posmax = max(0, total_rows - maxrow)
        if posmax <= 0:
            return False
        ratio = min(1.0, max(0.0, row / max(1, maxrow - 1)))
        ow.set_scrollpos(int(round(posmax * ratio)))
        return True


class BoDianUI:

    def __init__(self):
        self.client = BoDianClient()
        self.player = BoDianPlayer()
        self.queue = queue.Queue()
        self.playback_request_id = 0
        self.shutting_down = False
        self.download_dir = self.client.get_local_config("download_dir", DEFAULT_DOWNLOAD_DIR)

        self.browser_items = []
        self.current_songs = []
        self.play_queue = []
        self.play_queue_index = -1
        self.recommend_mode = False
        self.recommend_scroll_num = 1
        self.recommend_last_cold_start_time = 0
        self.recommend_loading = False
        self.current_song = None
        self.detail_song = None
        self.detail_album = None
        self.current_albums = []
        self.current_lyric_lines = []
        self.current_lyric_raw = ""
        self.active_lyric_index = -1
        self.browser_focus_index = -1
        self.song_focus_index = -1
        self.last_browser_click_index = -1
        self.last_browser_click_at = 0.0
        self.last_song_click_index = -1
        self.last_song_click_at = 0.0
        self.scroll_dragging = False
        self.center_mode = "song"
        saved_quality_key = self.client.get_local_config("quality", "6")
        self.playback_quality_key = self.client.get_local_config("playback_quality", saved_quality_key)
        self.download_quality_key = self.client.get_local_config("download_quality", saved_quality_key)
        self.lyric_overlay_enabled = bool(self.client.get_local_config("lyric_overlay_enabled", True))
        self.lyric_overlay_topmost = bool(self.client.get_local_config("lyric_overlay_topmost", True))
        self.lyric_overlay_locked = bool(self.client.get_local_config("lyric_overlay_locked", False))
        self.lyric_overlay_theme = int(self.client.get_local_config("lyric_overlay_theme", 0) or 0)
        self.lyric_overlay_opacity = float(self.client.get_local_config("lyric_overlay_opacity", 1.0) or 1.0)
        self.lyric_overlay_geometry = self.client.get_local_config("lyric_overlay_geometry", "")
        self.lyric_overlay_font_scale = float(self.client.get_local_config("lyric_overlay_font_scale", 1.0) or 1.0)
        self.lyric_overlay_primary_color = str(self.client.get_local_config("lyric_overlay_primary_color", "") or "")
        self.lyric_overlay_line_gap = int(float(self.client.get_local_config("lyric_overlay_line_gap", 0) or 0))
        self.login_restore = None
        self.login_deadline = 0
        self.login_qr_code = ""
        self.qr_checking = False
        self.next_qr_poll_at = 0
        self.lyric_overlay = None
        self._qr_overlay_widget = None

        self.status_text = urwid.Text("", wrap="clip")
        self.login_text = urwid.Text("", wrap="clip")
        self.source_text = urwid.Text("当前视图: 推荐", wrap="clip")
        self.now_playing_text = urwid.Text("未播放", wrap="clip")
        self.progress_bar = SeekBar(lambda position_ms: self._seek_current(position_ms=position_ms, quiet=True))
        self.detail_text = urwid.Text("请选择歌曲")
        self.cover_widget = CoverImageWidget()
        self.cover_text = self.cover_widget.text
        self.selected_cover_widget = CoverImageWidget()
        self.selected_cover_text = self.selected_cover_widget.text
        self.footer_cover_widget = CoverImageWidget(on_activate=self._toggle_playback_page, max_cols=14, max_rows=8)
        self.current_playback_quality_key = None
        self.playback_quality_button = urwid.Button("播放音质: 当前无歌曲", on_press=lambda *_: self._open_quality_picker("playback"))
        self.player_playback_quality_button = urwid.Button("播放音质: 当前无歌曲", on_press=lambda *_: self._open_quality_picker("playback"))
        self.playback_song_button = urwid.Button("", on_press=self._open_current_playback_detail)
        self.playback_artist_button = urwid.Button("", on_press=self._open_current_playback_artist)
        self.playback_album_button = urwid.Button("", on_press=self._open_current_playback_album)
        self.download_quality_header_button = urwid.Button("下载音质", on_press=lambda *_: self._open_quality_picker("download"))
        self.playback_quality_button_map = urwid.AttrMap(self.playback_quality_button, None, "focus")
        self.player_playback_quality_button_map = urwid.AttrMap(self.player_playback_quality_button, None, "focus")
        self.playback_song_button_map = urwid.AttrMap(self.playback_song_button, "play", "focus")
        self.playback_artist_button_map = urwid.AttrMap(self.playback_artist_button, None, "focus")
        self.playback_album_button_map = urwid.AttrMap(self.playback_album_button, None, "focus")
        self.download_quality_header_button_map = urwid.AttrMap(self.download_quality_header_button, None, "focus")
        for button in (self.playback_song_button, self.playback_artist_button, self.playback_album_button):
            button.button_left = urwid.Text("")
            button.button_right = urwid.Text("")
        self.search_edit = urwid.Edit(("title", "搜索: "), "")
        self.cover_binary_cache = {}
        self.cover_request_key = 0
        self.selected_cover_request_key = 0
        self.quality_choices = []
        self.page_mode = "main"
        self.selected_cover_widget.set_placeholder("未选择")

        self.browser_walker = urwid.SimpleFocusListWalker([])
        self.song_walker = urwid.SimpleFocusListWalker([])
        self.lyric_walker = urwid.SimpleFocusListWalker([urwid.Text("歌词将在此显示")])
        self.player_lyric_walker = urwid.SimpleFocusListWalker([urwid.Text("歌词将在此显示")])

        self.browser_list = ScrollListBox(self.browser_walker)
        self.song_list = ScrollListBox(self.song_walker)
        self.lyric_list = ScrollListBox(self.lyric_walker)
        self.player_lyric_list = ScrollListBox(self.player_lyric_walker)
        set_dragging = lambda dragging: setattr(self, "scroll_dragging", dragging)
        self.browser_view = DragScrollBar(self.browser_list, on_drag_state=set_dragging)
        self.song_view = DragScrollBar(self.song_list, on_drag_state=set_dragging)
        self.lyric_view = DragScrollBar(self.lyric_list)
        self.player_lyric_view = DragScrollBar(self.player_lyric_list)
        self.song_row_buttons = []

        self.main_body = self._build_body()
        self.playback_body = self._build_playback_body()
        self.frame = urwid.Frame(
            body=self.main_body,
            header=self._build_header(),
            footer=self._build_footer(),
        )
        self.loop = urwid.MainLoop(self.frame, PALETTE, screen=NativeImageScreen(), unhandled_input=self._on_input)
        self.loop.screen.set_terminal_properties(colors=2**24)

        self._update_login_text()
        self._load_recommend()
        self.loop.set_alarm_in(0.4, self._tick)
        if self.lyric_overlay_enabled:
            self._ensure_lyric_overlay()

    def _build_header(self):
        buttons = urwid.Columns(
            [
                ("pack", self._button("二维码登录", self._on_qr_login)),
                ("pack", self._button("手动登录", self._open_manual_login)),
                ("pack", self._button("提取凭证", self._on_extract)),
                ("pack", self._button("账号信息", self._open_auth_info)),
                ("pack", self._button("登出", self._on_logout)),
                ("pack", self._button("下载目录", self._open_download_dir)),
                ("pack", self._button("歌词浮窗", self._toggle_lyric_overlay)),
                ("pack", self.download_quality_header_button_map),
            ],
            dividechars=1,
        )
        top = urwid.Columns(
            [
                ("weight", 2, urwid.Text(("title", "波点音乐 TUI"))),
                ("weight", 3, self.login_text),
                ("weight", 5, buttons),
            ],
            dividechars=2,
        )
        return top

    def _build_body(self):
        self.left_panel = urwid.Pile(
            [
                ("pack", self.search_edit),
                ("pack", urwid.Divider()),
                ("pack", urwid.Pile(
                    [
                        self._button("推荐", lambda *_: self._load_recommend()),
                        self._button("喜欢的音乐", lambda *_: self._load_fond()),
                        self._button("创建歌单", lambda *_: self._load_created_playlists()),
                        self._button("收藏歌单", lambda *_: self._load_collected_playlists()),
                        self._button("关注艺人", lambda *_: self._load_followed_artists()),
                        self._button("播放历史", lambda *_: self._load_history()),
                        self._button("本地收藏", lambda *_: self._load_favorites()),
                    ]
                )),
                ("pack", urwid.Divider()),
                ("weight", 1, self.browser_view),
            ]
        )
        left = urwid.LineBox(
            self.left_panel,
        )
        center_toolbar = urwid.Columns(
            [
                ("pack", self._button("播放选中", self._play_selected)),
                ("pack", self._button("下载歌曲", self._download_selected_song)),
                ("pack", self._button("收藏", self._collect_selected_song)),
                ("pack", self._button("取消收藏", self._uncollect_selected_song)),
                ("pack", self._button("查看艺人", self._open_selected_artist)),
                ("pack", self._button("查看专辑", self._open_selected_album)),
                ("pack", self._button("保存歌词", self._download_selected_lyric)),
            ],
            dividechars=1,
        )
        self.center_panel = urwid.LineBox(
            urwid.Pile(
                [
                    ("pack", self.source_text),
                    ("pack", urwid.Divider()),
                    ("pack", center_toolbar),
                    ("pack", urwid.Divider()),
                    ("weight", 1, self.song_view),
                ]
            ),
            title="歌曲",
        )
        detail_columns = urwid.Columns(
            [
                (
                    "weight",
                    4,
                    urwid.Filler(self.detail_text, valign="top"),
                ),
                (
                    "weight",
                    5,
                    urwid.Pile(
                        [
                            (
                                "pack",
                                self.playback_quality_button_map,
                            ),
                            ("pack", urwid.Divider()),
                            (
                                "weight",
                                1,
                                urwid.Padding(
                                    urwid.Filler(self.selected_cover_widget, valign="top", top=1, bottom=0),
                                    left=1,
                                    right=1,
                                ),
                            ),
                        ]
                    ),
                ),
            ],
            dividechars=1,
        )
        detail_panel = urwid.LineBox(detail_columns)
        lyric_panel = urwid.LineBox(self.lyric_view, title="歌词")
        right = urwid.Pile(
            [
                ("weight", 10, detail_panel),
                ("weight", 11, lyric_panel),
            ]
        )
        return urwid.Columns(
            [
                ("weight", 28, left),
                ("weight", 45, self.center_panel),
                ("weight", 37, right),
            ],
            dividechars=1,
        )

    def _build_playback_body(self):
        return urwid.Columns(
            [
                (
                    "weight",
                    38,
                    urwid.Pile(
                        [
                            (
                                "weight",
                                1,
                                urwid.Padding(
                                    urwid.Filler(self.cover_widget, valign="middle"),
                                    left=3,
                                    right=1,
                                ),
                            ),
                            (
                                "pack",
                                urwid.Padding(
                                    urwid.Pile(
                                        [
                                            self.playback_song_button_map,
                                            self.playback_artist_button_map,
                                            self.playback_album_button_map,
                                        ]
                                    ),
                                    left=3,
                                    right=1,
                                ),
                            ),
                        ]
                    ),
                ),
                (
                    "weight",
                    62,
                    urwid.LineBox(
                        urwid.Pile(
                            [
                                ("pack", self.player_playback_quality_button_map),
                                ("pack", urwid.Divider()),
                                ("weight", 1, self.player_lyric_view),
                            ]
                        ),
                        title="播放歌词",
                    ),
                ),
            ],
            dividechars=1,
        )

    def _build_footer(self):
        controls = urwid.Columns(
            [
                ("pack", self._button("上一首", self._play_previous)),
                ("pack", self._button("退10秒", lambda *_: self._seek_current(-10000))),
                ("pack", self._button("播放/暂停", self._toggle_playback)),
                ("pack", self._button("进10秒", lambda *_: self._seek_current(10000))),
                ("pack", self._button("重播", self._restart_current_song)),
                ("pack", self._button("停止", self._stop_playback)),
                ("pack", self._button("下一首", self._play_next)),
                ("weight", 1, self.now_playing_text),
            ],
            dividechars=1,
        )
        footer_main = urwid.Pile([controls, self.progress_bar, self.status_text])
        hints = urwid.Text("快捷键: / 搜索 | Enter 执行或打开 | Space 播放/暂停 | n 下一首 | p 上一首 | s 停止 | r 重播 | [ 后退10秒 | ] 前进10秒 | d 下载 | l 保存歌词 | v 播放页 | o 浮窗 | t 置顶 | k 锁定 | c 换色 | Esc 返回 | q 退出", wrap="clip")
        footer_cover = urwid.BoxAdapter(self.footer_cover_widget, 4)
        return urwid.Pile(
            [
                (
                    "pack",
                    urwid.Columns(
                        [
                            ("given", 14, footer_cover),
                            ("weight", 1, footer_main),
                        ],
                        dividechars=1,
                    ),
                ),
                ("pack", hints),
            ]
        )

    def _button(self, label, callback):
        return urwid.AttrMap(urwid.Button(label, on_press=callback), None, "focus")

    def _update_login_text(self):
        if self.client.logged_in:
            nick = self.client.nickname or self.client.uid
            audio_state = "播放会话: 主账号"
            if self.client.qq_uid != "-1" and self.client.qq_token:
                audio_state += " | 已缓存QQ会话"
            elif self.client.qq_open_id and self.client.qq_open_token:
                audio_state += " | 已提取QQ授权"
            self.login_text.set_text(("play", f"已登录: {nick} (UID={self.client.uid}) | {audio_state}"))
        else:
            self.login_text.set_text(("warn", "未登录"))

    def _set_status(self, message, level="muted"):
        self.status_text.set_text((level, message))

    def _next_playback_request(self):
        self.playback_request_id += 1
        return self.playback_request_id

    def _is_current_playback_request(self, request_id):
        return request_id == self.playback_request_id and not self.shutting_down

    def _overlay_settings(self):
        return {
            "lyric_overlay_enabled": self.lyric_overlay_enabled,
            "lyric_overlay_topmost": self.lyric_overlay_topmost,
            "lyric_overlay_locked": self.lyric_overlay_locked,
            "lyric_overlay_theme": self.lyric_overlay_theme,
            "lyric_overlay_opacity": self.lyric_overlay_opacity,
            "lyric_overlay_geometry": self.lyric_overlay_geometry,
            "lyric_overlay_font_scale": self.lyric_overlay_font_scale,
            "lyric_overlay_primary_color": self.lyric_overlay_primary_color,
            "lyric_overlay_line_gap": self.lyric_overlay_line_gap,
        }

    def _save_overlay_settings(self):
        self.client.set_local_config(**self._overlay_settings())

    def _on_overlay_settings_changed(self, settings):
        def apply():
            self.lyric_overlay_enabled = bool(settings.get("lyric_overlay_enabled", self.lyric_overlay_enabled))
            self.lyric_overlay_topmost = bool(settings.get("lyric_overlay_topmost", self.lyric_overlay_topmost))
            self.lyric_overlay_locked = bool(settings.get("lyric_overlay_locked", self.lyric_overlay_locked))
            self.lyric_overlay_theme = int(settings.get("lyric_overlay_theme", self.lyric_overlay_theme) or 0)
            self.lyric_overlay_opacity = float(settings.get("lyric_overlay_opacity", self.lyric_overlay_opacity) or self.lyric_overlay_opacity)
            self.lyric_overlay_geometry = settings.get("lyric_overlay_geometry", self.lyric_overlay_geometry)
            self.lyric_overlay_font_scale = float(settings.get("lyric_overlay_font_scale", self.lyric_overlay_font_scale) or 1.0)
            self.lyric_overlay_primary_color = str(settings.get("lyric_overlay_primary_color", self.lyric_overlay_primary_color) or "")
            self.lyric_overlay_line_gap = int(float(settings.get("lyric_overlay_line_gap", self.lyric_overlay_line_gap) or 0))
            self._save_overlay_settings()

        if threading.current_thread() is threading.main_thread():
            apply()
        else:
            self.queue.put(apply)

    def _on_overlay_closed(self):
        def apply():
            self.lyric_overlay = None
            self._save_overlay_settings()

        if threading.current_thread() is threading.main_thread():
            apply()
        else:
            self.queue.put(apply)

    def _ensure_lyric_overlay(self):
        if self.lyric_overlay:
            return self.lyric_overlay
        self.lyric_overlay = LyricOverlay(
            settings=self._overlay_settings(),
            on_settings_change=self._on_overlay_settings_changed,
            on_closed=self._on_overlay_closed,
        )
        self.lyric_overlay.start()
        if not self.lyric_overlay.is_alive():
            self.lyric_overlay = None
            self.lyric_overlay_enabled = False
            self._save_overlay_settings()
            self._set_status("歌词浮窗启动失败", "warn")
            return None
        self._push_lyric_overlay(force=True)
        return self.lyric_overlay

    def _push_lyric_overlay(self, force=False):
        if not self.lyric_overlay:
            return
        if not force and not self.current_song and not self.current_lyric_raw:
            return
        song_title = self.current_song["name"] if self.current_song else ""
        artist = self.current_song["artist"] if self.current_song else ""
        text = self.current_lyric_raw or ("开始播放后加载歌词" if not self.current_song else "正在加载歌词")
        self.lyric_overlay.update(
            song_title=song_title,
            artist=artist,
            text=text,
            lines=list(self.current_lyric_lines),
            active_index=self.active_lyric_index,
            position_ms=self.player.get_position_ms(),
            duration_ms=self.player.duration_ms,
            playback_state=self.player.state,
        )

    def _toggle_lyric_overlay(self, *_args):
        if self.lyric_overlay:
            self.lyric_overlay.close()
            self.lyric_overlay = None
            self._set_status("歌词浮窗已关闭")
            return
        self.lyric_overlay_enabled = True
        self._save_overlay_settings()
        self._ensure_lyric_overlay()
        self._set_status("歌词浮窗已开启")

    def _toggle_lyric_overlay_topmost(self, *_args):
        self.lyric_overlay_topmost = not self.lyric_overlay_topmost
        self._save_overlay_settings()
        if self.lyric_overlay:
            self.lyric_overlay.set_topmost(self.lyric_overlay_topmost)
        self._set_status(f"歌词浮窗置顶: {'开启' if self.lyric_overlay_topmost else '关闭'}")

    def _toggle_lyric_overlay_lock(self, *_args):
        self.lyric_overlay_locked = not self.lyric_overlay_locked
        self._save_overlay_settings()
        if self.lyric_overlay:
            self.lyric_overlay.set_locked(self.lyric_overlay_locked)
        self._set_status(f"歌词浮窗锁定: {'开启' if self.lyric_overlay_locked else '关闭'}")

    def _cycle_lyric_overlay_theme(self, *_args):
        self.lyric_overlay_theme = (self.lyric_overlay_theme + 1) % len(THEMES)
        self._save_overlay_settings()
        if self.lyric_overlay:
            self.lyric_overlay.next_theme()
        self._set_status("歌词浮窗颜色已切换")

    def _async(self, status, worker, done):
        self._set_status(status)

        def run():
            try:
                result = worker()
                self.queue.put(lambda: done(result, None))
            except Exception as exc:
                message = str(exc)
                self.queue.put(lambda message=message: done(None, message))

        threading.Thread(target=run, daemon=True).start()

    def _confirm_exit(self, *_args):
        if self.loop.widget is not self.frame:
            self._close_overlay()
        body = urwid.Pile(
            [
                urwid.Text("确认退出并停止播放吗？"),
                urwid.Divider(),
                urwid.Columns(
                    [
                        ("pack", self._button("退出", self._exit_app)),
                        ("pack", self._button("取消", lambda *_: self._close_overlay())),
                    ],
                    dividechars=1,
                ),
            ]
        )
        self._open_overlay("退出确认", body)

    def _shutdown(self):
        self.shutting_down = True
        self._next_playback_request()
        if self.lyric_overlay:
            self.lyric_overlay.close()
            self.lyric_overlay = None
        self.player.close()
        if sys.platform == "win32":
            try:
                os.system("taskkill /IM ffplay.exe /T /F >nul 2>&1")
            except Exception:
                pass

    def _exit_app(self, *_args):
        if self.loop.widget is not self.frame:
            self._close_overlay()
        self._shutdown()
        raise urwid.ExitMainLoop()

    def _clear_browser(self):
        self.browser_items = []
        self.browser_focus_index = -1
        self.last_browser_click_index = -1
        self.last_browser_click_at = 0.0
        self.browser_walker[:] = [urwid.Text("当前视图没有子列表")]

    def _set_browser_items(self, title, items, kind):
        self.browser_items = [{"kind": kind, **item} for item in items]
        self.browser_focus_index = -1
        self.last_browser_click_index = -1
        self.last_browser_click_at = 0.0
        rows = [urwid.Text(("muted", title))]
        for index, item in enumerate(self.browser_items):
            if kind == "artist":
                meta = f"{item.get('musicCnt', 0)} 首 / {item.get('albumCnt', 0)} 专辑"
            else:
                meta = f"{item.get('musicCount', 0)} 首"
            label = f"{index + 1:>2}. {item.get('name', '?')}  {meta}"
            button = urwid.Button(label)
            urwid.connect_signal(button, "click", lambda current_button, current_index=index: self._activate_browser_row(current_button, current_index))
            rows.append(urwid.AttrMap(button, None, "focus"))
        self.browser_walker[:] = rows
        if len(rows) > 1:
            self.browser_list.focus_position = 1

    def _update_browser_from_focus(self, force=False):
        if self.scroll_dragging:
            return
        if not self.browser_items or not self.browser_walker:
            return
        try:
            focus = self.browser_list.focus_position
        except IndexError:
            return
        browser_index = focus - 1
        if browser_index < 0 or browser_index >= len(self.browser_items):
            return
        if not force and browser_index == self.browser_focus_index:
            return
        self.browser_focus_index = browser_index
        item = self.browser_items[browser_index]
        if item["kind"] == "artist":
            return
        if item["kind"] == "album":
            self._open_album_item(item)
            return
        self._open_playlist_item(item)

    def _set_song_list(self, title, songs):
        self.recommend_mode = title == "推荐"
        self.center_mode = "song"
        self.center_panel.set_title("歌曲")
        self.current_songs = songs
        self.current_albums = []
        self.detail_album = None
        self.last_song_click_index = -1
        self.last_song_click_at = 0.0
        self.source_text.set_text(f"当前视图: {title}  ({len(songs)} 首)")
        if not songs:
            self.song_row_buttons = []
            self.song_walker[:] = [urwid.Text("没有可显示的歌曲")]
            self.detail_song = None
            self.detail_text.set_text("请选择歌曲")
            self._set_quality_choices(None)
            self.selected_cover_widget.set_placeholder("未选择")
            if not self.current_song:
                self._show_lyric_text("暂无歌词")
            return
        self._rebuild_song_rows()
        self.song_list.focus_position = 0
        self.song_focus_index = -1
        self._update_detail_from_focus(force=True)

    def _set_album_list(self, title, albums):
        self.recommend_mode = False
        self.center_mode = "album"
        self.center_panel.set_title("专辑")
        self.current_albums = albums
        self.current_songs = []
        self.detail_song = None
        self.detail_album = None
        self.last_song_click_index = -1
        self.last_song_click_at = 0.0
        self.source_text.set_text(f"当前视图: {title}  ({len(albums)} 张专辑)")
        if not albums:
            self.song_row_buttons = []
            self.song_walker[:] = [urwid.Text("没有可显示的专辑")]
            self.detail_text.set_text("请选择专辑")
            self._set_quality_choices(None)
            self.selected_cover_widget.set_placeholder("未选择")
            if not self.current_song:
                self._show_lyric_text("选择专辑后查看歌曲")
            return
        self._rebuild_song_rows()
        self.song_list.focus_position = 0
        self.song_focus_index = -1
        self._update_detail_from_focus(force=True)

    def _rebuild_song_rows(self):
        rows = []
        buttons = []
        if self.center_mode == "album":
            now_value = time.monotonic()
            for index, album in enumerate(self.current_albums):
                button = urwid.Button("")
                urwid.connect_signal(button, "click", lambda current_button, current_index=index: self._activate_song_row(current_button, current_index))
                button.button_left = urwid.Text("")
                button.button_right = urwid.Text("")
                buttons.append(button)
                rows.append(urwid.AttrMap(button, None, "focus"))
                button.set_label(self._get_album_row_label(album, index, index == self.song_focus_index, now_value))
            self.song_row_buttons = buttons
            self.song_walker[:] = rows
            return
        now_value = time.monotonic()
        for index, song in enumerate(self.current_songs):
            button = urwid.Button("")
            urwid.connect_signal(button, "click", lambda current_button, current_index=index: self._activate_song_row(current_button, current_index))
            button.button_left = urwid.Text("")
            button.button_right = urwid.Text("")
            buttons.append(button)
            rows.append(urwid.AttrMap(button, None, "focus"))
            button.set_label(self._get_song_row_label(song, index, index == self.song_focus_index, now_value))
        self.song_row_buttons = buttons
        self.song_walker[:] = rows

    def _get_song_row_label(self, song, index, focused, now_value):
        offset_name = _scroll_offset(song["name"], SONG_NAME_COL, now_value) if focused else 0
        offset_artist = _scroll_offset(song["artist"], SONG_ARTIST_COL, now_value) if focused else 0
        offset_album = _scroll_offset(song["album"], SONG_ALBUM_COL, now_value) if focused else 0
        prefix = "* " if self.current_song and song["id"] == self.current_song["id"] else "  "
        return (
            f"{prefix}{index + 1:>2}. "
            f"{_fit_display(song['name'], SONG_NAME_COL, offset_name)}  "
            f"{_fit_display(_fmt_dur(song['duration']), SONG_DURATION_COL)}  "
            f"{_fit_display(song['artist'], SONG_ARTIST_COL, offset_artist)}  "
            f"{_fit_display(song['album'], SONG_ALBUM_COL, offset_album)}"
        )

    def _get_album_row_label(self, album, index, focused, now_value):
        song_count = album.get("musicCount") or album.get("songNum") or album.get("musicCnt") or 0
        release_date = str(album.get("showtime") or album.get("releaseDate") or "-")[:10]
        album_name = str(album.get("name") or "?")
        offset_name = _scroll_offset(album_name, ALBUM_NAME_COL, now_value) if focused else 0
        return (
            f" {index + 1:>2}. "
            f"{_fit_display(album_name, ALBUM_NAME_COL, offset_name)}  "
            f"{_fit_display(release_date, ALBUM_DATE_COL)}  "
            f"{_fit_display(f'{song_count} 首', ALBUM_COUNT_COL)}"
        )

    def _refresh_song_row_labels(self):
        if not self.song_row_buttons:
            return
        now_value = time.monotonic()
        if self.center_mode == "album":
            for index, button in enumerate(self.song_row_buttons):
                if index >= len(self.current_albums):
                    continue
                button.set_label(self._get_album_row_label(self.current_albums[index], index, index == self.song_focus_index, now_value))
            return
        for index, button in enumerate(self.song_row_buttons):
            if index >= len(self.current_songs):
                continue
            button.set_label(self._get_song_row_label(self.current_songs[index], index, index == self.song_focus_index, now_value))

    def _set_quality_choices(self, song):
        self.quality_choices = []
        if not song:
            message = "当前无歌曲" if self.center_mode == "song" else "请打开专辑后选择歌曲"
            self.playback_quality_button.set_label(f"播放音质: {message}")
            self._update_player_playback_quality_button()
            return
        choices = self.client.get_song_quality_choices(song)
        if not choices:
            self.playback_quality_button.set_label("播放音质: 当前歌曲无可用音质")
            self._update_player_playback_quality_button()
            return
        self.quality_choices = choices
        playback_key = self.playback_quality_key
        if self.current_song and song["id"] == self.current_song["id"] and self.current_playback_quality_key:
            playback_key = self.current_playback_quality_key
        resolved_playback_key = self.client.resolve_song_quality(song, playback_key) or choices[0]["key"]
        self.playback_quality_key = self.client.resolve_song_quality(song, self.playback_quality_key) or choices[0]["key"]
        self.download_quality_key = self.client.resolve_song_quality(song, self.download_quality_key) or choices[0]["key"]
        playback_label = next(choice["label"] for choice in choices if choice["key"] == resolved_playback_key)
        self.playback_quality_button.set_label(f"播放音质: {playback_label}")
        self._update_player_playback_quality_button()

    def _update_player_playback_quality_button(self):
        if not self.current_song:
            self.player_playback_quality_button.set_label("播放音质: 当前无歌曲")
            return
        choices = self.client.get_song_quality_choices(self.current_song)
        if not choices:
            self.player_playback_quality_button.set_label("播放音质: 当前歌曲无可用音质")
            return
        selected_key = self.current_playback_quality_key or self.playback_quality_key
        resolved_key = self.client.resolve_song_quality(self.current_song, selected_key) or choices[0]["key"]
        label = next(choice["label"] for choice in choices if choice["key"] == resolved_key)
        self.player_playback_quality_button.set_label(f"播放音质: {label}")

    def _show_lyric_text(self, text):
        self.current_lyric_lines = []
        self.current_lyric_raw = text
        self.active_lyric_index = -1
        lines = text.splitlines() or ["暂无歌词"]
        self.lyric_walker[:] = [urwid.Text(("muted", line)) for line in lines]
        self.player_lyric_walker[:] = [urwid.Text(("muted", line)) for line in lines]
        self._push_lyric_overlay(force=True)

    def _set_playing_detail(self, song):
        if not song:
            self.playback_song_button.set_label("未播放")
            self.playback_artist_button.set_label("-")
            self.playback_album_button.set_label("-")
            return
        self.playback_song_button.set_label(song["name"])
        self.playback_artist_button.set_label(song["artist"] or "-")
        self.playback_album_button.set_label(song["album"] or "-")

    def _open_current_playback_detail(self, *_args):
        song = self.current_song
        if not song:
            return
        self._close_playback_page()
        for index, current in enumerate(self.current_songs):
            if current["id"] != song["id"]:
                continue
            self.song_list.focus_position = index
            self.song_focus_index = -1
            self._update_detail_from_focus(force=True)
            return
        self.detail_song = song
        self.detail_album = None
        self.detail_text.set_text(
            "\n".join(
                [
                    f"歌曲: {song['name']}",
                    f"歌手: {song['artist']}",
                    f"专辑: {song['album'] or '-'}",
                    f"时长: {_fmt_dur(song['duration'])}",
                    f"发行: {song['releaseDate'] or '-'}",
                    f"歌曲 ID: {song['id']}",
                ]
            )
        )
        self._set_quality_choices(song)
        self._load_selected_cover(song)
        self.frame._invalidate()

    def _open_current_playback_artist(self, *_args):
        song = self.current_song
        if not song:
            return
        self.detail_song = song
        self._close_playback_page()
        self._open_selected_artist()

    def _open_current_playback_album(self, *_args):
        song = self.current_song
        if not song:
            return
        self.detail_song = song
        self._close_playback_page()
        self._open_selected_album()

    def _cover_url(self, item):
        return item.get("albumPic") or item.get("albumPic120") or item.get("artistPic") or item.get("pic") or ""

    def _set_playing_cover_placeholder(self, message, attr="muted"):
        self.cover_widget.set_placeholder(message, attr)
        self.footer_cover_widget.set_placeholder(message, attr)
        self.frame._invalidate()

    def _set_playing_cover_image(self, binary):
        self.cover_widget.set_image(binary)
        self.footer_cover_widget.set_image(binary)
        self.frame._invalidate()

    def _load_cover_image(self, song):
        self.cover_request_key += 1
        request_key = self.cover_request_key
        cover_url = self._cover_url(song)
        if not cover_url:
            self._set_playing_cover_placeholder("暂无封面")
            return
        cached_binary = self.cover_binary_cache.get(cover_url)
        if cached_binary:
            self._set_playing_cover_image(cached_binary)
            return
        self._set_playing_cover_placeholder("封面加载中")

        def run():
            try:
                cover_binary = self.client._request_binary(cover_url)
                self.queue.put(lambda: done(cover_binary, None))
            except Exception as exc:
                message = str(exc)
                self.queue.put(lambda message=message: done(None, message))

        def done(cover_binary, error):
            if request_key != self.cover_request_key:
                return
            if not self.current_song or self.current_song["id"] != song["id"]:
                return
            if error:
                self._set_playing_cover_placeholder("封面加载失败", "warn")
                message = str(error).splitlines()[0].strip() or "未知错误"
                self._set_status(f"封面加载失败: {message}", "warn")
                return
            self.cover_binary_cache[cover_url] = cover_binary
            self._set_playing_cover_image(cover_binary)

        threading.Thread(target=run, daemon=True).start()

    def _load_selected_cover(self, item):
        self.selected_cover_request_key += 1
        request_key = self.selected_cover_request_key
        cover_url = self._cover_url(item)
        if not cover_url:
            self.selected_cover_widget.set_placeholder("暂无封面")
            self.frame._invalidate()
            return
        cached_binary = self.cover_binary_cache.get(cover_url)
        if cached_binary:
            self.selected_cover_widget.set_image(cached_binary)
            self.frame._invalidate()
            return
        if not self.selected_cover_widget.image_binary:
            self.selected_cover_widget.set_placeholder("封面加载中")
            self.frame._invalidate()

        def run():
            try:
                cover_binary = self.client._request_binary(cover_url)
                self.queue.put(lambda: done(cover_binary, None))
            except Exception as exc:
                message = str(exc)
                self.queue.put(lambda message=message: done(None, message))

        def done(cover_binary, error):
            if request_key != self.selected_cover_request_key:
                return
            if self.detail_song is not item and self.detail_album is not item:
                return
            if error:
                self.selected_cover_widget.set_placeholder("封面加载失败", "warn")
                self.frame._invalidate()
                return
            self.cover_binary_cache[cover_url] = cover_binary
            self.selected_cover_widget.set_image(cover_binary)
            self.frame._invalidate()

        threading.Thread(target=run, daemon=True).start()

    def _refresh_lyric_rows(self):
        rows = []
        player_rows = []
        for index, line in enumerate(self.current_lyric_lines):
            translation = line.get("translation", False)
            if index == self.active_lyric_index:
                attr = "lyric_translation_active" if translation else "lyric_active"
            else:
                attr = "lyric_translation" if translation else "lyric"
            text = f"  {line['text']}" if translation else line["text"]
            rows.append(urwid.Text((attr, text)))
            player_rows.append(urwid.Text((attr, text)))
        self.lyric_walker[:] = rows
        self.player_lyric_walker[:] = player_rows

    def _show_lyric_lines(self, lyric):
        self.current_lyric_lines = []
        self.current_lyric_raw = lyric.get("raw", "")
        self.active_lyric_index = -1
        lines = lyric.get("lines", [])
        if not lines:
            self._show_lyric_text(self.current_lyric_raw or "暂无歌词")
            return
        self.current_lyric_lines = mark_translation_lines(lines)
        self._refresh_lyric_rows()
        self._push_lyric_overlay(force=True)

    def _activate_song_row(self, _button=None, user_data=None):
        items = self.current_albums if self.center_mode == "album" else self.current_songs
        index = self.song_list.focus_position if user_data is None else user_data
        if index is None or index < 0 or index >= len(items):
            return
        self.song_list.focus_position = index
        self.song_focus_index = -1
        self._update_detail_from_focus(force=True)
        now = time.monotonic()
        if self.last_song_click_index == index and now - self.last_song_click_at <= 0.35:
            self.last_song_click_index = -1
            self.last_song_click_at = 0.0
            if self.center_mode == "album":
                self._open_album_item(self.current_albums[index])
                return
            self._play_song_at(user_data=index)
            return
        self.last_song_click_index = index
        self.last_song_click_at = now

    def _activate_browser_row(self, _button=None, user_data=None):
        if user_data is None or user_data < 0 or user_data >= len(self.browser_items):
            return
        self.browser_list.focus_position = user_data + 1
        self.browser_focus_index = user_data
        item = self.browser_items[user_data]
        if item["kind"] != "artist":
            self._open_browser_item(user_data=user_data)
            return
        now = time.monotonic()
        if self.last_browser_click_index == user_data and now - self.last_browser_click_at <= 0.35:
            self.last_browser_click_index = -1
            self.last_browser_click_at = 0.0
            self._open_browser_item(user_data=user_data)
            return
        self.last_browser_click_index = user_data
        self.last_browser_click_at = now

    def _update_detail_from_focus(self, force=False):
        if self.scroll_dragging:
            return
        items = self.current_albums if self.center_mode == "album" else self.current_songs
        if not items or not self.song_walker:
            return
        try:
            focus = self.song_list.focus_position
        except IndexError:
            return
        if not force and focus == self.song_focus_index:
            return
        self.song_focus_index = focus
        self._refresh_song_row_labels()
        if self.center_mode == "album":
            album = self.current_albums[focus]
            self.detail_album = album
            self.detail_song = None
            song_count = album.get("musicCount") or album.get("songNum") or album.get("musicCnt") or "-"
            album_id = album.get("albumId") or album.get("id") or "-"
            self.detail_text.set_text(
                "\n".join(
                    [
                        f"专辑: {album.get('name') or '-'}",
                        f"歌手: {album.get('artist') or album.get('artistName') or '-'}",
                        f"曲目: {song_count}",
                        f"发行: {album.get('showtime') or album.get('releaseDate') or '-'}",
                        f"专辑 ID: {album_id}",
                    ]
                )
            )
            self._set_quality_choices(None)
            self._load_selected_cover(album)
            if not self.current_song:
                self._show_lyric_text("选择专辑后查看歌曲")
            return
        song = self.current_songs[focus]
        self.detail_song = song
        self.detail_album = None
        self.detail_text.set_text(
            "\n".join(
                [
                    f"歌曲: {song['name']}",
                    f"歌手: {song['artist']}",
                    f"专辑: {song['album'] or '-'}",
                    f"时长: {_fmt_dur(song['duration'])}",
                    f"发行: {song['releaseDate'] or '-'}",
                    f"歌曲 ID: {song['id']}",
                ]
            )
        )
        self._set_quality_choices(song)
        self._load_selected_cover(song)
        if not self.current_song:
            self._show_lyric_text("开始播放后加载歌词")

    def _load_lyric(self, song):
        def worker():
            return self.client.get_lyric(song["id"], song["name"], song["artist"])

        def done(result, error):
            if not self.current_song or self.current_song["id"] != song["id"]:
                return
            if error:
                self._show_lyric_text("暂无歌词")
                return
            self._show_lyric_lines(result)

        self._async(f"正在加载歌词: {song['name']}", worker, done)

    def _play_song_at(self, _button=None, user_data=None):
        index = self.song_list.focus_position if user_data is None else user_data
        if index is None or index < 0 or index >= len(self.current_songs):
            return
        self.play_queue = list(self.current_songs)
        self.play_queue_index = index
        self._start_playback(self.play_queue[index])

    def _play_next_from_context(self):
        if not self.current_song or not self.current_songs:
            return False
        current_id = self.current_song.get("id")
        for index, song in enumerate(self.current_songs):
            if song.get("id") != current_id:
                continue
            if index + 1 >= len(self.current_songs):
                return False
            self.play_queue = list(self.current_songs)
            self.play_queue_index = index + 1
            self._start_playback(self.play_queue[self.play_queue_index])
            return True
        return False

    def _start_playback(self, song, start_ms=0):
        request_id = self._next_playback_request()
        self.player.stop(reset_position=False)
        choices = self.client.get_song_quality_choices(song)
        requested_quality_key = self.client.resolve_song_quality(song, self.playback_quality_key)
        if not requested_quality_key:
            self._set_status("当前歌曲没有可用音质", "warn")
            return
        requested_fmt, requested_br, requested_desc = QUALITY_OPTIONS[requested_quality_key]
        choice_keys = [choice["key"] for choice in choices]
        try:
            start_index = choice_keys.index(requested_quality_key)
        except ValueError:
            start_index = 0
        attempt_keys = choice_keys[start_index:start_index + 2] or [requested_quality_key]

        def worker():
            last_error = "无法获取播放链接"
            fallback_reason = ""
            for quality_key in attempt_keys:
                fmt, br, desc = QUALITY_OPTIONS[quality_key]
                url, actual_fmt, audio_error = self.client.get_audio_url_with_fallback(
                    song["id"], fmt, br, free_sign=song.get("freeSign") or ""
                )
                if audio_error:
                    last_error = audio_error
                    if quality_key == requested_quality_key:
                        fallback_reason = f"{requested_desc} 请求失败: {audio_error}"
                    continue
                if not url:
                    last_error = "无法获取播放链接"
                    if quality_key == requested_quality_key:
                        fallback_reason = f"{requested_desc} 未返回可用链接"
                    continue
                if not self.client.can_local_playback_format(actual_fmt):
                    last_error = f"当前本地播放器无法直接解码 {actual_fmt.upper()} 码流"
                    if quality_key == requested_quality_key:
                        fallback_reason = f"{requested_desc} 为 {actual_fmt.upper()}，当前本地播放器无法直接解码"
                    continue
                fallback_note = ""
                if actual_fmt != fmt:
                    fallback_note = f"服务端实际返回 {actual_fmt.upper()}"
                if quality_key != requested_quality_key:
                    fallback_note = f"当前歌曲已自动降为 {desc}" if not fallback_note else f"{fallback_note}，并自动降为 {desc}"
                    if fallback_reason:
                        fallback_note = f"{fallback_note}，原因: {fallback_reason}"
                return {
                    "url": url,
                    "actual_fmt": actual_fmt,
                    "requested_quality_key": requested_quality_key,
                    "used_quality_key": quality_key,
                    "fallback_note": fallback_note,
                }
            return {
                "error": last_error,
                "requested_quality_key": requested_quality_key,
                "used_quality_key": requested_quality_key,
                "fallback_note": "",
            }

        def done(result, error):
            if not self._is_current_playback_request(request_id):
                return
            if error:
                self._set_status(f"播放失败: {error}", "warn")
                return
            if result.get("error"):
                self._set_status(f"播放失败: {result['error']}", "warn")
                return
            url = result["url"]
            actual_fmt = result["actual_fmt"]
            used_quality_key = result["used_quality_key"]
            requested_quality_key_local = result["requested_quality_key"]
            requested_desc_local = QUALITY_OPTIONS[result["requested_quality_key"]][2]
            used_desc_local = QUALITY_OPTIONS[used_quality_key][2]
            if not url:
                self._set_status("无法获取播放链接", "warn")
                return
            try:
                self.player.play(url, duration_ms=song["duration"] * 1000, start_ms=start_ms, audio_format=actual_fmt)
            except Exception as exc:
                self._set_status(str(exc), "warn")
                return
            self.current_song = song
            self.current_playback_quality_key = used_quality_key
            self._set_playing_detail(song)
            self._load_cover_image(song)
            quality_desc = used_desc_local
            if actual_fmt != QUALITY_OPTIONS[used_quality_key][0]:
                quality_desc = f"{quality_desc} -> {actual_fmt.upper()}"
            if result["used_quality_key"] != result["requested_quality_key"]:
                quality_desc = f"{quality_desc} | 已自动降档"
            if "/pay3_v2/" in url:
                quality_desc = f"{quality_desc} / 试听"
            self.now_playing_text.set_text(("play", f"正在播放: {song['artist']} - {song['name']}  [{quality_desc}]"))
            self.client.set_local_config(
                download_dir=self.download_dir,
                quality=self.download_quality_key,
                playback_quality=self.playback_quality_key,
                download_quality=self.download_quality_key,
            )
            for index, current in enumerate(self.current_songs):
                if current["id"] == song["id"]:
                    self.song_list.focus_position = index
                    self.song_focus_index = -1
                    self._update_detail_from_focus(force=True)
                    break
            else:
                if self.detail_song:
                    self._set_quality_choices(self.detail_song)
                else:
                    self._update_player_playback_quality_button()
            self._rebuild_song_rows()
            if "/pay3_v2/" in url:
                self._set_status(f"开始试听: {song['name']}", "warn")
            elif result["fallback_note"]:
                self._set_status(f"开始播放: {song['name']}，{result['fallback_note']}", "warn")
            else:
                self._set_status(f"开始播放: {song['name']}")
            self._preload_recommend_if_queue_tail()
            self._show_lyric_text("正在加载歌词")
            self._load_lyric(song)

        self._async(f"正在准备播放: {song['name']}", worker, done)

    def _toggle_playback(self, *_args):
        if self.player.state == "playing":
            self._next_playback_request()
            self.player.pause()
            self._set_status("已暂停")
            return
        if self.player.state == "paused":
            self.player.resume()
            self._set_status("继续播放")
            return
        if self.current_song:
            self._start_playback(self.current_song, start_ms=self.player.get_position_ms())
            return
        self._play_selected()

    def _play_selected(self, *_args):
        if self.center_mode == "album":
            if not self.current_albums:
                self._set_status("当前没有可打开的专辑", "warn")
                return
            self._open_album_item(self.current_albums[self.song_list.focus_position])
            return
        self._play_song_at()

    def _play_next(self, *_args):
        if self.play_queue and self.play_queue_index + 1 < len(self.play_queue):
            self.play_queue_index += 1
            self._start_playback(self.play_queue[self.play_queue_index])
            return
        if self.recommend_mode and self.play_queue:
            self._load_more_recommend(play_after=True)
            return
        self._set_status("当前没有下一首")

    def _play_previous(self, *_args):
        if not self.play_queue or self.play_queue_index <= 0:
            self._set_status("当前没有上一首")
            return
        self.play_queue_index -= 1
        self._start_playback(self.play_queue[self.play_queue_index])

    def _seek_current(self, delta_ms=0, position_ms=None, quiet=False):
        if not self.current_song:
            self._set_status("当前没有正在播放的歌曲", "warn")
            return
        target_ms = self.player.get_position_ms() + delta_ms if position_ms is None else position_ms
        if self.player.duration_ms and target_ms >= self.player.duration_ms - 1000:
            self.player.stop()
            self.player.just_finished = False
            if not self._play_next_from_context():
                self._next_playback_request()
                self._set_status(f"播放结束: {self.current_song['name']}")
            return
        self.player.seek(target_ms)
        self.player.just_finished = False
        if not quiet:
            self._set_status(f"已定位到 {_fmt_dur(self.player.get_position_ms() // 1000)}")

    def _restart_current_song(self, *_args):
        if not self.current_song:
            self._set_status("当前没有正在播放的歌曲", "warn")
            return
        self._start_playback(self.current_song, start_ms=0)

    def _stop_playback(self, *_args):
        self._next_playback_request()
        self.player.stop()
        self.current_song = None
        self.current_playback_quality_key = None
        self.cover_request_key += 1
        self._set_playing_cover_placeholder("未播放")
        self._set_playing_detail(None)
        self.now_playing_text.set_text("未播放")
        self._show_lyric_text("开始播放后加载歌词")
        if self.detail_song:
            self._set_quality_choices(self.detail_song)
        else:
            self._update_player_playback_quality_button()
        self._set_status("已停止播放")
        self._push_lyric_overlay(force=True)

    def _download_selected_song(self, *_args):
        song = self.detail_song
        if not song:
            return
        quality_key = self.client.resolve_song_quality(song, self.download_quality_key)
        if not quality_key:
            self._set_status("当前歌曲没有可用音质", "warn")
            return
        fmt, br, desc = QUALITY_OPTIONS[quality_key]

        def worker():
            url, actual_fmt, audio_error = self.client.get_audio_url_with_fallback(
                song["id"], fmt, br, free_sign=song.get("freeSign") or ""
            )
            if audio_error:
                raise RuntimeError(audio_error)
            if not url:
                raise RuntimeError("无法获取下载链接")
            os.makedirs(self.download_dir, exist_ok=True)
            ext = _detect_ext(actual_fmt, url)
            filename = _sanitize(f"{song['artist']} - {song['name']}.{ext}")
            filepath = os.path.join(self.download_dir, filename)
            ok = self.client.download(url, filepath, song=song, progress=False)
            if not ok:
                raise RuntimeError(self.client.last_download_error or "下载失败")
            quality_desc = desc if actual_fmt == fmt else f"{actual_fmt.upper()} (服务端实际返回)"
            if "/pay3_v2/" in url:
                quality_desc = f"{quality_desc} / 试听"
            return filepath, quality_desc

        def done(result, error):
            if error:
                self._set_status(f"下载失败: {error}", "warn")
                return
            filepath, quality_desc = result
            self.download_quality_key = quality_key
            self.client.set_local_config(
                download_dir=self.download_dir,
                quality=self.download_quality_key,
                playback_quality=self.playback_quality_key,
                download_quality=self.download_quality_key,
            )
            if self.detail_song:
                self._set_quality_choices(self.detail_song)
            self._set_status(f"已下载: {filepath} ({quality_desc})")

        self._async(f"正在下载: {song['name']}", worker, done)

    def _download_selected_lyric(self, *_args):
        song = self.detail_song
        if not song:
            return

        def worker():
            os.makedirs(self.download_dir, exist_ok=True)
            filepath = os.path.join(self.download_dir, _sanitize(f"{song['artist']} - {song['name']}.lrc"))
            self.client.save_lyric(song, filepath)
            return filepath

        def done(result, error):
            if error:
                self._set_status(f"保存歌词失败: {error}", "warn")
                return
            self._set_status(f"已保存歌词: {result}")

        self._async(f"正在保存歌词: {song['name']}", worker, done)

    def _collect_selected_song(self, *_args):
        song = self.detail_song
        if not song:
            return

        def worker():
            data, resp = self.client.collect_song(song["id"])
            if resp.get("code") != 200:
                raise RuntimeError(resp.get("msg") or "收藏失败")
            return data

        self._async(
            f"正在收藏: {song['name']}",
            worker,
            lambda _result, error: self._set_status("收藏完成" if not error else f"收藏失败: {error}", "warn" if error else "muted"),
        )

    def _uncollect_selected_song(self, *_args):
        song = self.detail_song
        if not song:
            return

        def worker():
            data, resp = self.client.uncollect_song(song["id"])
            if resp.get("code") != 200:
                raise RuntimeError(resp.get("msg") or "取消收藏失败")
            return data

        self._async(
            f"正在取消收藏: {song['name']}",
            worker,
            lambda _result, error: self._set_status("已取消收藏" if not error else f"取消收藏失败: {error}", "warn" if error else "muted"),
        )

    def _open_selected_artist(self, *_args):
        song = self.detail_song
        if not song:
            return
        artists = []
        seen_ids = set()
        for artist in song.get("artists") or []:
            artist_id = artist.get("id")
            artist_name = artist.get("name") or song.get("artist") or "-"
            if not artist_id or artist_id in seen_ids:
                continue
            seen_ids.add(artist_id)
            artists.append({"id": artist_id, "name": artist_name, "kind": "artist"})
        if not artists and song.get("artistId"):
            artists.append({"id": song["artistId"], "name": song["artist"], "kind": "artist"})
        if not artists:
            self._set_status("当前歌曲缺少艺人信息", "warn")
            return
        if len(artists) == 1:
            self._open_artist_album_items(artists[0])
            return
        self._open_overlay(
            "选择艺人",
            urwid.Pile(
                [
                    urwid.Text("当前歌曲包含多个艺人，请选择要查看的艺人"),
                    urwid.Divider(),
                    *[self._button(artist["name"], lambda _button, item=artist: (self._close_overlay(), self._open_artist_album_items(item))) for artist in artists],
                    urwid.Divider(),
                    self._button("取消", lambda *_: self._close_overlay()),
                ]
            ),
        )

    def _open_selected_album(self, *_args):
        song = self.detail_song
        if not song or not song.get("albumId"):
            return
        self._open_album_item({"id": song["albumId"], "name": song["album"], "kind": "album"})

    def _open_quality_picker(self, target):
        if target == "playback":
            song = self.current_song if self.page_mode == "player" and self.current_song else (self.detail_song or self.current_song)
        else:
            song = self.detail_song
        if not song:
            self._set_status("当前没有可调整音质的歌曲", "warn")
            return
        choices = self.quality_choices if song is self.detail_song and self.quality_choices else self.client.get_song_quality_choices(song)
        if not choices:
            self._set_status("当前歌曲无可用音质", "warn")
            return
        selected_key = self.playback_quality_key if target == "playback" else self.download_quality_key
        if target == "playback" and self.current_song and song["id"] == self.current_song["id"] and self.current_playback_quality_key:
            selected_key = self.current_playback_quality_key
        selected = self.client.resolve_song_quality(song, selected_key) or choices[0]["key"]
        title = "播放音质" if target == "playback" else "下载音质"
        self._open_overlay(
            title,
            urwid.Pile(
                [
                    urwid.Text(f"歌曲: {song['artist']} - {song['name']}"),
                    urwid.Text(("muted", "仅显示当前歌曲实际支持的音质")),
                    urwid.Divider(),
                    *[
                        self._button(
                            f"[{'当前' if choice['key'] == selected else '切换'}] {choice['label']}",
                            lambda _button, current_target=target, key=choice["key"]: self._on_quality_changed(current_target, key),
                        )
                        for choice in choices
                    ],
                    urwid.Divider(),
                    self._button("取消", lambda *_: self._close_overlay()),
                ]
            ),
        )

    def _open_browser_item(self, _button=None, user_data=None):
        if user_data is None or user_data >= len(self.browser_items):
            return
        item = self.browser_items[user_data]
        if item["kind"] == "artist":
            self._open_artist_album_items(item)
            return
        if item["kind"] == "album":
            self._open_album_item(item)
            return
        self._open_playlist_item(item)

    def _open_playlist_item(self, item):
        source = item.get("sourceType", 5)

        def worker():
            return self.client.get_playlist_song_list(item["id"], page=1, page_size=100, source=source)

        def done(result, error):
            if error:
                self._set_status(f"读取歌单失败: {error}", "warn")
                return
            self._set_song_list(f"歌单: {item['name']}", result)
            self._set_status(f"已打开歌单: {item['name']}")

        self._async(f"正在读取歌单: {item['name']}", worker, done)

    def _open_album_item(self, item):
        album_id = item.get("albumId") or item.get("id")
        if not album_id:
            self._set_status("当前专辑缺少 ID", "warn")
            return

        def worker():
            return self.client.get_album_song_list(album_id, page=1, page_size=100)

        def done(result, error):
            if error:
                self._set_status(f"读取专辑失败: {error}", "warn")
                return
            self._set_song_list(f"专辑: {item['name']}", result)
            self._set_status(f"已打开专辑: {item['name']}")

        self._async(f"正在读取专辑: {item['name']}", worker, done)

    def _open_artist_item(self, item):
        def worker():
            return self.client.get_artist_song_list(item["id"], page=1, page_size=100)

        def done(result, error):
            if error:
                self._set_status(f"读取艺人歌曲失败: {error}", "warn")
                return
            self._clear_browser()
            self._set_song_list(f"艺人歌曲: {item['name']}", result)
            self._set_status(f"已打开艺人歌曲: {item['name']}")

        self._async(f"正在读取艺人歌曲: {item['name']}", worker, done)

    def _open_artist_album_items(self, item):
        def worker():
            return self.client.get_all_artist_album_items(item["id"])

        def done(result, error):
            if error:
                self._set_status(f"读取艺人专辑失败: {error}", "warn")
                return
            albums = [{**album, "id": album.get("albumId") or album.get("id")} for album in (result or [])]
            self._set_album_list(f"艺人专辑: {item['name']}", albums)
            self._set_status(f"已加载艺人专辑: {item['name']}")

        self._async(f"正在读取艺人专辑: {item['name']}", worker, done)

    def _load_recommend(self):
        self._clear_browser()
        self.recommend_scroll_num = 1
        self.recommend_last_cold_start_time = int(time.time() * 1000)
        self._async(
            "正在加载推荐",
            lambda: self.client.get_recommendation_songs(
                scroll_num=self.recommend_scroll_num,
                total_num=RECOMMEND_VISIBLE_COUNT,
                last_cold_start_time=self.recommend_last_cold_start_time,
            ),
            lambda result, error: self._set_song_list("推荐", result) if not error else self._set_status(f"加载推荐失败: {error}", "warn"),
        )

    def _preload_recommend_if_queue_tail(self):
        if self.recommend_mode and self.play_queue and self.play_queue_index >= len(self.play_queue) - 1:
            self._load_more_recommend()

    def _load_more_recommend(self, play_after=False):
        if not self.recommend_mode or self.recommend_loading:
            return
        self.recommend_loading = True
        self.recommend_scroll_num += 1

        def done(result, error):
            self.recommend_loading = False
            if error:
                self._set_status(f"加载下一首推荐失败: {error}", "warn")
                return
            self._append_recommend_songs(result or [], play_after=play_after)

        self._async(
            "正在加载下一首推荐",
            lambda: self.client.get_recommendation_songs(
                scroll_num=self.recommend_scroll_num,
                total_num=1,
                last_cold_start_time=self.recommend_last_cold_start_time,
            ),
            done,
        )

    def _append_recommend_songs(self, songs, play_after=False):
        if not songs:
            return
        start_index = len(self.current_songs)
        self.current_songs.extend(songs)
        if self.play_queue:
            self.play_queue.extend(songs)
        focus = self.song_list.focus_position
        self._rebuild_song_rows()
        self.song_list.focus_position = focus
        self.source_text.set_text(f"当前视图: 推荐  ({len(self.current_songs)} 首)")
        if play_after:
            self.play_queue_index = start_index
            self._start_playback(self.play_queue[self.play_queue_index])

    def _load_fond(self):
        self._clear_browser()
        self._async(
            "正在读取喜欢的音乐",
            lambda: self.client.get_my_fond_songs(page=1, page_size=100),
            lambda result, error: self._set_song_list("喜欢的音乐", result) if not error else self._set_status(f"读取失败: {error}", "warn"),
        )

    def _load_created_playlists(self):
        self._async(
            "正在读取创建歌单",
            self.client.get_my_created_playlist_items,
            lambda result, error: self._after_browser_loaded("创建歌单", result, "playlist", error),
        )

    def _load_collected_playlists(self):
        self._async(
            "正在读取收藏歌单",
            lambda: self.client.get_my_collected_playlist_items(page=1, page_size=100),
            lambda result, error: self._after_browser_loaded("收藏歌单", result, "playlist", error),
        )

    def _load_followed_artists(self):
        self._async(
            "正在读取关注艺人",
            lambda: self.client.get_followed_artist_items(page=1, page_size=100),
            lambda result, error: self._after_browser_loaded("关注艺人", result, "artist", error),
        )

    def _load_history(self):
        self._clear_browser()

        def worker():
            items = self.client.get_history_db_snapshot(limit=100)
            return self.client.normalize_song_list(entry["data"] for entry in items)

        self._async(
            "正在读取播放历史",
            worker,
            lambda result, error: self._set_song_list("播放历史", result) if not error else self._set_status(f"读取历史失败: {error}", "warn"),
        )

    def _load_favorites(self):
        self._clear_browser()

        def worker():
            data = self.client.get_favorites_db_snapshot(limit=100)
            songs = [entry["data"] for entry in data.get("songs_data", [])]
            return self.client.normalize_song_list(songs)

        self._async(
            "正在读取本地收藏",
            worker,
            lambda result, error: self._set_song_list("本地收藏", result) if not error else self._set_status(f"读取本地收藏失败: {error}", "warn"),
        )

    def _after_browser_loaded(self, title, items, kind, error):
        if error:
            self._set_status(f"{title} 读取失败: {error}", "warn")
            return
        self._set_browser_items(title, items or [], kind)
        if self.browser_items:
            self._update_browser_from_focus(force=True)
        else:
            self._set_song_list(title, [])
        self._set_status(f"已加载 {title}")

    def _on_quality_changed(self, target, quality_key):
        restart_song = None
        restart_position_ms = 0
        if target == "playback":
            self.playback_quality_key = quality_key
            self.current_playback_quality_key = quality_key
            if self.current_song:
                restart_song = self.current_song
                restart_position_ms = self.player.get_position_ms()
        else:
            self.download_quality_key = quality_key
        self.client.set_local_config(
            download_dir=self.download_dir,
            quality=self.download_quality_key,
            playback_quality=self.playback_quality_key,
            download_quality=self.download_quality_key,
        )
        if self.detail_song:
            self._set_quality_choices(self.detail_song)
        else:
            self._update_player_playback_quality_button()
        if self.loop.widget is not self.frame:
            self._close_overlay()
        self.frame._invalidate()
        if restart_song:
            self._start_playback(restart_song, start_ms=restart_position_ms)
            return
        prefix = "播放默认音质" if target == "playback" else "下载默认音质"
        self._set_status(f"{prefix}已切换为: {QUALITY_OPTIONS[quality_key][2]}")

    def _on_search(self):
        keyword = self.search_edit.edit_text.strip()
        if not keyword:
            self._load_recommend()
            return
        self._clear_browser()

        def worker():
            return self.client.search(keyword, page=0, page_size=50)

        def done(result, error):
            if error:
                self._set_status(f"搜索失败: {error}", "warn")
                return
            self._set_song_list(f"搜索: {keyword}", result)
            self._set_status(f"搜索完成: {keyword}")

        self._async(f"正在搜索: {keyword}", worker, done)

    def _on_qr_login(self, *_args):
        if self.client.logged_in:
            self._set_status("当前已登录")
            return

        def worker():
            old_uid = self.client.uid
            old_token = self.client.token
            old_nickname = self.client.nickname
            old_logged_in = self.client.logged_in
            self.client.uid = "-1"
            self.client.token = ""
            self.client.nickname = ""
            self.client.logged_in = False
            qr_data, qr_resp = self.client.request_login_qr()
            qr_code = qr_data.get("qrCode") if qr_data else ""
            if not qr_code:
                self.client.uid = old_uid
                self.client.token = old_token
                self.client.nickname = old_nickname
                self.client.logged_in = old_logged_in
                raise RuntimeError(f"获取二维码失败: {qr_resp}")
            return {
                "qr_code": qr_code,
                "restore": (old_uid, old_token, old_nickname, old_logged_in),
            }

        def done(result, error):
            if error:
                self._set_status(error, "warn")
                return
            self.login_restore = result["restore"]
            self.login_qr_code = result["qr_code"]
            self.login_deadline = time.time() + 120
            self.next_qr_poll_at = 0
            self._show_qr_overlay(self.login_qr_code)
            self._set_status("请用波点移动端扫描二维码确认登录")

        self._async("正在请求二维码登录", worker, done)

    def _poll_qr_login(self):
        if not self.login_qr_code or self.qr_checking:
            return
        if time.time() > self.login_deadline:
            self._restore_login_state()
            self._set_status("二维码登录超时", "warn")
            return
        if self.next_qr_poll_at and time.time() < self.next_qr_poll_at:
            return
        self.qr_checking = True

        def worker():
            status_data, status_resp = self.client.check_login_qr(self.login_qr_code)
            return status_data, status_resp

        def done(result, error):
            self.qr_checking = False
            if error:
                self._set_status(f"二维码状态检查失败: {error}", "warn")
                return
            status_data, status_resp = result
            status = status_data.get("status") if status_data else None
            if status == 1:
                self.next_qr_poll_at = time.time() + 2
                self._set_status("等待移动端确认二维码")
                return
            if status == 3:
                qr_code = self.login_qr_code
                self.login_qr_code = ""
                self._exchange_qr_login(qr_code, status_data)
                return
            if status is None:
                self.next_qr_poll_at = time.time() + 2
                return
            self._restore_login_state()
            self._set_status(f"二维码登录未完成: {status_resp}", "warn")

        self._async("正在检查二维码状态", worker, done)

    def _exchange_qr_login(self, qr_code, status_data):
        def worker():
            last_resp = None
            while time.time() < self.login_deadline:
                data, resp = self.client.login_from_qr_status(qr_code, status_data)
                if data and self.client.logged_in:
                    return data
                last_resp = resp
                time.sleep(0.8)
            raise RuntimeError(last_resp.get("msg") if isinstance(last_resp, dict) else "换取凭证超时")

        def done(_result, error):
            if error:
                self._close_qr_overlay()
                if self.client.logged_in and self.client.uid != "-1":
                    self.login_qr_code = ""
                    self.login_deadline = 0
                    self.login_restore = None
                    self._update_login_text()
                    self._set_status("二维码登录成功")
                    return
                self._restore_login_state()
                self._set_status(f"二维码登录失败: {error}", "warn")
                return
            self._close_qr_overlay()
            self.login_qr_code = ""
            self.login_deadline = 0
            self.login_restore = None
            self._update_login_text()
            self._set_status("二维码登录成功")

        self._async("正在换取登录凭证", worker, done)

    def _restore_login_state(self):
        if self.client.logged_in and self.client.uid != "-1":
            self.login_qr_code = ""
            self.login_deadline = 0
            self.login_restore = None
            self.qr_checking = False
            self.next_qr_poll_at = 0
            self._update_login_text()
            return
        if not self.login_restore:
            return
        self._close_qr_overlay()
        old_uid, old_token, old_nickname, old_logged_in = self.login_restore
        self.client.uid = old_uid
        self.client.token = old_token
        self.client.nickname = old_nickname
        self.client.logged_in = old_logged_in
        self.login_qr_code = ""
        self.login_deadline = 0
        self.login_restore = None
        self.qr_checking = False
        self.next_qr_poll_at = 0
        self._update_login_text()

    def _open_manual_login(self, *_args):
        uid_edit = urwid.Edit("UID: ")
        token_edit = urwid.Edit("Token: ")

        def confirm(_button):
            uid = uid_edit.edit_text.strip()
            token = token_edit.edit_text.strip()
            if not uid or not token:
                self._set_status("UID 和 Token 不能为空", "warn")
                return
            self.client.set_credentials(uid, token)
            self._update_login_text()
            self._close_overlay()
            self._set_status("手动登录成功")

        self._open_overlay(
            "手动登录",
            urwid.Pile(
                [
                    uid_edit,
                    token_edit,
                    urwid.Divider(),
                    urwid.Columns(
                        [
                            ("pack", self._button("确认", confirm)),
                            ("pack", self._button("取消", lambda *_: self._close_overlay())),
                        ],
                        dividechars=1,
                    ),
                ]
            ),
        )

    def _open_download_dir(self, *_args):
        path_edit = urwid.Edit("目录: ", self.download_dir)

        def confirm(_button):
            value = path_edit.edit_text.strip()
            if not value:
                self._set_status("目录不能为空", "warn")
                return
            self.download_dir = value
            self.client.set_local_config(
                download_dir=value,
                quality=self.download_quality_key,
                playback_quality=self.playback_quality_key,
                download_quality=self.download_quality_key,
            )
            self._close_overlay()
            self._set_status(f"下载目录已更新: {value}")

        self._open_overlay(
            "下载目录",
            urwid.Pile(
                [
                    path_edit,
                    urwid.Divider(),
                    urwid.Columns(
                        [
                            ("pack", self._button("保存", confirm)),
                            ("pack", self._button("取消", lambda *_: self._close_overlay())),
                        ],
                        dividechars=1,
                    ),
                ]
            ),
        )

    def _open_auth_info(self, *_args):
        auth = self.client.get_auth_state()
        main = auth["main"]
        qq_music_auth = auth["qq_music_auth"]
        audio = auth["audio_session"]
        qq_session = auth["qq_session"]
        lines = [
            f"主账号 UID: {main['uid']}",
            f"主账号昵称: {main['nickname'] or '-'}",
            f"主账号 authType: {main['auth_type']}",
            f"DevID: {main['dev_id']}",
            f"QQ音乐 openId: {qq_music_auth['open_id'] or '未提取'}",
            f"QQ音乐导入状态: {qq_music_auth['import_status']}",
            f"播放会话 UID: {audio['uid']}",
            f"播放会话昵称: {audio['nickname'] or '-'}",
            f"播放会话 authType: {audio['auth_type']}",
            f"QQ会话 UID: {qq_session['uid']}",
            f"QQ会话昵称: {qq_session['nickname'] or '-'}",
            f"QQ会话 authType: {qq_session['auth_type']}",
            f"认证文件: {AUTH_FILE}",
        ]
        self._open_overlay(
            "账号信息",
            urwid.Pile(
                [
                    urwid.Text("\n".join(lines)),
                    urwid.Divider(),
                    urwid.Columns(
                        [
                            ("pack", self._button("关闭", lambda *_: self._close_overlay())),
                        ],
                        dividechars=1,
                    ),
                ]
            ),
        )

    def _show_qr_overlay(self, qr_code):
        """在 TUI 中嵌入显示原版扫码链接二维码"""
        qr_widget = CoverImageWidget(max_cols=24, max_rows=12)
        png_data = _generate_qr_png(_make_qr_url(qr_code))
        qr_widget.set_image(png_data)
        self._qr_overlay_widget = qr_widget
        body = urwid.Pile([
            ("pack", urwid.Text(("muted", "请用波点移动端扫描下方二维码"), align="center", wrap="clip")),
            ("pack", urwid.Divider()),
            (12, qr_widget),
            ("pack", urwid.Divider()),
            ("pack", urwid.Text(("muted", "扫码后在此界面等待确认..."), align="center", wrap="clip")),
            ("pack", urwid.Divider()),
            ("pack", self._button("关闭", lambda *_: self._close_qr_overlay())),
        ])
        self.loop.widget = urwid.Overlay(
            urwid.Filler(urwid.LineBox(body, title="二维码登录"), valign="middle"),
            self.frame,
            align="center",
            width=40,
            valign="middle",
            height=18,
        )

    def _close_qr_overlay(self):
        """关闭二维码 overlay"""
        if not self._qr_overlay_widget:
            return
        self._qr_overlay_widget = None
        self.loop.widget = self.frame

    def _open_overlay(self, title, body):
        self.loop.widget = urwid.Overlay(
            urwid.Filler(urwid.LineBox(body, title=title)),
            self.frame,
            align="center",
            width=("relative", 60),
            valign="middle",
            height=("relative", 40),
        )

    def _close_overlay(self):
        self.loop.widget = self.frame

    def _open_playback_page(self, *_args):
        if self.page_mode == "player":
            return
        self.page_mode = "player"
        self.frame.body = self.playback_body
        self._set_playing_detail(self.current_song)
        self._update_player_playback_quality_button()
        self.frame._invalidate()
        self._set_status("已进入播放页")

    def _close_playback_page(self):
        if self.page_mode == "main":
            return
        self.page_mode = "main"
        self.frame.body = self.main_body
        self._update_browser_from_focus(force=True)
        self._update_detail_from_focus(force=True)
        self._refresh_song_row_labels()
        self.selected_cover_widget._invalidate()
        self.cover_widget._invalidate()
        self.footer_cover_widget._invalidate()
        self.frame._invalidate()
        self._set_status("已返回主页面")

    def _toggle_playback_page(self, *_args):
        if self.page_mode == "main":
            self._open_playback_page()
            return
        self._close_playback_page()

    def _on_extract(self, *_args):
        self._async(
            "正在从客户端提取凭证",
            self.client.extract_from_client,
            lambda result, error: self._after_login_action(result, error, "提取凭证成功"),
        )

    def _on_logout(self, *_args):
        self._next_playback_request()
        self.player.stop()
        self.client.logout(quiet=True)
        self.current_song = None
        self.current_playback_quality_key = None
        self.cover_request_key += 1
        self._set_playing_cover_placeholder("未播放")
        self._set_playing_detail(None)
        self.now_playing_text.set_text("未播放")
        self._show_lyric_text("开始播放后加载歌词")
        if self.detail_song:
            self._set_quality_choices(self.detail_song)
        else:
            self._update_player_playback_quality_button()
        self._update_login_text()
        self._set_status("已登出")
        self._push_lyric_overlay(force=True)

    def _after_login_action(self, result, error, success_text):
        if error:
            self._set_status(str(error), "warn")
            return
        if not result:
            self._set_status("操作未成功", "warn")
            return
        self._update_login_text()
        self._set_status(success_text)

    def _on_input(self, key):
        if key in ("q", "Q", "ctrl c", "ctrl C"):
            self._shutdown()
            raise urwid.ExitMainLoop()
        if key == "esc":
            if self.loop.widget is not self.frame:
                self._close_overlay()
                return
            if self.page_mode == "player":
                self._close_playback_page()
                return
        if key == "/":
            if self.page_mode == "player":
                self._close_playback_page()
            self.frame.set_focus("body")
            self.frame.body.focus_position = 0
            self.left_panel.focus_position = 0
            return
        if key == "enter":
            if self.loop.widget is not self.frame:
                return
            if self.page_mode == "player":
                return
            if self.frame.body.focus_position == 0 and self.left_panel.focus_position == 0:
                self._on_search()
                return
            if self.frame.body.focus_position == 0:
                try:
                    browser_index = self.browser_list.focus_position - 1
                except IndexError:
                    return
                if browser_index >= 0:
                    self._open_browser_item(user_data=browser_index)
                return
            if self.frame.body.focus_position == 1:
                self._play_selected()
                return
        if key == "tab":
            self.frame.body.focus_position = (self.frame.body.focus_position + 1) % len(self.frame.body.contents)
            return
        if key in ("v", "V"):
            self._toggle_playback_page()
            return
        if key in ("o", "O"):
            self._toggle_lyric_overlay()
            return
        if key in ("t", "T"):
            self._toggle_lyric_overlay_topmost()
            return
        if key in ("k", "K"):
            self._toggle_lyric_overlay_lock()
            return
        if key in ("c", "C"):
            self._cycle_lyric_overlay_theme()
            return
        if key == " ":
            self._toggle_playback()
        elif key in ("n", "N"):
            self._play_next()
        elif key in ("p", "P"):
            self._play_previous()
        elif key in ("s", "S"):
            self._stop_playback()
        elif key in ("r", "R"):
            self._restart_current_song()
        elif key == "[":
            self._seek_current(-10000)
        elif key == "]":
            self._seek_current(10000)
        elif key in ("d", "D"):
            self._download_selected_song()
        elif key in ("l", "L"):
            self._download_selected_lyric()

    def _tick(self, loop, _data):
        while True:
            try:
                callback = self.queue.get_nowait()
            except queue.Empty:
                break
            callback()

        if self.login_qr_code and time.time() < self.login_deadline:
            self._poll_qr_login()

        self._update_browser_from_focus()
        self._update_detail_from_focus()
        if self.player.poll_finished() and self.player.just_finished:
            self.player.just_finished = False
            if self._play_next_from_context():
                pass
            elif self.play_queue and self.play_queue_index + 1 < len(self.play_queue):
                self.play_queue_index += 1
                self._start_playback(self.play_queue[self.play_queue_index])
            elif self.recommend_mode:
                self._load_more_recommend(play_after=True)
            elif self.current_song:
                self._set_status(f"播放结束: {self.current_song['name']}")

        position_ms = self.player.get_position_ms()
        total_ms = self.player.duration_ms
        self.progress_bar.set_state(position_ms, total_ms)
        self._refresh_song_row_labels()
        self._update_lyric_highlight(position_ms)
        self._push_lyric_overlay()
        loop.set_alarm_in(0.4, self._tick)

    def _update_lyric_highlight(self, position_ms):
        if not self.current_lyric_lines:
            return
        active = 0
        for index, line in enumerate(self.current_lyric_lines):
            if position_ms >= line["time_ms"]:
                active = index
            else:
                break
        if active == self.active_lyric_index:
            return
        self.active_lyric_index = active
        self._refresh_lyric_rows()
        self._push_lyric_overlay()
        if active < len(self.lyric_walker):
            self.lyric_list.set_focus(active)
        if active < len(self.player_lyric_walker):
            self.player_lyric_list.set_focus(active)

    def run(self):
        try:
            self.loop.run()
        except KeyboardInterrupt:
            self._shutdown()
        finally:
            self._shutdown()


def main():
    BoDianUI().run()


if __name__ == "__main__":
    main()
