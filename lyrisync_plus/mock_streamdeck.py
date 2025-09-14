# mock_streamdeck.py
class MockStreamDeck:
    def __init__(self, on_button_press):
        self.on_button_press = on_button_press
        self.running = True

    def start(self):
        print("[MockDeck] Starting simulation. Press Ctrl+C to exit.")
        try:
            while self.running:
                inp = input("Press button (e.g., '0', '1', '2'): ").strip()
                if inp.isdigit():
                    self.on_button_press(inp)
        except KeyboardInterrupt:
            print("\n[MockDeck] Stopped.")
