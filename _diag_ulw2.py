import ctypes
import sys
import threading
import time
import tkinter

sys.path.insert(0, r"C:\Files\项目\PyBodian")
from bodian_lyric_overlay import _enable_dpi_awareness, _WinLayered
from PIL import Image, ImageDraw

_enable_dpi_awareness()
result = {}

def worker():
    root = tkinter.Tk()
    root.geometry("400x120+100+100")
    root.overrideredirect(True)
    root.update_idletasks()
    L = _WinLayered()
    hwnd = L.get_toplevel_hwnd(root)
    L.enable(hwnd)
    img = Image.new("RGBA", (400, 120), (99, 240, 163, 255))
    d = ImageDraw.Draw(img)
    d.text((20, 40), "DIAGNOSTIC", fill=(255, 255, 255, 255))
    root.deiconify()
    root.update()
    time.sleep(0.2)

    bgra = L._premultiply_bgra(img)
    result["bgra_len"] = len(bgra)

    rect = L._RECT()
    ok_rect = L.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    result["rect"] = (ok_rect, rect.left, rect.top, rect.right, rect.bottom)
    err1 = ctypes.GetLastError()

    screen_dc = L.user32.GetDC(None)
    result["screen_dc"] = bool(screen_dc)
    mem_dc = L.gdi32.CreateCompatibleDC(screen_dc)
    result["mem_dc"] = bool(mem_dc)

    bits = ctypes.c_void_p()
    bmi = L._BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(L._BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = 400
    bmi.bmiHeader.biHeight = -120
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    dib = L.gdi32.CreateDIBSection(screen_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    result["dib"] = bool(dib)
    result["bits"] = bool(bits)
    err2 = ctypes.GetLastError()

    if dib and bits:
        old = L.gdi32.SelectObject(mem_dc, dib)
        result["select"] = bool(old)
        ctypes.memmove(bits, bgra, len(bgra))
        pt_dst = L._POINT(rect.left, rect.top)
        size = L._SIZE(400, 120)
        pt_src = L._POINT(0, 0)
        blend = L._BLENDFUNCTION(0, 0, 255, 1)
        result["blend_size"] = ctypes.sizeof(L._BLENDFUNCTION)
        ok = L.user32.UpdateLayeredWindow(hwnd, screen_dc, ctypes.byref(pt_dst), ctypes.byref(size),
                                          mem_dc, ctypes.byref(pt_src), 0, ctypes.byref(blend), 2)
        result["ulw"] = ok
        result["ulw_err"] = ctypes.GetLastError() if not ok else 0
        L.gdi32.SelectObject(mem_dc, old)
    L.gdi32.DeleteObject(dib)
    L.gdi32.DeleteDC(mem_dc)
    L.user32.ReleaseDC(None, screen_dc)

    root.after(1000, root.destroy)
    root.mainloop()
    import gc
    gc.collect()

t = threading.Thread(target=worker, daemon=True)
t.start()
t.join()
for k, v in result.items():
    print(k, "=", v)
