# main.py
import time, threading
from flask import Flask, request, jsonify
from vmix_openlp_handler import VmixController, OpenLPController
from gui_manager import LyriSyncGUI, load_config, save_config
from splash_screen import show_splash
import ttkbootstrap as tb

app_state = {"lyrics": "", "overlay_on": False, "recording": False}
last_lyrics_ts = 0
gui_ref = None
settings = {}

def soft_wrap(text: str, max_chars: int = 48) -> str:
    """Soft-wrap text into up to 2 lines around max_chars per line."""
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

def ensure_always_on_overlay():
    try:
        if settings.get("overlay_always_on", False):
            ch = int(settings.get("overlay_channel", 1))
            vmix.trigger_overlay(ch, action="On")
    except Exception:
        pass

def handle_action(action):
    print(f"[ACTION] → {action}")
    if isinstance(action, tuple) and action[0] == "set_lyrics_text":
        app_state["lyrics"] = action[1]
        return

    if action == "show_lyrics":
        title_input = settings.get("vmix_title_input", "SongTitle")
        title_field = settings.get("vmix_title_field", "Message.Text")
        max_chars = int(settings.get("max_chars_per_line", 48))
        text_to_send = soft_wrap(app_state["lyrics"], max_chars=max_chars)
        vmix.send_title_text(title_input, title_field, text_to_send)
        ch = int(settings.get("overlay_channel", 1))
        if settings.get("overlay_always_on", False):
            try: vmix.trigger_overlay(ch, action="On")
            except Exception: pass
        elif settings.get("auto_overlay_on_send", True):
            try: vmix.trigger_overlay(ch, action="In")
            except Exception: pass

    elif action == "clear_lyrics":
        title_input = settings.get("vmix_title_input", "SongTitle")
        title_field = settings.get("vmix_title_field", "Message.Text")
        vmix.send_title_text(title_input, title_field, "")
        ch = int(settings.get("overlay_channel", 1))
        if settings.get("overlay_always_on", False):
            pass  # overlay stays on-air; just blank text
        elif settings.get("auto_overlay_out_on_clear", True):
            try: vmix.trigger_overlay(ch, action="Out")
            except Exception: pass

    elif action == "toggle_overlay":
        ch = int(settings.get("overlay_channel", 1))
        vmix.trigger_overlay(ch)  # default action="In" (toggle-ish)
    elif action == "start_recording":
        vmix.start_recording(); app_state["recording"] = True
        if gui_ref: 
            try: gui_ref.set_recording(True)
            except Exception: pass
    elif action == "stop_recording":
        vmix.stop_recording(); app_state["recording"] = False
        if gui_ref:
            try: gui_ref.set_recording(False)
            except Exception: pass

# --- Flask API ---
api = Flask(__name__)

@api.route("/api/show_lyrics", methods=["POST"])
def api_show():
    data = request.json or {}
    if "text" in data:
        app_state["lyrics"] = str(data.get("text") or "")
    handle_action("show_lyrics")
    return jsonify(success=True)

@api.route("/api/clear_lyrics", methods=["POST"])
def api_clear():
    handle_action("clear_lyrics"); return jsonify(success=True)

@api.route("/api/toggle_overlay", methods=["POST"])
def api_overlay():
    handle_action("toggle_overlay"); return jsonify(success=True)

@api.route("/api/start_recording", methods=["POST"])
def api_start():
    handle_action("start_recording"); return jsonify(success=True)

@api.route("/api/stop_recording", methods=["POST"])
def api_stop():
    handle_action("stop_recording"); return jsonify(success=True)

@api.route("/api/status")
def api_status():
    return jsonify(app_state)

def run_api():
    port = int(settings.get("api_port", 5000))
    print(f"[API] Running on http://localhost:{port}")
    api.run(port=port)

def update_lyrics(text):
    global last_lyrics_ts
    app_state["lyrics"] = text
    last_lyrics_ts = time.time()
    print(f"[OpenLP] Updated current lyrics:\n{text}")

def idle_watcher():
    while True:
        try:
            idle = int(settings.get("auto_clear_idle_sec", 0))
            if idle > 0 and last_lyrics_ts:
                if (time.time() - last_lyrics_ts) >= idle:
                    handle_action("clear_lyrics")
                    globals()["last_lyrics_ts"] = 0
        except Exception: pass
        time.sleep(1)

def health_watcher():
    while True:
        try:
            s = vmix.get_status() or {}
            vmix_ok = bool(s)
        except Exception:
            vmix_ok = False
        try:
            openlp_ok = bool(openlp and openlp.thread.is_alive())
        except Exception:
            openlp_ok = False
        try:
            if gui_ref: gui_ref.set_conn_status(vmix_ok=vmix_ok, openlp_ok=openlp_ok)
        except Exception: pass
        time.sleep(max(1, int(settings.get("poll_interval_sec", 2))))

def poll_status():
    while True:
        try:
            status = vmix.get_status() or {}
            rec = str(status.get("recording", "")).lower() == "true"
            # overlay_on is just overlay1 flag for indicator purposes
            ov_on = str(status.get("overlay1", "")).lower() == "true"
            app_state["recording"] = rec
            app_state["overlay_on"] = ov_on
            if gui_ref:
                gui_ref.set_recording(rec)
                gui_ref.set_overlay(ov_on)
        except Exception: pass
        time.sleep(int(settings.get("poll_interval_sec", 2)))

if __name__ == "__main__":
    config = load_config()
    settings = config.get("settings", {})
    vmix = VmixController(api_url=settings.get("vmix_api_url", "http://localhost:8088/api"))
    openlp = OpenLPController(ws_url=settings.get("openlp_ws_url", "ws://localhost:4317"))

    # Wire OpenLP → immediate clear on blanks; otherwise show lyrics
    openlp.on_new_lyrics = lambda payload: (
        update_lyrics(payload[0] if isinstance(payload, tuple) else str(payload)),
        handle_action("clear_lyrics")
        if (isinstance(payload, tuple) and payload[1] and settings.get("clear_on_blank", True))
        else handle_action("show_lyrics")
    )
    openlp.on_connect = lambda: (gui_ref and gui_ref.set_conn_status(openlp_ok=True))
    openlp.on_disconnect = lambda: (gui_ref and gui_ref.set_conn_status(openlp_ok=False))
    openlp.start()

    # GUI
    chosen_theme = (config.get("ui", {}) or {}).get("theme", "darkly")
    root = tb.Window(themename=chosen_theme)
    gui_ref = LyriSyncGUI(root, config, save_config, action_callback=handle_action)

    ensure_always_on_overlay()

    if bool(settings.get("splash_enabled", True)):
        try: show_splash("splash.png", duration_ms=1600)
        except Exception: pass

    threading.Thread(target=run_api, daemon=True).start()
    threading.Thread(target=poll_status, daemon=True).start()
    threading.Thread(target=idle_watcher, daemon=True).start()
    threading.Thread(target=health_watcher, daemon=True).start()

    root.mainloop()
