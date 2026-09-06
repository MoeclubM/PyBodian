#!/usr/bin/env python3
"""临时测试：验证字号/行距/颜色可调。先默认样式 6 秒，再切大字号+行距+自定义色 8 秒，退出。"""

import sys
import time

sys.path.insert(0, r"C:\Files\项目\PyBodian")

from bodian_lyric_overlay import _TkLyricOverlay

SETTINGS = {
    "lyric_overlay_topmost": True,
    "lyric_overlay_locked": False,
    "lyric_overlay_theme": 0,
    "lyric_overlay_opacity": 1.0,
    "lyric_overlay_geometry": "",
    "lyric_overlay_font_scale": 1.0,
    "lyric_overlay_primary_color": "",
    "lyric_overlay_line_gap": 0,
}

LINES = [
    {"time_ms": 0, "text": "月光落在左手边 你的侧脸", "translation": False},
    {"time_ms": 0, "text": "Moonlight on your face", "translation": True},
    {"time_ms": 60000, "text": "时间像安静的河流过窗前", "translation": False},
    {"time_ms": 120000, "text": "我们聊到深夜也没有倦意", "translation": False},
]

overlay = _TkLyricOverlay(settings=SETTINGS)
overlay.start()
time.sleep(1.2)


def push():
    overlay.update(
        song_title="测试歌曲", artist="测试歌手", text="测试",
        lines=LINES, active_index=0, position_ms=0,
        duration_ms=240000, playback_state="playing",
    )


push()
time.sleep(5)

# 阶段2：大字号 + 大行距 + 玫红主题 + 自定义颜色
overlay.set_font_scale(1.45)
overlay.set_line_gap(22)
overlay.set_primary_color("#ff9ad5")
time.sleep(8)

print("OVERLAY SETTINGS TEST DONE")
