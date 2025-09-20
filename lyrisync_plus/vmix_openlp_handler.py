# vmix_openlp_handler.py

import asyncio
import json
import time
import threading
from typing import Callable, Optional, Tuple, Dict, Any, List

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError, InvalidURI

import xml.etree.ElementTree as ET


# ---------------------
# vMix Controller (async over HTTP)  -- from Version 2
# ---------------------
class VmixController:
    """
    Minimal async vMix controller using the HTTP API at http://host:8088/api
    Exposes:
      - send_title_text(input_name, field, text)
      - trigger_overlay(overlay_number, action)  # action in {"In","Out","On","Off"}
      - start_recording(), stop_recording()
      - get_status() -> dict
      - close()
    """

    def __init__(self, api_url: str = "http://localhost:8088/api", timeout_sec: float = 4.0):
        self.api_url = api_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def _get_xml(self) -> Optional[ET.Element]:
        try:
            session = await self._get_session()
            async with session.get(self.api_url) as res:
                if res.status != 200:
                    txt = await res.text()
                    print(f"[vMix] API error: HTTP {res.status} {txt[:200]}")
                    return None
                text = await res.text()
                try:
                    return ET.fromstring(text)
                except ET.ParseError as e:
                    print(f"[vMix] XML parse error: {e}")
                    return None
        except Exception as e:
            print("[vMix] API request failed:", e)
            return None

    async def send_title_text(self, input_name: str, field: str, text: str) -> None:
        try:
            session = await self._get_session()
            params = {
                "Function": "SetText",
                "Input": input_name,
                "SelectedName": field,
                "Value": text or "",
            }
            async with session.get(self.api_url, params=params) as res:
                if res.status == 200:
                    print(f"[vMix] Text → {input_name}.{field} := {text!r}")
                else:
                    print(f"[vMix] SetText failed: HTTP {res.status}")
        except Exception as e:
            print("[vMix] Failed to send title text:", e)

    async def trigger_overlay(self, overlay_number: int = 1, action: str = "In") -> None:
        try:
            n = max(1, min(4, int(overlay_number))))
            action = action if action in {"In", "Out", "On", "Off"} else "In"
            session = await self._get_session()
            params = {"Function": f"OverlayInput{n}{action}"}
            async with session.get(self.api_url, params=params) as res:
                if res.status == 200:
                    print(f"[vMix] Overlay {n} {action}")
                else:
                    print(f"[vMix] Overlay {n} {action} failed: HTTP {res.status}")
        except Exception as e:
            print("[vMix] Overlay trigger error:", e)

    async def start_recording(self) -> None:
        await self._simple_function("StartRecording")

    async def stop_recording(self) -> None:
        await self._simple_function("StopRecording")

    async def _simple_function(self, func_name: str) -> None:
        try:
            session = await self._get_session()
            async with session.get(self.api_url, params={"Function": func_name}) as res:
                if res.status == 200:
                    print(f"[vMix] {func_name} OK")
                else:
                    print(f"[vMix] {func_name} failed: HTTP {res.status}")
        except Exception as e:
            print(f"[vMix] {func_name} error:", e)

    async def get_status(self) -> Dict[str, Any]:
        root = await self._get_xml()
        if not root:
            return {}
        status = {
            "recording": (root.findtext("recording") or "").strip(),
            "overlay1": (root.findtext("overlay1") or "").strip(),
            "overlay2": (root.findtext("overlay2") or "").strip(),
            "overlay3": (root.findtext("overlay3") or "").strip(),
            "overlay4": (root.findtext("overlay4") or "").strip(),
        }
        return status

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None


# ---------------------
# OpenLP Controller (WebSocket with keepalive + backoff)  -- from Version 1
# ---------------------
class OpenLPController:
    """
    Connects to OpenLP WebSocket (default ws://host:4317) and emits:
      - on_connect()
      - on_disconnect()
      - on_new_lyrics((text, is_blank))
    Debounced connect/disconnect reporting; recv timeout keeps loop alive.
    """

    def __init__(self, ws_url: str = "ws://localhost:4317"):
        self.ws_url = ws_url
        self.running = False
        self.on_new_lyrics: Optional[Callable[[Tuple[str, bool]], None]] = None
        self.on_connect: Optional[Callable[[], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None

        self.last_slide: str = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

        # Status flags (debounce)
        self._connected_reported = False
        self._last_disconnect_time = 0.0

        # Fixed backoff schedule (seconds)
        self._backoff_steps: List[int] = [1, 2, 5, 10, 20]
        self._backoff_index: int = 0

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_async, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._loop:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass

    def _run_async(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._listen_forever())
        except Exception as e:
            print("[OpenLP] Async loop error:", e)
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _listen_forever(self):
        while self.running:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                    open_timeout=5,
                ) as ws:
                    print("[OpenLP] Connected to WebSocket")
                    self._report_connected()
                    self._backoff_index = 0  # reset backoff after good connect

                    # Recv loop with timeout to keep task alive even if no messages
                    while self.running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            continue  # periodic timeout; confirm connection via ping
                        await self._process_message(msg)

            except (ConnectionClosedOK, ConnectionClosedError) as e:
                print(f"[OpenLP] Connection closed: {getattr(e, 'code', '')} {getattr(e, 'reason', '')}")
            except InvalidURI:
                print("[OpenLP] Invalid WS URL:", self.ws_url)
            except Exception as e:
                print("[OpenLP] WebSocket error:", e)

            self._report_disconnected()

            if self.running:
                delay = self._backoff_steps[min(self._backoff_index, len(self._backoff_steps) - 1)]
                self._backoff_index = min(self._backoff_index + 1, len(self._backoff_steps) - 1)
                await asyncio.sleep(delay)

    async def _process_message(self, message: Any):
        text = ""
        is_blank = False
        try:
            # decode bytes if necessary
            if isinstance(message, (bytes, bytearray)):
                message = message.decode("utf-8", errors="ignore")
            data = json.loads(message) if isinstance(message, str) else {}
        except Exception:
            data = {}

        if isinstance(data, dict):
            text = str(data.get("text", "") or "")
            typ = str(data.get("type", "")).lower()
            act = str(data.get("action", "")).lower()
            if not text.strip():
                is_blank = True
            if typ in {"blank", "clear"} or act in {"blank", "clear"}:
                is_blank = True
        else:
            text = str(message or "")
            is_blank = (text.strip() == "")

        self.last_slide = text
        print(f"[OpenLP] Message → blank={is_blank} text={text!r}")

        if callable(self.on_new_lyrics):
            try:
                self.on_new_lyrics((text, is_blank))
            except Exception as e:
                print("[OpenLP] on_new_lyrics error:", e)

    # ---- Debounced status reporting ----
    def _report_connected(self):
        if not self._connected_reported:
            self._connected_reported = True
            try:
                if callable(self.on_connect):
                    self.on_connect()
            except Exception:
                pass

    def _report_disconnected(self):
        self._last_disconnect_time = time.time()
        if self._connected_reported:
            self._connected_reported = False
            try:
                if callable(self.on_disconnect):
                    self.on_disconnect()
            except Exception:
                pass
