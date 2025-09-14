# splash_screen.py
import logging
import tkinter as tk
from typing import Optional

try:
    from PIL import Image, ImageTk  # Pillow
    _PIL_OK = True
except Exception:
    _PIL_OK = False

logger = logging.getLogger("SplashScreen")

def show_splash(image_path: str = "splash.png", duration_ms: int = 1800) -> None:
    """
    Show a centered splash for duration_ms.
    - Creates a temporary hidden root if none exists.
    - Falls back to text banner if image not available.
    - Skips gracefully in headless environments (no DISPLAY).
    - If a Tk root exists but mainloop isn't running yet, runs a tiny local loop
      so the splash actually appears and auto-closes on time.
    """
    import os, sys, time

    # Headless guard (Linux/Unix)
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        logger.warning("No DISPLAY available; skipping splash.")
        return

    # Do we already have a Tk root?
    root_owner = False
    root: Optional[tk.Tk] = None
    try:
        root = tk._default_root  # type: ignore[attr-defined]
    except Exception:
        root = None

    try:
        if root is None:
            # Create our own hidden root
            root_owner = True
            root = tk.Tk()
            root.withdraw()

        # Create top-level splash
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.configure(bg="#0e1117")
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass

        # Content widget
        w = h = None
        label = None
        if _PIL_OK:
            try:
                img = Image.open(image_path)
                photo = ImageTk.PhotoImage(img)
                w, h = img.size
                label = tk.Label(win, image=photo, borderwidth=0, highlightthickness=0, bg="#0e1117")
                label.image = photo  # keep ref
            except Exception as e:
                logger.warning(f"Splash image not available: {e}")

        if label is None:
            # Fallback text
            w, h = (600, 300)
            label = tk.Label(
                win,
                text="LyriSync+",
                fg="white",
                bg="#0e1117",
                font=("Segoe UI", 28, "bold"),
                padx=40,
                pady=40,
            )

        # Center the splash
        win.update_idletasks()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        w = w or win.winfo_reqwidth()
        h = h or win.winfo_reqheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        win.geometry(f"{w}x{h}+{x}+{y}")

        label.pack(fill="both", expand=True)

        # Close after duration
        duration_ms = max(300, int(duration_ms))
        win.after(duration_ms, win.destroy)

        # Ensure it paints at least once right now
        try:
            win.update()
        except tk.TclError:
            # If update fails (e.g., immediate teardown), just bail
            return

        if root_owner:
            # We own the root: block locally until splash closes
            try:
                win.wait_window()
            finally:
                try:
                    root.destroy()
                except Exception:
                    pass
        else:
            # A root exists but mainloop may not be running yet.
            # Run a tiny local loop so the splash actually shows and auto-closes.
            end = time.monotonic() + (duration_ms / 1000.0) + 0.05
            while time.monotonic() < end:
                if not win.winfo_exists():
                    break
                try:
                    root.update()
                except tk.TclError:
                    break
                time.sleep(0.01)

    except tk.TclError:
        logger.warning("Tk not available (likely headless); skipping splash.")
    except Exception as e:
        logger.error(f"Splash screen error: {e}")
