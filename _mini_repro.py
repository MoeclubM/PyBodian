import threading, time

def worker():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    root.after(300, root.destroy)
    root.mainloop()

t = threading.Thread(target=worker, daemon=True)
t.start()
t.join()
time.sleep(0.5)
print("MINI DONE")
