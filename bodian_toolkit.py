#!/usr/bin/env python3
"""
波点音乐工具核心模块
CTF Competition - Authorized Security Research

功能: 登录、搜索、元数据、歌单、播放、下载等能力
"""

import urllib.request
import urllib.parse
import json
import time
import ssl
import gzip
import os
import sys
import re
import hashlib
import uuid
import webbrowser
import sqlite3
import http.cookiejar
import shutil
import subprocess
import tempfile

from bodian_media import (
    QUALITY_OPTIONS,
    build_quality_choices,
    can_local_playback_format,
    decode_lyric_content,
    encode_lyric_query,
    parse_lrc_lines,
    resolve_playback_quality_key,
    resolve_quality_key,
)

# ─── UTF-8 ──────────────────────────────────────────────────────────

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ─── 配置 ───────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_STATE_DIR = os.path.join(BASE_DIR, ".bodian")
AUTH_FILE = os.path.join(LOCAL_STATE_DIR, "auth.json")
CONFIG_FILE = os.path.join(LOCAL_STATE_DIR, "config.json")
DEFAULT_DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
API_BASE = "https://bd-api.kuwo.cn"
LOCAL_APPDATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "cn.wenyu.bodian", "bodian_pc")
BDLOG_DIR = os.path.join(LOCAL_APPDATA_DIR, "bdlog")
PLAYLIST_DB_FILE = os.path.join(LOCAL_APPDATA_DIR, "database", "38925918_self_collect_songlist.db")
HISTORY_DB_FILE = os.path.join(LOCAL_APPDATA_DIR, "database", "songDB.db")

DEFAULT_HEADERS = {
    "user-agent": "Dart/3.3 (dart:io)",
    "plat": "win",
    "accept-encoding": "gzip",
    "api-ver": "application/json",
    "channel": "W1",
    "brand": "Windows 11 Pro for Workstations",
    "net": "wifi",
    "content-type": "application/json",
    "ver": "1.1.5",
    "svrver": "13",
}

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def _ensure_local_state_dir():
    os.makedirs(LOCAL_STATE_DIR, exist_ok=True)


def _load_local_json(path, default):
    if not os.path.exists(path):
        return dict(default)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            merged = dict(default)
            merged.update(data)
            return merged
    except Exception:
        pass
    return dict(default)


def _save_local_json(path, data):
    _ensure_local_state_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _recent_log_files(limit=4):
    if not os.path.isdir(BDLOG_DIR):
        return []
    log_files = [
        os.path.join(BDLOG_DIR, name)
        for name in os.listdir(BDLOG_DIR)
        if name.startswith("log_") and name.endswith(".log")
    ]
    log_files.sort(key=os.path.getmtime, reverse=True)
    return log_files[:limit]


def load_local_config():
    config = _load_local_json(CONFIG_FILE, {
        "download_dir": DEFAULT_DOWNLOAD_DIR,
        "quality": "6",
        "playback_quality": "2",
        "download_quality": "6",
        "lyric_overlay_enabled": True,
        "lyric_overlay_topmost": True,
        "lyric_overlay_locked": False,
        "lyric_overlay_theme": 0,
        "lyric_overlay_opacity": 0.96,
        "lyric_overlay_geometry": "",
    })
    if config.get("lyric_overlay_geometry") in ("460x220+120+120", "720x280+120+120"):
        config["lyric_overlay_geometry"] = ""
    legacy_quality = config.get("quality", "6")
    if not config.get("playback_quality"):
        config["playback_quality"] = "2" if legacy_quality == "11" else legacy_quality
    if not config.get("download_quality"):
        config["download_quality"] = legacy_quality
    if config.get("playback_quality") == "11":
        config["playback_quality"] = "2"
    return config


def save_local_config(config):
    _save_local_json(CONFIG_FILE, config)


# ─── 终端 QR 码渲染（纯 Python，无第三方依赖）─────────────────────

def _print_qr_terminal(data_str):
    """在终端渲染 QR 码，优先用 qrcode 库，否则用 webbrowser 打开在线图片"""
    try:
        import qrcode
        qr = qrcode.QRCode(box_size=1, border=1)
        qr.add_data(data_str)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
        return
    except ImportError:
        pass

    # 兜底：用浏览器打开在线二维码
    qr_url = (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size=300x300&data={urllib.parse.quote(data_str)}"
    )
    print(f"\n  (终端 QR 需要: pip install qrcode)")
    print(f"  已在浏览器中打开二维码，或手动访问:")
    print(f"  {qr_url}")
    try:
        webbrowser.open(qr_url)
    except Exception:
        pass


# ─── HTTP 客户端 ────────────────────────────────────────────────────

class BoDianClient:

    def __init__(self):
        self.uid = "-1"
        self.token = ""
        self.nickname = ""
        self.auth_type = 0
        self.qq_open_id = ""
        self.qq_open_token = ""
        self.qq_expire_time = 0
        self.qq_nickname = ""
        self.qq_head_img = ""
        self.qq_import_status = 0
        self.qq_uid = "-1"
        self.qq_token = ""
        self.qq_auth_type = 0
        self.qq_session_nickname = ""
        self.user_is_vip = 0
        self.vip_type = 0
        self.pay_vip_type = 0
        self.act_vip_type = 0
        self.pay_expire_date = 0
        self.act_expire_date = 0
        self.pay_is_vip_boolean = False
        self.dev_id = hashlib.md5(uuid.uuid4().bytes).hexdigest()
        self.local_config = load_local_config()
        self.headers = {**DEFAULT_HEADERS, "devid": self.dev_id, "qimei36": self.dev_id}
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar),
            urllib.request.HTTPSHandler(context=SSL_CTX),
        )
        self.ffmpeg = shutil.which("ffmpeg")
        self.logged_in = False
        self._log_free_sign_cache = None
        self._try_load_credentials()

    # ── 凭证持久化 ─────────────────────────────────────────────────

    def _try_load_credentials(self):
        if not os.path.exists(AUTH_FILE):
            self._sync_main_credentials_from_logs()
            return
        try:
            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                creds = json.load(f)
            self.uid = creds.get("uid", "-1")
            self.token = creds.get("token", "")
            self.dev_id = creds.get("dev_id", self.dev_id)
            self.nickname = creds.get("nickname", "")
            self.auth_type = creds.get("auth_type", 0) or 0
            self.qq_open_id = str(creds.get("qq_open_id") or "")
            self.qq_open_token = creds.get("qq_open_token", "") or ""
            self.qq_expire_time = creds.get("qq_expire_time", 0) or 0
            self.qq_nickname = creds.get("qq_nickname", "") or ""
            self.qq_head_img = creds.get("qq_head_img", "") or ""
            self.qq_import_status = creds.get("qq_import_status", 0) or 0
            self.qq_uid = str(creds.get("qq_uid", "-1") or "-1")
            self.qq_token = creds.get("qq_token", "") or ""
            self.qq_auth_type = creds.get("qq_auth_type", 0) or 0
            self.qq_session_nickname = creds.get("qq_session_nickname", "") or ""
            self.user_is_vip = creds.get("user_is_vip", 0) or 0
            self.vip_type = creds.get("vip_type", 0) or 0
            self.pay_vip_type = creds.get("pay_vip_type", 0) or 0
            self.act_vip_type = creds.get("act_vip_type", 0) or 0
            self.pay_expire_date = creds.get("pay_expire_date", 0) or 0
            self.act_expire_date = creds.get("act_expire_date", 0) or 0
            self.pay_is_vip_boolean = bool(creds.get("pay_is_vip_boolean", False))
            self.headers["devid"] = self.dev_id
            self.headers["qimei36"] = self.dev_id
            if self.uid != "-1" and self.token:
                self.logged_in = True
        except Exception:
            pass
        self._sync_main_credentials_from_logs()

    def _save_credentials(self):
        try:
            _save_local_json(AUTH_FILE, {
                "uid": self.uid,
                "token": self.token,
                "dev_id": self.dev_id,
                "nickname": self.nickname,
                "auth_type": self.auth_type,
                "qq_open_id": self.qq_open_id,
                "qq_open_token": self.qq_open_token,
                "qq_expire_time": self.qq_expire_time,
                "qq_nickname": self.qq_nickname,
                "qq_head_img": self.qq_head_img,
                "qq_import_status": self.qq_import_status,
                "qq_uid": self.qq_uid,
                "qq_token": self.qq_token,
                "qq_auth_type": self.qq_auth_type,
                "qq_session_nickname": self.qq_session_nickname,
                "user_is_vip": self.user_is_vip,
                "vip_type": self.vip_type,
                "pay_vip_type": self.pay_vip_type,
                "act_vip_type": self.act_vip_type,
                "pay_expire_date": self.pay_expire_date,
                "act_expire_date": self.act_expire_date,
                "pay_is_vip_boolean": self.pay_is_vip_boolean,
            })
        except Exception:
            pass

    def _sync_main_credentials_from_logs(self):
        latest_uid = ""
        latest_token = ""
        latest_login = None
        for path in _recent_log_files():
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in reversed(f.readlines()):
                        if latest_login is None and '"code":200' in line and '"userInfo"' in line and '"payInfo"' in line and '"token":"' in line:
                            json_start = line.find('{"code":200')
                            if json_start >= 0:
                                try:
                                    payload = json.loads(line[json_start:])
                                except Exception:
                                    payload = None
                                data = (payload or {}).get("data") or {}
                                if data.get("id") and data.get("token"):
                                    latest_login = data
                        if not latest_uid or not latest_token:
                            match = re.search(r"uid=(\d+)&token=([0-9a-f]{32})", line)
                            if match:
                                latest_uid, latest_token = match.groups()
                        if latest_login and latest_uid and latest_token:
                            break
            except Exception:
                continue
            if latest_login and latest_uid and latest_token:
                break
        if latest_login:
            user_info = latest_login.get("userInfo") or {}
            pay_info = latest_login.get("payInfo") or {}
            self.uid = str(latest_login.get("id", latest_uid or self.uid))
            self.token = latest_login.get("token", latest_token or self.token)
            self.nickname = user_info.get("nickname", self.nickname)
            self.auth_type = user_info.get("authType", self.auth_type) or 0
            self.user_is_vip = user_info.get("isVip", self.user_is_vip) or 0
            self.vip_type = pay_info.get("vipType", user_info.get("vipType", self.vip_type)) or 0
            self.pay_vip_type = pay_info.get("payVipType", user_info.get("payVipType", self.pay_vip_type)) or 0
            self.act_vip_type = pay_info.get("actVipType", self.act_vip_type) or 0
            self.pay_expire_date = pay_info.get("payExpireDate", pay_info.get("expireDate", self.pay_expire_date)) or 0
            self.act_expire_date = pay_info.get("actExpireDate", self.act_expire_date) or 0
            self.pay_is_vip_boolean = bool(pay_info.get("isVipBoolean", self.pay_is_vip_boolean))
            qq_auth = latest_login.get("qqMusicAuth") or {}
            self.qq_open_id = str(qq_auth.get("openId", self.qq_open_id) or self.qq_open_id or "")
            self.qq_open_token = qq_auth.get("openToken", self.qq_open_token) or self.qq_open_token
            self.qq_expire_time = qq_auth.get("expireTime", self.qq_expire_time) or self.qq_expire_time
            self.qq_nickname = qq_auth.get("nickname", self.qq_nickname) or self.qq_nickname
            self.qq_head_img = qq_auth.get("headImg", self.qq_head_img) or self.qq_head_img
            self.qq_import_status = qq_auth.get("importStatus", self.qq_import_status) or self.qq_import_status
            self.logged_in = self.uid != "-1" and bool(self.token)
            self._save_credentials()
            return
        if not latest_uid or not latest_token:
            return
        if self.uid == latest_uid and self.token == latest_token:
            if self.uid != "-1" and self.token:
                self.logged_in = True
            return
        self.uid = latest_uid
        self.token = latest_token
        self.logged_in = True
        self._save_credentials()

    def get_auth_state(self):
        return {
            "main": {
                "uid": self.uid,
                "nickname": self.nickname,
                "auth_type": self.auth_type,
                "logged_in": self.logged_in,
                "dev_id": self.dev_id,
            },
            "membership": {
                "user_is_vip": self.user_is_vip,
                "vip_type": self.vip_type,
                "pay_vip_type": self.pay_vip_type,
                "act_vip_type": self.act_vip_type,
                "pay_expire_date": self.pay_expire_date,
                "act_expire_date": self.act_expire_date,
                "pay_is_vip_boolean": self.pay_is_vip_boolean,
            },
            "qq_music_auth": {
                "open_id": self.qq_open_id,
                "expire_time": self.qq_expire_time,
                "import_status": self.qq_import_status,
            },
            "audio_session": {
                "uid": self.uid,
                "nickname": self.nickname,
                "auth_type": self.auth_type,
                "enabled": self.uid != "-1" and bool(self.token),
                "ready": self.uid != "-1" and bool(self.token),
                "source": "main",
            },
            "qq_session": {
                "uid": self.qq_uid,
                "nickname": self.qq_session_nickname,
                "auth_type": self.qq_auth_type,
                "ready": self.qq_uid != "-1" and bool(self.qq_token),
            },
        }

    def get_local_config(self, key=None, default=None):
        if key is None:
            return dict(self.local_config)
        return self.local_config.get(key, default)

    def set_local_config(self, **kwargs):
        self.local_config.update(kwargs)
        save_local_config(self.local_config)

    # ── HTTP ────────────────────────────────────────────────────────

    def _query_sign(self, path, params, body_text=""):
        query = urllib.parse.urlencode(params)
        seed = f"kuwotest{''.join(sorted(ch for ch in query if ch.isalnum()))}"
        if body_text:
            seed += hashlib.md5(f"{body_text}kuwotest".encode("utf-8")).hexdigest()
        return hashlib.md5(f"{seed}{path}".encode("utf-8")).hexdigest()

    def _request(self, path, params=None, data=None, method=None, extra_headers=None, sign_body_text=None):
        params = {} if params is None else dict(params)
        body_text = data.decode("utf-8") if data else ""
        sign_text = body_text if sign_body_text is None else sign_body_text
        if "timestamp" in params and "sign" not in params:
            params["sign"] = self._query_sign(path, params, body_text=sign_text)
        qs = urllib.parse.urlencode(params)
        url = f"{API_BASE}{path}?{qs}" if qs else f"{API_BASE}{path}"
        headers = self.headers if not extra_headers else {**self.headers, **extra_headers}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            resp = self.opener.open(req, timeout=15)
            data = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return json.loads(data.decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                body = gzip.decompress(body)
            except Exception:
                pass
            return {"code": e.code, "msg": body.decode("utf-8", errors="replace")[:200]}
        except Exception as e:
            return {"code": -1, "msg": str(e)}

    def _request_ok(self, path, params=None, data=None, method=None, extra_headers=None, sign_body_text=None):
        resp = self._request(
            path,
            params,
            data=data,
            method=method,
            extra_headers=extra_headers,
            sign_body_text=sign_body_text,
        )
        if resp.get("code") != 200:
            return None, resp
        return resp.get("data"), resp

    def _request_json_ok(self, path, params=None, body=None):
        payload = json.dumps(body or {}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        resp = self._request(path, params, data=payload, method="POST")
        if resp.get("code") != 200:
            return None, resp
        return resp.get("data"), resp

    def _request_text(self, url):
        req = urllib.request.Request(url, headers=self.headers)
        resp = self.opener.open(req, timeout=15)
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data.decode("utf-8", errors="replace")

    def _request_binary(self, url):
        req = urllib.request.Request(url, headers=self.headers)
        resp = self.opener.open(req, timeout=30)
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data

    # ── 从客户端提取凭证 ────────────────────────────────────────────

    def extract_from_client(self):
        """从波点PC客户端日志中提取最新的 uid + token"""
        try:
            from extract_credentials import extract_and_show
            result = extract_and_show(do_save=True)
            if result:
                self._try_load_credentials()
                return True
        except ImportError:
            print("  extract_credentials.py 不在同目录下")
        except Exception as e:
            print(f"  提取失败: {e}")
        return False

    def set_credentials(self, uid, token):
        if self.uid not in ("-1", str(uid)):
            self.qq_open_id = ""
            self.qq_open_token = ""
            self.qq_expire_time = 0
            self.qq_nickname = ""
            self.qq_head_img = ""
            self.qq_import_status = 0
            self.qq_uid = "-1"
            self.qq_token = ""
            self.qq_auth_type = 0
            self.qq_session_nickname = ""
        self.uid = str(uid)
        self.token = token
        self.logged_in = True
        self._save_credentials()

    def logout(self):
        self.uid = "-1"
        self.token = ""
        self.nickname = ""
        self.auth_type = 0
        self.qq_open_id = ""
        self.qq_open_token = ""
        self.qq_expire_time = 0
        self.qq_nickname = ""
        self.qq_head_img = ""
        self.qq_import_status = 0
        self.qq_uid = "-1"
        self.qq_token = ""
        self.qq_auth_type = 0
        self.qq_session_nickname = ""
        self.logged_in = False
        if os.path.exists(AUTH_FILE):
            os.remove(AUTH_FILE)
        print("  已登出")

    def request_login_qr(self):
        data, resp = self._request_ok("/api/ucenter/login/qrCode", {
            "uid": "-1",
            "token": "",
            "timestamp": str(int(time.time() * 1000)),
        })
        return data, resp

    def check_login_qr(self, qr_code):
        data, resp = self._request_ok("/api/ucenter/login/qrCodeStatus", {
            "qrCode": qr_code,
            "uid": "-1",
            "token": "",
            "timestamp": str(int(time.time() * 1000)),
        })
        return data, resp

    def users_login(self, uid=None, token=None):
        login_uid = self.uid if uid is None else str(uid)
        login_token = self.token if token is None else token
        data, resp = self._request_ok("/api/ucenter/users/login", {
            "uid": login_uid,
            "token": login_token,
            "timestamp": str(int(time.time() * 1000)),
        })
        if data:
            self.uid = str(data.get("id", self.uid))
            self.token = data.get("token", self.token)
            user_info = data.get("userInfo", {})
            pay_info = data.get("payInfo") or {}
            self.nickname = user_info.get("nickname", self.nickname)
            self.auth_type = user_info.get("authType", self.auth_type) or 0
            self.user_is_vip = user_info.get("isVip", self.user_is_vip) or 0
            self.vip_type = pay_info.get("vipType", user_info.get("vipType", self.vip_type)) or 0
            self.pay_vip_type = pay_info.get("payVipType", user_info.get("payVipType", self.pay_vip_type)) or 0
            self.act_vip_type = pay_info.get("actVipType", self.act_vip_type) or 0
            self.pay_expire_date = pay_info.get("payExpireDate", pay_info.get("expireDate", self.pay_expire_date)) or 0
            self.act_expire_date = pay_info.get("actExpireDate", self.act_expire_date) or 0
            self.pay_is_vip_boolean = bool(pay_info.get("isVipBoolean", self.pay_is_vip_boolean))
            qq_auth = data.get("qqMusicAuth") or {}
            new_open_id = str(qq_auth.get("openId", "") or "")
            new_open_token = qq_auth.get("openToken", "") or ""
            if self.qq_open_id != new_open_id or self.qq_open_token != new_open_token:
                self.qq_uid = "-1"
                self.qq_token = ""
                self.qq_auth_type = 0
                self.qq_session_nickname = ""
            self.qq_open_id = new_open_id
            self.qq_open_token = new_open_token
            self.qq_expire_time = qq_auth.get("expireTime", 0) or 0
            self.qq_nickname = qq_auth.get("nickname", "") or ""
            self.qq_head_img = qq_auth.get("headImg", "") or ""
            self.qq_import_status = qq_auth.get("importStatus", 0) or 0
            self.logged_in = self.uid != "-1" and bool(self.token)
            self._save_credentials()
        return data, resp

    def login_qq_music(self):
        if not self.qq_open_id or not self.qq_open_token:
            return None, {"code": -1, "msg": "当前主账号未返回 QQ 音乐授权信息"}
        qq_uid = int(self.qq_open_id) if str(self.qq_open_id).isdigit() else self.qq_open_id
        data, resp = self._request_json_ok("/api/ucenter/users/login", {
            "uid": "-1",
            "token": "",
            "timestamp": str(int(time.time() * 1000)),
        }, {
            "authType": 9,
            "uid": qq_uid,
            "token": self.qq_open_token,
        })
        if data:
            self.qq_uid = str(data.get("id", self.qq_uid))
            self.qq_token = data.get("token", self.qq_token)
            user_info = data.get("userInfo", {})
            self.qq_auth_type = user_info.get("authType", 9) or 9
            self.qq_session_nickname = user_info.get("nickname", "") or ""
            self._save_credentials()
        return data, resp

    def get_audio_credentials(self, refresh=False):
        if self.uid != "-1" and self.token:
            return {"uid": self.uid, "token": self.token, "source": "main"}, None
        if not self.qq_open_id or not self.qq_open_token:
            return None, {"code": -1, "msg": "当前主账号没有可用凭证"}
        if refresh or self.qq_uid == "-1" or not self.qq_token:
            data, resp = self.login_qq_music()
            if not data or self.qq_uid == "-1" or not self.qq_token:
                return None, resp
        return {"uid": self.qq_uid, "token": self.qq_token, "source": "qq"}, None

    # ── 搜索 ────────────────────────────────────────────────────────

    def normalize_song(self, song):
        audios = song.get("audios", [])
        free_sign = song.get("freeSign") or song.get("fsig") or self.get_cached_free_sign(song.get("id"))
        return {
            "id": song.get("id"),
            "name": song.get("name", song.get("songName", "?")),
            "artist": song.get("artist", "?"),
            "artistId": song.get("artistId"),
            "artistPic": song.get("artistPic", ""),
            "artists": song.get("artists", []),
            "album": song.get("album", ""),
            "albumId": song.get("albumId"),
            "albumPic": song.get("albumPic", ""),
            "albumPic120": song.get("albumPic120", ""),
            "duration": song.get("duration", 0),
            "releaseDate": song.get("releaseDate", ""),
            "language": song.get("language", ""),
            "musicRid": song.get("musicRid", ""),
            "audios": audios,
            "freeSign": free_sign,
            "payInfo": song.get("payInfo", {}),
            "formats_str": ", ".join(f'{a.get("format", "?")}/{a.get("bitrate", "?")}k' for a in audios[:6]),
            "quality_choices": build_quality_choices(audios),
            "preferred_quality": resolve_quality_key(audios, self.get_local_config("quality", "6")),
        }

    def normalize_song_list(self, songs):
        return [self.normalize_song(song) for song in songs or []]

    def search(self, keyword, page=0, page_size=20):
        resp = self._request("/api/search/music/list", {
            "pn": str(page), "rn": str(page_size),
            "keyword": keyword, "correct": "1",
            "uid": self.uid, "token": self.token,
        })
        if resp.get("code") != 200:
            return []
        return self.normalize_song_list(resp.get("data", {}).get("resultList", []))

    def search_artists(self, keyword, page=0, page_size=20):
        data, _ = self._request_ok("/api/search/artist/list", {
            "pn": str(page), "rn": str(page_size),
            "keyword": keyword, "correct": "1",
            "uid": self.uid, "token": self.token,
        })
        if not data:
            return []
        return data.get("resultList", [])

    def search_playlists(self, keyword, page=0, page_size=20):
        data, _ = self._request_ok("/api/search/playlist/list", {
            "pn": str(page), "rn": str(page_size),
            "keyword": keyword, "correct": "1",
            "uid": self.uid, "token": self.token,
        })
        if not data:
            return []
        return data.get("resultList", [])

    def search_tips(self, keyword):
        data, _ = self._request_ok("/api/search/tip/v2/list", {
            "keyword": keyword,
            "uid": self.uid, "token": self.token,
        })
        if not data:
            return []
        return data.get("resultList", [])

    def hot_topics(self):
        data, _ = self._request_ok("/api/search/topic/word/list", {
            "uid": self.uid, "token": self.token,
        })
        if not data:
            return []
        return data.get("hotTopic", [])

    def get_artist(self, artist_id):
        data, _ = self._request_ok(f"/api/service/artist/{artist_id}", {
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_artist_music(self, artist_id, page=1, page_size=50):
        data, _ = self._request_ok(f"/api/service/artist/music/{artist_id}", {
            "artistId": str(artist_id), "pn": str(page), "rn": str(page_size),
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_artist_song_list(self, artist_id, page=1, page_size=50):
        data = self.get_artist_music(artist_id, page=page, page_size=page_size)
        return self.normalize_song_list(
            (data or {}).get("list") or (data or {}).get("musicList") or (data or {}).get("resultList") or []
        )

    def get_artist_albums(self, artist_id, page=1, page_size=50):
        data, _ = self._request_ok(f"/api/service/artist/album/{artist_id}", {
            "pn": str(page), "rn": str(page_size),
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_artist_album_items(self, artist_id, page=1, page_size=50):
        return (self.get_artist_albums(artist_id, page=page, page_size=page_size) or {}).get("resultList", [])

    def get_all_artist_album_items(self, artist_id, page_size=50):
        items = []
        page = 1
        total = 0
        while True:
            data = self.get_artist_albums(artist_id, page=page, page_size=page_size) or {}
            batch = data.get("resultList", [])
            if not batch:
                break
            items.extend(batch)
            total = data.get("total", total) or total
            if total and len(items) >= int(total):
                break
            if len(batch) < page_size:
                break
            page += 1
        return items

    def get_album(self, album_id):
        data, _ = self._request_ok(f"/api/service/album/{album_id}", {
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_album_music(self, album_id, page=1, page_size=50):
        data, _ = self._request_ok(f"/api/service/album/music/{album_id}", {
            "pn": str(page), "rn": str(page_size),
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_album_song_list(self, album_id, page=1, page_size=50):
        data = self.get_album_music(album_id, page=page, page_size=page_size)
        return self.normalize_song_list(
            (data or {}).get("list") or (data or {}).get("musicList") or (data or {}).get("resultList") or []
        )

    def get_all_album_song_list(self, album_id, page_size=100):
        songs = []
        page = 1
        total = 0
        while True:
            data = self.get_album_music(album_id, page=page, page_size=page_size) or {}
            batch = self.normalize_song_list(
                data.get("list") or data.get("musicList") or data.get("resultList") or []
            )
            if not batch:
                break
            songs.extend(batch)
            total = data.get("total", total) or total
            if total and len(songs) >= int(total):
                break
            if len(batch) < page_size:
                break
            page += 1
        return songs

    def get_playlist_info(self, playlist_id, source=5):
        data, _ = self._request_ok(f"/api/service/playlist/info/{playlist_id}", {
            "source": str(source),
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_playlist_music(self, playlist_id, page=1, page_size=100, source=5):
        data, _ = self._request_ok(f"/api/service/playlist/{playlist_id}/musicList", {
            "source": str(source), "pn": str(page), "rn": str(page_size),
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_playlist_song_list(self, playlist_id, page=1, page_size=100, source=5):
        data = self.get_playlist_music(playlist_id, page=page, page_size=page_size, source=source)
        return self.normalize_song_list((data or {}).get("list") or (data or {}).get("musicList") or [])

    def get_my_fond_playlist(self):
        data, _ = self._request_ok("/api/service/playlist/fond", {
            "userId": self.uid,
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_my_fond_songs(self, page=1, page_size=100):
        fond = self.get_my_fond_playlist()
        if not fond:
            return []
        return self.get_playlist_song_list(fond["id"], page=page, page_size=page_size, source=fond.get("sourceType", 5))

    def get_my_created_playlists(self):
        data, _ = self._request_ok("/api/service/playlist/userCreate", {
            "userId": self.uid,
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_my_created_playlist_items(self):
        return (self.get_my_created_playlists() or {}).get("playLists", [])

    def get_my_collected_playlists(self, page=1, page_size=50):
        data, _ = self._request_ok("/api/service/collect/4/list", {
            "userId": self.uid, "fromUid": self.uid,
            "pn": str(page), "rn": str(page_size),
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_my_collected_playlist_items(self, page=1, page_size=50):
        return (self.get_my_collected_playlists(page=page, page_size=page_size) or {}).get("playLists", [])

    def get_followed_artists(self, page=1, page_size=200):
        data, _ = self._request_ok("/api/service/collect/7/list", {
            "userId": self.uid, "fromUid": self.uid,
            "pn": str(page), "rn": str(page_size),
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_followed_artist_items(self, page=1, page_size=200):
        return (self.get_followed_artists(page=page, page_size=page_size) or {}).get("artistList", [])

    def get_user_profile(self, user_id, from_uid=None):
        if from_uid is None:
            from_uid = self.uid
        data, _ = self._request_ok(f"/api/ucenter/users/pub/{user_id}", {
            "fromUid": str(from_uid),
            "uid": self.uid,
            "token": self.token,
        })
        return data

    def get_recommendations(self, scroll_num=1, total_num=1, last_cold_start_time=None):
        if last_cold_start_time is None:
            last_cold_start_time = int(time.time() * 1000)
        data, resp = self._request_ok("/api/service/music/recommendList", {
            "fg": "1",
            "lock": "0",
            "lastColdStartTime": str(last_cold_start_time),
            "scrollNum": str(scroll_num),
            "resourceId": "0",
            "recoMode": "normal",
            "source": "0",
            "sourceId": "0",
            "totalNum": str(total_num),
            "uid": self.uid, "token": self.token,
            "timestamp": str(int(time.time() * 1000)),
        })
        return data, resp

    def get_recommendation_songs(self, scroll_num=1, total_num=20, last_cold_start_time=None):
        data, _ = self.get_recommendations(
            scroll_num=scroll_num,
            total_num=total_num,
            last_cold_start_time=last_cold_start_time,
        )
        return self.normalize_song_list((data or {}).get("musicList", []))

    def get_advert_config(self):
        data, _ = self._request_ok("/api/service/advert/config", {
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_home_index(self):
        data, _ = self._request_ok("/api/service/home/index", {
            "uid": self.uid,
            "token": self.token,
            "timestamp": str(int(time.time() * 1000)),
        })
        return data

    def get_home_module(self, module_id):
        data, _ = self._request_ok("/api/service/home/module", {
            "uid": self.uid,
            "token": self.token,
            "timestamp": str(int(time.time() * 1000)),
            "moduleId": str(module_id),
        })
        return data

    def get_bang_music(self, bang_id, page=1, page_size=50):
        data, _ = self._request_ok(f"/api/service/bang/{bang_id}/musics", {
            "pn": str(page),
            "rn": str(page_size),
            "uid": self.uid,
            "token": self.token,
        })
        return data

    def get_paid_card_info(self):
        data, _ = self._request_ok("/api/pay/ct/durationAndUrl", {
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_pay_products(self, ig=1):
        data, _ = self._request_ok("/api/pay/pc/products", {
            "uid": self.uid,
            "token": self.token,
            "timestamp": str(int(time.time() * 1000)),
            "ig": str(ig),
        })
        return data

    def get_purchased_albums(self, page=1, page_size=30):
        data, _ = self._request_ok("/api/ucenter/pay/album/purchasedList", {
            "rn": str(page_size),
            "pn": str(page),
            "uid": self.uid,
            "token": self.token,
        })
        return data

    def get_pc_config(self):
        data, _ = self._request_ok("/api/service/pc/conf/all", {
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_version_popup(self):
        data, _ = self._request_ok("/api/service/version/popup/pc", {
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_version_check(self):
        data, _ = self._request_ok("/api/service/version/check/pc", {
            "uid": self.uid, "token": self.token,
        })
        return data

    def get_history_db_snapshot(self, limit=20):
        if not os.path.exists(HISTORY_DB_FILE):
            return []
        conn = sqlite3.connect(HISTORY_DB_FILE)
        try:
            rows = conn.execute(
                "select id, json, time from hist_song order by ord desc limit ?",
                (limit,),
            ).fetchall()
            out = []
            for song_id, raw_json, when in rows:
                try:
                    obj = json.loads(raw_json)
                except Exception:
                    obj = {"raw": raw_json[:500]}
                out.append({"id": song_id, "time": when, "data": obj})
            return out
        finally:
            conn.close()

    def get_favorites_db_snapshot(self, limit=20):
        db_file = os.path.join(LOCAL_APPDATA_DIR, "database", f"{self.uid}_self_collect_songlist.db")
        if not os.path.exists(db_file):
            return {"song_list": [], "songs_data": []}
        conn = sqlite3.connect(db_file)
        try:
            song_lists = []
            songs = []
            for song_id, raw_json, when in conn.execute(
                "select id, json, time from song_list order by ord desc limit ?", (limit,)
            ).fetchall():
                try:
                    obj = json.loads(raw_json)
                except Exception:
                    obj = {"raw": raw_json[:500]}
                song_lists.append({"id": song_id, "time": when, "data": obj})
            for song_id, raw_json, when in conn.execute(
                "select id, json, time from songs_data order by ord desc limit ?", (limit,)
            ).fetchall():
                try:
                    obj = json.loads(raw_json)
                except Exception:
                    obj = {"raw": raw_json[:500]}
                songs.append({"id": song_id, "time": when, "data": obj})
            return {"song_list": song_lists, "songs_data": songs}
        finally:
            conn.close()

    def get_cached_free_sign(self, music_id):
        if not music_id:
            return ""
        if self._log_free_sign_cache is None:
            self._log_free_sign_cache = {}
            for path in _recent_log_files():
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        for line in reversed(f.readlines()):
                            match = re.search(r"musicId=(\d+).*?freeSign=([^&]*)", line)
                            if match:
                                log_music_id = int(match.group(1))
                                free_sign = urllib.parse.unquote(match.group(2).strip())
                                if free_sign and log_music_id not in self._log_free_sign_cache:
                                    self._log_free_sign_cache[log_music_id] = free_sign
                                continue
                            match = re.search(r"id:\s*(\d+),\s*fsig:\s*(.*)$", line)
                            if not match:
                                continue
                            log_music_id = int(match.group(1))
                            free_sign = match.group(2).strip()
                            if free_sign and log_music_id not in self._log_free_sign_cache:
                                self._log_free_sign_cache[log_music_id] = free_sign
                except Exception:
                    pass
        return self._log_free_sign_cache.get(int(music_id), "")

    def check_right_with_freesign(self, music_id, free_sign):
        free_sign = urllib.parse.unquote(free_sign or "")
        auth, auth_resp = self.get_audio_credentials()
        if not auth:
            return None, auth_resp or {"code": -1, "msg": "无法建立音频会话"}
        payload = json.dumps({
            "musicId": int(music_id),
            "freeSign": free_sign,
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        data, resp = self._request_ok("/api/play/music/v2/checkRight", {
            "uid": auth["uid"],
            "token": auth["token"],
            "timestamp": str(int(time.time() * 1000)),
            "musicId": str(music_id),
            "freeSign": free_sign,
        }, data=payload, method="GET", sign_body_text="")
        if resp.get("code") == 11027 and auth["source"] == "qq":
            auth, auth_resp = self.get_audio_credentials(refresh=True)
            if not auth:
                return None, auth_resp or resp
            data, resp = self._request_ok("/api/play/music/v2/checkRight", {
                "uid": auth["uid"],
                "token": auth["token"],
                "timestamp": str(int(time.time() * 1000)),
                "musicId": str(music_id),
                "freeSign": free_sign,
            }, data=payload, method="GET", sign_body_text="")
        return data, resp

    # ── 音频 URL ────────────────────────────────────────────────────

    def get_audio_url(self, music_id, fmt="flac", br="2000kflac", free_sign=""):
        free_sign = urllib.parse.unquote(free_sign or "")
        auth, auth_resp = self.get_audio_credentials()
        if not auth:
            return None, (auth_resp or {}).get("msg") or "无法建立音频会话"
        payload = json.dumps({
            "devId": self.dev_id,
            "musicId": int(music_id),
            "format": fmt,
            "br": br,
            "freeSign": free_sign,
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        resp = self._request("/api/play/music/v2/audioUrl", {
            "uid": auth["uid"], "token": auth["token"],
            "timestamp": str(int(time.time() * 1000)),
            "devId": self.dev_id,
            "musicId": str(music_id),
            "format": fmt, "br": br, "freeSign": free_sign,
        }, data=payload, method="GET", extra_headers={"uid": auth["uid"], "token": auth["token"]}, sign_body_text="")
        if resp.get("code") == 11027 and auth["source"] == "qq":
            auth, auth_resp = self.get_audio_credentials(refresh=True)
            if not auth:
                return None, (auth_resp or {}).get("msg") or "无法刷新音频会话"
            resp = self._request("/api/play/music/v2/audioUrl", {
                "uid": auth["uid"], "token": auth["token"],
                "timestamp": str(int(time.time() * 1000)),
                "devId": self.dev_id,
                "musicId": str(music_id),
                "format": fmt, "br": br, "freeSign": free_sign,
            }, data=payload, method="GET", extra_headers={"uid": auth["uid"], "token": auth["token"]}, sign_body_text="")
        if resp.get("code") == 200 and resp.get("data"):
            return resp["data"].get("audioHttpsUrl") or resp["data"].get("audioUrl"), None
        msg = resp.get("msg", "")
        code = resp.get("code", -1)
        return None, msg or f"请求失败: {code}"

    def get_audio_url_with_fallback(self, music_id, fmt, br, free_sign=""):
        """按指定音质取链，并保留服务端实际返回的格式"""
        resolved_free_sign = urllib.parse.unquote(free_sign or "") or self.get_cached_free_sign(music_id)
        right_data, right_resp = self.check_right_with_freesign(music_id, resolved_free_sign)
        if right_resp.get("code") != 200:
            return None, fmt, right_resp.get("msg") or "播放权限校验失败"

        right_status = (right_data or {}).get("status")
        if right_status == 3:
            audition = (right_data or {}).get("audition") or {}
            audition_url = (
                audition.get("https")
                or audition.get("car_url_https")
                or audition.get("url")
                or audition.get("car_url")
            )
            if audition_url:
                return audition_url, _detect_ext(audition.get("format", "mp3"), audition_url), None
            return None, fmt, "当前歌曲仅提供试听片段"

        if right_status not in (1, 4):
            return None, fmt, f"服务端返回未知播放权限状态: {right_status}"

        url, err = self.get_audio_url(music_id, fmt, br, free_sign=resolved_free_sign)
        if url:
            return url, _detect_ext(fmt, url), None

        if err and ("没有解锁付费歌曲" in err or "播放授权" in err):
            if fmt in ("flac", "mflac", "mgg", "zp") and self.user_is_vip and not self.pay_vip_type:
                if self.pay_expire_date:
                    pay_expire = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.pay_expire_date / 1000))
                    return None, fmt, f"当前主账号仍显示为 VIP，但付费 VIP 已于 {pay_expire} 到期，现仅剩活动 VIP；服务端不再放行当前请求的无损音质"
                return None, fmt, "当前主账号仍显示为 VIP，但已不是付费 VIP；服务端不再放行当前请求的无损音质"
            if right_status == 4 and not resolved_free_sign:
                return None, fmt, "当前歌曲返回 status=4，但原版客户端在部分场景可直接取链；当前实现尚未复现对应授权上下文"
            return None, fmt, err
        return None, fmt, err or "无法获取播放链接"

    def get_song_quality_choices(self, song):
        return build_quality_choices(song.get("audios", []))

    def resolve_song_quality(self, song, preferred_key=None):
        return resolve_quality_key(song.get("audios", []), preferred_key or self.get_local_config("quality", "6"))

    def resolve_song_playback_quality(self, song, preferred_key=None):
        return resolve_playback_quality_key(song.get("audios", []), preferred_key or self.get_local_config("quality", "6"))

    def can_local_playback_format(self, fmt):
        return can_local_playback_format(fmt)

    def get_lyric(self, song_id, song_name, artist):
        q = encode_lyric_query(song_id, song_name, artist)
        url = (
            "http://mlyric.kuwo.cn/mobi.s"
            f"?f=bodian&q={q}&uid={self.uid}&token={self.token}"
        )
        text = self._request_text(url)
        resp = json.loads(text)
        if resp.get("code") != 200:
            raise RuntimeError(resp.get("msg") or "获取歌词失败")
        raw_text = decode_lyric_content(resp.get("data", {}).get("content", ""))
        lines = parse_lrc_lines(raw_text)
        return {
            "raw": raw_text,
            "lines": lines,
            "lrcx": resp.get("lrcx"),
        }

    def save_lyric(self, song, filepath):
        lyric = self.get_lyric(song["id"], song["name"], song["artist"])
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(lyric["raw"])
        return lyric

    def collect_song(self, music_id):
        fond = self.get_my_fond_playlist()
        data, resp = self._request_json_ok("/api/service/playlist/music", {
            "uid": self.uid,
            "token": self.token,
            "timestamp": str(int(time.time() * 1000)),
        }, {
            "playListId": int(fond["id"]),
            "musicIdList": [int(music_id)],
        })
        return data, resp

    def uncollect_song(self, music_id):
        fond = self.get_my_fond_playlist()
        data, resp = self._request_json_ok("/api/service/playlist/music/delete", {
            "uid": self.uid,
            "token": self.token,
            "timestamp": str(int(time.time() * 1000)),
        }, {
            "playListId": int(fond["id"]),
            "musicIdList": [int(music_id)],
        })
        return data, resp

    # ── 下载 ─────────────────────────────────────────────────────────

    def _guess_cover_suffix(self, url):
        path = urllib.parse.urlparse(url or "").path.lower()
        for suffix in (".jpg", ".jpeg", ".png", ".webp"):
            if path.endswith(suffix):
                return suffix
        return ".img"

    def _write_audio_tags(self, audio_path, filepath, song, cover_path=None):
        if not self.ffmpeg:
            raise RuntimeError("未找到 ffmpeg，无法写入封面")
        artist_names = " / ".join(
            item.get("name", "").strip()
            for item in (song.get("artists") or [])
            if item.get("name", "").strip()
        ) or (song.get("artist") or "")
        album_artist = song.get("albumArtist") or artist_names
        track_value = ""
        if song.get("trackNumber"):
            track_value = str(song["trackNumber"])
            if song.get("trackTotal"):
                track_value = f"{track_value}/{song['trackTotal']}"
        disc_value = ""
        if song.get("discNumber"):
            disc_value = str(song["discNumber"])
            if song.get("discTotal"):
                disc_value = f"{disc_value}/{song['discTotal']}"
        command = [
            self.ffmpeg,
            "-y",
            "-i",
            audio_path,
        ]
        if cover_path:
            command.extend([
                "-i",
                cover_path,
                "-map",
                "0:a:0",
                "-map",
                "1:v:0",
                "-c:v",
                "mjpeg",
            ])
        else:
            command.extend([
                "-map",
                "0:a:0",
            ])
        command.extend([
            "-c:a",
            "copy",
        ])
        if filepath.lower().endswith(".mp3"):
            command.extend(["-id3v2_version", "3", "-write_id3v1", "1"])
        metadata_items = [
            ("title", song.get("name") or ""),
            ("artist", artist_names),
            ("album", song.get("album") or ""),
            ("album_artist", album_artist),
            ("date", song.get("releaseDate") or ""),
            ("track", track_value),
            ("disc", disc_value),
            ("genre", song.get("language") or ""),
            ("description", song.get("musicRid") or ""),
        ]
        for key, value in metadata_items:
            if value:
                command.extend(["-metadata", f"{key}={value}"])
        if cover_path:
            command.extend([
                "-disposition:v:0",
                "attached_pic",
                "-metadata:s:v:0",
                "title=Album cover",
                "-metadata:s:v:0",
                "comment=Cover (front)",
            ])
        command.extend(
            [
                filepath,
            ]
        )
        proc = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode("utf-8", errors="replace")[-400:])

    def download(self, url, filepath, song=None):
        temp_dir = tempfile.mkdtemp(prefix="bodian_", dir=os.path.dirname(os.path.abspath(filepath)))
        temp_audio = os.path.join(temp_dir, os.path.basename(filepath))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Dart/3.3 (dart:io)"})
            resp = self.opener.open(req, timeout=60)
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0

            with open(temp_audio, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded * 100 / total
                        filled = int(pct // 2.5)
                        bar = "#" * filled + "-" * (40 - filled)
                        print(f"\r  [{bar}] {pct:.1f}% ({downloaded//1024}/{total//1024}KB)", end="", flush=True)

            if song:
                cover_path = None
                if song.get("albumPic") or song.get("albumPic120"):
                    cover_url = song.get("albumPic") or song.get("albumPic120")
                    cover_path = os.path.join(temp_dir, f"cover{self._guess_cover_suffix(cover_url)}")
                    with open(cover_path, "wb") as f:
                        f.write(self._request_binary(cover_url))
                final_path = os.path.join(temp_dir, f"final{os.path.splitext(filepath)[1]}")
                self._write_audio_tags(temp_audio, final_path, song, cover_path=cover_path)
                temp_audio = final_path

            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
            os.replace(temp_audio, filepath)
            size_mb = downloaded / 1024 / 1024
            print(f"\n  OK: {filepath} ({size_mb:.1f}MB)")
            return True
        except Exception as e:
            print(f"\n  下载失败: {e}")
            return False
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


# ─── 工具函数 ────────────────────────────────────────────────────────

def _fmt_dur(sec):
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"

def _sanitize(name):
    return re.sub(r'[\\/:*?"<>|]', '_', name)

def _detect_ext(fmt, url):
    lower_url = (url or "").lower()
    if ".mflac" in lower_url:
        return "mflac"
    if ".mgg" in lower_url:
        return "mgg"
    if ".zp" in lower_url:
        return "zp"
    if ".flac" in lower_url:
        return "flac"
    if ".ogg" in lower_url:
        return "ogg"
    if ".aac" in lower_url or ".m4a" in lower_url:
        return "aac"
    if ".mp3" in lower_url:
        return "mp3"
    return fmt
