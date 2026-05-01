#!/usr/bin/env python3

import base64
import re
import urllib.parse


QUALITY_OPTIONS = {
    "1": ("mp3", "128kmp3", "MP3 128kbps"),
    "2": ("mp3", "320kmp3", "MP3 320kbps"),
    "3": ("ogg", "100kogg", "OGG 100kbps"),
    "4": ("ogg", "192kogg", "OGG 192kbps"),
    "5": ("ogg", "300kogg", "OGG 300kbps"),
    "6": ("flac", "2000kflac", "FLAC 无损"),
    "7": ("mflac", "20201kmflac", "MFLAC 20201kbps (ZPGA201)"),
    "8": ("aac", "48kaac", "AAC 48kbps"),
    "9": ("mflac", "20501kmflac", "MFLAC 20501kbps (ZPGA501)"),
    "10": ("mflac", "20900kmflac", "MFLAC 20900kbps (ZPLY)"),
    "11": ("mgg", "22000kmgg", "MGG 22000kbps (BCMS)"),
    "12": ("zp", "20000kzp", "ZP 20000kbps"),
}

LOCAL_PLAYBACK_UNSUPPORTED_FORMATS = {"mflac", "mgg", "zp"}
LOCAL_PLAYBACK_PREFERRED_KEYS = ("6", "5", "2", "4", "1", "3", "8")

_AUDIO_TO_QUALITY = {
    ("mp3", "128"): "1",
    ("mp3", "320"): "2",
    ("ogg", "100"): "3",
    ("ogg", "192"): "4",
    ("ogg", "300"): "5",
    ("flac", "2000"): "6",
    ("mflac", "20201"): "7",
    ("aac", "48"): "8",
    ("mflac", "20501"): "9",
    ("mflac", "20900"): "10",
    ("mgg", "22000"): "11",
    ("zp", "20000"): "12",
}


def build_quality_choices(audios):
    choices = []
    found = set()
    for audio in audios or []:
        fmt = str(audio.get("format", "")).lower()
        bitrate = str(audio.get("bitrate", ""))
        key = _AUDIO_TO_QUALITY.get((fmt, bitrate))
        if not key or key in found:
            continue
        found.add(key)
        choices.append({
            "key": key,
            "format": QUALITY_OPTIONS[key][0],
            "bitrate": QUALITY_OPTIONS[key][1],
            "label": QUALITY_OPTIONS[key][2],
        })
    return choices


def resolve_quality_key(audios, preferred_key):
    choices = build_quality_choices(audios)
    for item in choices:
        if item["key"] == preferred_key:
            return preferred_key
    return choices[0]["key"] if choices else None


def can_local_playback_format(fmt):
    return str(fmt or "").lower() not in LOCAL_PLAYBACK_UNSUPPORTED_FORMATS


def resolve_playback_quality_key(audios, preferred_key):
    choices = build_quality_choices(audios)
    available_keys = {item["key"] for item in choices}
    if preferred_key in available_keys and can_local_playback_format(QUALITY_OPTIONS[preferred_key][0]):
        return preferred_key
    for key in LOCAL_PLAYBACK_PREFERRED_KEYS:
        if key in available_keys:
            return key
    return None


def build_lyric_query(song_id, song_name, artist):
    return (
        f"type=lyric&req=2&lrcx=1&rid={song_id}"
        f"&songname={song_name}&artist={artist}&corp=kuwo&fromchannel=bodian"
    )


def encode_lyric_query(song_id, song_name, artist):
    query = build_lyric_query(song_id, song_name, artist)
    return base64.b64encode(urllib.parse.quote(query, safe="=&").encode("utf-8")).decode("ascii")


def decode_lyric_content(content):
    if not content:
        return ""
    return base64.b64decode(content).decode("utf-8", errors="replace")


def parse_lrc_lines(text):
    results = []
    for raw_line in (text or "").splitlines():
        match = re.match(r"^\[(\d{2}):(\d{2})\.(\d{3})\](.*)$", raw_line)
        if not match:
            continue
        minute, second, millis, content = match.groups()
        clean = re.sub(r"<[^>]+>", "", content).strip()
        if not clean:
            continue
        total_ms = int(minute) * 60000 + int(second) * 1000 + int(millis)
        results.append({
            "time_ms": total_ms,
            "timestamp": f"{minute}:{second}.{millis}",
            "text": clean,
        })
    return results
