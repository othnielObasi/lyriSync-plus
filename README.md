# LyriSync+

Sync OpenLP lyrics to vMix titles with Stream Deck/Companion support and a modern operator GUI.

## Features
- vMix: SetText, Overlay In/Out/On/Off (1–4), Start/Stop Recording, status poll
- OpenLP: WebSocket listener with auto-reconnect; **instant clear on blank slide** (toggle)
- GUI: Theme switcher, status indicators, Live Status test controls
- **Settings dialog**: URLs/ports, overlay channel & modes, idle auto-clear, soft-wrap width, **Clear on Blank Slide**
- vMix **Input & Field discovery** + quick **Test Send / Clear / Overlay In/Out**
- HTTP API for Companion: `/api/show_lyrics`, `/api/clear_lyrics`, `/api/toggle_overlay`, `/api/start_recording`, `/api/stop_recording`, `/api/status`
- Packaging: PyInstaller batch, installer scripts (Inno/NSIS), splash & icon

## Quick Start
```bash
pip install -r requirements.txt
python main.py
```

## Settings
See `lyrisync_config.yaml` → `settings`.

- `vmix_api_url`, `openlp_ws_url`, `api_port`
- `vmix_title_input`, `vmix_title_field`
- `overlay_channel` (1–4)
- `auto_overlay_on_send`, `overlay_always_on`, `auto_overlay_out_on_clear`
- `auto_clear_idle_sec` (0=off)
- `max_chars_per_line`
- `clear_on_blank` (instant clear on OpenLP blank)

## Build EXE
```
pyinstaller --onefile --windowed --icon=iconLyriSync.ico main.py
```
