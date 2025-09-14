# gui_manager.py
import logging
import asyncio
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from typing import Dict, List, Optional, Callable, Tuple
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import ttkbootstrap as tb
import yaml
import aiohttp


# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("LyriSyncGUI")

CONFIG_FILE = "lyrisync_config.yaml"


# Helper: deep-merge dicts
def _deep_merge(a: dict, b: dict) -> dict:
    """Recursively merge dict b into dict a and return a new dict."""
    out = dict(a)
    for k, v in (b or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# Config I/O
def load_config() -> Dict:
    """Load configuration from YAML with defaults and deep merge."""
    default_config = {
        "roles": [],
        "ui": {"theme": "darkly"},
        "settings": {
            "vmix_api_url": "http://localhost:8088/api",
            "openlp_ws_url": "ws://localhost:4317",
            "api_port": 5000,
            "vmix_title_input": "SongTitle",
            "vmix_title_field": "Message.Text",
            "splash_enabled": True,
            "poll_interval_sec": 2,
            "overlay_channel": 1,
            "auto_overlay_on_send": True,
            "auto_overlay_out_on_clear": True,
            "overlay_always_on": False,
            "auto_clear_idle_sec": 0,
            "max_chars_per_line": 48,
            "clear_on_blank": True,
        },
    }

    path = Path(CONFIG_FILE)
    if not path.exists():
        return default_config

    try:
        with open(path, "r", encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
            merged = _deep_merge(default_config, user_cfg)
            return merged
    except Exception as e:
        logger.error("Failed to load config: %s", e)
        # If we have a GUI context, messagebox will show; else it’s harmless.
        try:
            messagebox.showerror(
                "Config Error",
                f"Failed to load configuration: {e}\nUsing default settings.",
            )
        except Exception:
            pass
        return default_config


def save_config(config: Dict) -> bool:
    """Save configuration to YAML with error handling."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, default_flow_style=False, allow_unicode=True)
        logger.info("Configuration saved to %s", CONFIG_FILE)
        return True
    except Exception as e:
        logger.error("Failed to save config: %s", e)
        try:
            messagebox.showerror("Config Error", f"Failed to save configuration: {e}")
        except Exception:
            pass
        return False


# Async vMix Discovery
class AsyncVmixDiscoverer:
    """Async vMix input and field discovery using aiohttp."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = threading.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        with self._lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
            return self._session

    async def discover_vmix_inputs(
        self, api_url: str
    ) -> Tuple[List[str], Dict[str, List[str]]]:
        """
        Query vMix /api and return (input_names, fields_by_input).
        Raises exceptions with descriptive messages for UI.
        """
        try:
            session = await self._get_session()
            async with session.get(api_url, timeout=5) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise Exception(f"HTTP {resp.status}: {body[:200]}")

                content = await resp.text()
                try:
                    root = ET.fromstring(content)
                except ET.ParseError as e:
                    raise Exception(f"Failed to parse vMix XML response: {e}")

                input_names: List[str] = []
                fields_by_input: Dict[str, List[str]] = {}
                seen_inputs = set()

                for inp in root.findall(".//inputs/input"):
                    name = (
                        inp.get("title")
                        or inp.get("shortTitle")
                        or inp.get("number")
                        or "Unknown"
                    )

                    if name not in seen_inputs:
                        seen_inputs.add(name)
                        input_names.append(name)

                        # Collect title field names
                        fields: List[str] = []
                        data_node = inp.find("data")
                        if data_node is not None:
                            for t in data_node.findall("text"):
                                field_name = t.get("name")
                                if field_name and field_name not in fields:
                                    fields.append(field_name)

                        fields_by_input[name] = fields

                return input_names, fields_by_input

        except asyncio.TimeoutError:
            raise Exception("vMix discovery timed out after 5 seconds")
        except Exception as e:
            raise Exception(f"vMix discovery failed: {e}")

    async def close(self):
        with self._lock:
            if self._session and not self._session.closed:
                await self._session.close()
            self._session = None


# Main GUI
class LyriSyncGUI:
    def __init__(
        self,
        master,
        config: Dict,
        on_config_save: Callable[[Dict], bool],
        action_callback: Optional[Callable] = None,
    ):
        self.master = master
        self.config = config
        self.on_config_save = on_config_save
        self.action_callback = action_callback

        self.discoverer = AsyncVmixDiscoverer()
        self._vmix_inputs: List[str] = []
        self._fields_by_input: Dict[str, List[str]] = {}

        # Window
        self.master.title("LyriSync+")
        self.master.geometry("960x600")
        self.master.minsize(800, 500)

        # Theme (safe)
        initial_theme = (self.config.get("ui") or {}).get("theme", "darkly")
        try:
            self.style = tb.Style(initial_theme)
        except Exception:
            self.style = tb.Style("darkly")
            logger.warning("Failed to load theme '%s', falling back to 'darkly'", initial_theme)
        self.theme_var = tk.StringVar(value=initial_theme)

        # Build UI
        self._build_ui()
        self.refresh_roles_list()

        # Async event loop for background aio tasks
        self.loop = asyncio.new_event_loop()
        self.async_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self.async_thread.start()

        # Close handling
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)

    # event loop 
    def _run_async_loop(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            try:
                self.loop.close()
            except Exception:
                pass

    def on_close(self):
        """Gracefully stop async tasks and close window."""
        async def _close_async():
            await self.discoverer.close()

        try:
            fut = asyncio.run_coroutine_threadsafe(_close_async(), self.loop)
            # Wait briefly to cleanly close aiohttp
            fut.result(timeout=2)
        except Exception:
            pass

        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
            if getattr(self, "async_thread", None):
                self.async_thread.join(timeout=2)
        except Exception:
            pass

        self.master.destroy()

    # helpers 
    def thread_safe_update(self, method: Callable, *args, **kwargs):
        self.master.after(0, lambda: method(*args, **kwargs))

    def update_status(self, message: str):
        self.thread_safe_update(self.status_label.configure, text=message)

    # UI
    def _build_ui(self):
        header = ttk.Frame(self.master)
        header.pack(fill="x", padx=10, pady=(8, 0))

        ttk.Label(header, text="LyriSync+", font=("Segoe UI", 14, "bold")).pack(side="left")

        # Status indicators
        status_frame = ttk.Frame(header)
        status_frame.pack(side="right", padx=5)

        status_labels = [
            ("OpenLP", "openlp_lbl"),
            ("vMix", "vmix_lbl"),
            ("Overlay", "ovr_lbl"),
            ("Recording", "rec_lbl"),
        ]
        for text, attr in status_labels:
            f = ttk.Frame(status_frame)
            f.pack(side="left", padx=2)
            ttk.Label(f, text=text, font=("Segoe UI", 9)).pack()
            lbl = ttk.Label(f, text="●", font=("Segoe UI", 12), foreground="#c43c3c")
            lbl.pack()
            setattr(self, f"_{attr}", lbl)

        # Theme selector
        theme_frame = ttk.Frame(header)
        theme_frame.pack(side="right", padx=10)
        ttk.Label(theme_frame, text="Theme:").pack(side="left")
        themes = [
            "darkly",
            "flatly",
            "cosmo",
            "pulse",
            "cyborg",
            "morph",
            "sandstone",
            "superhero",
        ]
        theme_dd = ttk.Combobox(
            theme_frame, values=themes, textvariable=self.theme_var, state="readonly", width=12
        )
        theme_dd.pack(side="left", padx=5)
        theme_dd.bind("<<ComboboxSelected>>", self._apply_theme)

        ttk.Button(header, text="Settings", command=self.open_settings_dialog).pack(
            side="right", padx=5
        )

        # Notebook
        notebook = ttk.Notebook(self.master)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.roles_frame = ttk.Frame(notebook)
        notebook.add(self.roles_frame, text="🎭 Roles & Decks")

        self.status_frame = ttk.Frame(notebook)
        notebook.add(self.status_frame, text="📡 Live Status")

        self._build_roles_tab()
        self._build_status_tab()

    def _apply_theme(self, event=None):
        chosen = self.theme_var.get()
        try:
            self.style.theme_use(chosen)
            self.config.setdefault("ui", {})["theme"] = chosen
            ok = self.on_config_save(self.config)
            if ok:
                logger.info("Theme changed to %s", chosen)
        except Exception as e:
            logger.error("Failed to apply theme %s: %s", chosen, e)
            messagebox.showerror("Theme Error", f"Failed to apply theme: {e}")

    # Roles tab
    def _build_roles_tab(self):
        tree_frame = ttk.Frame(self.roles_frame)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=10)

        sb_y = ttk.Scrollbar(tree_frame)
        sb_y.pack(side="right", fill="y")
        sb_x = ttk.Scrollbar(tree_frame, orient="horizontal")
        sb_x.pack(side="bottom", fill="x")

        self.roles_tree = ttk.Treeview(
            tree_frame,
            columns=("Name", "Decks", "Buttons"),
            show="headings",
            height=15,
            yscrollcommand=sb_y.set,
            xscrollcommand=sb_x.set,
        )
        sb_y.config(command=self.roles_tree.yview)
        sb_x.config(command=self.roles_tree.xview)

        self.roles_tree.heading("Name", text="Role Name")
        self.roles_tree.heading("Decks", text="Deck IDs")
        self.roles_tree.heading("Buttons", text="Button Mappings")

        self.roles_tree.column("Name", width=150, minwidth=100)
        self.roles_tree.column("Decks", width=100, minwidth=80)
        self.roles_tree.column("Buttons", width=320, minwidth=200)

        self.roles_tree.pack(fill="both", expand=True)

        btn_frame = ttk.Frame(self.roles_frame)
        btn_frame.pack(pady=5)

        for text, cmd in [
            ("➕ Add Role", self.add_role),
            ("✏️ Edit Role", self.edit_role),
            ("❌ Delete Role", self.delete_role),
            ("🔄 Refresh", self.refresh_roles_list),
        ]:
            ttk.Button(btn_frame, text=text, command=cmd).pack(side="left", padx=5)

    # Status tab
    def _build_status_tab(self):
        self.status_label = ttk.Label(
            self.status_frame, text="Ready", font=("Segoe UI", 11), wraplength=800
        )
        self.status_label.pack(pady=10, padx=10, fill="x")

        test_frame = ttk.LabelFrame(self.status_frame, text="Test Controls", padding=10)
        test_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(test_frame, text="Test Lyrics:").grid(row=0, column=0, sticky="w", pady=5)
        self._lyrics_var = tk.StringVar(value="Sample lyrics for testing")
        lyrics_entry = ttk.Entry(test_frame, textvariable=self._lyrics_var, width=50)
        lyrics_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")

        ttk.Button(test_frame, text="Send to vMix", command=self._send_test_lyrics).grid(
            row=0, column=2, padx=5, pady=5
        )
        ttk.Button(test_frame, text="Clear", command=self._clear_lyrics).grid(
            row=0, column=3, padx=5, pady=5
        )

        action_frame = ttk.Frame(test_frame)
        action_frame.grid(row=1, column=0, columnspan=4, pady=10, sticky="w")

        for text, action in [
            ("Toggle Overlay", "toggle_overlay"),
            ("Start Recording", "start_recording"),
            ("Stop Recording", "stop_recording"),
            ("Show Lyrics", "show_lyrics"),
        ]:
            ttk.Button(
                action_frame, text=text, command=lambda a=action: self._trigger_action(a)
            ).pack(side="left", padx=5)

        conn_frame = ttk.LabelFrame(self.status_frame, text="Connection Status", padding=10)
        conn_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(conn_frame, text="vMix:").grid(row=0, column=0, sticky="w", padx=5)
        self.vmix_status_var = tk.StringVar(value="Disconnected")
        ttk.Label(conn_frame, textvariable=self.vmix_status_var, foreground="red").grid(
            row=0, column=1, sticky="w", padx=5
        )

        ttk.Label(conn_frame, text="OpenLP:").grid(row=0, column=2, sticky="w", padx=5)
        self.openlp_status_var = tk.StringVar(value="Disconnected")
        ttk.Label(conn_frame, textvariable=self.openlp_status_var, foreground="red").grid(
            row=0, column=3, sticky="w", padx=5
        )

        test_frame.columnconfigure(1, weight=1)
        conn_frame.columnconfigure(1, weight=1)
        conn_frame.columnconfigure(3, weight=1)

    # Actions
    def _trigger_action(self, action: str):
        if callable(self.action_callback):
            try:
                self.action_callback(action)
                self.update_status(f"Action triggered: {action}")
            except Exception as e:
                logger.error("Failed to trigger action %s: %s", action, e)
                messagebox.showerror("Action Error", f"Failed to trigger {action}: {e}")
        else:
            self.update_status("No action callback configured")

    def _send_test_lyrics(self):
        lyrics = self._lyrics_var.get().strip()
        if lyrics and callable(self.action_callback):
            try:
                self.action_callback(("set_lyrics_text", lyrics))
                self.action_callback("show_lyrics")
                self.update_status(f"Sent test lyrics: {lyrics}")
            except Exception as e:
                logger.error("Failed to send test lyrics: %s", e)
                messagebox.showerror("Send Error", f"Failed to send lyrics: {e}")

    def _clear_lyrics(self):
        if callable(self.action_callback):
            try:
                self.action_callback("clear_lyrics")
                self.update_status("Cleared lyrics")
            except Exception as e:
                logger.error("Failed to clear lyrics: %s", e)
                messagebox.showerror("Clear Error", f"Failed to clear lyrics: {e}")

    # Roles CRUD 
    def refresh_roles_list(self):
        try:
            for item in self.roles_tree.get_children():
                self.roles_tree.delete(item)

            for role in self.config.get("roles", []):
                decks = ", ".join(str(d) for d in role.get("decks", []))
                buttons = ", ".join([f"{k} → {v}" for k, v in (role.get("buttons", {}) or {}).items()])
                self.roles_tree.insert(
                    "", "end", values=(role.get("name", "Unnamed"), decks, buttons)
                )
        except Exception as e:
            logger.error("Failed to refresh roles list: %s", e)
            messagebox.showerror("Refresh Error", f"Failed to refresh roles: {e}")

    def add_role(self):
        self.open_role_editor()

    def edit_role(self):
        selected = self.roles_tree.selection()
        if not selected:
            messagebox.showwarning("Select Role", "Please select a role to edit.")
            return
        try:
            idx = self.roles_tree.index(selected[0])
            role = self.config["roles"][idx]
            self.open_role_editor(role, idx)
        except Exception as e:
            logger.error("Failed to edit role: %s", e)
            messagebox.showerror("Edit Error", f"Failed to edit role: {e}")

    def delete_role(self):
        selected = self.roles_tree.selection()
        if not selected:
            return
        try:
            idx = self.roles_tree.index(selected[0])
            role_name = self.config["roles"][idx].get("name", "Unnamed")
            if messagebox.askyesno("Confirm Delete", f"Delete role '{role_name}'?"):
                del self.config["roles"][idx]
                self.refresh_roles_list()
                if self.on_config_save(self.config):
                    self.update_status(f"Deleted role: {role_name}")
        except Exception as e:
            logger.error("Failed to delete role: %s", e)
            messagebox.showerror("Delete Error", f"Failed to delete role: {e}")

    def open_role_editor(self, role=None, role_index=None):
        dialog = RoleEditorDialog(
            self.master, role, role_index, self.config, self.on_role_saved, self.on_config_save
        )
        dialog.show()

    def on_role_saved(self, new_role, role_index):
        try:
            # prevent duplicate names (case-insensitive)
            existing = {
                (r.get("name") or "").strip().lower()
                for r in self.config.get("roles", [])
                if r is not None
            }
            incoming = (new_role.get("name") or "").strip().lower()
            if role_index is not None:
                # allow same name if editing the same role index
                pass
            elif incoming in existing:
                messagebox.showerror("Error", f"Role '{new_role.get('name')}' already exists.")
                return

            if role_index is not None:
                self.config["roles"][role_index] = new_role
            else:
                self.config["roles"].append(new_role)

            self.refresh_roles_list()
            if self.on_config_save(self.config):
                self.update_status(f"Saved role: {new_role.get('name', 'Unnamed')}")
        except Exception as e:
            logger.error("Failed to save role: %s", e)
            messagebox.showerror("Save Error", f"Failed to save role: {e}")

    # Status indicators 
    def set_recording(self, is_on: bool):
        color = "#2ca34a" if is_on else "#c43c3c"
        self.thread_safe_update(self._rec_lbl.configure, foreground=color)

    def set_overlay(self, is_on: bool):
        color = "#2ca34a" if is_on else "#c43c3c"
        self.thread_safe_update(self._ovr_lbl.configure, foreground=color)

    def set_conn_status(self, vmix_ok: bool = None, openlp_ok: bool = None):
        if vmix_ok is not None:
            color = "#2ca34a" if vmix_ok else "#c43c3c"
            status = "Connected" if vmix_ok else "Disconnected"
            self.thread_safe_update(self._vmix_lbl.configure, foreground=color)
            self.thread_safe_update(self.vmix_status_var.set, status)
        if openlp_ok is not None:
            color = "#2ca34a" if openlp_ok else "#c43c3c"
            status = "Connected" if openlp_ok else "Disconnected"
            self.thread_safe_update(self._openlp_lbl.configure, foreground=color)
            self.thread_safe_update(self.openlp_status_var.set, status)

    # Settings dialog 
    def open_settings_dialog(self):
        SettingsDialog(
            parent=self.master,
            config=self.config,
            on_config_save=self.on_config_save,
            discoverer=self.discoverer,
            loop=self.loop,  # pass GUI async loop
        ).show()


# Role Editor Dialog
class RoleEditorDialog:
    def __init__(self, parent, role, role_index, config, on_save, on_config_save):
        self.parent = parent
        self.role = role or {}
        self.role_index = role_index
        self.config = config
        self.on_save = on_save
        self.on_config_save = on_config_save
        self.window: Optional[tk.Toplevel] = None

    def show(self):
        self.window = tk.Toplevel(self.parent)
        self.window.title("Role Editor")
        self.window.geometry("450x350")
        self.window.transient(self.parent)
        self.window.grab_set()

        # center
        self.window.update_idletasks()
        x = self.parent.winfo_x() + max(0, (self.parent.winfo_width() - self.window.winfo_width()) // 2)
        y = self.parent.winfo_y() + max(0, (self.parent.winfo_height() - self.window.winfo_height()) // 2)
        self.window.geometry(f"+{x}+{y}")

        self._build_ui()

    def _build_ui(self):
        main = ttk.Frame(self.window, padding=20)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="Role Name:").grid(row=0, column=0, sticky="w", pady=5)
        self.name_var = tk.StringVar(value=self.role.get("name", ""))
        name_entry = ttk.Entry(main, textvariable=self.name_var, width=30)
        name_entry.grid(row=0, column=1, sticky="ew", pady=5, padx=5)

        ttk.Label(main, text="Deck IDs (comma-separated):").grid(row=1, column=0, sticky="w", pady=5)
        decks_str = ", ".join(str(d) for d in self.role.get("decks", []))
        self.decks_var = tk.StringVar(value=decks_str)
        decks_entry = ttk.Entry(main, textvariable=self.decks_var, width=30)
        decks_entry.grid(row=1, column=1, sticky="ew", pady=5, padx=5)

        ttk.Label(main, text="Button Mappings (key:action, comma-separated):").grid(
            row=2, column=0, sticky="w", pady=5
        )
        buttons_str = ", ".join([f"{k}:{v}" for k, v in (self.role.get("buttons", {}) or {}).items()])
        self.buttons_var = tk.StringVar(value=buttons_str)
        buttons_entry = ttk.Entry(main, textvariable=self.buttons_var, width=30)
        buttons_entry.grid(row=2, column=1, sticky="ew", pady=5, padx=5)

        ttk.Label(
            main,
            text="Example: 0:show_lyrics, 1:clear_lyrics, 2:toggle_overlay",
            font=("Segoe UI", 8),
            foreground="gray",
        ).grid(row=3, column=1, sticky="w", pady=2, padx=5)

        btns = ttk.Frame(main)
        btns.grid(row=4, column=0, columnspan=2, pady=20)
        ttk.Button(btns, text="💾 Save", command=self._save).pack(side="left", padx=10)
        ttk.Button(btns, text="❌ Cancel", command=self.window.destroy).pack(side="left", padx=10)

        main.columnconfigure(1, weight=1)
        name_entry.focus()

    def _save(self):
        try:
            name = (self.name_var.get() or "").strip()
            if not name:
                messagebox.showerror("Error", "Role name is required.")
                return

            # Parse decks
            decks: List[int] = []
            for d in (self.decks_var.get() or "").split(","):
                d = d.strip()
                if d.isdigit():
                    decks.append(int(d))

            # Parse buttons
            buttons: Dict[str, str] = {}
            for mapping in (self.buttons_var.get() or "").split(","):
                mapping = mapping.strip()
                if ":" in mapping:
                    k, v = mapping.split(":", 1)
                    buttons[k.strip()] = v.strip()

            new_role = {"name": name, "decks": decks, "buttons": buttons}
            self.on_save(new_role, self.role_index)
            self.window.destroy()

        except Exception as e:
            logger.error("Failed to save role: %s", e)
            messagebox.showerror("Save Error", f"Failed to save role: {e}")


# Settings Dialog
class SettingsDialog:
    """Settings dialog with async vMix discovery (using LyriSyncGUI loop)."""

    def __init__(self, parent, config, on_config_save, discoverer: AsyncVmixDiscoverer, loop: asyncio.AbstractEventLoop):
        self.parent = parent
        self.config = config
        self.on_config_save = on_config_save
        self.discoverer = discoverer
        self.loop = loop

        self.window: Optional[tk.Toplevel] = None
        self.settings = config.get("settings", {}) or {}

        # cached fields map (input -> [fields])
        self._fields_map: Dict[str, List[str]] = {}

    def show(self):
        self.window = tk.Toplevel(self.parent)
        self.window.title("Settings")
        self.window.geometry("700x600")
        self.window.transient(self.parent)
        self.window.grab_set()

        self._build_ui()

    #  UI 
    def _build_ui(self):
        main = ttk.Frame(self.window)
        main.pack(fill="both", expand=True, padx=10, pady=10)

        canvas = tk.Canvas(main, highlightthickness=0)
        sbar = ttk.Scrollbar(main, orient="vertical", command=canvas.yview)
        scroll = ttk.Frame(canvas)

        scroll.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll, anchor="nw")
        canvas.configure(yscrollcommand=sbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        sbar.pack(side="right", fill="y")

        self._build_settings_content(scroll)

        # cross-platform wheel scrolling
        def _on_wheel(event):
            delta = event.delta
            # Linux: Button-4/5
            if delta == 0 and getattr(event, "num", None) in (4, 5):
                delta = 120 if event.num == 4 else -120
            canvas.yview_scroll(int(-1 * (delta / 120)), "units")

        canvas.bind("<MouseWheel>", _on_wheel)   # Windows/macOS
        canvas.bind("<Button-4>", _on_wheel)     # Linux up
        canvas.bind("<Button-5>", _on_wheel)     # Linux down

    def _build_settings_content(self, parent):
        r = 0

        # vMix Settings
        ttk.Label(parent, text="vMix Settings", font=("Segoe UI", 12, "bold")).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0, 10)
        ); r += 1

        ttk.Label(parent, text="vMix API URL:").grid(row=r, column=0, sticky="w", pady=5)
        self.vmix_api_var = tk.StringVar(value=self.settings.get("vmix_api_url", "http://localhost:8088/api"))
        ttk.Entry(parent, textvariable=self.vmix_api_var, width=48).grid(row=r, column=1, sticky="ew", padx=5, pady=5); r += 1

        ttk.Label(parent, text="vMix Input:").grid(row=r, column=0, sticky="w", pady=5)
        self.input_var = tk.StringVar(value=self.settings.get("vmix_title_input", "SongTitle"))
        self.input_combo = ttk.Combobox(parent, textvariable=self.input_var, state="readonly", width=32)
        self.input_combo.grid(row=r, column=1, sticky="ew", padx=5, pady=5)
        ttk.Button(parent, text="Discover", command=self._discover_inputs).grid(row=r, column=2, padx=5, pady=5); r += 1

        ttk.Label(parent, text="vMix Field:").grid(row=r, column=0, sticky="w", pady=5)
        self.field_var = tk.StringVar(value=self.settings.get("vmix_title_field", "Message.Text"))
        self.field_combo = ttk.Combobox(parent, textvariable=self.field_var, state="readonly", width=32)
        self.field_combo.grid(row=r, column=1, sticky="ew", padx=5, pady=5); r += 1

        # update fields when input changes
        def _on_input_changed(event=None):
            fields = self._fields_map.get(self.input_var.get(), [])
            self.field_combo["values"] = fields
            if fields and self.field_var.get() not in fields:
                self.field_var.set(fields[0])

        self.input_combo.bind("<<ComboboxSelected>>", _on_input_changed)

        # OpenLP Settings
        ttk.Label(parent, text="OpenLP Settings", font=("Segoe UI", 12, "bold")).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(20, 10)
        ); r += 1

        ttk.Label(parent, text="OpenLP WS URL:").grid(row=r, column=0, sticky="w", pady=5)
        self.openlp_ws_var = tk.StringVar(value=self.settings.get("openlp_ws_url", "ws://localhost:4317"))
        ttk.Entry(parent, textvariable=self.openlp_ws_var, width=48).grid(row=r, column=1, sticky="ew", padx=5, pady=5); r += 1

        # API Settings
        ttk.Label(parent, text="API Settings", font=("Segoe UI", 12, "bold")).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(20, 10)
        ); r += 1

        ttk.Label(parent, text="API Port:").grid(row=r, column=0, sticky="w", pady=5)
        self.api_port_var = tk.StringVar(value=str(self.settings.get("api_port", 5000)))
        ttk.Entry(parent, textvariable=self.api_port_var, width=10).grid(row=r, column=1, sticky="w", padx=5, pady=5); r += 1

        # Overlay Settings
        ttk.Label(parent, text="Overlay Settings", font=("Segoe UI", 12, "bold")).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(20, 10)
        ); r += 1

        ttk.Label(parent, text="Overlay Channel:").grid(row=r, column=0, sticky="w", pady=5)
        self.overlay_var = tk.StringVar(value=str(self.settings.get("overlay_channel", 1)))
        overlay_combo = ttk.Combobox(parent, textvariable=self.overlay_var, values=["1", "2", "3", "4"], state="readonly", width=6)
        overlay_combo.grid(row=r, column=1, sticky="w", padx=5, pady=5); r += 1

        self.auto_overlay_var = tk.BooleanVar(value=self.settings.get("auto_overlay_on_send", True))
        ttk.Checkbutton(parent, text="Auto Overlay on Send", variable=self.auto_overlay_var).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=5
        ); r += 1

        self.auto_clear_var = tk.BooleanVar(value=self.settings.get("auto_overlay_out_on_clear", True))
        ttk.Checkbutton(parent, text="Overlay Out on Clear", variable=self.auto_clear_var).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=5
        ); r += 1

        self.always_on_var = tk.BooleanVar(value=self.settings.get("overlay_always_on", False))
        ttk.Checkbutton(parent, text="Overlay Always On", variable=self.always_on_var).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=5
        ); r += 1

        self.clear_blank_var = tk.BooleanVar(value=self.settings.get("clear_on_blank", True))
        ttk.Checkbutton(parent, text="Clear on Blank Slide", variable=self.clear_blank_var).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=5
        ); r += 1

        self.splash_var = tk.BooleanVar(value=self.settings.get("splash_enabled", True))
        ttk.Checkbutton(parent, text="Show Splash Screen", variable=self.splash_var).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=5
        ); r += 1

        # Text/poll
        ttk.Label(parent, text="Max Chars per Line:").grid(row=r, column=0, sticky="w", pady=5)
        self.wrap_var = tk.StringVar(value=str(self.settings.get("max_chars_per_line", 48)))
        ttk.Entry(parent, textvariable=self.wrap_var, width=10).grid(row=r, column=1, sticky="w", padx=5, pady=5); r += 1

        ttk.Label(parent, text="Auto-Clear Idle (sec, 0=off):").grid(row=r, column=0, sticky="w", pady=5)
        self.idle_var = tk.StringVar(value=str(self.settings.get("auto_clear_idle_sec", 0)))
        ttk.Entry(parent, textvariable=self.idle_var, width=10).grid(row=r, column=1, sticky="w", padx=5, pady=5); r += 1

        ttk.Label(parent, text="Poll Interval (sec):").grid(row=r, column=0, sticky="w", pady=5)
        self.poll_var = tk.StringVar(value=str(self.settings.get("poll_interval_sec", 2)))
        ttk.Entry(parent, textvariable=self.poll_var, width=10).grid(row=r, column=1, sticky="w", padx=5, pady=5); r += 1

        # Overlay quick-test
        test_overlay = ttk.Frame(parent)
        test_overlay.grid(row=r, column=0, columnspan=3, pady=(10, 0), sticky="w")
        ttk.Button(test_overlay, text="Overlay In", command=lambda: self._call_vmix_overlay("In")).pack(side="left", padx=4)
        ttk.Button(test_overlay, text="Overlay Out", command=lambda: self._call_vmix_overlay("Out")).pack(side="left", padx=4)
        r += 1

        # Bottom buttons
        btns = ttk.Frame(parent)
        btns.grid(row=r, column=0, columnspan=3, pady=20, sticky="ew")
        ttk.Button(btns, text="Test vMix Connection", command=self._test_vmix).pack(side="left", padx=5)
        ttk.Button(btns, text="Save", command=self._save_settings).pack(side="right", padx=5)
        ttk.Button(btns, text="Cancel", command=self.window.destroy).pack(side="right", padx=5)

        parent.columnconfigure(1, weight=1)

    #  async ops 
    def _discover_inputs(self):
        asyncio.run_coroutine_threadsafe(self._discover_inputs_async(), self.loop)

    async def _discover_inputs_async(self):
        try:
            api_url = self.vmix_api_var.get().strip() or "http://localhost:8088/api"
            inputs, fields_map = await self.discoverer.discover_vmix_inputs(api_url)

            def update_ui():
                self._fields_map = fields_map
                self.input_combo["values"] = inputs
                if inputs and self.input_var.get() not in inputs:
                    self.input_var.set(inputs[0])

                cur = self.input_var.get()
                fields = fields_map.get(cur, [])
                self.field_combo["values"] = fields
                if fields and self.field_var.get() not in fields:
                    self.field_var.set(fields[0])

            self.window.after(0, update_ui)

        except Exception as e:
            self.window.after(0, lambda: messagebox.showerror("Discovery Error", f"Failed to discover vMix inputs: {e}"))

    def _test_vmix(self):
        asyncio.run_coroutine_threadsafe(self._test_vmix_async(), self.loop)

    async def _test_vmix_async(self):
        try:
            api_url = self.vmix_api_var.get().strip() or "http://localhost:8088/api"
            inputs, _ = await self.discoverer.discover_vmix_inputs(api_url)
            self.window.after(0, lambda: messagebox.showinfo("Connection Test", f"Connected to vMix.\nFound {len(inputs)} inputs."))
        except Exception as e:
            self.window.after(0, lambda: messagebox.showerror("Connection Test", f"Failed to connect to vMix: {e}"))

    def _call_vmix_overlay(self, action: str):
        asyncio.run_coroutine_threadsafe(self._call_vmix_overlay_async(action), self.loop)

    async def _call_vmix_overlay_async(self, action: str):
        """Fire an OverlayInput{ch}{action} call to vMix for quick testing."""
        try:
            api_url = self.vmix_api_var.get().strip() or "http://localhost:8088/api"
            ch = max(1, min(4, int(self.overlay_var.get() or "1")))
            params = {"Function": f"OverlayInput{ch}{action}"}
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, params=params, timeout=5) as r:
                    if r.status != 200:
                        raise Exception(f"HTTP {r.status}")
            self.window.after(0, lambda: messagebox.showinfo("Overlay", f"Overlay {action} OK (ch {ch})"))
        except Exception as e:
            self.window.after(0, lambda: messagebox.showerror("Overlay", f"Overlay {action} failed: {e}"))

    # save settings 
    @staticmethod
    def _valid_url(u: str, scheme: str) -> bool:
        try:
            p = urlparse(u)
            return p.scheme == scheme and bool(p.netloc)
        except Exception:
            return False

    def _save_settings(self):
        try:
            vmix = (self.vmix_api_var.get() or "").strip()
            openlp = (self.openlp_ws_var.get() or "").strip()

            if not self._valid_url(vmix, "http"):
                messagebox.showerror(
                    "Save Error",
                    "vMix API URL must start with http:// and include host:port, e.g. http://localhost:8088/api",
                )
                return
            if not self._valid_url(openlp, "ws"):
                messagebox.showerror(
                    "Save Error",
                    "OpenLP WS URL must start with ws:// and include host:port, e.g. ws://localhost:4317",
                )
                return

            new_settings = {
                "vmix_api_url": vmix,
                "openlp_ws_url": openlp,
                "api_port": max(1024, min(65535, int((self.api_port_var.get() or "5000").strip()))),
                "vmix_title_input": (self.input_var.get() or "SongTitle").strip(),
                "vmix_title_field": (self.field_var.get() or "Message.Text").strip(),
                "splash_enabled": bool(self.splash_var.get()),
                "poll_interval_sec": max(1, int((self.poll_var.get() or "2").strip())),
                "overlay_channel": max(1, min(4, int((self.overlay_var.get() or "1").strip()))),
                "auto_overlay_on_send": bool(self.auto_overlay_var.get()),
                "auto_overlay_out_on_clear": bool(self.auto_clear_var.get()),
                "overlay_always_on": bool(self.always_on_var.get()),
                "auto_clear_idle_sec": max(0, int((self.idle_var.get() or "0").strip())),
                "max_chars_per_line": max(10, int((self.wrap_var.get() or "48").strip())),
                "clear_on_blank": bool(self.clear_blank_var.get()),
            }

            self.config["settings"] = new_settings
            if self.on_config_save(self.config):
                logger.info(
                    "Saved settings: vmix=%s, openlp=%s, overlay_ch=%s",
                    new_settings["vmix_api_url"],
                    new_settings["openlp_ws_url"],
                    new_settings["overlay_channel"],
                )
                messagebox.showinfo("Settings", "Settings saved successfully!")
                self.window.destroy()

        except ValueError as e:
            messagebox.showerror("Save Error", f"Invalid numeric value: {e}")
        except Exception as e:
            logger.error("Failed to save settings: %s", e)
            messagebox.showerror("Save Error", f"Failed to save settings: {e}")
