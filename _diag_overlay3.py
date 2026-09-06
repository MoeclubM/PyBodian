import sys
import time
import threading

sys.path.insert(0, r"C:\Files\项目\PyBodian")
from bodian_lyric_overlay import _TkLyricOverlay

result = {}

SETTINGS = {
    "lyric_overlay_topmost": True, "lyric_overlay_locked": False,
    "lyric_overlay_theme": 0, "lyric_overlay_opacity": 1.0,
    "lyric_overlay_geometry": "", "lyric_overlay_font_scale": 1.0,
    "lyric_overlay_primary_color": "", "lyric_overlay_line_gap": 0,
}

overlay = _TkLyricOverlay(settings=SETTINGS)
overlay.start()
time.sleep(2)

def probe():
    root = overlay._root
    result["thread_alive"] = overlay._thread.is_alive()
    result["root"] = bool(root)
    if root:
        result["layered_ok"] = overlay._layered_ok
        result["geometry"] = root.geometry()
        result["viewable"] = root.winfo_viewable()
        result["screen"] = (root.winfo_screenwidth(), root.winfo_screenheight())
    root.after(200, root.destroy) if False else None

# 在主线程直接读取（跨线程读取 winfo 有风险但通常可行）
probe()
print("PROBE:", result)
overlay.close()
