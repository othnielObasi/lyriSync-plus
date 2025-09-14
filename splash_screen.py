# splash_screen.py
import tkinter as tk
from PIL import Image, ImageTk

def show_splash(image_path="splash.png", duration_ms=1800):
    try:
        root = tk.Toplevel()
    except tk.TclError:
        return  # no display
    root.overrideredirect(True)
    try:
        img = Image.open(image_path)
        photo = ImageTk.PhotoImage(img)
        w, h = img.size
        lbl = tk.Label(root, image=photo, borderwidth=0, highlightthickness=0)
        lbl.image = photo
    except Exception:
        w, h = 600, 300
        lbl = tk.Label(root, text="LyriSync+", fg="white", bg="#0e1117", font=("Segoe UI", 28, "bold"))
    sw = root.winfo_screenwidth(); sh = root.winfo_screenheight()
    x = (sw - w) // 2; y = (sh - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")
    lbl.pack(fill="both", expand=True)
    root.after(duration_ms, root.destroy)
    root.mainloop()
