import ctypes
import sys
import time

sys.path.insert(0, r"C:\Files\项目\PyBodian")
from bodian_lyric_overlay import _TkLyricOverlay, _WinLayered
from PIL import Image

SETTINGS = {
    "lyric_overlay_topmost": True, "lyric_overlay_locked": False,
    "lyric_overlay_theme": 0, "lyric_overlay_opacity": 1.0,
    "lyric_overlay_geometry": "", "lyric_overlay_font_scale": 1.0,
    "lyric_overlay_primary_color": "", "lyric_overlay_line_gap": 0,
}

LINES = [{"time_ms": 0, "text": "月光落在左手边 你的侧脸", "translation": False}]

overlay = _TkLyricOverlay(settings=SETTINGS)
overlay.start()
time.sleep(1.5)
overlay.update(song_title="t", artist="t", text="测试", lines=LINES, active_index=0,
               position_ms=0, duration_ms=60000, playback_state="playing")
time.sleep(1.5)

L = overlay._layered
hwnd = overlay._hwnd

rect = L._RECT()
L.user32.GetWindowRect(hwnd, ctypes.byref(rect))
w, h = rect.right - rect.left, rect.bottom - rect.top
print("rect:", w, h)

PW_RENDERFULLCONTENT = 0x00000002
gdi32 = ctypes.windll.gdi32
user32 = L.user32
hdc_window = user32.GetWindowDC(hwnd)
hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
info = ctypes.WINFUNCTYPE(ctypes.c_int)
hbmp = gdi32.CreateCompatibleBitmap(hdc_window, w, h)
gdi32.SelectObject(hdc_mem, hbmp)
ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
print("PrintWindow:", ok)

class BMPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]

class BMPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BMPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 1)]

bmi = BMPINFO()
bmi.bmiHeader.biSize = ctypes.sizeof(BMPINFOHEADER)
bmi.bmiHeader.biWidth = w
bmi.bmiHeader.biHeight = -h
bmi.bmiHeader.biPlanes = 1
bmi.bmiHeader.biBitCount = 32
bmi.bmiHeader.biCompression = 0
bits = ctypes.c_void_p()
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
gdi32.GetDIBits.restype = ctypes.c_int
buf = ctypes.create_string_buffer(w * h * 4)
got = gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
print("GetDIBits:", got)

image = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
image.save("_printwin_debug.png")
alpha = image.getchannel("A")
print("printwin alpha extrema:", alpha.getextrema())

gdi32.DeleteObject(hbmp)
gdi32.DeleteDC(hdc_mem)
user32.ReleaseDC(hwnd, hdc_window)

overlay.close()
print("DONE")
