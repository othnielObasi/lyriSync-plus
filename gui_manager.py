# gui_manager.py
import tkinter as tk
from tkinter import ttk, messagebox
import ttkbootstrap as tb
import yaml
import requests
import xml.etree.ElementTree as ET
from pathlib import Path

CONFIG_FILE = "lyrisync_config.yaml"

def load_config():
    path = Path(CONFIG_FILE)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            pass
    return {
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
            "clear_on_blank": True
        }
    }

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f)

class LyriSyncGUI:
    def __init__(self, master, config, on_config_save, action_callback=None):
        self.master = master
        self.config = config
        self.on_config_save = on_config_save
        self.action_callback = action_callback

        self.master.title("LyriSync+")
        self.master.geometry("960x600")
        initial_theme = (self.config.get("ui", {}) or {}).get("theme", "darkly")
        self.style = tb.Style(initial_theme)

        self._build_ui()
        self.refresh_roles_list()

    # ---- vMix Input Discovery ----
    def _discover_vmix_inputs(self, api_url: str):
        """Return (input_names, fields_by_input) by querying vMix /api."""
        try:
            resp = requests.get(api_url, timeout=3)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
        except Exception as e:
            messagebox.showerror("vMix Discovery", f"Failed to fetch from {api_url}\n\n{e}")
            return [], {}

        input_names = []
        fields_by_input = {}
        for inp in root.findall(".//inputs/input"):
            name = inp.get("title") or inp.get("shortTitle") or inp.get("number") or "Unknown"
            input_names.append(name)
            fields = []
            data_node = inp.find("data")
            if data_node is not None:
                for t in data_node.findall("text"):
                    nm = t.get("name")
                    if nm: fields.append(nm)
            seen = set(); fields = [f for f in fields if not (f in seen or seen.add(f))]
            fields_by_input[name] = fields or []
        seen_inp = set(); input_names = [n for n in input_names if not (n in seen_inp or seen_inp.add(n))]
        return input_names, fields_by_input

    def _build_ui(self):
        header = ttk.Frame(self.master); header.pack(fill="x", padx=10, pady=(8,0))
        ttk.Label(header, text="LyriSync+", font=("Segoe UI", 14, "bold")).pack(side="left")

        # Connection dots + status
        self._rec_lbl = ttk.Label(header, text="●", foreground="#c43c3c", font=("Segoe UI", 14))
        self._ovr_lbl = ttk.Label(header, text="●", foreground="#c43c3c", font=("Segoe UI", 14))
        self._vmix_lbl = ttk.Label(header, text="●", foreground="#c43c3c", font=("Segoe UI", 14))
        self._openlp_lbl = ttk.Label(header, text="●", foreground="#c43c3c", font=("Segoe UI", 14))

        ttk.Label(header, text=" Recording ").pack(side="right")
        self._rec_lbl.pack(side="right", padx=(0,8))
        ttk.Label(header, text=" Overlay ").pack(side="right")
        self._ovr_lbl.pack(side="right", padx=(0,14))

        ttk.Label(header, text=" vMix ").pack(side="right")
        self._vmix_lbl.pack(side="right")
        ttk.Label(header, text=" OpenLP ").pack(side="right", padx=(12,0))
        self._openlp_lbl.pack(side="right")

        ttk.Label(header, text=" Theme: ").pack(side="right")
        themes = ["darkly","flatly","cosmo","pulse","cyborg","morph"]
        self.theme_var = tk.StringVar(value=initial_theme := self.style.theme.name if hasattr(self.style, 'theme') else (self.config.get('ui',{}).get('theme','darkly')))
        theme_dd = ttk.Combobox(header, values=themes, textvariable=self.theme_var, state="readonly", width=12)
        theme_dd.pack(side="right", padx=(0,6))
        def _apply_theme(event=None):
            chosen = self.theme_var.get()
            try: self.style.theme_use(chosen)
            except Exception: pass
            self.config.setdefault("ui", {})["theme"] = chosen
            self.on_config_save(self.config)
        theme_dd.bind("<<ComboboxSelected>>", _apply_theme)

        # Notebook
        notebook = ttk.Notebook(self.master); notebook.pack(fill="both", expand=True, padx=10, pady=10)
        self.roles_frame = ttk.Frame(notebook)
        self.status_frame = ttk.Frame(notebook)
        notebook.add(self.roles_frame, text="🎭 Roles & Decks")
        notebook.add(self.status_frame, text="📡 Live Status")

        self._build_roles_tab()
        self._build_status_tab()

        # Settings button
        ttk.Button(header, text="Settings", command=self.open_settings_dialog).pack(side="right", padx=(0,8))

    def _build_roles_tab(self):
        self.roles_tree = ttk.Treeview(self.roles_frame, columns=("Decks", "Buttons"), show="headings")
        self.roles_tree.heading("Decks", text="Deck IDs")
        self.roles_tree.heading("Buttons", text="Button Mappings")
        self.roles_tree.pack(fill="both", expand=True, pady=10)
        btns = ttk.Frame(self.roles_frame); btns.pack(pady=5)
        ttk.Button(btns, text="➕ Add Role", command=self.add_role).pack(side="left", padx=5)
        ttk.Button(btns, text="✏️ Edit Role", command=self.edit_role).pack(side="left", padx=5)
        ttk.Button(btns, text="❌ Delete Role", command=self.delete_role).pack(side="left", padx=5)

    def _build_status_tab(self):
        self.status_label = ttk.Label(self.status_frame, text="Status updates will appear here", font=("Segoe UI", 12))
        self.status_label.pack(pady=8)

        controls = ttk.Frame(self.status_frame); controls.pack(pady=8)
        ttk.Label(controls, text="Test Lyrics:").grid(row=0, column=0, sticky="w", padx=(0,6))
        self._lyrics_var = tk.StringVar(value="")
        ttk.Entry(controls, textvariable=self._lyrics_var, width=48).grid(row=0, column=1, padx=(0,10))

        def _send_lyrics():
            if callable(self.action_callback):
                try: self.action_callback(("set_lyrics_text", self._lyrics_var.get()))
                except Exception: pass
                self.action_callback("show_lyrics")

        def _clear_lyrics():
            if callable(self.action_callback): self.action_callback("clear_lyrics")

        ttk.Button(controls, text="Send Lyrics to vMix", command=_send_lyrics).grid(row=0, column=2, padx=4)
        ttk.Button(controls, text="Clear Lyrics", command=_clear_lyrics).grid(row=0, column=3, padx=4)

        ttk.Separator(self.status_frame).pack(fill="x", padx=10, pady=10)

        toggles = ttk.Frame(self.status_frame); toggles.pack(pady=4)
        ttk.Button(toggles, text="Toggle Overlay", command=lambda: self.action_callback and self.action_callback("toggle_overlay")).pack(side="left", padx=6)
        ttk.Button(toggles, text="Start Recording", command=lambda: self.action_callback and self.action_callback("start_recording")).pack(side="left", padx=6)
        ttk.Button(toggles, text="Stop Recording", command=lambda: self.action_callback and self.action_callback("stop_recording")).pack(side="left", padx=6)

    # ---- Role CRUD ----
    def refresh_roles_list(self):
        for i in self.roles_tree.get_children():
            self.roles_tree.delete(i)
        for role in self.config.get("roles", []):
            decks = ", ".join(str(d) for d in role.get("decks", []))
            buttons = ", ".join([f"{k} → {v}" for k, v in role.get("buttons", {}).items()])
            self.roles_tree.insert("", "end", values=(decks, buttons))

    def add_role(self): self.open_role_editor()
    def edit_role(self):
        selected = self.roles_tree.selection()
        if not selected: 
            messagebox.showwarning("Select Role", "Please select a role to edit."); return
        idx = self.roles_tree.index(selected[0])
        role = self.config["roles"][idx]
        self.open_role_editor(role, idx)
    def delete_role(self):
        selected = self.roles_tree.selection()
        if not selected: return
        idx = self.roles_tree.index(selected[0])
        del self.config["roles"][idx]
        self.refresh_roles_list()
        self.on_config_save(self.config)

    def open_role_editor(self, role=None, role_index=None):
        window = tk.Toplevel(self.master); window.title("Role Editor"); window.geometry("420x420")
        name_var = tk.StringVar(value=role["name"] if role else "")
        decks_var = tk.StringVar(value=", ".join(str(d) for d in role.get("decks", [])) if role else "")
        buttons_var = tk.StringVar(value=", ".join([f"{k}:{v}" for k, v in role.get("buttons", {}).items()]) if role else "")
        ttk.Label(window, text="Role Name").pack(pady=5); ttk.Entry(window, textvariable=name_var).pack(fill="x", padx=10)
        ttk.Label(window, text="Deck IDs (comma-separated)").pack(pady=5); ttk.Entry(window, textvariable=decks_var).pack(fill="x", padx=10)
        ttk.Label(window, text="Button Mappings (e.g., 0:show_lyrics)").pack(pady=5); ttk.Entry(window, textvariable=buttons_var).pack(fill="x", padx=10)
        def save():
            name = name_var.get().strip()
            decks = [int(x.strip()) for x in decks_var.get().split(",") if x.strip().isdigit()]
            btn_map = {}
            for item in buttons_var.get().split(","):
                if ":" in item:
                    k, v = item.split(":", 1); btn_map[k.strip()] = v.strip()
            new_role = {"name": name, "decks": decks, "buttons": btn_map}
            if role_index is not None: self.config["roles"][role_index] = new_role
            else: self.config["roles"].append(new_role)
            self.refresh_roles_list(); self.on_config_save(self.config); window.destroy()
        ttk.Button(window, text="💾 Save", command=save).pack(pady=10)

    # ---- Status indicator API ----
    def set_recording(self, is_on: bool):
        color = "#2ca34a" if is_on else "#c43c3c"
        self._rec_lbl.configure(foreground=color)

    def set_overlay(self, is_on: bool):
        color = "#2ca34a" if is_on else "#c43c3c"
        self._ovr_lbl.configure(foreground=color)

    def set_conn_status(self, vmix_ok: bool=None, openlp_ok: bool=None):
        if vmix_ok is not None:
            self._vmix_lbl.configure(foreground=("#2ca34a" if vmix_ok else "#c43c3c"))
        if openlp_ok is not None:
            self._openlp_lbl.configure(foreground=("#2ca34a" if openlp_ok else "#c43c3c"))

    # ---- Settings Dialog ----
    def open_settings_dialog(self):
        win = tk.Toplevel(self.master); win.title("Settings"); win.geometry("620x520"); win.transient(self.master); win.grab_set()
        s = self.config.setdefault("settings", {})

        # Vars
        vmix_api_var = tk.StringVar(value=s.get("vmix_api_url", "http://localhost:8088/api"))
        openlp_ws_var = tk.StringVar(value=s.get("openlp_ws_url", "ws://localhost:4317"))
        api_port_var = tk.StringVar(value=str(s.get("api_port", 5000)))
        title_input_var = tk.StringVar(value=s.get("vmix_title_input", "SongTitle"))
        title_field_var = tk.StringVar(value=s.get("vmix_title_field", "Message.Text"))
        splash_var = tk.BooleanVar(value=bool(s.get("splash_enabled", True)))
        poll_var = tk.StringVar(value=str(s.get("poll_interval_sec", 2)))
        overlay_var = tk.StringVar(value=str(s.get("overlay_channel", 1)))
        aoin_var = tk.BooleanVar(value=bool(s.get("auto_overlay_on_send", True)))
        aoout_var = tk.BooleanVar(value=bool(s.get("auto_overlay_out_on_clear", True)))
        always_on_var = tk.BooleanVar(value=bool(s.get("overlay_always_on", False)))
        idle_var = tk.StringVar(value=str(s.get("auto_clear_idle_sec", 0)))
        wrap_var = tk.StringVar(value=str(s.get("max_chars_per_line", 48)))
        cob_var = tk.BooleanVar(value=bool(s.get("clear_on_blank", True)))

        frm = ttk.Frame(win); frm.pack(fill="both", expand=True, padx=14, pady=12)
        def row(r, label, widget): ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=6); widget.grid(row=r, column=1, sticky="ew", padx=(8,0))
        frm.columnconfigure(1, weight=1)

        row(0, "vMix API URL", ttk.Entry(frm, textvariable=vmix_api_var))
        row(1, "OpenLP WS URL", ttk.Entry(frm, textvariable=openlp_ws_var))
        row(2, "LyriSync+ API Port", ttk.Entry(frm, textvariable=api_port_var))

        # Discovery dropdowns
        ttk.Label(frm, text="vMix Input").grid(row=3, column=0, sticky="w", pady=6)
        input_var = tk.StringVar(value=title_input_var.get())
        input_dd = ttk.Combobox(frm, textvariable=input_var, values=[], state="readonly")
        input_dd.grid(row=3, column=1, sticky="ew", padx=(8,0))

        ttk.Label(frm, text="vMix Field").grid(row=4, column=0, sticky="w", pady=6)
        field_var = tk.StringVar(value=title_field_var.get())
        field_dd = ttk.Combobox(frm, textvariable=field_var, values=[], state="readonly")
        field_dd.grid(row=4, column=1, sticky="ew", padx=(8,0))

        def _on_input_changed(event=None):
            chosen = input_var.get()
            fields = getattr(self, "_fields_by_input", {}).get(chosen, [])
            field_dd.configure(values=fields)
            if fields and field_var.get() not in fields:
                field_var.set(fields[0])
        input_dd.bind("<<ComboboxSelected>>", _on_input_changed)

        def _do_discover():
            inputs, fmap = self._discover_vmix_inputs(vmix_api_var.get().strip() or "http://localhost:8088/api")
            if not inputs:
                messagebox.showwarning("vMix Discovery", "No inputs found. Is vMix running and API enabled?"); return
            self._vmix_inputs = inputs; self._fields_by_input = fmap
            input_dd.configure(values=inputs); _on_input_changed()
        ttk.Button(frm, text="Discover", command=_do_discover).grid(row=3, column=2, padx=8)

        # Overlay channel dropdown
        ttk.Label(frm, text="Overlay Channel (1-4)").grid(row=7, column=0, sticky="w", pady=6)
        overlay_dd = ttk.Combobox(frm, values=["1","2","3","4"], textvariable=overlay_var, state="readonly", width=6)
        overlay_dd.grid(row=7, column=1, sticky="w", padx=(8,0))

        ttk.Label(frm, text="Auto Overlay (on Send)").grid(row=8, column=0, sticky="w", pady=6)
        ttk.Checkbutton(frm, variable=aoin_var).grid(row=8, column=1, sticky="w", padx=(8,0))
        ttk.Label(frm, text="Overlay Out on Clear").grid(row=9, column=0, sticky="w", pady=6)
        ttk.Checkbutton(frm, variable=aoout_var).grid(row=9, column=1, sticky="w", padx=(8,0))
        ttk.Label(frm, text="Overlay Always On").grid(row=9, column=2, sticky="w", pady=6)
        ttk.Checkbutton(frm, variable=always_on_var).grid(row=9, column=3, sticky="w")
        ttk.Label(frm, text="Clear on Blank Slide").grid(row=10, column=0, sticky="w", pady=6)
        ttk.Checkbutton(frm, variable=cob_var).grid(row=10, column=1, sticky="w", padx=(8,0))

        row(11, "Auto-Clear if Idle (sec, 0=off)", ttk.Entry(frm, textvariable=idle_var))
        row(12, "Max Chars per Line", ttk.Entry(frm, textvariable=wrap_var))
        ttk.Label(frm, text="Splash Enabled").grid(row=13, column=0, sticky="w", pady=6)
        ttk.Checkbutton(frm, variable=splash_var).grid(row=13, column=1, sticky="w", padx=(8,0))

        # Test Send / Clear / Overlay In/Out
        ttk.Label(frm, text="Test Text").grid(row=14, column=0, sticky="w", pady=6)
        test_text_var = tk.StringVar(value="Hello from LyriSync+")
        ttk.Entry(frm, textvariable=test_text_var).grid(row=14, column=1, sticky="ew", padx=(8,0))

        def _test_send():
            api_url = vmix_api_var.get().strip() or "http://localhost:8088/api"
            in_name = (input_var.get().strip() or title_input_var.get().strip() or "SongTitle")
            fld = (field_var.get().strip() or title_field_var.get().strip() or "Message.Text")
            val = test_text_var.get()
            try:
                r = requests.get(api_url, params={"Function":"SetText","Input":in_name,"SelectedName":fld,"Value":val}, timeout=3)
                r.raise_for_status(); messagebox.showinfo("Test Send", f"Sent sample text to {in_name}.{fld}")
            except Exception as e:
                messagebox.showerror("Test Send", f"Failed to send to {in_name}.{fld}\n\n{e}")

        def _test_clear():
            api_url = vmix_api_var.get().strip() or "http://localhost:8088/api"
            in_name = (input_var.get().strip() or title_input_var.get().strip() or "SongTitle")
            fld = (field_var.get().strip() or title_field_var.get().strip() or "Message.Text")
            try:
                r = requests.get(api_url, params={"Function":"SetText","Input":in_name,"SelectedName":fld,"Value":""}, timeout=3)
                r.raise_for_status(); messagebox.showinfo("Clear Test", f"Cleared text on {in_name}.{fld}")
            except Exception as e:
                messagebox.showerror("Clear Test", f"Failed to clear {in_name}.{fld}\n\n{e}")

        def _overlay_in():
            api_url = vmix_api_var.get().strip() or "http://localhost:8088/api"
            try:
                r = requests.get(api_url, params={"Function": f"OverlayInput{int(overlay_var.get() or 1)}In"}, timeout=3)
                r.raise_for_status(); messagebox.showinfo("Overlay In", f"OverlayInput{overlay_var.get()}In triggered")
            except Exception as e:
                messagebox.showerror("Overlay In", f"Failed to trigger overlay in\n\n{e}")

        def _overlay_out():
            api_url = vmix_api_var.get().strip() or "http://localhost:8088/api"
            try:
                r = requests.get(api_url, params={"Function": f"OverlayInput{int(overlay_var.get() or 1)}Out"}, timeout=3)
                r.raise_for_status(); messagebox.showinfo("Overlay Out", f"OverlayInput{overlay_var.get()}Out triggered")
            except Exception as e:
                messagebox.showerror("Overlay Out", f"Failed to trigger overlay out\n\n{e}")

        ttk.Button(frm, text="Test Send", command=_test_send).grid(row=14, column=2, padx=8)
        ttk.Button(frm, text="Clear Test", command=_test_clear).grid(row=15, column=1, sticky="w", padx=(8,0), pady=(4,0))
        ttk.Button(frm, text="Overlay In", command=_overlay_in).grid(row=15, column=2, padx=8, pady=(4,0), sticky="w")
        ttk.Button(frm, text="Overlay Out", command=_overlay_out).grid(row=15, column=3, padx=8, pady=(4,0), sticky="w")

        # Save/Cancel
        btns = ttk.Frame(win); btns.pack(fill="x", pady=(10,0))
        def _save():
            try:
                s["vmix_api_url"] = vmix_api_var.get().strip() or "http://localhost:8088/api"
                s["openlp_ws_url"] = openlp_ws_var.get().strip() or "ws://localhost:4317"
                s["api_port"] = int(api_port_var.get().strip())
                s["vmix_title_input"] = (input_var.get().strip() or title_input_var.get().strip() or "SongTitle")
                s["vmix_title_field"] = (field_var.get().strip() or title_field_var.get().strip() or "Message.Text")
                s["splash_enabled"] = bool(splash_var.get())
                s["poll_interval_sec"] = max(1, int(poll_var.get().strip()))
                s["overlay_channel"] = max(1, min(4, int(overlay_var.get().strip() or "1")))
                s["auto_overlay_on_send"] = bool(aoin_var.get())
                s["auto_overlay_out_on_clear"] = bool(aoout_var.get())
                s["overlay_always_on"] = bool(always_on_var.get())
                s["auto_clear_idle_sec"] = max(0, int(idle_var.get().strip() or "0"))
                s["max_chars_per_line"] = max(10, int(wrap_var.get().strip() or "48"))
                s["clear_on_blank"] = bool(cob_var.get())
                self.on_config_save(self.config)
                messagebox.showinfo("Settings", "Saved. Some changes apply next launch (e.g., API port).")
                win.destroy()
            except Exception as e:
                messagebox.showerror("Settings", f"Invalid values: {e}")
        ttk.Button(btns, text="Save", command=_save).pack(side="right", padx=8)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")
