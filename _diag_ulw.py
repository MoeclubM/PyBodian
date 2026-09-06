import sys
import threading
import time
import tkinter

sys.path.insert(0, r"C:\Files\项目\PyBodian")
from bodian_lyric_overlay import _WinLayered, _dpi_scale, _enable_dpi_awareness
from PIL import Image, ImageDraw

_enable_dpi_awareness()
result = {}

def worker():
    root = tkinter.Tk()
    root.geometry("400x120+100+100")
    root.overrideredirect(True)
    root.update_idletasks()
    layered = _WinLayered()
    hwnd = layered.get_toplevel_hwnd(root)
    result["hwnd"] = bool(hwnd)
    result["enable"] = layered.enable(hwnd)
    img = Image.new("RGBA", (400, 120), (99, 240, 163, 255))
    d = ImageDraw.Draw(img)
    d.text((20, 40), "DIAGNOSTIC LYRIC", fill=(255, 255, 255, 255))
    root.deiconify()
    root.update()
    time.sleep(0.3)
    result["update1"] = layered.update(hwnd, img, 1.0)
    time.sleep(0.5)
    result["update2"] = layered.update(hwnd, img, 1.0)
    result["visible_x"] = root.winfo_x()
    result["visible_y"] = root.winfo_y()
    result["w"] = root.winfo_width()
    result["h"] = root.winfo_height()
    root.after(1200, root.destroy)
    root.mainloop()
    # 清理
    import gc
    gc.collect()

t = threading.Thread(target=worker, daemon=True)
t.start()
t.join()
print("DIAG RESULT:", result)
