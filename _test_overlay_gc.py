import gc
import os
import sys
import time
import tkinter

sys.path.insert(0, r"C:\Files\项目\PyBodian")
import bodian_lyric_overlay as lo

MODE = sys.argv[1] if len(sys.argv) > 1 else "fallback"
if MODE == "fallback":
    lo.IS_WIN32 = False  # 强制走画布回退路径

SETTINGS = {"lyric_overlay_topmost": True, "lyric_overlay_locked": False,
            "lyric_overlay_theme": 0, "lyric_overlay_opacity": 1.0, "lyric_overlay_geometry": ""}

overlay = lo._TkLyricOverlay(settings=SETTINGS)
overlay.start()
time.sleep(1.5)
overlay.update(song_title="测试歌曲", artist="测试歌手", text="测试", lines=[],
               active_index=-1, position_ms=0, duration_ms=0, playback_state="stopped")
time.sleep(1.0)
overlay.close()
overlay._thread.join(timeout=3)
time.sleep(0.5)
gc.collect()
roots = [o for o in gc.get_objects() if isinstance(o, tkinter.Tk)]
print("mode:", MODE, "remaining Tk roots:", len(roots))
print("GC CHECK DONE")
