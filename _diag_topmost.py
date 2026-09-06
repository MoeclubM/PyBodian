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
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x8

def report(tag):
    ex = L.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    rect = L._RECT()
    L.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    print(f"{tag}: TOPMOST={'ON' if ex & WS_EX_TOPMOST else 'OFF'} exstyle=0x{ex & 0xFFFFFFFF:08x} rect=({rect.left},{rect.top},{rect.right},{rect.bottom})")

report("t+2s")

# 等待 topmost_loop 运行几轮
time.sleep(2.5)
report("t+4.5s")

# 前台窗口是谁？
fg = L.user32.GetForegroundWindow()
buf = ctypes.create_unicode_buffer(64)
L.user32.GetClassNameW(fg, buf, 64)
print("foreground class:", buf.value)

time.sleep(3)
overlay.close()
print("DONE")
