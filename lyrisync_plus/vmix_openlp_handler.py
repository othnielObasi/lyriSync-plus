## vmix_openlp_handler.py
import asyncio
import json
import time
import threading
from typing import Callable, Optional, Tuple, Dict, Any, List

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed, InvalidURI

import xml.etree.ElementTree as ET


# vMix HTTP API (async)
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
                # vMix always returns 200 with XML when OK
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
            n = max(1, min(4, int(overlay_number)))
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
        # Pull common flags
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


# OpenLP WebSocket listener (async thread)
class OpenLPController:
    """
    Lightweight OpenLP WebSocket client.
    - Connects to ws://host:4317
    - Emits callbacks on connect/disconnect/new_lyrics
    - Reconnects with incremental backoff
    """

    def __init__(self, ws_url: str = "ws://localhost:4317"):
        self.ws_url = ws_url
        self.last_slide: str = ""
        self.running: bool = False

        # Callbacks
        self.on_new_lyrics: Optional[Callable[[Tuple[str, bool]], None]] = None
        self.on_connect: Optional[Callable[[], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None

        # Internals
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

    # Public control
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_async, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    # Internal loop
    def _run_async(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._listen_ws())
        except Exception as e:
            print("[OpenLP] Async loop error:", e)
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _listen_ws(self) -> None:
        backoff = 2  # seconds, grows up to max_backoff
        max_backoff = 15
        while self.running:
            try:
                async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
                    print("[OpenLP] Connected to WebSocket")
                    if callable(self.on_connect):
                        try:
                            self.on_connect()
                        except Exception:
                            pass

                    backoff = 2  # reset after successful connect

                    while self.running:
                        try:
                            msg = await ws.recv()
                        except ConnectionClosed:
                            print("[OpenLP] Connection closed")
                            break
                        except Exception as e:
                            print("[OpenLP] Receive error:", e)
                            break
                        await self._process_message(msg)

            except InvalidURI:
                print("[OpenLP] Invalid WS URL:", self.ws_url)
                await asyncio.sleep(5)
            except Exception as e:
                print("[OpenLP] WebSocket error:", e)

            # Notify disconnect
            if callable(self.on_disconnect):
                try:
                    self.on_disconnect()
                except Exception:
                    pass

            if self.running:
                await asyncio.sleep(backoff)
                backoff = min(max_backoff, backoff * 2)

    async def _process_message(self, message: Any) -> None:
        """
        Expect messages like:
          {"text": "...", "type": "...", "action": "..."}
        Blank detection:
          - empty/whitespace text
          - type/action in {"blank","clear"}
        """
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
            try:
                cb((text, is_blank))
            except Exception as e:
                print("[OpenLP] on_new_lyrics error:", e)
