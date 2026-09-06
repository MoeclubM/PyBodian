import sys
import time
import threading

sys.path.insert(0, r"C:\Files\项目\PyBodian")
from bodian_lyric_overlay import _TkLyricOverlay
from PIL import Image, ImageDraw

SETTINGS = {
    "lyric_overlay_topmost": True, "lyric_overlay_locked": False,
    "lyric_overlay_theme": 0, "lyric_overlay_opacity": 1.0,
    "lyric_overlay_geometry": "", "lyric_overlay_font_scale": 1.0,
    "lyric_overlay_primary_color": "", "lyric_overlay_line_gap": 0,
}

overlay = _TkLyricOverlay(settings=SETTINGS)
overlay.start()
time.sleep(2)

overlay.update(song_title="t", artist="t", text="测试", lines=[], active_index=-1,
               position_ms=0, duration_ms=0, playback_state="stopped")
time.sleep(1)

# 直接手动调用一次 ULW，检查返回值
def probe():
    root = overlay._root
    img = Image.new("RGBA", (root.winfo_width(), root.winfo_height()), (99, 240, 163, 220))
    d = ImageDraw.Draw(img)
    d.text((30, 30), "MANUAL ULW TEST", fill=(255, 255, 255, 255))
    ok = overlay._layered.update(overlay._hwnd, img, 1.0)
    print("MANUAL ULW:", ok)

probe()
time.sleep(3)
overlay.close()
print("PROBE DONE")
