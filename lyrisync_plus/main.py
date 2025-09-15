# main.py
import os
import sys
import time
import argparse
import threading
import asyncio
from typing import Optional, Awaitable

from flask import Flask, request, jsonify
import ttkbootstrap as tb

from vmix_openlp_handler import VmixController, OpenLPController
from gui_manager import LyriSyncGUI, load_config, save_config
try:
    from splash_screen import show_splash
except Exception:
    show_splash = None  # optional


# -----------------------------
# Global state
# -----------------------------
app_state = {"lyrics": "", "overlay_on": False, "recording": False}
last_lyrics_ts: float = 0.0
state_lock = threading.Lock()

settings = {}
gui_ref: Optional[LyriSyncGUI] = None
vmix: Optional[VmixController] = None
openlp: Optional[OpenLPController] = None

shutdown_event = threading.Event()

# Async loop in background thread
loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
_loop_thread = None

# -----------------------------
# Helpers
# -----------------------------
def soft_wrap(text: str, max_chars: int = 48) -> str:
    """Wrap into at most 2 lines around max_chars per line (soft wrap by words)."""
    if not text or max_chars <= 0:
        return text or ""
    words = text.strip().split()
    if not words:
        return ""
    line1, line2 = "", ""
    for w in words:
        cand = (line1 + " " + w).strip() if line1 else w
        if len(cand) <= max_chars or not line1:
            line1 = cand
        else:
            cand2 = (line2 + " " + w).strip() if line2 else w
            line2 = cand2
    return line1 if not line2 else (line1 + "\n" + line2)


def _submit(coro: Awaitable):
    """Submit a coroutine to the background loop."""
    return asyncio.run_coroutine_threadsafe(coro, loop)


async def ensure_always_on_overlay():
    try:
        if settings.get("overlay_always_on", False):
            ch = int(settings.get("overlay_channel", 1))
            await vmix.trigger_overlay(ch, action="On")
    except Exception:
        pass


async def handle_action_async(action):
    """Core async action handler (vMix operations)."""
    print(f"[ACTION] → {action}")

    if isinstance(action, tuple) and action[0] == "set_lyrics_text":
        with state_lock:
            app_state["lyrics"] = action[1]
        return

    if action == "show_lyrics":
        title_input = settings.get("vmix_title_input", "SongTitle")
        title_field = settings.get("vmix_title_field", "Message.Text")
        max_chars = int(settings.get("max_chars_per_line", 48))
        with state_lock:
            text_to_send = soft_wrap(app_state["lyrics"], max_chars=max_chars)

        await vmix.send_title_text(title_input, title_field, text_to_send)

        ch = int(settings.get("overlay_channel", 1))
        if settings.get("overlay_always_on", False):
            try:
                await vmix.trigger_overlay(ch, action="On")
            except Exception:
                pass
        elif settings.get("auto_overlay_on_send", True):
            try:
                await vmix.trigger_overlay(ch, action="In")
            except Exception:
                pass
        return

    if action == "clear_lyrics":
        title_input = settings.get("vmix_title_input", "SongTitle")
        title_field = settings.get("vmix_title_field", "Message.Text")
        await vmix.send_title_text(title_input, title_field, "")
        ch = int(settings.get("overlay_channel", 1))
        if settings.get("overlay_always_on", False):
            pass
        elif settings.get("auto_overlay_out_on_clear", True):
            try:
                await vmix.trigger_overlay(ch, action="Out")
            except Exception:
                pass
        return

    if action == "toggle_overlay":
        ch = int(settings.get("overlay_channel", 1))
        await vmix.trigger_overlay(ch)
        return

    if action == "start_recording":
        await vmix.start_recording()
        with state_lock:
            app_state["recording"] = True
        if gui_ref:
            gui_ref.thread_safe_update(gui_ref.set_recording, True)
        return

    if action == "stop_recording":
        await vmix.stop_recording()
        with state_lock:
            app_state["recording"] = False
        if gui_ref:
            gui_ref.thread_safe_update(gui_ref.set_recording, False)
        return


def handle_action(action):
    """Sync wrapper (GUI buttons / Flask handlers call this)."""
    _submit(handle_action_async(action))


def update_lyrics(text: str):
    """Called from OpenLP callback to update current lyrics and idle timer."""
    global last_lyrics_ts
    with state_lock:
        app_state["lyrics"] = text
        last_lyrics_ts = time.time()
    print(f"[OpenLP] Updated current lyrics:\n{text}")


# -----------------------------
# Background tasks (async)
# -----------------------------
async def idle_watcher():
    """Auto-clear if no new lyrics after N seconds."""
    global last_lyrics_ts
    while not shutdown_event.is_set():
        try:
            idle = int(settings.get("auto_clear_idle_sec", 0))
            if idle > 0:
                with state_lock:
                    current_ts = last_lyrics_ts
                if current_ts and (time.time() - current_ts) >= idle:
                    await handle_action_async("clear_lyrics")
                    with state_lock:
                        last_lyrics_ts = 0
        except Exception as e:
            print(f"[IdleWatcher] Error: {e}")
        await asyncio.sleep(1)


async def health_watcher():
    """Ping vMix/OpenLP and update GUI traffic lights."""
    while not shutdown_event.is_set():
        try:
            s = await vmix.get_status()
            vmix_ok = bool(s)
        except Exception:
            vmix_ok = False

        try:
            openlp_ok = bool(openlp and openlp.running and openlp._thread and openlp._thread.is_alive())
        except Exception:
            openlp_ok = False

        if gui_ref:
            gui_ref.thread_safe_update(gui_ref.set_conn_status, vmix_ok, openlp_ok)

        await asyncio.sleep(max(1, int(settings.get("poll_interval_sec", 2))))


async def poll_status():
    """Mirror vMix recording/overlay flags into app_state + GUI."""
    while not shutdown_event.is_set():
        try:
            status = await vmix.get_status() or {}
            rec = str(status.get("recording", "")).lower() == "true"
            ov_on = str(status.get("overlay1", "")).lower() == "true"  # using overlay1 as indicator

            with state_lock:
                app_state["recording"] = rec
                app_state["overlay_on"] = ov_on

            if gui_ref:
                gui_ref.thread_safe_update(gui_ref.set_recording, rec)
                gui_ref.thread_safe_update(gui_ref.set_overlay, ov_on)

        except Exception as e:
            print(f"[PollStatus] Error: {e}")

        await asyncio.sleep(int(settings.get("poll_interval_sec", 2)))


# -----------------------------
# Flask API (runs in its own thread)
# -----------------------------
api = Flask(__name__)

@api.route("/api/show_lyrics", methods=["POST"])
def api_show():
    data = request.json or {}
    if "text" in data:
        with state_lock:
            app_state["lyrics"] = str(data.get("text") or "")
    handle_action("show_lyrics")
    return jsonify(success=True)

@api.route("/api/clear_lyrics", methods=["POST"])
def api_clear():
    handle_action("clear_lyrics")
    return jsonify(success=True)

@api.route("/api/toggle_overlay", methods=["POST"])
def api_overlay():
    handle_action("toggle_overlay")
    return jsonify(success=True)

@api.route("/api/start_recording", methods=["POST"])
def api_start():
    handle_action("start_recording")
    return jsonify(success=True)

@api.route("/api/stop_recording", methods=["POST"])
def api_stop():
    handle_action("stop_recording")
    return jsonify(success=True)

@api.route("/api/status")
def api_status():
    with state_lock:
        return jsonify(app_state)

def run_api(bind_host: str):
    port = int(settings.get("api_port", 5000))
    print(f"[API] Running on http://{bind_host}:{port}")
    api.run(host=bind_host, port=port, threaded=True)


# -----------------------------
# Entry
# -----------------------------
def main():
    global settings, vmix, openlp, gui_ref, _loop_thread

    # Argparse — tolerate unknown args (e.g., Jupyter's -f)
    parser = argparse.ArgumentParser(description="LyriSync+ controller", add_help=True)
    parser.add_argument("--nogui", action="store_true", help="Run without GUI")
    parser.add_argument("--no-openlp", action="store_true", help="Do not connect to OpenLP WS")
    parser.add_argument("--bind", type=str, default="0.0.0.0", help="Flask bind host (default 0.0.0.0)")
    args, _unknown = parser.parse_known_args()

    # Load config
    config = load_config()
    settings = config.get("settings", {}) or {}

    # Controllers
    vmix = VmixController(api_url=settings.get("vmix_api_url", "http://localhost:8088/api"))
    openlp = OpenLPController(ws_url=settings.get("openlp_ws_url", "ws://localhost:4317"))

    # Start asyncio loop in background thread
    def _loop_runner():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    _loop_thread = threading.Thread(target=_loop_runner, daemon=True)
    _loop_thread.start()

    # Wire OpenLP callbacks
    if not args.no_openlp:
        openlp.on_new_lyrics = lambda payload: (
            update_lyrics(payload[0] if isinstance(payload, tuple) else str(payload)),
            _submit(
                handle_action_async("clear_lyrics")
                if (isinstance(payload, tuple) and payload[1] and settings.get("clear_on_blank", True))
                else handle_action_async("show_lyrics")
            )
        )
        openlp.on_connect = lambda: (gui_ref and gui_ref.thread_safe_update(gui_ref.set_conn_status, openlp_ok=True))
        openlp.on_disconnect = lambda: (gui_ref and gui_ref.thread_safe_update(gui_ref.set_conn_status, openlp_ok=False))
        openlp.start()
    else:
        print("[OpenLP] Skipped ( --no-openlp )")

    # Headless detection for Linux (no DISPLAY)
    headless_env = sys.platform != "win32" and not os.environ.get("DISPLAY")
    if args.nogui or headless_env:
        if headless_env and not args.nogui:
            print("[GUI] Headless environment detected (no display). Running without GUI.")
        # Start Flask API thread
        api_thread = threading.Thread(target=run_api, args=(args.bind,), daemon=True)
        api_thread.start()

        _submit(ensure_always_on_overlay())
        _submit(idle_watcher())
        _submit(health_watcher())
        _submit(poll_status())

        try:
            while api_thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    else:
        # GUI mode
        chosen_theme = (config.get("ui", {}) or {}).get("theme", "darkly")
        root = tb.Window(themename=chosen_theme)
        gui_ref = LyriSyncGUI(
            root,
            config,
            save_config,
            action_callback=lambda action: handle_action(action),
        )

        if bool(settings.get("splash_enabled", True)) and show_splash:
            try:
                show_splash("splash.png", duration_ms=1600)
            except Exception:
                pass

        threading.Thread(target=run_api, args=(args.bind,), daemon=True).start()

        _submit(ensure_always_on_overlay())
        _submit(idle_watcher())
        _submit(health_watcher())
        _submit(poll_status())

        def _on_close():
            shutdown_event.set()
            try:
                openlp.stop()
            except Exception:
                pass
            try:
                _submit(vmix.close())
            except Exception:
                pass
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", _on_close)
        try:
            root.mainloop()
        finally:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass

    shutdown_event.set()
    try:
        _submit(vmix.close())
    except Exception:
        pass
    try:
        openlp.stop()
    except Exception:
        pass

    if _loop_thread and _loop_thread.is_alive():
        _loop_thread.join(timeout=2)


if __name__ == "__main__":
    main()
