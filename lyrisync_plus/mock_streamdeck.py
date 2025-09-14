# mock_streamdeck.py
import threading
import sys
from typing import Callable, Dict, Optional

class MockStreamDeck:
    """
    Simple console-based Stream Deck simulator.

    - Non-blocking: run start() in a thread (or call start(block=True)).
    - on_button(action_or_tuple) is called for valid button presses.
    - Optional 'button_map' translates keys (e.g. "0") to actions (e.g. "show_lyrics").
    """

    def __init__(
        self,
        on_button: Callable[[object], None],
        button_map: Optional[Dict[str, object]] = None,
        max_key: int = 15,
    ):
        """
        on_button: callback receiving either a string action (e.g., "show_lyrics")
                   or a tuple action like ("set_lyrics_text", "Your text")
        button_map: dict mapping str key -> action object
        max_key: highest numeric key accepted (0..max_key)
        """
        self.on_button = on_button
        self.button_map = button_map or {}
        self.max_key = max(0, int(max_key))
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._running.clear()

    def start(self, block: bool = False):
        """Start the input loop. If block=False, runs in a background thread."""
        if self._running.is_set():
            return
        self._running.set()
        if block:
            self._loop()
        else:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self):
        """Stop the loop and join the thread."""
        self._running.clear()
        if self._thread and self._thread.is_alive():
            try:
                self._thread.join(timeout=1.0)
            except Exception:
                pass

    def _loop(self):
        print("[MockDeck] Stream Deck simulator running. Type a key and press Enter.")
        print(f"[MockDeck] Valid: 0-{self.max_key}, custom keys from map, or 'quit'. Ctrl+C to exit.")
        try:
            while self._running.is_set():
                try:
                    line = input("Press key (0..N) or 'quit': ").strip()
                except EOFError:
                    # stdin closed (e.g., service or CI) → stop
                    print("\n[MockDeck] stdin closed. Stopping.")
                    break
                except KeyboardInterrupt:
                    print("\n[MockDeck] Interrupted. Stopping.")
                    break

                if not self._running.is_set():
                    break

                if line.lower() == "quit":
                    break

                # mapped keys first
                if line in self.button_map:
                    action = self.button_map[line]
                    self._dispatch(action)
                    continue

                # numeric keys
                if line.isdigit():
                    n = int(line)
                    if 0 <= n <= self.max_key:
                        # If no map, pass the raw key string back
                        self._dispatch(line)
                        continue

                print(f"[MockDeck] Invalid key '{line}'. Try 0..{self.max_key}, mapped key, or 'quit'.")

        finally:
            self._running.clear()
            print("[MockDeck] Stopped.")

    def _dispatch(self, action):
        try:
            self.on_button(action)
        except Exception as e:
            print(f"[MockDeck] on_button error: {e}", file=sys.stderr)
