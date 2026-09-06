import ctypes
import sys
import time

sys.path.insert(0, r"C:\Files\项目\PyBodian")
from bodian_lyric_overlay import _TkLyricOverlay, _WinLayered

SETTINGS = {
    "lyric_overlay_topmost": True, "lyric_overlay_locked": False,
    "lyric_overlay_theme": 0, "lyric_overlay_opacity": 1.0,
    "lyric_overlay_geometry": "", "lyric_overlay_font_scale": 1.0,
    "lyric_overlay_primary_color": "", "lyric_overlay_line_gap": 0,
}

overlay = _TkLyricOverlay(settings=SETTINGS)
overlay.start()
time.sleep(2)

L = overlay._layered
hwnd = overlay._hwnd
WS_EX_TOPMOST = 0x8
GWL_EXSTYLE = -20

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

L.user32.WindowFromPoint.argtypes = [POINT]
L.user32.WindowFromPoint.restype = ctypes.c_void_p
L.user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]

def who_on_top(tag):
    buf = ctypes.create_unicode_buffer(64)
    pt = POINT(1280, 1450)
    top_win = L.user32.WindowFromPoint(pt)
    L.user32.GetClassNameW(top_win, buf, 64)
    ex = L.user32.GetWindowLongW(top_win, GWL_EXSTYLE) if top_win else 0
    print(f"{tag}: hwnd={hex(top_win or 0)} class={buf.value} topmost={bool(ex & WS_EX_TOPMOST)} is_overlay_child={L.user32.GetParent(top_win) == hwnd if top_win else '?'}")

who_on_top("t+2s")
time.sleep(1.5)
who_on_top("t+3.5s")
time.sleep(2)
who_on_top("t+5.5s")

overlay.close()
print("DONE")
