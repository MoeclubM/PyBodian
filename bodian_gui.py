#!/usr/bin/env python3
"""波点音乐 GUI（tkinter 实现，可打包为 exe）。

复用 bodian_toolkit 的 BoDianClient、bodian_player 的 BoDianPlayer 和
bodian_lyric_overlay 的桌面歌词浮窗，界面交互与终端版保持一致。
"""

import os
import queue
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import webbrowser
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None

from bodian_lyric_overlay import THEMES, LyricOverlay, _dpi_scale, _enable_dpi_awareness
from bodian_media import mark_translation_lines
from bodian_player import BoDianPlayer
from bodian_toolkit import (
    QUALITY_OPTIONS,
    BoDianClient,
    _detect_ext,
    _fmt_dur,
    _generate_qr_png,
    _make_qr_url,
    _sanitize,
)

APP_BG = "#12161a"
APP_PANEL = "#181e24"
APP_PANEL_ALT = "#1d242b"
APP_FG = "#e6e9ec"
APP_MUTED = "#8a939c"
APP_ACCENT = "#63f0a3"
APP_ACCENT_DIM = "#2b5c44"
APP_SELECT = "#24483a"
APP_WARN = "#ff8f8f"
FONT_MAIN = ("Microsoft YaHei UI", 10)
FONT_SMALL = ("Microsoft YaHei UI", 9)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 11, "bold")

CATEGORY_DEFS = [
    ("recommend", "推荐"),
    ("fond", "喜欢的音乐"),
    ("created", "创建歌单"),
    ("collected", "收藏歌单"),
    ("artists", "关注艺人"),
    ("history", "播放历史"),
    ("favorites", "本地收藏"),
]


class BoDianGUI:

    def __init__(self, lyrics_only=False):
        self.lyrics_only = lyrics_only
        self.client = BoDianClient()
        self.player = BoDianPlayer()
        self.queue = queue.Queue()
        self.playback_request_id = 0
        self.shutting_down = False
        self.download_dir = self.client.get_local_config("download_dir", os.getcwd())

        self.follow_client_enabled = bool(self.client.get_local_config("follow_client_enabled", True))
        self.follow_checking = False
        self.last_follow_song_id = None
        self.follow_started_at = 0.0
        self.next_follow_poll_at = 0.0
        self.next_cred_sync_at = 0.0
        self.manual_started_at = 0.0

        self.current_songs = []
        self.play_queue = []
        self.play_queue_index = -1
        self.current_song = None
        self.current_playback_quality_key = None
        self.selected_index = -1
        self.current_lyric_lines = []
        self.current_lyric_raw = ""
        self.active_lyric_index = -1
        self.cover_binary_cache = {}
        self.cover_request_key = 0
        self.quality_choices = []
        self.category_children = {}
        self.category_loaded = set()
        self.seek_dragging = False
        self.volume_var = None
        self._overlay_slider_active = False
        self._overlay_slider_syncing = False

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
        self.lyric_overlay = None

        self.login_restore = None
        self.login_deadline = 0
        self.login_qr_code = ""
        self.qr_checking = False
        self.next_qr_poll_at = 0
        self._qr_window = None

        self._build_root()
        self._build_style()
        if lyrics_only:
            self._build_lyrics_ui()
        else:
            self._build_ui()

        self._update_login_label()
        if lyrics_only:
            self._set_status("等待波点客户端播放…" if self.follow_client_enabled else "已就绪")
        else:
            self._load_category("recommend")
        if self.lyric_overlay_enabled:
            self._ensure_lyric_overlay()
        self.root.after(300, self._tick)

    # ── 界面搭建 ──────────────────────────────────────────────────

    def _apply_window_icon(self, window=None):
        target = window or self.root
        if getattr(sys, "frozen", False):
            bases = [os.path.dirname(os.path.abspath(sys.executable))]
        else:
            bases = [os.path.dirname(os.path.abspath(__file__))]
        for base in bases:
            for icon_name in ("icon.ico", os.path.join("assets", "icon.ico")):
                icon_path = os.path.join(base, icon_name)
                if not os.path.isfile(icon_path):
                    continue
                try:
                    target.iconbitmap(icon_path)
                except Exception:
                    continue
                return True
        return False

    def _build_root(self):
        self.root = tk.Tk()
        self.root.title("波点桌面歌词" if self.lyrics_only else "波点音乐 PyBodian")
        if self.lyrics_only:
            self.root.geometry("520x300")
            self.root.resizable(False, True)
        else:
            self.root.geometry("1180x740")
            self.root.minsize(960, 620)
        self.root.configure(bg=APP_BG)
        self._apply_window_icon()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", background=APP_BG, foreground=APP_FG, font=FONT_MAIN)
        style.configure("Treeview", background=APP_PANEL, fieldbackground=APP_PANEL,
                        foreground=APP_FG, rowheight=26, borderwidth=0, font=FONT_MAIN)
        style.map("Treeview",
                  background=[("selected", APP_SELECT)],
                  foreground=[("selected", APP_ACCENT)])
        style.configure("Treeview.Heading", background=APP_PANEL_ALT, foreground=APP_MUTED,
                        relief="flat", font=FONT_SMALL)
        style.map("Treeview.Heading", background=[("active", APP_PANEL_ALT)])
        style.configure("TScale", background=APP_BG, troughcolor=APP_PANEL_ALT,
                        bordercolor=APP_BG, lightcolor=APP_ACCENT_DIM, darkcolor=APP_ACCENT_DIM)
        style.configure("Accent.TButton", font=FONT_BOLD)
        style.configure("TCheckbutton", background=APP_BG, foreground=APP_FG)
        style.map("TCheckbutton",
                  background=[("active", APP_BG)],
                  foreground=[("selected", APP_ACCENT)])

    def _make_button(self, parent, text, command, accent=False, width=None):
        bg = APP_ACCENT_DIM if accent else APP_PANEL_ALT
        fg = APP_ACCENT if accent else APP_FG
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=APP_SELECT,
            activeforeground=APP_ACCENT,
            relief="flat",
            bd=0,
            padx=10,
            pady=3,
            cursor="hand2",
            font=FONT_SMALL if not accent else FONT_BOLD,
        )
        if width:
            button.configure(width=width)
        return button

    def _build_ui(self):
        top = tk.Frame(self.root, bg=APP_BG)
        top.pack(fill="x", padx=10, pady=(8, 4))

        self.login_label = tk.Label(top, text="未登录", bg=APP_BG, fg=APP_WARN, font=FONT_SMALL)
        self.login_label.pack(side="left")

        for text, command in (
            ("二维码登录", self._on_qr_login),
            ("提取凭证", self._on_extract),
            ("手动登录", self._open_manual_login),
            ("登出", self._on_logout),
            ("下载目录", self._open_download_dir),
        ):
            self._make_button(top, text, command).pack(side="left", padx=(8 if text == "二维码登录" else 4, 0))

        search_frame = tk.Frame(top, bg=APP_BG)
        search_frame.pack(side="right")
        self.search_var = tk.StringVar()
        search_entry = tk.Entry(
            search_frame,
            textvariable=self.search_var,
            bg=APP_PANEL,
            fg=APP_FG,
            insertbackground=APP_FG,
            relief="flat",
            width=30,
            font=FONT_MAIN,
        )
        search_entry.pack(side="left", ipady=3, padx=(0, 4))
        search_entry.bind("<Return>", lambda _event: self._on_search())
        self._make_button(search_frame, "搜索", self._on_search, accent=True).pack(side="left")
        search_entry.focus_set()

        main = tk.PanedWindow(self.root, orient="horizontal", bg=APP_BG, sashwidth=4,
                              bd=0, relief="flat")
        main.pack(fill="both", expand=True, padx=10, pady=4)

        left = tk.Frame(main, bg=APP_BG)
        self.category_tree = ttk.Treeview(left, show="tree", selectmode="browse")
        for key, label in CATEGORY_DEFS:
            self.category_tree.insert("", "end", iid=f"cat:{key}", text=f" {label}", open=False)
        category_scroll = ttk.Scrollbar(left, orient="vertical", command=self.category_tree.yview)
        self.category_tree.configure(yscrollcommand=category_scroll.set)
        self.category_tree.pack(side="left", fill="both", expand=True)
        category_scroll.pack(side="right", fill="y")
        self.category_tree.bind("<Double-1>", self._on_category_activate)
        main.add(left, width=240, minsize=180)

        center = tk.Frame(main, bg=APP_BG)
        columns = ("index", "name", "artist", "album", "duration")
        self.song_tree = ttk.Treeview(center, columns=columns, show="headings", selectmode="browse")
        headings = (("index", "#", 46, "center"), ("name", "歌曲", 300, "w"),
                    ("artist", "歌手", 160, "w"), ("album", "专辑", 200, "w"),
                    ("duration", "时长", 70, "center"))
        for column, text, width, anchor in headings:
            self.song_tree.heading(column, text=text)
            self.song_tree.column(column, width=width, anchor=anchor, stretch=(column in ("name", "album")))
        self.song_tree.tag_configure("playing", foreground=APP_ACCENT)
        song_scroll = ttk.Scrollbar(center, orient="vertical", command=self.song_tree.yview)
        self.song_tree.configure(yscrollcommand=song_scroll.set)
        self.song_tree.pack(side="left", fill="both", expand=True)
        song_scroll.pack(side="right", fill="y")
        self.song_tree.bind("<Double-1>", lambda _event: self._play_selected())
        self.song_tree.bind("<<TreeviewSelect>>", self._on_song_select)
        self.song_menu = tk.Menu(self.root, tearoff=0, bg=APP_PANEL_ALT, fg=APP_FG,
                                 activebackground=APP_SELECT, activeforeground=APP_ACCENT, font=FONT_SMALL)
        for label, command in (
            ("播放", lambda: self._play_selected()),
            ("下载当前音质", lambda: self._download_selected_song()),
            ("收藏到喜欢", lambda: self._collect_selected_song()),
            ("取消收藏", lambda: self._uncollect_selected_song()),
            ("保存歌词 (.lrc)", lambda: self._download_selected_lyric()),
            ("查看歌手", lambda: self._open_selected_artist()),
            ("查看专辑", lambda: self._open_selected_album()),
        ):
            self.song_menu.add_command(label=label, command=command)
        self.song_tree.bind("<Button-3>", self._show_song_menu)
        main.add(center, minsize=420)

        bottom = tk.Frame(self.root, bg=APP_BG)
        bottom.pack(fill="x", padx=10, pady=(0, 8))

        lyric_box = tk.LabelFrame(bottom, text=" 歌词 ", bg=APP_BG, fg=APP_MUTED, font=FONT_SMALL, bd=0)
        lyric_box.pack(fill="x", pady=(0, 6))
        self.lyric_text = tk.Text(
            lyric_box,
            height=4,
            bg=APP_BG,
            fg=APP_MUTED,
            relief="flat",
            bd=0,
            font=("Microsoft YaHei UI", 11),
            wrap="word",
            cursor="arrow",
            takefocus=0,
        )
        self.lyric_text.pack(fill="x")
        self.lyric_text.tag_configure("active", foreground=APP_ACCENT, font=("Microsoft YaHei UI", 12, "bold"))
        self.lyric_text.tag_configure("normal", foreground=APP_MUTED)
        self.lyric_text.tag_configure("translation", foreground="#6f7a83")
        self.lyric_text.insert("1.0", "开始播放后加载歌词", ("normal",))
        self.lyric_text.configure(state="disabled")

        player = tk.Frame(bottom, bg=APP_PANEL, bd=0)
        player.pack(fill="x")

        self.cover_label = tk.Label(player, text="♪", bg=APP_PANEL_ALT, fg=APP_MUTED,
                                    width=7, height=3, font=FONT_TITLE)
        self.cover_label.pack(side="left", padx=(10, 8), pady=8)

        info = tk.Frame(player, bg=APP_PANEL)
        info.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=(6, 0))
        self.now_playing_label = tk.Label(info, text="未播放", bg=APP_PANEL, fg=APP_FG,
                                          font=FONT_TITLE, anchor="w")
        self.now_playing_label.pack(fill="x")
        self.quality_label = tk.Label(info, text="播放音质: -", bg=APP_PANEL, fg=APP_MUTED,
                                      font=FONT_SMALL, anchor="w", cursor="hand2")
        self.quality_label.pack(fill="x")
        self.quality_label.bind("<Button-1>", lambda _event: self._open_quality_picker("playback"))
        self.download_quality_label = tk.Label(info, text="下载音质: -", bg=APP_PANEL, fg=APP_MUTED,
                                               font=FONT_SMALL, anchor="w", cursor="hand2")
        self.download_quality_label.pack(fill="x")
        self.download_quality_label.bind("<Button-1>", lambda _event: self._open_quality_picker("download"))

        seek_row = tk.Frame(info, bg=APP_PANEL)
        seek_row.pack(fill="x", pady=(0, 2))
        self.position_label = tk.Label(seek_row, text="0:00", bg=APP_PANEL, fg=APP_MUTED, font=FONT_SMALL)
        self.position_label.pack(side="left")
        self.seek_var = tk.DoubleVar(value=0.0)
        self.seek_scale = ttk.Scale(seek_row, orient="horizontal", variable=self.seek_var,
                                    from_=0, to=1000, command=self._on_seek_drag)
        self.seek_scale.pack(side="left", fill="x", expand=True, padx=6)
        self.seek_scale.bind("<ButtonPress-1>", self._on_seek_press)
        self.seek_scale.bind("<ButtonRelease-1>", self._on_seek_release)
        self.duration_label = tk.Label(seek_row, text="0:00", bg=APP_PANEL, fg=APP_MUTED, font=FONT_SMALL)
        self.duration_label.pack(side="left")

        controls = tk.Frame(player, bg=APP_PANEL)
        controls.pack(side="left", padx=(0, 10), pady=8)
        for text, command in (
            ("⏮", self._play_previous),
            ("▶ / ⏸", self._toggle_playback),
            ("⏭", self._play_next),
            ("⏹", self._stop_playback),
            ("↻", self._restart_current_song),
            ("−10s", lambda: self._seek_current(-10000)),
            ("+10s", lambda: self._seek_current(10000)),
        ):
            self._make_button(controls, text, command, accent=text in ("▶ / ⏸",)).pack(side="left", padx=2)

        volume_row = tk.Frame(player, bg=APP_PANEL)
        volume_row.pack(side="left", padx=(0, 10))
        tk.Label(volume_row, text="音量", bg=APP_PANEL, fg=APP_MUTED, font=FONT_SMALL).pack(side="left")
        self.volume_var = tk.IntVar(value=100)
        volume_scale = ttk.Scale(volume_row, orient="horizontal", variable=self.volume_var,
                                 from_=0, to=100, length=90, command=self._on_volume_change)
        volume_scale.pack(side="left", padx=4)

        overlay_row = tk.Frame(player, bg=APP_PANEL)
        overlay_row.pack(side="left", padx=(0, 10))
        self.overlay_var = tk.BooleanVar(value=self.lyric_overlay_enabled)
        overlay_check = tk.Checkbutton(
            overlay_row,
            text="歌词浮窗",
            variable=self.overlay_var,
            command=self._toggle_lyric_overlay,
            bg=APP_PANEL,
            fg=APP_FG,
            activebackground=APP_PANEL,
            activeforeground=APP_ACCENT,
            selectcolor=APP_PANEL_ALT,
            font=FONT_SMALL,
            bd=0,
            highlightthickness=0,
        )
        overlay_check.pack(side="left")
        for text, command in (
            ("置顶", self._toggle_lyric_overlay_topmost),
            ("锁定", self._toggle_lyric_overlay_lock),
            ("浮窗设置", self._open_overlay_settings),
        ):
            self._make_button(overlay_row, text, command).pack(side="left", padx=2)

        self.status_label = tk.Label(self.root, text="就绪", bg=APP_BG, fg=APP_MUTED,
                                     font=FONT_SMALL, anchor="w")
        self.status_label.pack(fill="x", padx=12, pady=(0, 6))

    def _build_lyrics_ui(self):
        """仅歌词模式：配合波点官方客户端，只负责桌面歌词展示。"""
        outer = tk.Frame(self.root, bg=APP_BG)
        outer.pack(fill="both", expand=True, padx=12, pady=10)

        self.login_label = tk.Label(outer, text="未登录", bg=APP_BG, fg=APP_WARN, font=FONT_SMALL, anchor="w")
        self.login_label.pack(fill="x")
        login_row = tk.Frame(outer, bg=APP_BG)
        login_row.pack(fill="x", pady=(4, 8))
        self._make_button(login_row, "提取凭证", self._on_extract).pack(side="left")
        self._make_button(login_row, "扫码登录", self._on_qr_login).pack(side="left", padx=(6, 0))
        self._make_button(login_row, "登出", self._on_logout).pack(side="left", padx=(6, 0))

        follow_row = tk.Frame(outer, bg=APP_BG)
        follow_row.pack(fill="x", pady=(0, 6))
        self.follow_var = tk.BooleanVar(value=self.follow_client_enabled)
        follow_check = tk.Checkbutton(
            follow_row,
            text="自动跟随波点客户端播放",
            variable=self.follow_var,
            command=self._toggle_follow_client,
            bg=APP_BG,
            fg=APP_FG,
            activebackground=APP_BG,
            activeforeground=APP_ACCENT,
            selectcolor=APP_PANEL_ALT,
            font=FONT_MAIN,
            bd=0,
            highlightthickness=0,
        )
        follow_check.pack(side="left")

        self.now_following_label = tk.Label(outer, text="当前：-", bg=APP_BG, fg=APP_ACCENT,
                                            font=FONT_TITLE, anchor="w")
        self.now_following_label.pack(fill="x", pady=(2, 6))

        search_row = tk.Frame(outer, bg=APP_BG)
        search_row.pack(fill="x", pady=(0, 6))
        tk.Label(search_row, text="手动指定:", bg=APP_BG, fg=APP_MUTED, font=FONT_SMALL).pack(side="left")
        self.lyrics_search_var = tk.StringVar()
        search_entry = tk.Entry(
            search_row,
            textvariable=self.lyrics_search_var,
            bg=APP_PANEL,
            fg=APP_FG,
            insertbackground=APP_FG,
            relief="flat",
            font=FONT_MAIN,
        )
        search_entry.pack(side="left", fill="x", expand=True, padx=6, ipady=3)
        search_entry.bind("<Return>", lambda _event: self._on_lyrics_search())
        self._make_button(search_row, "搜索", self._on_lyrics_search, accent=True).pack(side="left")

        overlay_row = tk.Frame(outer, bg=APP_BG)
        overlay_row.pack(fill="x", pady=(4, 0))
        self.overlay_var = tk.BooleanVar(value=self.lyric_overlay_enabled)
        overlay_check = tk.Checkbutton(
            overlay_row,
            text="歌词浮窗",
            variable=self.overlay_var,
            command=self._toggle_lyric_overlay,
            bg=APP_BG,
            fg=APP_FG,
            activebackground=APP_BG,
            activeforeground=APP_ACCENT,
            selectcolor=APP_PANEL_ALT,
            font=FONT_SMALL,
            bd=0,
            highlightthickness=0,
        )
        overlay_check.pack(side="left")
        for text, command in (
            ("置顶", self._toggle_lyric_overlay_topmost),
            ("锁定", self._toggle_lyric_overlay_lock),
            ("浮窗设置", self._open_overlay_settings),
        ):
            self._make_button(overlay_row, text, command).pack(side="left", padx=(6, 0))

        self.status_label = tk.Label(outer, text="就绪", bg=APP_BG, fg=APP_MUTED,
                                     font=FONT_SMALL, anchor="w")
        self.status_label.pack(fill="x", side="bottom", pady=(10, 0))

    # ── 通用工具 ──────────────────────────────────────────────────

    def _set_status(self, message, warn=False):
        self.status_label.configure(text=message, fg=APP_WARN if warn else APP_MUTED)

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

    def _next_playback_request(self):
        self.playback_request_id += 1
        return self.playback_request_id

    def _is_current_playback_request(self, request_id):
        return request_id == self.playback_request_id and not self.shutting_down

    def _fmt_quality_label(self, key):
        option = QUALITY_OPTIONS.get(key)
        return option[2] if option else str(key)

    # ── 左侧分类 / 歌曲列表 ──────────────────────────────────────

    def _on_category_activate(self, _event):
        item_id = self.category_tree.focus()
        if not item_id:
            return
        if item_id.startswith("cat:"):
            self._load_category(item_id[4:])
            return
        info = self.category_children.get(item_id)
        if not info:
            return
        if info["kind"] == "artist":
            self._open_artist_songs(info)
        elif info["kind"] == "album":
            self._open_album_songs(info)
        else:
            self._open_playlist_songs(info)

    def _load_category(self, key):
        title = dict(CATEGORY_DEFS).get(key, key)
        self.category_tree.item(f"cat:{key}", open=True)

        def worker():
            if key == "recommend":
                return self.client.get_recommendation_songs(scroll_num=1, total_num=50)
            if key == "fond":
                return self.client.get_my_fond_songs(page=1, page_size=100)
            if key == "created":
                return self.client.get_my_created_playlist_items()
            if key == "collected":
                return self.client.get_my_collected_playlist_items(page=1, page_size=100)
            if key == "artists":
                return self.client.get_followed_artist_items(page=1, page_size=100)
            if key == "history":
                items = self.client.get_history_db_snapshot(limit=100)
                return self.client.normalize_song_list(entry["data"] for entry in items)
            if key == "favorites":
                data = self.client.get_favorites_db_snapshot(limit=100)
                return self.client.normalize_song_list([entry["data"] for entry in data.get("songs_data", [])])
            return []

        def done(result, error):
            if error:
                self._set_status(f"{title} 读取失败: {error}", warn=True)
                return
            if key in ("created", "collected"):
                self._set_category_children(key, result or [], "playlist")
                self._set_status(f"已加载 {title}: {len(result or [])} 个歌单")
            elif key == "artists":
                self._set_category_children(key, result or [], "artist")
                self._set_status(f"已加载 {title}: {len(result or [])} 位艺人")
            else:
                self._set_song_list(f"{title}", result or [])
                self._set_status(f"已加载 {title}: {len(result or [])} 首")

        self._async(f"正在读取 {title}", worker, done)

    def _set_category_children(self, key, items, kind):
        parent = f"cat:{key}"
        for child in self.category_tree.get_children(parent):
            self.category_tree.delete(child)
        self.category_children = {
            item_id: info for item_id, info in self.category_children.items()
            if not item_id.startswith(f"{kind}:{key}:")
        }
        self.category_tree.item(parent, open=True)
        for index, item in enumerate(items or []):
            name = str(item.get("name") or "?")
            if kind == "artist":
                label = f"  ♪ {name}"
            else:
                count = item.get("musicCount") or item.get("musicCnt") or item.get("songNum") or ""
                label = f"  ☰ {name}" + (f" ({count})" if count else "")
            item_id = f"{kind}:{key}:{index}"
            self.category_children[item_id] = {**item, "kind": kind}
            self.category_tree.insert(parent, "end", iid=item_id, text=label)

    def _set_song_list(self, title, songs):
        self.current_songs = list(songs or [])
        self.selected_index = -1
        self.song_tree.delete(*self.song_tree.get_children())
        for index, song in enumerate(self.current_songs):
            values = (
                index + 1,
                song.get("name", "?"),
                song.get("artist", "-"),
                song.get("album") or "-",
                _fmt_dur(song.get("duration") or 0),
            )
            tags = ("playing",) if self.current_song and song.get("id") == self.current_song.get("id") else ()
            self.song_tree.insert("", "end", iid=str(index), values=values, tags=tags)
        self._update_quality_labels()

    def _on_song_select(self, _event):
        focus = self.song_tree.focus()
        try:
            self.selected_index = int(focus)
        except (TypeError, ValueError):
            self.selected_index = -1

    def _show_song_menu(self, event):
        row = self.song_tree.identify_row(event.y)
        if row:
            self.song_tree.selection_set(row)
            self.song_tree.focus(row)
            self.song_menu.tk_popup(event.x_root, event.y_root)

    def _selected_song(self):
        if 0 <= self.selected_index < len(self.current_songs):
            return self.current_songs[self.selected_index]
        return None

    # ── 播放 ─────────────────────────────────────────────────────

    def _play_selected(self):
        song = self._selected_song()
        if not song:
            self._set_status("请先选择歌曲", warn=True)
            return
        index = self.selected_index
        self.play_queue = list(self.current_songs)
        self.play_queue_index = index
        self._start_playback(song)

    def _start_playback(self, song, start_ms=0):
        request_id = self._next_playback_request()
        self.player.stop(reset_position=False)
        choices = self.client.get_song_quality_choices(song)
        requested_quality_key = self.client.resolve_song_quality(song, self.playback_quality_key)
        if not requested_quality_key:
            self._set_status("当前歌曲没有可用音质", warn=True)
            return
        requested_fmt, _requested_br, requested_desc = QUALITY_OPTIONS[requested_quality_key]
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
            if error or result.get("error"):
                self._set_status(f"播放失败: {error or result['error']}", warn=True)
                return
            url = result["url"]
            actual_fmt = result["actual_fmt"]
            used_quality_key = result["used_quality_key"]
            if not url:
                self._set_status("无法获取播放链接", warn=True)
                return
            try:
                self.player.play(url, duration_ms=song["duration"] * 1000, start_ms=start_ms, audio_format=actual_fmt)
            except Exception as exc:
                self._set_status(str(exc), warn=True)
                return
            self.current_song = song
            self.current_playback_quality_key = used_quality_key
            self._update_now_playing(song, url, result)
            self.client.set_local_config(
                download_dir=self.download_dir,
                quality=self.download_quality_key,
                playback_quality=self.playback_quality_key,
                download_quality=self.download_quality_key,
            )
            self._refresh_playing_tags()
            self._update_quality_labels()
            self._load_cover_image(song)
            if "/pay3_v2/" in url:
                self._set_status(f"开始试听: {song['name']}", warn=True)
            elif result["fallback_note"]:
                self._set_status(f"开始播放: {song['name']}，{result['fallback_note']}", warn=True)
            else:
                self._set_status(f"开始播放: {song['name']}")
            self._show_lyric_text("正在加载歌词")
            self._load_lyric(song)

        self._async(f"正在准备播放: {song['name']}", worker, done)

    def _update_now_playing(self, song, url, result):
        used_quality_key = result["used_quality_key"]
        quality_desc = self._fmt_quality_label(used_quality_key)
        if result["actual_fmt"] != QUALITY_OPTIONS[used_quality_key][0]:
            quality_desc = f"{quality_desc} -> {result['actual_fmt'].upper()}"
        if result["used_quality_key"] != result["requested_quality_key"]:
            quality_desc = f"{quality_desc} | 已自动降档"
        if "/pay3_v2/" in url:
            quality_desc = f"{quality_desc} / 试听"
        self.now_playing_label.configure(text=f"{song['artist']} - {song['name']}")
        self.quality_label.configure(text=f"播放音质: {quality_desc}（点击切换）")

    def _refresh_playing_tags(self):
        playing_id = self.current_song.get("id") if self.current_song else None
        for index, song in enumerate(self.current_songs):
            tags = ("playing",) if playing_id and song.get("id") == playing_id else ()
            try:
                self.song_tree.item(str(index), tags=tags)
            except tk.TclError:
                pass

    def _toggle_playback(self):
        if self.player.state == "playing":
            self._next_playback_request()
            self.player.pause()
            self._set_status("已暂停")
        elif self.player.state == "paused":
            self.player.resume()
            self._set_status("继续播放")
        elif self.current_song:
            self._start_playback(self.current_song, start_ms=self.player.get_position_ms())
        else:
            self._play_selected()

    def _play_next(self):
        if not self.play_queue or self.play_queue_index + 1 >= len(self.play_queue):
            self._set_status("当前没有下一首")
            return
        self.play_queue_index += 1
        self._start_playback(self.play_queue[self.play_queue_index])

    def _play_previous(self):
        if not self.play_queue or self.play_queue_index <= 0:
            self._set_status("当前没有上一首")
            return
        self.play_queue_index -= 1
        self._start_playback(self.play_queue[self.play_queue_index])

    def _restart_current_song(self):
        if not self.current_song:
            self._set_status("当前没有正在播放的歌曲", warn=True)
            return
        self._start_playback(self.current_song, start_ms=0)

    def _stop_playback(self):
        self._next_playback_request()
        self.player.stop()
        self.current_song = None
        self.current_playback_quality_key = None
        self.cover_request_key += 1
        self._reset_cover()
        self.now_playing_label.configure(text="未播放")
        self._update_quality_labels()
        self._show_lyric_text("开始播放后加载歌词")
        self._set_status("已停止播放")
        self._push_lyric_overlay(force=True)

    def _seek_current(self, delta_ms=0, position_ms=None):
        if not self.current_song:
            self._set_status("当前没有正在播放的歌曲", warn=True)
            return
        target_ms = self.player.get_position_ms() + delta_ms if position_ms is None else position_ms
        if self.player.duration_ms and target_ms >= self.player.duration_ms - 1000:
            self.player.stop()
            self.player.just_finished = False
            if self.play_queue and self.play_queue_index + 1 < len(self.play_queue):
                self.play_queue_index += 1
                self._start_playback(self.play_queue[self.play_queue_index])
            else:
                self._set_status(f"播放结束: {self.current_song['name']}")
            return
        self.player.seek(target_ms)
        self.player.just_finished = False
        self._set_status(f"已定位到 {_fmt_dur(self.player.get_position_ms() // 1000)}")

    def _on_volume_change(self, _value=None):
        if self.player and self.volume_var is not None:
            self.player.set_volume(int(float(self.volume_var.get())))

    def _on_seek_press(self, _event):
        self.seek_dragging = True

    def _on_seek_drag(self, _value):
        if self.seek_dragging and self.player.duration_ms:
            display_ms = int(self.seek_var.get())
            self.position_label.configure(text=_fmt_dur(display_ms // 1000))

    def _on_seek_release(self, _event):
        if not self.seek_dragging:
            return
        self.seek_dragging = False
        if not self.player.duration_ms:
            return
        position_ms = int(self.seek_var.get())
        self._seek_current(position_ms=position_ms)

    # ── 歌词 ─────────────────────────────────────────────────────

    def _load_lyric(self, song):
        def worker():
            return self.client.get_lyric(song["id"], song["name"], song["artist"])

        def done(result, error):
            if not self.current_song or self.current_song.get("id") != song.get("id"):
                return
            if error:
                self._show_lyric_text("暂无歌词")
                return
            self._show_lyric_lines(result)

        self._async(f"正在加载歌词: {song['name']}", worker, done)

    def _show_lyric_text(self, text):
        self.current_lyric_lines = []
        self.current_lyric_raw = text
        self.active_lyric_index = -1
        lines = text.splitlines() or ["暂无歌词"]
        self._render_lyric_panel([(line, False, False) for line in lines], -1)
        self._push_lyric_overlay(force=True)

    def _show_lyric_lines(self, lyric):
        self.current_lyric_lines = []
        self.current_lyric_raw = lyric.get("raw", "")
        self.active_lyric_index = -1
        lines = lyric.get("lines", [])
        if not lines:
            self._show_lyric_text(self.current_lyric_raw or "暂无歌词")
            return
        self.current_lyric_lines = mark_translation_lines(lines)
        self._render_lyric_panel([], -1)
        self._push_lyric_overlay(force=True)

    def _render_lyric_panel(self, plain_lines, active_index):
        if self.lyrics_only or not getattr(self, "lyric_text", None):
            return
        self.lyric_text.configure(state="normal")
        self.lyric_text.delete("1.0", "end")
        if plain_lines:
            for line, _is_translation, _unused in plain_lines:
                self.lyric_text.insert("end", f"{line}\n", ("normal",))
        else:
            for index, line in enumerate(self.current_lyric_lines):
                tag = "active" if index == active_index else ("translation" if line.get("translation") else "normal")
                prefix = "    " if line.get("translation") else ""
                self.lyric_text.insert("end", f"{prefix}{line['text']}\n", (tag,))
        self.lyric_text.configure(state="disabled")
        if active_index >= 0:
            try:
                self.lyric_text.see(f"{active_index + 1}.0")
            except tk.TclError:
                pass

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
        self._render_lyric_panel([], active)
        self._push_lyric_overlay()

    # ── 封面 ─────────────────────────────────────────────────────

    def _reset_cover(self):
        if self.lyrics_only or not getattr(self, "cover_label", None):
            return
        self.cover_label.configure(text="♪", image="", width=7, height=3)
        self._cover_photo = None

    def _cover_url(self, item):
        return item.get("albumPic") or item.get("albumPic120") or item.get("artistPic") or item.get("pic") or ""

    def _load_cover_image(self, song):
        self.cover_request_key += 1
        request_key = self.cover_request_key
        cover_url = self._cover_url(song)
        if not cover_url:
            self._reset_cover()
            return
        cached = self.cover_binary_cache.get(cover_url)
        if cached:
            self._set_cover_binary(cached)
            return
        self._reset_cover()

        def run():
            binary = self.client._request_binary(cover_url)
            self.queue.put(lambda: done(binary, None))

        def done(binary, error):
            if request_key != self.cover_request_key:
                return
            if error:
                return
            self.cover_binary_cache[cover_url] = binary
            self._set_cover_binary(binary)

        threading.Thread(target=run, daemon=True).start()

    def _set_cover_binary(self, binary):
        if Image is None or ImageTk is None:
            return
        try:
            image = Image.open(__import__("io").BytesIO(binary))
            image = image.convert("RGB").resize((72, 72), Image.LANCZOS)
            photo = ImageTk.PhotoImage(image)
        except Exception:
            return
        self._cover_photo = photo
        self.cover_label.configure(image=photo, text="", width=72, height=72)

    # ── 音质 ─────────────────────────────────────────────────────

    def _update_quality_labels(self):
        song = self.current_song
        if not song:
            self.quality_label.configure(text="播放音质: 当前无歌曲（播放后点击切换）")
        else:
            selected_key = self.current_playback_quality_key or self.playback_quality_key
            resolved = self.client.resolve_song_quality(song, selected_key)
            if resolved:
                self.quality_label.configure(text=f"播放音质: {self._fmt_quality_label(resolved)}（点击切换）")
        self.download_quality_label.configure(text=f"下载音质: {self._fmt_quality_label(self.download_quality_key)}")

    def _open_quality_picker(self, target):
        if target == "playback":
            song = self.current_song or self._selected_song()
        else:
            song = self._selected_song() or self.current_song
        if not song:
            self._set_status("当前没有可调整音质的歌曲", warn=True)
            return
        choices = self.client.get_song_quality_choices(song)
        if not choices:
            self._set_status("当前歌曲无可用音质", warn=True)
            return
        if target == "playback":
            selected_key = self.current_playback_quality_key or self.playback_quality_key
            if self.current_song and song.get("id") == self.current_song.get("id") and self.current_playback_quality_key:
                selected_key = self.current_playback_quality_key
        else:
            selected_key = self.download_quality_key
        selected = self.client.resolve_song_quality(song, selected_key) or choices[0]["key"]
        title = "播放音质" if target == "playback" else "下载音质"
        self._pick_from_list(
            title,
            f"{song['artist']} - {song['name']}",
            [choice["label"] for choice in choices],
            lambda index: self._on_quality_changed(target, choices[index]["key"]),
        )

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
        self._update_quality_labels()
        if restart_song:
            self._start_playback(restart_song, start_ms=restart_position_ms)
        else:
            prefix = "播放默认音质" if target == "playback" else "下载默认音质"
            self._set_status(f"{prefix}已切换为: {self._fmt_quality_label(quality_key)}")

    def _pick_from_list(self, title, subtitle, labels, on_pick):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=APP_BG)
        win.transient(self.root)
        win.resizable(False, False)
        tk.Label(win, text=subtitle, bg=APP_BG, fg=APP_MUTED, font=FONT_SMALL).pack(anchor="w", padx=14, pady=(10, 4))
        for index, label in enumerate(labels):
            self._make_button(
                win,
                label,
                lambda picked=index: (on_pick(picked), win.destroy()),
                accent=(labels[index] == label and False),
            ).pack(fill="x", padx=14, pady=2)
        self._make_button(win, "取消", win.destroy).pack(fill="x", padx=14, pady=(2, 10))

    # ── 下载 / 收藏 ──────────────────────────────────────────────

    def _download_selected_song(self):
        song = self._selected_song()
        if not song:
            self._set_status("请先选择歌曲", warn=True)
            return
        quality_key = self.client.resolve_song_quality(song, self.download_quality_key)
        if not quality_key:
            self._set_status("当前歌曲没有可用音质", warn=True)
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
                self._set_status(f"下载失败: {error}", warn=True)
                return
            filepath, quality_desc = result
            self.download_quality_key = quality_key
            self.client.set_local_config(
                download_dir=self.download_dir,
                quality=self.download_quality_key,
                playback_quality=self.playback_quality_key,
                download_quality=self.download_quality_key,
            )
            self._update_quality_labels()
            self._set_status(f"已下载: {filepath} ({quality_desc})")

        self._async(f"正在下载: {song['name']}", worker, done)

    def _download_selected_lyric(self):
        song = self._selected_song()
        if not song:
            self._set_status("请先选择歌曲", warn=True)
            return

        def worker():
            os.makedirs(self.download_dir, exist_ok=True)
            filepath = os.path.join(self.download_dir, _sanitize(f"{song['artist']} - {song['name']}.lrc"))
            self.client.save_lyric(song, filepath)
            return filepath

        def done(result, error):
            if error:
                self._set_status(f"保存歌词失败: {error}", warn=True)
                return
            self._set_status(f"已保存歌词: {result}")

        self._async(f"正在保存歌词: {song['name']}", worker, done)

    def _collect_selected_song(self, collect=True):
        song = self._selected_song()
        if not song:
            self._set_status("请先选择歌曲", warn=True)
            return

        def worker():
            if collect:
                _data, resp = self.client.collect_song(song["id"])
            else:
                _data, resp = self.client.uncollect_song(song["id"])
            if resp.get("code") != 200:
                raise RuntimeError(resp.get("msg") or "操作失败")
            return resp

        action = "正在收藏" if collect else "正在取消收藏"
        success = "收藏完成" if collect else "已取消收藏"
        failed = "收藏失败" if collect else "取消收藏失败"

        def done(_result, error):
            if error:
                self._set_status(f"{failed}: {error}", warn=True)
                return
            self._set_status(success)

        self._async(f"{action}: {song['name']}", worker, done)

    def _uncollect_selected_song(self):
        self._collect_selected_song(collect=False)

    def _open_selected_artist(self):
        song = self._selected_song()
        if not song:
            return
        artist_id = song.get("artistId")
        artists = song.get("artists") or []
        if not artist_id and artists:
            artist_id = artists[0].get("id")
        if not artist_id:
            self._set_status("当前歌曲缺少艺人信息", warn=True)
            return

        def worker():
            return self.client.get_artist_song_list(artist_id, page=1, page_size=100)

        def done(result, error):
            if error:
                self._set_status(f"读取艺人歌曲失败: {error}", warn=True)
                return
            self._set_song_list(f"艺人歌曲: {song.get('artist') or '-'}", result or [])
            self._set_status(f"已打开艺人歌曲: {song.get('artist') or '-'}")

        self._async(f"正在读取艺人歌曲", worker, done)

    def _open_selected_album(self):
        song = self._selected_song()
        if not song or not song.get("albumId"):
            self._set_status("当前歌曲缺少专辑信息", warn=True)
            return
        album_id = song["albumId"]

        def worker():
            return self.client.get_album_song_list(album_id, page=1, page_size=100)

        def done(result, error):
            if error:
                self._set_status(f"读取专辑失败: {error}", warn=True)
                return
            self._set_song_list(f"专辑: {song.get('album') or '-'}", result or [])
            self._set_status(f"已打开专辑: {song.get('album') or '-'}")

        self._async("正在读取专辑", worker, done)

    def _open_playlist_songs(self, item):
        source = item.get("sourceType", 5)

        def worker():
            return self.client.get_playlist_song_list(item["id"], page=1, page_size=100, source=source)

        def done(result, error):
            if error:
                self._set_status(f"读取歌单失败: {error}", warn=True)
                return
            self._set_song_list(f"歌单: {item['name']}", result or [])
            self._set_status(f"已打开歌单: {item['name']}")

        self._async(f"正在读取歌单: {item['name']}", worker, done)

    def _open_album_songs(self, item):
        album_id = item.get("albumId") or item.get("id")
        if not album_id:
            self._set_status("当前专辑缺少 ID", warn=True)
            return

        def worker():
            return self.client.get_album_song_list(album_id, page=1, page_size=100)

        def done(result, error):
            if error:
                self._set_status(f"读取专辑失败: {error}", warn=True)
                return
            self._set_song_list(f"专辑: {item['name']}", result or [])
            self._set_status(f"已打开专辑: {item['name']}")

        self._async(f"正在读取专辑: {item['name']}", worker, done)

    def _open_artist_songs(self, item):
        def worker():
            return self.client.get_artist_song_list(item["id"], page=1, page_size=100)

        def done(result, error):
            if error:
                self._set_status(f"读取艺人歌曲失败: {error}", warn=True)
                return
            self._set_song_list(f"艺人歌曲: {item['name']}", result or [])
            self._set_status(f"已打开艺人歌曲: {item['name']}")

        self._async(f"正在读取艺人歌曲: {item['name']}", worker, done)

    # ── 搜索 ─────────────────────────────────────────────────────

    def _on_search(self):
        keyword = self.search_var.get().strip()
        if not keyword:
            self._load_category("recommend")
            return

        def worker():
            return self.client.search(keyword, page=0, page_size=50)

        def done(result, error):
            if error:
                self._set_status(f"搜索失败: {error}", warn=True)
                return
            self._set_song_list(f"搜索: {keyword}", result or [])
            self._set_status(f"搜索完成: {keyword}（{len(result or [])} 首）")

        self._async(f"正在搜索: {keyword}", worker, done)

    # ── 登录 ─────────────────────────────────────────────────────

    def _update_login_label(self):
        if self.client.logged_in:
            nick = self.client.nickname or self.client.uid
            extra = ""
            if self.client.qq_uid != "-1" and self.client.qq_token:
                extra = " | 已缓存QQ会话"
            elif self.client.qq_open_id and self.client.qq_open_token:
                extra = " | 已提取QQ授权"
            self.login_label.configure(text=f"已登录: {nick} (UID={self.client.uid}){extra}", fg=APP_ACCENT)
        else:
            self.login_label.configure(text="未登录（请先登录）", fg=APP_WARN)

    def _on_qr_login(self):
        if self.client.logged_in:
            self._set_status("当前已登录")
            return

        def worker():
            old_state = (self.client.uid, self.client.token, self.client.nickname, self.client.logged_in)
            self.client.uid = "-1"
            self.client.token = ""
            self.client.nickname = ""
            self.client.logged_in = False
            qr_data, qr_resp = self.client.request_login_qr()
            qr_code = qr_data.get("qrCode") if qr_data else ""
            if not qr_code:
                self.client.uid, self.client.token, self.client.nickname, self.client.logged_in = old_state
                raise RuntimeError(f"获取二维码失败: {qr_resp}")
            return {"qr_code": qr_code, "restore": old_state}

        def done(result, error):
            if error:
                self._set_status(str(error), warn=True)
                return
            self.login_restore = result["restore"]
            self.login_qr_code = result["qr_code"]
            self.login_deadline = time.time() + 120
            self.next_qr_poll_at = 0
            self._show_qr_window(self.login_qr_code)
            self._set_status("请用波点 App 扫描二维码确认登录")

        self._async("正在请求二维码登录", worker, done)

    def _poll_qr_login(self):
        if not self.login_qr_code or self.qr_checking:
            return
        if time.time() > self.login_deadline:
            self._close_qr_window()
            self._restore_login_state()
            self._set_status("二维码登录超时", warn=True)
            return
        if self.next_qr_poll_at and time.time() < self.next_qr_poll_at:
            return
        self.qr_checking = True

        def worker():
            status_data, status_resp = self.client.check_login_qr(self.login_qr_code)
            return status_data.get("status") if status_data else None, status_resp

        def done(result, error):
            self.qr_checking = False
            if error:
                self._set_status(f"二维码状态检查失败: {error}", warn=True)
                return
            status, status_resp = result
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
            self._set_status(f"二维码登录未完成: {status_resp}", warn=True)

        self._async("正在检查二维码状态", worker, done)

    def _exchange_qr_login(self, qr_code, status_data):
        self._close_qr_window()

        def worker():
            last_resp = None
            rounds = 0
            while time.time() < self.login_deadline:
                rounds += 1
                try:
                    data, resp = self.client.login_from_qr_status(qr_code, status_data)
                except Exception as exc:
                    data, resp = None, {"code": -1, "msg": str(exc)}
                if data and self.client.logged_in:
                    return data
                last_resp = resp
                # 扫码确认后登录态会写入本机波点官方客户端日志，换凭证接口
                # 未及时返回时，从日志兜底同步（与重启后的补登录同一路径）
                if rounds % 3 == 0:
                    try:
                        self.client._sync_main_credentials_from_logs()
                    except Exception:
                        pass
                    if self.client.logged_in and self.client.uid != "-1":
                        return {"synced": True}
                time.sleep(0.8)
            detail = ""
            if isinstance(last_resp, dict):
                detail = f"（code={last_resp.get('code')} {last_resp.get('msg') or ''}）".strip()
            raise RuntimeError(f"换取凭证超时 {detail}")

        def done(_result, error):
            self._close_qr_window()
            if error:
                if self.client.logged_in and self.client.uid != "-1":
                    self.login_qr_code = ""
                    self.login_deadline = 0
                    self.login_restore = None
                    self._update_login_label()
                    self._set_status("二维码登录成功")
                    return
                self._restore_login_state()
                self._set_status(f"二维码登录失败: {error}", warn=True)
                return
            self.login_qr_code = ""
            self.login_deadline = 0
            self.login_restore = None
            self._update_login_label()
            self._set_status("二维码登录成功")

        self._async("正在换取登录凭证", worker, done)

    def _show_qr_window(self, qr_code):
        """弹窗展示官方登录链接的二维码（替代旧版浏览器打开方式）。"""
        self._close_qr_window()
        try:
            png_data = _generate_qr_png(_make_qr_url(qr_code))
            import io
            image = Image.open(io.BytesIO(png_data))
            photo = ImageTk.PhotoImage(image)
        except Exception:
            qr_url = (
                "https://api.qrserver.com/v1/create-qr-code/"
                f"?size=280x280&data={urllib.parse.quote(_make_qr_url(qr_code))}"
            )
            webbrowser.open(qr_url)
            self._set_status("二维码已在浏览器打开，请用波点 App 扫描", warn=True)
            return
        win = tk.Toplevel(self.root)
        win.title("扫码登录")
        win.configure(bg="white")
        win.resizable(False, False)
        label = tk.Label(win, image=photo, bg="white")
        label.image = photo
        label.pack(padx=18, pady=(16, 4))
        tk.Label(win, text="请用波点 App 扫码确认登录", bg="white", fg="#333333",
                 font=FONT_MAIN).pack(padx=18, pady=(0, 14))
        self._qr_window = win

    def _close_qr_window(self):
        if self._qr_window is not None:
            try:
                self._qr_window.destroy()
            except tk.TclError:
                pass
            self._qr_window = None

    def _restore_login_state(self):
        if self.client.logged_in and self.client.uid != "-1":
            self.login_qr_code = ""
            self.login_deadline = 0
            self.login_restore = None
            self.qr_checking = False
            self.next_qr_poll_at = 0
            self._update_login_label()
            return
        if not self.login_restore:
            return
        self.client.uid, self.client.token, self.client.nickname, self.client.logged_in = self.login_restore
        self.login_qr_code = ""
        self.login_deadline = 0
        self.login_restore = None
        self.qr_checking = False
        self.next_qr_poll_at = 0
        self._update_login_label()

    def _open_manual_login(self):
        win = tk.Toplevel(self.root)
        win.title("手动登录")
        win.configure(bg=APP_BG)
        win.transient(self.root)
        win.resizable(False, False)
        tk.Label(win, text="UID:", bg=APP_BG, fg=APP_FG, font=FONT_MAIN).grid(row=0, column=0, sticky="e", padx=(12, 6), pady=(12, 4))
        uid_edit = tk.Entry(win, bg=APP_PANEL, fg=APP_FG, insertbackground=APP_FG, relief="flat", width=32)
        uid_edit.grid(row=0, column=1, padx=(0, 12), pady=(12, 4), ipady=3)
        tk.Label(win, text="Token:", bg=APP_BG, fg=APP_FG, font=FONT_MAIN).grid(row=1, column=0, sticky="e", padx=(12, 6), pady=4)
        token_edit = tk.Entry(win, bg=APP_PANEL, fg=APP_FG, insertbackground=APP_FG, relief="flat", width=32)
        token_edit.grid(row=1, column=1, padx=(0, 12), pady=4, ipady=3)

        def confirm():
            uid = uid_edit.get().strip()
            token = token_edit.get().strip()
            if not uid or not token:
                self._set_status("UID 和 Token 不能为空", warn=True)
                return
            self.client.set_credentials(uid, token)
            self._update_login_label()
            win.destroy()
            self._set_status("手动登录成功")

        buttons = tk.Frame(win, bg=APP_BG)
        buttons.grid(row=2, column=0, columnspan=2, pady=(8, 12))
        self._make_button(buttons, "确认", confirm, accent=True).pack(side="left", padx=6)
        self._make_button(buttons, "取消", win.destroy).pack(side="left", padx=6)
        uid_edit.focus_set()

    def _on_extract(self):
        def worker():
            ok = self.client.extract_from_client()
            if not ok:
                raise RuntimeError("未能从波点 PC 客户端提取到凭证")
            return ok

        def done(_result, error):
            if error:
                self._set_status(str(error), warn=True)
                return
            self._update_login_label()
            self._set_status("提取凭证成功")

        self._async("正在从客户端提取凭证", worker, done)

    def _on_logout(self):
        self._next_playback_request()
        self.player.stop()
        self.client.logout(quiet=True)
        self.current_song = None
        self.current_playback_quality_key = None
        self.last_follow_song_id = None
        self.follow_started_at = 0.0
        self._reset_cover()
        if not self.lyrics_only:
            self.now_playing_label.configure(text="未播放")
        else:
            self.now_following_label.configure(text="当前：-")
        self._show_lyric_text("开始播放后加载歌词")
        self._update_login_label()
        self._set_status("已登出")
        self._push_lyric_overlay(force=True)

    def _open_download_dir(self):
        chosen = filedialog.askdirectory(parent=self.root, initialdir=self.download_dir or ".", title="选择下载目录")
        if not chosen:
            return
        self.download_dir = chosen
        self.client.set_local_config(
            download_dir=chosen,
            quality=self.download_quality_key,
            playback_quality=self.playback_quality_key,
            download_quality=self.download_quality_key,
        )
        self._set_status(f"下载目录已更新: {chosen}")

    # ── 歌词浮窗 ─────────────────────────────────────────────────

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
            self.lyric_overlay_enabled = False
            if self.overlay_var is not None:
                self.overlay_var.set(False)
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
            if self.overlay_var is not None:
                self.overlay_var.set(False)
            self._save_overlay_settings()
            self._set_status("歌词浮窗启动失败", warn=True)
            return None
        self._push_lyric_overlay(force=True)
        return self.lyric_overlay

    def _push_lyric_overlay(self, force=False):
        if not self.lyric_overlay:
            return
        if not force and not self.current_song and not self.current_lyric_raw:
            return
        song_title = self.current_song.get("name", "") if self.current_song else ""
        artist = self.current_song.get("artist", "") if self.current_song else ""
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

    def _toggle_lyric_overlay(self):
        if self.lyric_overlay:
            self.lyric_overlay.close()
            self.lyric_overlay = None
            self.lyric_overlay_enabled = False
            self.overlay_var.set(False)
            self._save_overlay_settings()
            self._set_status("歌词浮窗已关闭")
            return
        self.lyric_overlay_enabled = True
        self.overlay_var.set(True)
        self._save_overlay_settings()
        self._ensure_lyric_overlay()
        self._set_status("歌词浮窗已开启")

    def _toggle_lyric_overlay_topmost(self):
        self.lyric_overlay_topmost = not self.lyric_overlay_topmost
        self._save_overlay_settings()
        if self.lyric_overlay:
            self.lyric_overlay.set_topmost(self.lyric_overlay_topmost)
        self._set_status(f"歌词浮窗置顶: {'开启' if self.lyric_overlay_topmost else '关闭'}")

    def _toggle_lyric_overlay_lock(self):
        self.lyric_overlay_locked = not self.lyric_overlay_locked
        self._save_overlay_settings()
        if self.lyric_overlay:
            self.lyric_overlay.set_locked(self.lyric_overlay_locked)
        self._set_status(f"歌词浮窗锁定: {'开启' if self.lyric_overlay_locked else '关闭'}")

    def _cycle_lyric_overlay_theme(self):
        self.lyric_overlay_theme = (self.lyric_overlay_theme + 1) % len(THEMES)
        self.lyric_overlay_primary_color = ""
        self._save_overlay_settings()
        if self.lyric_overlay:
            self.lyric_overlay.next_theme()
        self._set_status(f"歌词浮窗颜色: {THEMES[self.lyric_overlay_theme]['name']}")

    def _mark_slider_active(self, active):
        self._overlay_slider_active = bool(active)

    def _slider_sync_done(self):
        self._overlay_slider_syncing = False

    def _open_overlay_settings(self):
        if getattr(self, "_overlay_settings_win", None) is not None:
            try:
                self._overlay_settings_win.lift()
                return
            except tk.TclError:
                self._overlay_settings_win = None
        win = tk.Toplevel(self.root)
        win.title("歌词浮窗设置")
        win.configure(bg=APP_BG)
        win.transient(self.root)
        win.resizable(False, False)
        self._overlay_settings_win = win

        def on_close():
            self._overlay_settings_win = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

        tk.Label(win, text="主题颜色", bg=APP_BG, fg=APP_MUTED, font=FONT_SMALL).pack(anchor="w", padx=14, pady=(12, 2))
        theme_row = tk.Frame(win, bg=APP_BG)
        theme_row.pack(anchor="w", padx=14)
        for index, theme in enumerate(THEMES):
            self._make_button(
                theme_row,
                theme["name"],
                lambda picked=index: self._apply_overlay_theme(picked),
            ).pack(side="left", padx=2)

        custom_row = tk.Frame(win, bg=APP_BG)
        custom_row.pack(anchor="w", padx=14, pady=(6, 0))
        self._make_button(custom_row, "自定义颜色…", self._pick_overlay_color, accent=True).pack(side="left", padx=2)
        self._make_button(custom_row, "恢复主题色", self._reset_overlay_color).pack(side="left", padx=2)
        self._make_button(custom_row, "复位浮窗位置", self._reset_overlay_geometry).pack(side="left", padx=2)

        slider_specs = []

        font_row = tk.Frame(win, bg=APP_BG)
        font_row.pack(fill="x", padx=14, pady=(12, 0))
        tk.Label(font_row, text="歌词字号", bg=APP_BG, fg=APP_FG, font=FONT_SMALL, width=9, anchor="w").pack(side="left")
        font_value = tk.Label(font_row, text=f"{int(self.lyric_overlay_font_scale * 100)}%", bg=APP_BG, fg=APP_MUTED, font=FONT_SMALL, width=6)
        font_value.pack(side="right")
        font_scale_var = tk.DoubleVar(value=self.lyric_overlay_font_scale)

        def on_font(value):
            if self._overlay_slider_syncing:
                return
            scale = max(0.6, min(2.0, float(value)))
            self.lyric_overlay_font_scale = round(scale, 2)
            font_value.configure(text=f"{int(scale * 100)}%")
            if self.lyric_overlay:
                self.lyric_overlay.set_font_scale(scale)
            self._save_overlay_settings()

        font_slider = ttk.Scale(font_row, orient="horizontal", from_=60, to=200, variable=font_scale_var, command=on_font)
        font_slider.pack(side="left", fill="x", expand=True, padx=6)
        font_slider.bind("<ButtonPress-1>", lambda _e: self._mark_slider_active(True))
        font_slider.bind("<ButtonRelease-1>", lambda _e: self._mark_slider_active(False))
        slider_specs.append((font_scale_var, self.lyric_overlay_font_scale))

        gap_row = tk.Frame(win, bg=APP_BG)
        gap_row.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(gap_row, text="上下行距", bg=APP_BG, fg=APP_FG, font=FONT_SMALL, width=9, anchor="w").pack(side="left")
        gap_value = tk.Label(gap_row, text="自动" if not self.lyric_overlay_line_gap else f"{self.lyric_overlay_line_gap}px",
                             bg=APP_BG, fg=APP_MUTED, font=FONT_SMALL, width=6)
        gap_value.pack(side="right")
        gap_var = tk.DoubleVar(value=self.lyric_overlay_line_gap)

        def on_gap(value):
            if self._overlay_slider_syncing:
                return
            gap = int(float(value))
            self.lyric_overlay_line_gap = gap
            gap_value.configure(text="自动" if gap == 0 else f"{gap}px")
            if self.lyric_overlay:
                self.lyric_overlay.set_line_gap(gap)
            self._save_overlay_settings()

        gap_slider = ttk.Scale(gap_row, orient="horizontal", from_=0, to=60, variable=gap_var, command=on_gap)
        gap_slider.pack(side="left", fill="x", expand=True, padx=6)
        gap_slider.bind("<ButtonPress-1>", lambda _e: self._mark_slider_active(True))
        gap_slider.bind("<ButtonRelease-1>", lambda _e: self._mark_slider_active(False))
        slider_specs.append((gap_var, self.lyric_overlay_line_gap))

        opacity_row = tk.Frame(win, bg=APP_BG)
        opacity_row.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(opacity_row, text="不透明度", bg=APP_BG, fg=APP_FG, font=FONT_SMALL, width=9, anchor="w").pack(side="left")
        opacity_value = tk.Label(opacity_row, text=f"{int(self.lyric_overlay_opacity * 100)}%", bg=APP_BG, fg=APP_MUTED, font=FONT_SMALL, width=6)
        opacity_value.pack(side="right")
        opacity_var = tk.DoubleVar(value=int(self.lyric_overlay_opacity * 100))

        def on_opacity(value):
            if self._overlay_slider_syncing:
                return
            opacity = max(0.3, min(1.0, float(value) / 100.0))
            self.lyric_overlay_opacity = opacity
            opacity_value.configure(text=f"{int(float(value))}%")
            if self.lyric_overlay:
                self.lyric_overlay.set_opacity(opacity)
            self._save_overlay_settings()

        opacity_slider = ttk.Scale(opacity_row, orient="horizontal", from_=30, to=100, variable=opacity_var, command=on_opacity)
        opacity_slider.pack(side="left", fill="x", expand=True, padx=6)
        opacity_slider.bind("<ButtonPress-1>", lambda _e: self._mark_slider_active(True))
        opacity_slider.bind("<ButtonRelease-1>", lambda _e: self._mark_slider_active(False))
        slider_specs.append((opacity_var, int(self.lyric_overlay_opacity * 100)))

        hint = tk.Label(
            win,
            text="提示：鼠标滚轮悬停在歌词浮窗上可直接调整字号；浮窗顶栏 A－/A＋ 同样可调。",
            bg=APP_BG,
            fg=APP_MUTED,
            font=FONT_SMALL,
            wraplength=360,
            justify="left",
        )
        hint.pack(anchor="w", padx=14, pady=(12, 4))
        self._make_button(win, "关闭", on_close).pack(fill="x", padx=14, pady=(2, 12))

        def refresh_vars():
            if self._overlay_settings_win is not win:
                return
            if self._overlay_slider_active:
                win.after(1500, refresh_vars)
                return
            for var, current in zip(slider_specs, (
                self.lyric_overlay_font_scale,
                self.lyric_overlay_line_gap,
                int(self.lyric_overlay_opacity * 100),
            )):
                try:
                    self._overlay_slider_syncing = True
                    var.set(current)
                except tk.TclError:
                    pass
                finally:
                    win.after(120, self._slider_sync_done)
            win.after(1500, refresh_vars)

        win.after(1500, refresh_vars)

    def _apply_overlay_theme(self, index):
        self.lyric_overlay_theme = index % len(THEMES)
        self.lyric_overlay_primary_color = ""
        self._save_overlay_settings()
        if self.lyric_overlay:
            self.lyric_overlay.set_theme(index)
        self._set_status(f"歌词浮窗颜色: {THEMES[self.lyric_overlay_theme]['name']}")

    def _pick_overlay_color(self):
        from tkinter import colorchooser
        current = self.lyric_overlay_primary_color or THEMES[self.lyric_overlay_theme]["text"]
        picked = colorchooser.askcolor(color=current, parent=self.root, title="选择歌词颜色")
        if not picked or not picked[1]:
            return
        color = picked[1]
        if isinstance(color, tuple):
            color = "#%02x%02x%02x" % tuple(int(round(c)) for c in color[:3])
        color = str(color).strip()
        self.lyric_overlay_primary_color = color
        self._save_overlay_settings()
        if self.lyric_overlay:
            self.lyric_overlay.set_primary_color(color)
        self._set_status(f"歌词颜色已设为 {color}")

    def _reset_overlay_color(self):
        self.lyric_overlay_primary_color = ""
        self._save_overlay_settings()
        if self.lyric_overlay:
            self.lyric_overlay.set_primary_color("")
        self._set_status("歌词颜色已恢复主题默认")

    def _reset_overlay_geometry(self):
        self.lyric_overlay_geometry = ""
        self._save_overlay_settings()
        if self.lyric_overlay:
            self.lyric_overlay.reset_geometry()
        self._set_status("浮窗位置已复位（底部居中）")

    # ── 主循环 ───────────────────────────────────────────────────

    def _overlay_position_state(self):
        """歌词进度来源：完整模式取本机播放器，仅歌词模式按时间推算。"""
        if self.lyrics_only:
            if self.current_song and self.follow_started_at:
                elapsed_ms = int((time.time() - self.follow_started_at) * 1000)
                duration_ms = int(float(self.current_song.get("duration") or 0)) * 1000
                state = "playing" if elapsed_ms <= duration_ms + 1500 else "stopped"
                return max(0, elapsed_ms), duration_ms, state
            return 0, 0, "stopped"
        return self.player.get_position_ms(), self.player.duration_ms, self.player.state

    def _tick(self):
        if self.shutting_down:
            return
        while True:
            try:
                callback = self.queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:
                pass

        self._poll_qr_login()

        if self.lyrics_only:
            self._poll_credentials_from_logs()
            self._poll_follow_client()
            position_ms, _duration_ms, _state = self._overlay_position_state()
        else:
            if self.player.poll_finished() and self.player.just_finished:
                self.player.just_finished = False
                if self.play_queue and self.play_queue_index + 1 < len(self.play_queue):
                    self.play_queue_index += 1
                    self._start_playback(self.play_queue[self.play_queue_index])
                elif self.current_song:
                    self._set_status(f"播放结束: {self.current_song['name']}")

            position_ms = self.player.get_position_ms()
            total_ms = self.player.duration_ms
            if total_ms > 0:
                self.duration_label.configure(text=_fmt_dur(total_ms // 1000))
                if not self.seek_dragging:
                    try:
                        self.seek_scale.configure(to=total_ms)
                        self.seek_var.set(min(position_ms, total_ms))
                    except tk.TclError:
                        pass
            if not self.seek_dragging:
                self.position_label.configure(text=_fmt_dur(position_ms // 1000))

        self._update_lyric_highlight(position_ms)
        self._push_lyric_overlay()
        self.root.after(300, self._tick)

    def _on_close(self):
        if self.shutting_down:
            return
        self.shutting_down = True
        self._next_playback_request()
        self._close_qr_window()
        if self.lyric_overlay:
            self.lyric_overlay.close()
            self.lyric_overlay = None
        self.player.close()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # ── 仅歌词模式：跟随波点客户端 ────────────────────────────────

    def _toggle_follow_client(self):
        self.follow_client_enabled = bool(self.follow_var.get())
        self.client.set_local_config(follow_client_enabled=self.follow_client_enabled)
        if self.follow_client_enabled:
            self._set_status("已开启跟随：等待波点客户端播放…")
        else:
            self._set_status("已关闭跟随")

    def _poll_credentials_from_logs(self):
        if self.client.logged_in:
            return
        now = time.monotonic()
        if now < self.next_cred_sync_at:
            return
        self.next_cred_sync_at = now + 60.0

        def job():
            self.client._sync_main_credentials_from_logs()

        def done(_result, _error):
            if self.client.logged_in:
                self._update_login_label()
                self._set_status("已从波点客户端日志同步到登录凭证")

        self._async("正在从波点客户端日志同步登录状态", job, done)

    def _poll_follow_client(self):
        if not self.follow_client_enabled or self.follow_checking:
            return
        now = time.monotonic()
        if now < self.next_follow_poll_at:
            return
        self.next_follow_poll_at = now + 2.0
        self.follow_checking = True

        def worker():
            items = self.client.get_history_db_snapshot(limit=1)
            if not items:
                return None
            entry = items[0]
            return entry

        def done(entry, error):
            self.follow_checking = False
            if error or not entry:
                return
            song_id = entry.get("id")
            if song_id is None or str(song_id) == str(self.last_follow_song_id):
                return
            data = entry.get("data") or {}
            name = data.get("name")
            if not name:
                return
            self.last_follow_song_id = song_id
            self._start_follow_song(entry)

        threading.Thread(target=lambda: done(*_safe_call(worker)), daemon=True).start()

    def _start_follow_song(self, entry):
        def apply():
            data = entry.get("data") or {}
            song = self.client.normalize_song(data)
            self.current_song = song
            self.current_playback_quality_key = None
            started_at = _parse_db_time(entry.get("time"))
            self.follow_started_at = started_at or time.time()
            self.manual_started_at = self.follow_started_at
            title = f"{song.get('artist', '-')} - {song.get('name', '?')}"
            self.now_following_label.configure(text=f"当前：{title}")
            self._show_lyric_text("正在加载歌词")
            self._load_lyric(song)
            self._set_status(f"已跟随客户端播放: {title}")

        if threading.current_thread() is threading.main_thread():
            apply()
        else:
            self.queue.put(apply)

    def _on_lyrics_search(self):
        keyword = self.lyrics_search_var.get().strip()
        if not keyword:
            self._set_status("请输入要搜索的歌曲", warn=True)
            return

        def worker():
            return self.client.search(keyword, page=0, page_size=20)

        def done(result, error):
            if error:
                self._set_status(f"搜索失败: {error}", warn=True)
                return
            songs = result or []
            if not songs:
                self._set_status("无搜索结果", warn=True)
                return
            self._show_lyrics_pick_dialog(keyword, songs)

        self._async(f"正在搜索: {keyword}", worker, done)

    def _show_lyrics_pick_dialog(self, keyword, songs):
        win = tk.Toplevel(self.root)
        win.title(f"选择歌曲: {keyword}")
        win.configure(bg=APP_BG)
        win.transient(self.root)
        win.geometry("560x360")
        listbox = tk.Listbox(
            win,
            bg=APP_PANEL,
            fg=APP_FG,
            selectbackground=APP_SELECT,
            selectforeground=APP_ACCENT,
            relief="flat",
            font=FONT_MAIN,
            activestyle="none",
        )
        for song in songs:
            listbox.insert("end", f"{song.get('artist', '-')} - {song.get('name', '?')}  [{_fmt_dur(song.get('duration') or 0)}]")
        listbox.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        def pick(_event=None):
            selection = listbox.curselection()
            if not selection:
                return
            song = songs[selection[0]]
            win.destroy()
            self.current_song = song
            self.follow_started_at = time.time()
            self.manual_started_at = self.follow_started_at
            self.now_following_label.configure(text=f"当前：{song.get('artist', '-')} - {song.get('name', '?')}")
            self._show_lyric_text("正在加载歌词")
            self._load_lyric(song)
            self._set_status(f"展示歌词: {song.get('artist', '-')} - {song.get('name', '?')}")

        listbox.bind("<Double-1>", pick)
        listbox.bind("<Return>", pick)
        listbox.focus_set()


def _parse_db_time(value):
    """把播放历史里的时间字符串解析为本地 epoch 秒，失败返回 0。"""
    if not value:
        return 0.0
    try:
        from datetime import datetime
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            return time.mktime(time.strptime(text[:19], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


def _safe_call(func):
    try:
        return func(), None
    except Exception as exc:
        return None, str(exc)


def main():
    lyrics_only = "--lyrics-only" in sys.argv or "--lyric" in sys.argv
    _enable_dpi_awareness()
    app = BoDianGUI(lyrics_only=lyrics_only)
    app.root.mainloop()


if __name__ == "__main__":
    main()
