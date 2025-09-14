# vmix_openlp_handler.py
import requests
import xml.etree.ElementTree as ET
import websocket
import threading
import json
import time

class VmixController:
    def __init__(self, api_url="http://localhost:8088/api"):
        self.api_url = api_url

    def _get_xml(self):
        try:
            res = requests.get(self.api_url, timeout=3)
            if res.status_code == 200:
                return ET.fromstring(res.text)
        except Exception as e:
            print("[vMix] API error:", e)
        return None

    def send_title_text(self, input_name, field, text):
        try:
            url = f"{self.api_url}?Function=SetText&Input={input_name}&SelectedName={field}&Value={text}"
            requests.get(url, timeout=3)
            print(f"[vMix] Sent text to {input_name}.{field}: {text!r}")
        except Exception as e:
            print("[vMix] Failed to send title text:", e)

    def trigger_overlay(self, overlay_number=1, action="In"):
        try:
            overlay_number = max(1, min(4, int(overlay_number)))
            action = action if action in ("In", "Out", "On", "Off") else "In"
            url = f"{self.api_url}?Function=OverlayInput{overlay_number}{action}"
            requests.get(url, timeout=3)
        except Exception as e:
            print("[vMix] Overlay trigger error:", e)

    def start_recording(self):
        try: requests.get(f"{self.api_url}?Function=StartRecording", timeout=3)
        except Exception as e: print("[vMix] Start recording failed:", e)

    def stop_recording(self):
        try: requests.get(f"{self.api_url}?Function=StopRecording", timeout=3)
        except Exception as e: print("[vMix] Stop recording failed:", e)

    def get_status(self):
        root = self._get_xml()
        if not root:
            return {}
        status = {
            "recording": root.findtext("recording"),
            "overlay1": root.findtext("overlay1") or "",
            "overlay2": root.findtext("overlay2") or "",
            "overlay3": root.findtext("overlay3") or "",
            "overlay4": root.findtext("overlay4") or "",
        }
        return status

class OpenLPController:
    """OpenLP WebSocket listener. Emits (text, is_blank) to on_new_lyrics callback."""
    def __init__(self, ws_url="ws://localhost:4317"):
        self.ws_url = ws_url
        self.last_slide = ""
        self.running = True
        self.thread = threading.Thread(target=self._listen_ws, daemon=True)
        self.on_new_lyrics = None
        self.on_connect = None
        self.on_disconnect = None

    def start(self):
        print("[OpenLP] Connecting to WebSocket...")
        self.thread.start()

    def _listen_ws(self):
        def on_message(ws, message):
            text = ""
            is_blank = False
            try:
                data = json.loads(message) if isinstance(message, str) else {}
            except Exception:
                data = {}
            if isinstance(data, dict):
                text = str(data.get("text", "") or "")
                if not text.strip():
                    is_blank = True
                typ = str(data.get("type", "")).lower()
                act = str(data.get("action", "")).lower()
                if typ in {"blank", "clear"} or act in {"blank", "clear"}:
                    is_blank = True
            self.last_slide = text
            print(f"[OpenLP] Message → blank={is_blank} text={text!r}")
            cb = self.on_new_lyrics
            if callable(cb):
                try: cb((text, is_blank))
                except Exception as e: print("[OpenLP] on_new_lyrics error:", e)

        def on_error(ws, error):
            print("[OpenLP] WebSocket error:", error)

        def on_close(ws, close_status_code, close_msg):
            print("[OpenLP] Connection closed.")
            try:
                if self.on_disconnect: self.on_disconnect()
            except Exception: pass

        def on_open(ws):
            print("[OpenLP] Connected.")
            try:
                if self.on_connect: self.on_connect()
            except Exception: pass

        ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )
        while self.running:
            ws.run_forever()
            time.sleep(3)

    def stop(self):
        self.running = False
