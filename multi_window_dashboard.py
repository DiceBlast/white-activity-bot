"""Desktop multi-window guild monitor with restrained glass-inspired theme.

Works with Tk on Windows and Linux.
Each Discord guild gets its own Tk window; all share ONE running bot instance.
"""
import json
import os
from collections import Counter, deque
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from guild_log_parser import parse_line, MAX_LINES
from dashboard_metrics import guild_metrics, recent_reward_activity
from analytics_history import history_for_guild
from alert_center import recent_alerts, add_alert, settings as alert_settings, save_settings, DEFAULTS
from dashboard_preferences import load as load_dashboard_preferences, save as save_dashboard_preferences, THEMES, REFRESH_CHOICES, STARTUP_CHOICES
from bot_supervisor import request_action, read_json, HEARTBEAT_TIMEOUT_SECONDS
import supervisor_storage as supervisor_db
from connection_health import display_status
from tkinter import messagebox, filedialog
from analytics_reports import load_samples, summarize, export_csv, RANGE_SECONDS
from backup_center import guild_ids, backup_folders, validate, create_manual, restore_offline, delete_backup
from update_manager import check_for_update, current_version, update_from_git
from config_manager import load_config, save_config, mark_setup_section, setup_status
import shutil
import subprocess
import sys
import time

try:
    import psutil
except ImportError:
    psutil = None

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "logs" / "bot.log"
# Presentation-only palette; no changes to data or bot controls.
PALETTES = {
    "Dark": {"BG": "#151c28", "PANEL": "#253142", "PANEL_DARK": "#1c2736",
             "BORDER": "#405064", "TEXT": "#e8edf4", "MUTED": "#aebac9",
             "ACCENT": "#a5bed7", "GOOD": "#b1d6c7"},
    "White": {"BG": "#f5f7fa", "PANEL": "#ffffff", "PANEL_DARK": "#edf1f5",
              "BORDER": "#ced7e1", "TEXT": "#243244", "MUTED": "#5e6c7c",
              "ACCENT": "#465d77", "GOOD": "#28725a"},
}
BG, PANEL, PANEL_DARK, BORDER, TEXT, MUTED, ACCENT, GOOD = PALETTES["Dark"].values()


def activate_palette(theme):
    global BG, PANEL, PANEL_DARK, BORDER, TEXT, MUTED, ACCENT, GOOD
    BG, PANEL, PANEL_DARK, BORDER, TEXT, MUTED, ACCENT, GOOD = PALETTES[theme].values()

POLL_MS = 750
MAX_AUTO_WINDOWS = 12
FONT_SETTINGS = Path(__file__).resolve().parent / ".dashboard_font.json"

def load_font_size():
    try:
        return max(10, min(24, int(json.loads(FONT_SETTINGS.read_text()).get("font_size", 15))))
    except (OSError, ValueError, TypeError, AttributeError):
        return 15



class LogTail:
    """Incrementally follow a rotating log; do not touch the database."""
    def __init__(self, path=LOG):
        self.path = Path(path)
        self.key = None
        self.offset = 0

    def fetch(self):
        try:
            stat = self.path.stat()
            key = (stat.st_dev, stat.st_ino)
            if key != self.key or stat.st_size < self.offset:
                self.key, self.offset = key, 0
            with self.path.open("rb") as f:
                f.seek(self.offset)
                chunk = f.read()
            end = chunk.rfind(b"\n")
            if end == -1:
                return []
            self.offset += end + 1
            return [x.decode("utf-8", errors="replace") for x in chunk[:end+1].splitlines()]
        except (FileNotFoundError, PermissionError):
            return []


def modern_button(parent, text, command, kind="normal", **pack_options):
    colors = {
        "normal": ("#344355", "#e8edf4"),
        "accent": ("#4a637f", "#f5f7fa"),
        "warning": ("#66594f", "#f5f1ed"),
        "danger": ("#6a5157", "#fff3f4"),
        "shutdown": ("#59434c", "#fff3f4"),
    }
    if BG == PALETTES["White"]["BG"]:
        colors = {
            "normal": ("#e3eaf2", "#243244"),
            "accent": ("#cbd9e8", "#243244"),
            "warning": ("#eee6db", "#564735"),
            "danger": ("#f0dfe1", "#704047"),
            "shutdown": ("#eadde2", "#683c4b"),
        }
    bg, fg = colors[kind]
    btn = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                    activebackground=("#b9cadc" if BG == PALETTES["White"]["BG"] else "#53667c"), activeforeground=fg,
                    font=("Segoe UI", 10, "bold"), padx=13, pady=9,
                    relief="flat", bd=0, cursor="hand2", highlightthickness=0)
    btn._theme_button_kind = kind
    btn.pack(**pack_options)
    return btn


def create_log_area(parent, font_size=15):
    box = tk.Frame(parent, bg=PANEL_DARK, highlightbackground=BORDER, highlightthickness=1, bd=0)
    box.pack(fill="both", expand=True, padx=12, pady=(4, 12))
    scroll = tk.Scrollbar(box)
    scroll.pack(side="right", fill="y")
    output = tk.Text(box, wrap="word", bg=PANEL_DARK, fg=TEXT, font=("Consolas", font_size),
                     relief="flat", state="disabled", yscrollcommand=scroll.set, padx=8, pady=8)
    output.pack(side="left", fill="both", expand=True)
    scroll.config(command=output.yview)
    light = BG == PALETTES["White"]["BG"]
    log_colors = ({"voice": "#315e80", "reward": "#28725a", "error": "#a13f52", "event": "#8a651b"}
                  if light else {"voice": "#a9c4d9", "reward": "#b4d8c2", "error": "#e2aeb4", "event": "#d9c9ac"})
    for tag, color in log_colors.items():
        output.tag_config(tag, foreground=color)
    return output


def text_tag(line):
    s = line.upper()
    if "ERROR" in s or "FAILED" in s or "EXCEPTION" in s:
        return "error"
    if "VOICE" in s:
        return "voice"
    if "REWARD" in s or "BONUS" in s:
        return "reward"
    if "EVENT" in s or "MULTIPLIER" in s:
        return "event"
    return None


def refresh_text(widget, lines):
    # Repaint only if log content changed; showing a bounded recent history.
    snapshot = tuple(lines)
    if getattr(widget, "_snapshot", None) == snapshot:
        return
    widget._snapshot = snapshot
    at_bottom = widget.yview()[1] >= 0.98
    widget.config(state="normal")
    widget.delete("1.0", "end")
    for line in snapshot:
        widget.insert("end", line + "\n", text_tag(line))
    widget.config(state="disabled")
    if at_bottom:
        widget.see("end")


class Dashboard:
    def __init__(self):
        self.preferences = load_dashboard_preferences(ROOT)
        activate_palette(self.preferences["theme"])
        self.root = tk.Tk()
        self.root.title("Activity Rewards — All Guilds")
        self.root.geometry("1220x790+40+40")
        self.root.minsize(950, 620)
        self.root.configure(bg=BG)
        self.font_size = load_font_size()
        self.accent_labels = []
        self.auto_opened = set()
        self.log_widgets = []
        self.guilds = {}
        self.recent = deque(maxlen=MAX_LINES)
        self.windows = {}
        self.summary_widgets = {}
        self.detail_tabs = {}
        self.analytics_canvases = {}
        self.analytics_ranges = {}
        self.analytics_summaries = {}
        self._analytics_last_draw = {}
        self.tail = LogTail()
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.configure_table_styles()
        overview = self.root
        tk.Label(overview, text="ACTIVITY REWARDS   /   CONTROL CENTER", bg=BG, fg=TEXT, font=("Segoe UI", self.font_size + 4, "bold")).pack(anchor="w", padx=20, pady=(20, 5))
        tk.Label(overview, text="MULTI-GUILD MONITOR    •    Open a guild when you need its details", bg=BG, fg=MUTED, font=("Segoe UI", self.font_size - 1)).pack(anchor="w", padx=20, pady=(0, 16))
        toolbar = tk.Frame(overview, bg=BG)
        toolbar.pack(fill="x", padx=20, pady=(0, 12))
        modern_button(toolbar, "Open selected guild", self.open_selected, side="left", padx=(0, 8))
        modern_button(toolbar, "Open all guild windows", self.open_all, side="left", padx=(0, 8))
        modern_button(toolbar, "Refresh Now", self.refresh_now, "accent", side="left", padx=(0, 8))
        modern_button(toolbar, "A+", lambda: self.change_font(1), side="left", padx=4)
        modern_button(toolbar, "A−", lambda: self.change_font(-1), side="left", padx=4)
        modern_button(toolbar, "Restart Bot", self.restart_bot, "warning", side="left", padx=(22, 6))
        modern_button(toolbar, "Stop Bot", self.stop_bot, "danger", side="left", padx=4)
        modern_button(toolbar, "Shutdown All", self.shutdown_all, "shutdown", side="left", padx=4)
        self.health = tk.Label(overview, text="Health: checking...", bg=BG, fg=TEXT, font=("DejaVu Sans", self.font_size))
        self.health.pack(anchor="w", padx=20, pady=(0, 5))
        self.system_usage = tk.Label(overview, text="SYSTEM USAGE  |  Loading...", bg=BG, fg=ACCENT,
                                     font=("Segoe UI", 11, "bold"), anchor="w")
        self.system_usage.pack(fill="x", padx=20, pady=(0, 4))
        self.discord_connection = tk.Label(overview, text="DISCORD  |  Waiting for connection status...",
                                           bg=BG, fg=ACCENT, font=("Segoe UI", 10, "bold"), anchor="w")
        self.discord_connection.pack(fill="x", padx=20, pady=(0, 12))
        self.accent_labels.append(self.discord_connection)
        self.accent_labels.append(self.system_usage)
        self._last_usage_refresh = 0
        self._last_resource_alert = {'cpu': 0, 'ram': 0}
        alerts_bar = tk.Frame(overview, bg=BG)
        alerts_bar.pack(fill='x', padx=20, pady=(0, 10))
        self.alert_badge = tk.Label(alerts_bar, text='ALERTS  |  None yet', bg=BG, fg=ACCENT, font=('Segoe UI', 10, 'bold'))
        self.alert_badge.pack(side='left')
        self.accent_labels.append(self.alert_badge)
        modern_button(alerts_bar, 'View Alerts', self.show_alerts, side='right', padx=(8, 0))
        modern_button(alerts_bar, 'Alert Settings', self.show_alert_settings, side='right')
        modern_button(alerts_bar, 'Settings', self.show_dashboard_settings, side='right', padx=(0, 8))
        modern_button(alerts_bar, 'Backup Center', self.show_backup_center, side='right', padx=(0, 8))
        self._last_alert_count = 0

        self.root.bind_all("<Control-plus>", lambda e: self.change_font(1))
        self.root.bind_all("<Control-equal>", lambda e: self.change_font(1))
        self.root.bind_all("<Control-minus>", lambda e: self.change_font(-1))
        self.status = tk.Label(toolbar, text="Waiting for guild logs…", bg=BG, fg=GOOD)
        self.status.pack(side="right")
        self.table = ttk.Treeview(overview, columns=("name", "members", "voice", "status"), show="headings", height=6, style="Guild.Treeview")
        for col, label, width in [("name", "Guild", 375), ("members", "Members", 90), ("voice", "In voice", 90), ("status", "Status", 145)]:
            self.table.heading(col, text=label)
            self.table.column(col, width=width, anchor="w")
        self.table.pack(fill="x", padx=20, pady=(0, 14))
        self.table.bind("<Double-1>", lambda e: self.open_selected())
        tk.Label(overview, text="RECENT ACTIVITY   /   ALL GUILDS", bg=BG, fg=ACCENT, font=("Segoe UI", self.font_size, "bold")).pack(anchor="w", padx=20, pady=(0, 6))
        self.global_log = create_log_area(overview, self.font_size)
        self.log_widgets.append(self.global_log)
        self.apply_accent()
        self.root.after(self.poll_delay_ms(), self.tick)

    def configure_table_styles(self):
        light = self.preferences["theme"] == "White"
        selected = "#d9e4f0" if light else "#566d86"
        heading = "#dce5ee" if light else "#344456"
        hover = "#c8d7e7" if light else "#40566c"
        self.style.configure("Guild.Treeview", background=PANEL, foreground=TEXT, fieldbackground=PANEL,
                             rowheight=self.font_size + 16, font=("DejaVu Sans", self.font_size))
        self.style.map("Guild.Treeview", background=[("selected", selected)], foreground=[("selected", TEXT)])
        self.style.configure("Guild.Treeview.Heading", background=heading, foreground=TEXT,
                             font=("Segoe UI", self.font_size, "bold"), padding=(8, 10))
        # Explicit hover/pressed colors avoid white-on-white on Windows.
        self.style.map("Guild.Treeview.Heading",
                       background=[("pressed", hover), ("active", hover), ("!disabled", heading)],
                       foreground=[("pressed", TEXT), ("active", TEXT), ("!disabled", TEXT)])
        self.style.configure("TNotebook", background=BG, borderwidth=0)
        self.style.configure("TNotebook.Tab", background=heading, foreground=TEXT,
                             font=("Segoe UI", 11, "bold"), padding=(18, 9))
        self.style.map("TNotebook.Tab", background=[("selected", selected), ("active", hover)],
                       foreground=[("selected", TEXT), ("active", TEXT)])

    def apply_theme(self, previous_theme):
        """Repaint widgets once, without cascading palette conversions.

        A previous implementation traversed the root *and* its children a
        second time.  In White Mode, the second pass converted the new pale
        background into a dark text color, creating unreadable dark bars.
        """
        old_palette = PALETTES[previous_theme]
        new_palette = PALETTES[self.preferences["theme"]]
        activate_palette(self.preferences["theme"])
        translations = {old_palette[key]: new_palette[key] for key in old_palette}
        # One pass over the complete widget tree: root owns all Toplevels.
        self._recolor_widget(self.root, translations)
        self.configure_table_styles()
        self.apply_accent()
        for gid in tuple(self.analytics_canvases):
            self._analytics_last_draw.pop(gid, None)
            self.draw_analytics(gid)

    def _recolor_widget(self, widget, translations):
        try:
            if isinstance(widget, tk.Button) and hasattr(widget, "_theme_button_kind"):
                # Button colors are semantic, not palette colors. Explicitly
                # repaint them to avoid foreground/background collisions.
                kind = widget._theme_button_kind
                light = self.preferences["theme"] == "White"
                scheme = ({
                    "normal": ("#e3eaf2", "#243244"),
                    "accent": ("#cbd9e8", "#243244"),
                    "warning": ("#eee6db", "#564735"),
                    "danger": ("#f0dfe1", "#704047"),
                    "shutdown": ("#eadde2", "#683c4b"),
                } if light else {
                    "normal": ("#344355", "#e8edf4"),
                    "accent": ("#4a637f", "#f5f7fa"),
                    "warning": ("#66594f", "#f5f1ed"),
                    "danger": ("#6a5157", "#fff3f4"),
                    "shutdown": ("#59434c", "#fff3f4"),
                })
                bg, fg = scheme[kind]
                widget.configure(bg=bg, fg=fg,
                                 activebackground=("#b9cadc" if light else "#53667c"),
                                 activeforeground=fg)
            else:
                available = widget.configure()
                for key in ("background", "foreground", "activebackground", "activeforeground",
                            "highlightbackground", "selectcolor", "insertbackground"):
                    if key in available:
                        current = str(widget.cget(key)).lower()
                        if current in translations:
                            widget.configure(**{key: translations[current]})
            if isinstance(widget, tk.Text):
                light = self.preferences["theme"] == "White"
                colors = ({"voice": "#315e80", "reward": "#28725a",
                           "error": "#a13f52", "event": "#8a651b"}
                          if light else {"voice": "#a9c4d9", "reward": "#b4d8c2",
                                         "error": "#e2aeb4", "event": "#d9c9ac"})
                for tag, color in colors.items():
                    widget.tag_configure(tag, foreground=color)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._recolor_widget(child, translations)

    def poll_delay_ms(self):
        return self.preferences["refresh_seconds"] * 1000

    def apply_accent(self):
        color = ACCENT
        for label in list(self.accent_labels):
            try:
                if label.winfo_exists():
                    label.configure(fg=color)
            except tk.TclError:
                pass
        self.style.configure("TNotebook.Tab", foreground=TEXT)

    def show_dashboard_settings(self):
        popup = tk.Toplevel(self.root)
        popup.title("Activity Rewards — Settings")
        popup.geometry("640x440")
        popup.minsize(500, 360)
        popup.resizable(True, True)
        popup.configure(bg=BG)
        popup.transient(self.root)
        tk.Label(popup, text="SETTINGS", bg=BG, fg=TEXT,
                 font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=22, pady=(20, 10))
        tabs = ttk.Notebook(popup)
        general_tab = tk.Frame(tabs, bg=BG)
        updates_tab = tk.Frame(tabs, bg=BG)
        tabs.add(general_tab, text="General")
        tabs.add(updates_tab, text="Updates")
        tabs.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        refresh = tk.StringVar(value=str(self.preferences["refresh_seconds"]))
        theme = tk.StringVar(value=self.preferences["theme"])
        startup = tk.StringVar(value=self.preferences["startup_windows"])
        for label, variable, choices in (
            ("Refresh interval (seconds)", refresh, tuple(str(x) for x in REFRESH_CHOICES)),
            ("Theme", theme, THEMES),
            ("Guild windows on next startup", startup, STARTUP_CHOICES),
        ):
            row = tk.Frame(general_tab, bg=BG)
            row.pack(fill="x", padx=12, pady=7)
            tk.Label(row, text=label, bg=BG, fg=TEXT, font=("Segoe UI", 10)).pack(side="left")
            menu = ttk.Combobox(row, textvariable=variable, values=choices, state="readonly", width=20)
            menu.pack(side="right")
        tk.Label(general_tab, text="Theme and refresh changes apply now. Startup applies next launch.",
                 bg=BG, fg=MUTED, wraplength=480, justify="left", anchor="w").pack(fill="x", padx=12, pady=12)

        def save():
            try:
                previous_theme = self.preferences["theme"]
                self.preferences = save_dashboard_preferences(ROOT, {
                    "refresh_seconds": refresh.get(), "theme": theme.get(),
                    "startup_windows": startup.get(),
                })
            except OSError as exc:
                messagebox.showerror("Preferences not saved", str(exc), parent=popup)
                return
            if previous_theme != self.preferences["theme"]:
                self.apply_theme(previous_theme)
            else:
                self.apply_accent()
            popup.destroy()

        modern_button(general_tab, "Save Preferences", save, "accent", side="left", padx=12, pady=8)
        self.build_updates_tab(updates_tab)

    def build_updates_tab(self, tab):
        """Create operator-controlled update controls."""
        tk.Label(tab, text="UPDATES", bg=BG, fg=TEXT,
                 font=("Segoe UI", self.font_size + 2, "bold")).pack(anchor="w", padx=16, pady=(18, 7))
        tk.Label(tab, text=f"Installed version: {current_version(ROOT)}", bg=BG, fg=TEXT,
                 font=("Segoe UI", 11)).pack(anchor="w", padx=16, pady=(0, 8))
        self.update_help = tk.Label(
            tab,
            text="Update this Git clone from its configured GitHub remote. The action only changes tracked source files, then installs requirements such as davey. Tokens, data, backups, logs, and preferences remain local.",
            bg=BG, fg=MUTED, justify="left", anchor="w", wraplength=480,
        )
        self.update_help.pack(fill="x", anchor="w", padx=16, pady=(0, 16))
        self.update_status = tk.Label(tab, text="Update feed not configured.", bg=BG, fg=ACCENT,
                                      font=("Segoe UI", 11, "bold"), justify="left", anchor="w", wraplength=480)
        self.update_status.pack(fill="x", anchor="w", padx=16, pady=(0, 16))
        self.accent_labels.append(self.update_status)
        modern_button(tab, "Update from GitHub", self.update_from_github, "accent", anchor="w", padx=16, pady=6)
        modern_button(tab, "Check Release Feed", self.check_for_updates, "normal", anchor="w", padx=16, pady=6)
        tab.bind("<Configure>", self.resize_update_text, add="+")

    def resize_update_text(self, event):
        """Keep update messages visible as the Settings window is resized."""
        wraplength = max(300, event.width - 36)
        for label in (getattr(self, "update_help", None), getattr(self, "update_status", None)):
            if label is not None:
                label.configure(wraplength=wraplength)

    def check_for_updates(self):
        manifest_url = os.environ.get("ACTIVITY_REWARDS_UPDATE_MANIFEST_URL", "").strip()
        if not manifest_url:
            self.update_status.configure(
                text="No update feed is configured yet. Set ACTIVITY_REWARDS_UPDATE_MANIFEST_URL after publishing a verified GitHub Release.",
            )
            return
        try:
            release = check_for_update(ROOT, manifest_url)
        except (OSError, ValueError) as exc:
            self.update_status.configure(text=f"Update check failed: {exc}")
            return
        if release is None:
            self.update_status.configure(text="You are running the latest available version.")
        else:
            self.update_status.configure(text=f"Version {release.version} is available and ready to be staged after confirmation.")

    def update_from_github(self):
        if not messagebox.askyesno(
            "Update from GitHub",
            "Download the latest tracked code from this installation's configured GitHub remote and install its Python requirements?\n\n"
            "After a successful update, Activity Rewards will restart automatically. Local token, data, backups, logs, and preferences are not changed.",
            parent=self.root,
        ):
            return
        self.update_status.configure(text="Updating source and requirements from GitHub…")
        self.root.update_idletasks()
        try:
            result = update_from_git(ROOT)
        except (OSError, RuntimeError) as exc:
            self.update_status.configure(text=f"GitHub update failed: {exc}")
            return
        if not result.updated:
            self.update_status.configure(text=result.details)
            return
        helper = ROOT / 'restart_after_update.py'
        if not helper.is_file():
            self.update_status.configure(
                text='GitHub source updated, but the restart helper is missing. Use Restart Bot once, then try again.'
            )
            return
        try:
            flags = 0
            if os.name == 'nt':
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            subprocess.Popen(
                [sys.executable, str(helper)], cwd=ROOT,
                creationflags=flags, close_fds=(os.name != 'nt'),
            )
            request_action('shutdown')
        except (OSError, RuntimeError, ValueError) as exc:
            self.update_status.configure(
                text=f'GitHub source updated, but automatic restart could not start: {exc}. Use Restart Bot.'
            )
            return
        self.update_status.configure('Update installed. Restarting Activity Rewards…')

    def refresh_now(self):
        """Refresh all guild data immediately without changing the polling schedule."""
        self.tick(schedule_next=False)

    def refresh_guild_now(self, guild_id):
        """Update data in one guild window and refresh the overall view."""
        self.tick(schedule_next=False)
        if guild_id in self.windows:
            self.update_guild(guild_id)

    def restart_bot(self):
        if not messagebox.askyesno("Restart Activity Rewards", "Restart the Discord bot for ALL servers?\nThe dashboard will stay open.", parent=self.root):
            return
        self.send_control("restart")

    def stop_bot(self):
        if not messagebox.askyesno("Stop Activity Rewards", "Stop the Discord bot for ALL servers?\nVoice tracking and rewards will pause until you restart it.", parent=self.root):
            return
        self.send_control("stop")

    def shutdown_all(self):
        if not messagebox.askyesno(
            "Shutdown Activity Rewards",
            "Stop the Discord bot AND close all monitoring windows?\n"
            "All connected servers will go offline until you start the bot again.",
            parent=self.root,
        ):
            return
        self.send_control("shutdown")

    def send_control(self, action):
        try:
            request_action(action)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Bot control unavailable", str(exc), parent=self.root)

    def update_health(self):
        status = supervisor_db.get_status(ROOT)
        current = time.time()
        supervisor_age = current - float(status.get('timestamp', 0))
        supervisor_ok = -2 <= supervisor_age <= HEARTBEAT_TIMEOUT_SECONDS
        if not supervisor_ok:
            state = "Supervisor unavailable — run START_BOT.bat"
        elif status.get('stopped_by_user'):
            state = "Bot stopped by operator (Restart Bot to resume)"
        elif status.get('running'):
            state = "Bot process running (Discord connection shown per guild)"
        else:
            state = f"Bot recovery pending ({status.get('retry_seconds', 0)}s)"
        backup_dir = ROOT / 'backups'
        try:
            backups = [x for x in backup_dir.rglob('*') if x.is_file()]
            newest = max((x.stat().st_mtime for x in backups), default=None)
            age = f"{int((current - newest)//3600)}h ago" if newest else "none found"
        except OSError:
            age = "unavailable"
        try:
            free_gb = shutil.disk_usage(ROOT).free / 1024**3
            disk = f"{free_gb:.1f} GB free"
        except OSError:
            disk = "unavailable"
        recovery = f"  |  Unexpected exits: {status.get('restart_count', 0)}"
        self.health.config(text=f"HEALTH  |  {state}{recovery}   |   Last backup: {age}   |   Disk: {disk}")
        try:
            monitor = read_json(ROOT / 'logs' / 'dashboard_status.json') or {}
            conn = monitor.get('discord_connection')
            state_text, latency, reconnects = display_status(
                conn, monitor.get('timestamp'), supervisor_ok and bool(status.get('running')), now=current)
            latency_text = f'{latency} ms' if latency is not None else 'N/A'
            self.discord_connection.config(text=(
                f'DISCORD  |  {state_text}   •   Gateway latency: {latency_text}   •   '
                f'Reconnect events: {reconnects}'))
        except (OSError, ValueError, TypeError):
            self.discord_connection.config(text='DISCORD  |  Connection data unavailable')
        alerts = recent_alerts(ROOT, 5)
        self.alert_badge.config(text=f'ALERTS  |  {len(alerts)} recent' if alerts else 'ALERTS  |  None yet')
        # Resource monitoring is optional, read-only, and throttled independently
        # from the faster log refresh cycle.
        if current - self._last_usage_refresh >= 3:
            self._last_usage_refresh = current
            self.update_system_usage(status, disk)

    def update_system_usage(self, status, disk):
        """Update host and bot resource usage without changing Discord data."""
        if psutil is None:
            self.system_usage.config(text="SYSTEM USAGE  |  Install psutil for CPU/RAM: python -m pip install psutil")
            return
        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            pid = status.get("pid")
            bot_text = "Bot: offline"
            if pid is not None and status.get("running"):
                try:
                    proc = psutil.Process(int(pid))
                    bot_mb = proc.memory_info().rss / (1024 ** 2)
                    bot_text = f"Bot RAM: {bot_mb:.0f} MB"
                except (ValueError, TypeError, psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    bot_text = "Bot RAM: unavailable"
            # Rate-limit threshold warnings; never alert every refresh cycle.
            for key, value, threshold in (('cpu', cpu, 90), ('ram', mem.percent, 90)):
                if value >= threshold and time.time() - self._last_resource_alert[key] >= 900:
                    if add_alert(ROOT, 'resources', f'High {key.upper()} usage: {value:.0f}% (threshold {threshold}%)'):
                        self._last_resource_alert[key] = time.time()
            self.system_usage.config(text=(
                f"SYSTEM USAGE  |  CPU: {cpu:.0f}%  |  RAM: {mem.percent:.0f}% "
                f"({mem.used / (1024 ** 3):.1f}/{mem.total / (1024 ** 3):.1f} GB)  |  "
                f"{bot_text}  |  Disk: {disk}"
            ))
        except (OSError, ValueError, AttributeError):
            self.system_usage.config(text="SYSTEM USAGE  |  Metrics temporarily unavailable")

    def show_alerts(self):
        popup = tk.Toplevel(self.root)
        popup.title('Activity Rewards — Notification Center')
        popup.geometry('870x530')
        popup.configure(bg=BG)
        tk.Label(popup, text='NOTIFICATION CENTER', bg=BG, fg=TEXT, font=('Segoe UI', 16, 'bold')).pack(anchor='w', padx=18, pady=(18, 5))
        tk.Label(popup, text='Operational alerts only — no guild scores or settings are changed.', bg=BG, fg=MUTED).pack(anchor='w', padx=18, pady=(0, 10))
        box = create_log_area(popup, 11)
        def refresh():
            entries = recent_alerts(ROOT, 150)
            lines = [time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(row.get('time', 0)))) +
                     '  [' + str(row.get('severity', 'warning')).upper() + '] ' +
                     str(row.get('message', '')) for row in reversed(entries)]
            refresh_text(box, lines or ['No alerts recorded yet.'])
        modern_button(popup, 'Refresh Alerts', refresh, 'accent', padx=18, pady=(0, 12), anchor='w')
        refresh()

    def show_alert_settings(self):
        popup = tk.Toplevel(self.root)
        popup.title('Activity Rewards — Alert Settings')
        popup.geometry('510x340')
        popup.configure(bg=BG)
        tk.Label(popup, text='ALERT SETTINGS', bg=BG, fg=TEXT, font=('Segoe UI', 16, 'bold')).pack(anchor='w', padx=18, pady=(18, 12))
        options = alert_settings(ROOT)
        labels = {'recovery': 'Automatic recovery / crash alerts', 'reward_failures': 'Reward failure alerts',
                  'discord': 'Discord connection warnings', 'resources': 'High CPU / RAM warnings (90%)'}
        vars_ = {}
        for key in DEFAULTS:
            var = tk.BooleanVar(value=options[key])
            vars_[key] = var
            tk.Checkbutton(popup, text=labels[key], variable=var, bg=BG, fg=TEXT,
                           selectcolor=PANEL, activebackground=BG, activeforeground=TEXT,
                           font=('Segoe UI', 11)).pack(anchor='w', padx=18, pady=5)
        def save():
            try:
                save_settings(ROOT, {k: v.get() for k, v in vars_.items()})
            except OSError as exc:
                messagebox.showerror('Save failed', str(exc), parent=popup)
                return
            popup.destroy()
        modern_button(popup, 'Save Settings', save, 'accent', padx=18, pady=10, anchor='w')

    def show_backup_center(self):
        popup = tk.Toplevel(self.root)
        popup.title('Activity Rewards — Backup & Recovery Center')
        popup.geometry('860x590')
        popup.configure(bg=BG)
        tk.Label(popup, text='BACKUP & RECOVERY CENTER', bg=BG, fg=TEXT,
                 font=('Segoe UI', 16, 'bold')).pack(anchor='w', padx=18, pady=(16, 6))
        tk.Label(popup, text='Per-guild backups • No automatic overwrite • Stop Bot before restoring',
                 bg=BG, fg=MUTED).pack(anchor='w', padx=18, pady=(0, 12))
        controls = tk.Frame(popup, bg=BG)
        controls.pack(fill='x', padx=18, pady=6)
        tk.Label(controls, text='Guild:', bg=BG, fg=TEXT).pack(side='left')
        ids = guild_ids(ROOT)
        # Show names as the primary UI identity; preserve IDs internally.
        status_names = {}
        try:
            status = read_json(ROOT / 'logs' / 'dashboard_status.json') or {}
            status_names = status.get('guilds', {}) or {}
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        names = {}
        for gid in ids:
            name = str(self.guilds.get(gid, {}).get('name', '')).strip()
            if not name or name == 'Guild ' + gid:
                name = str(status_names.get(gid, {}).get('name', '')).strip()
            names[gid] = name if name and name != 'Guild ' + gid else f'Guild {gid}'
        counts = Counter(names.values())
        labels = {gid: (f'{name} ({gid})' if counts[name] > 1 else name)
                  for gid, name in names.items()}
        lookup = {label: gid for gid, label in labels.items()}
        choice = tk.StringVar(value=labels[ids[0]] if ids else '')
        def chosen_guild_id():
            return lookup.get(choice.get(), '')
        guild_combo = ttk.Combobox(controls, textvariable=choice, values=list(lookup), state='readonly', width=42)
        guild_combo.pack(side='left', padx=10)
        tk.Label(popup, text='Available backup folders (newest first)', bg=BG, fg=TEXT).pack(anchor='w', padx=18)
        panel = tk.Frame(popup, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        panel.pack(fill='both', expand=True, padx=18, pady=8)
        listbox = tk.Listbox(panel, bg=PANEL_DARK, fg=TEXT, selectbackground=BORDER,
                             selectforeground=TEXT, font=('Consolas', 11), relief='flat')
        listbox.pack(fill='both', expand=True, padx=5, pady=5)
        info = tk.StringVar(value='Select a guild to view its backups.')
        tk.Label(popup, textvariable=info, bg=BG, fg=MUTED, wraplength=805,
                 justify='left').pack(anchor='w', padx=18, pady=6)
        current = []

        def refresh():
            nonlocal current
            current = backup_folders(ROOT, chosen_guild_id()) if chosen_guild_id() else []
            listbox.delete(0, 'end')
            for folder in current:
                listbox.insert('end', folder.name)
            info.set(f'{len(current)} backup(s) found. Scheduled backups continue using your existing guild configuration.')

        def selected():
            selection = listbox.curselection()
            if not selection:
                raise ValueError('Select a backup from the list first.')
            return current[selection[0]]

        def inspect():
            try:
                folder = selected()
                files = validate(folder)
                info.set('VALID: ' + folder.name + ' | Files: ' + ', '.join(files))
            except (OSError, ValueError, json.JSONDecodeError) as error:
                messagebox.showerror('Backup validation', str(error), parent=popup)

        def make_backup():
            try:
                if not chosen_guild_id():
                    raise ValueError('No guild data found.')
                folder = create_manual(ROOT, chosen_guild_id())
                refresh()
                info.set('Created: ' + folder.name + ' (original files untouched)')
            except (OSError, ValueError) as error:
                messagebox.showerror('Backup failed', str(error), parent=popup)

        def restore():
            try:
                folder = selected()
                files = validate(folder)
                from bot_supervisor import get_live_supervisor_status
                status = get_live_supervisor_status(ROOT)
                if status.get('running') or not status.get('stopped_by_user'):
                    raise RuntimeError('Click Stop Bot and wait until Health shows stopped before restoring.')
                guild = chosen_guild_id()
                if not messagebox.askyesno('Restore backup?',
                    f'Restore {labels.get(guild, guild)} from {folder.name}?\n\n'
                    'A separate pre-restore safety backup will be created. '
                    'Current guild files listed in this backup will be replaced. '
                    'Other guilds remain unchanged.', parent=popup):
                    return
                # Enter the exact guild ID to avoid accidental destructive clicks.
                from tkinter import simpledialog
                confirmation = simpledialog.askstring('Confirm guild ID',
                    f'Type guild ID {guild} to confirm restoring {len(files)} files:', parent=popup)
                if confirmation != guild:
                    info.set('Restore cancelled; guild ID not confirmed.')
                    return
                safety, restored = restore_offline(ROOT, guild, folder, running=False)
                refresh()
                info.set(f'Restored {len(restored)} files. Pre-restore safety copy: {safety.name}. Restart Bot when ready.')
            except (OSError, ValueError, RuntimeError) as error:
                messagebox.showerror('Restore blocked / failed', str(error), parent=popup)

        def remove_selected():
            try:
                folder = selected()
                guild = chosen_guild_id()
                if 'before_restore_DO_NOT_DELETE' in folder.name:
                    raise ValueError('Pre-restore safety backups cannot be deleted here.')
                validate(folder)
                if not messagebox.askyesno('Delete backup permanently?',
                    f'Delete backup {folder.name} for {labels.get(guild, guild)}?\n\n'
                    'This permanently deletes ONLY this selected backup folder. '
                    'Live guild data and other backups will not be changed.', parent=popup):
                    return
                from tkinter import simpledialog
                confirmation = simpledialog.askstring('Confirm backup deletion',
                    f'Type the guild name exactly to delete this selected backup:\n{names[guild]}', parent=popup)
                if confirmation != names[guild]:
                    info.set('Deletion cancelled; guild name did not match.')
                    return
                deleted_name = delete_backup(ROOT, guild, folder)
                refresh()
                info.set('Deleted backup: ' + deleted_name + '. Live guild data was not changed.')
            except (OSError, ValueError) as error:
                messagebox.showerror('Delete blocked / failed', str(error), parent=popup)

        buttons = tk.Frame(popup, bg=BG)
        buttons.pack(fill='x', padx=18, pady=(5, 18))
        modern_button(buttons, 'Refresh List', refresh, side='left', padx=(0, 8))
        modern_button(buttons, 'Validate Selected', inspect, side='left', padx=(0, 8))
        modern_button(buttons, 'Create Backup Now', make_backup, 'accent', side='left', padx=(0, 8))
        modern_button(buttons, 'Restore Selected (Bot Stopped)', restore, 'danger', side='right')
        modern_button(buttons, 'Delete Selected', remove_selected, 'danger', side='right', padx=(0, 8))
        guild_combo.bind('<<ComboboxSelected>>', lambda event: refresh())
        refresh()

    def change_font(self, delta):
        new_size = max(10, min(24, self.font_size + delta))
        if new_size == self.font_size:
            return
        self.font_size = new_size
        for widget in self.log_widgets:
            try:
                if widget.winfo_exists():
                    widget.configure(font=("Consolas", new_size))
            except tk.TclError:
                pass
        self.style.configure("Guild.Treeview", font=("DejaVu Sans", new_size), rowheight=new_size + 16)
        self.style.configure("Guild.Treeview.Heading", font=("DejaVu Sans", new_size, "bold"))
        for gid, (win, label, detail, output) in self.windows.items():
            label.configure(font=("DejaVu Sans", new_size + 1, "bold"))
            detail.configure(font=("DejaVu Sans", new_size))
            if gid in self.summary_widgets:
                self.summary_widgets[gid].configure(font=("Consolas", new_size))
        try:
            FONT_SETTINGS.write_text(json.dumps({"font_size": new_size}) + "\n")
        except OSError:
            pass

    def open_selected(self):
        for guild_id in self.table.selection():
            self.open_guild(guild_id)

    def open_all(self):
        for gid in sorted(self.guilds):
            self.open_guild(gid)

    def open_guild(self, gid):
        if gid not in self.guilds:
            return
        if gid in self.windows:
            try:
                self.windows[gid][0].deiconify()
                self.windows[gid][0].lift()
                return
            except tk.TclError:
                del self.windows[gid]
        index = len(self.windows)
        win = tk.Toplevel(self.root)
        win.geometry(f"1010x780+{130+(index%4)*45}+{100+(index%4)*45}")
        win.configure(bg=BG)
        label = tk.Label(win, bg=BG, fg=GOOD, font=("DejaVu Sans", self.font_size + 1, "bold"))
        label.pack(anchor="w", padx=14, pady=(15, 5))
        detail = tk.Label(win, bg=BG, fg=MUTED, font=("DejaVu Sans", self.font_size))
        detail.pack(anchor="w", padx=14, pady=(0, 10))
        modern_button(win, "Refresh Now", lambda guild_id=gid: self.refresh_guild_now(guild_id), "accent",
                      anchor="w", padx=14, pady=(0, 10))
        summary = tk.Label(win, bg=BG, fg=TEXT, justify="left", anchor="w", font=("Consolas", self.font_size), text="Loading metrics…")
        summary.pack(fill="x", padx=14, pady=(0, 8))
        self.summary_widgets[gid] = summary
        tabs = ttk.Notebook(win)
        tabs.pack(fill='both', expand=True, padx=12, pady=(0, 8))
        voice_page = tk.Frame(tabs, bg=BG)
        rewards_page = tk.Frame(tabs, bg=BG)
        analytics_page = tk.Frame(tabs, bg=BG)
        setup_page = tk.Frame(tabs, bg=BG)
        tabs.add(voice_page, text='Live Voice')
        tabs.add(rewards_page, text='Rewards')
        tabs.add(analytics_page, text='Analytics')
        tabs.add(setup_page, text='Setup')
        self.build_guild_setup_tab(setup_page, gid)
        tk.Label(analytics_page, text='VOICE & MEMBER HISTORY', bg=BG, fg=ACCENT, font=('Segoe UI', 11, 'bold')).pack(anchor='w', padx=14, pady=(12, 3))
        analytics_toolbar = tk.Frame(analytics_page, bg=BG)
        analytics_toolbar.pack(fill='x', padx=14, pady=(4, 5))
        tk.Label(analytics_toolbar, text='Period:', bg=BG, fg=TEXT).pack(side='left', padx=(0, 6))
        period = ttk.Combobox(analytics_toolbar, values=list(RANGE_SECONDS), state='readonly', width=12)
        period.set('24 hours')
        period.pack(side='left', padx=(0, 10))
        self.analytics_ranges[gid] = period
        period.bind('<<ComboboxSelected>>', lambda event, guild_id=gid: self.draw_analytics(guild_id))
        modern_button(analytics_toolbar, 'Export CSV Report', lambda guild_id=gid: self.export_guild_analytics(guild_id), 'accent', side='right')
        summary_label = tk.Label(analytics_page, text='Awaiting samples', bg=BG, fg=TEXT, anchor='w')
        summary_label.pack(fill='x', padx=14, pady=(0, 3))
        self.analytics_summaries[gid] = summary_label
        graph = tk.Canvas(analytics_page, bg=PANEL_DARK, highlightthickness=1, highlightbackground=BORDER, height=190)
        graph.pack(fill='both', expand=True, padx=14, pady=(4, 12))
        self.analytics_canvases[gid] = graph
        graph.bind('<Configure>', lambda event, guild_id=gid: self.draw_analytics(guild_id))
        voice_display = create_log_area(voice_page, self.font_size)
        reward_display = create_log_area(rewards_page, self.font_size)
        self.log_widgets.extend((voice_display, reward_display))
        self.detail_tabs[gid] = (voice_display, reward_display)
        tk.Label(win, text="RECENT ACTIVITY / REWARDS / ERRORS", bg=BG, fg=ACCENT, font=("DejaVu Sans", self.font_size, "bold")).pack(anchor="w", padx=14)
        output = create_log_area(win, self.font_size)
        self.log_widgets.append(output)
        self.windows[gid] = (win, label, detail, output)
        self.accent_labels.append(label)
        win.protocol("WM_DELETE_WINDOW", lambda: self.close_guild(gid))
        self.update_guild(gid)

    def build_guild_setup_tab(self, tab, guild_id):
        """Desktop equivalent of the Discord /setup flow for one guild."""
        config = load_config(guild_id)
        status = setup_status(config)
        canvas = tk.Canvas(tab, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(tab, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y', pady=2)
        canvas.pack(side='left', fill='both', expand=True)
        content = tk.Frame(canvas, bg=BG)
        content_window = canvas.create_window((0, 0), window=content, anchor='nw')

        def sync_scroll_region(event=None):
            canvas.configure(scrollregion=canvas.bbox('all'))

        def fit_content_width(event):
            canvas.itemconfigure(content_window, width=event.width)

        canvas.bind('<Configure>', fit_content_width)
        content.bind('<Configure>', sync_scroll_region)
        canvas.bind('<MouseWheel>', lambda event: canvas.yview_scroll(-int(event.delta / 120), 'units'))

        tk.Label(content, text='GUILD SETUP', bg=BG, fg=TEXT,
                 font=('Segoe UI', self.font_size + 2, 'bold')).pack(anchor='w', padx=16, pady=(14, 2))
        tk.Label(
            content,
            text='Configure this guild locally. The Discord /setup command remains available and uses the same saved settings.',
            bg=BG, fg=MUTED, justify='left', anchor='w', wraplength=900,
        ).pack(fill='x', padx=16, pady=(0, 8))
        status_label = tk.Label(content, bg=BG, fg=ACCENT, anchor='w', font=('Segoe UI', 10, 'bold'))
        status_label.pack(fill='x', padx=16, pady=(0, 10))

        form = tk.Frame(content, bg=BG)
        form.pack(fill='both', expand=True, padx=16, pady=(0, 8))
        left = tk.Frame(form, bg=BG)
        right = tk.Frame(form, bg=BG)
        left.pack(side='left', fill='both', expand=True, padx=(0, 10))
        right.pack(side='left', fill='both', expand=True, padx=(10, 0))

        def section(parent, title):
            box = tk.Frame(parent, bg=PANEL_DARK, highlightthickness=1, highlightbackground=BORDER)
            box.pack(fill='x', pady=(0, 10))
            tk.Label(box, text=title, bg=PANEL_DARK, fg=ACCENT,
                     font=('Segoe UI', 10, 'bold')).pack(anchor='w', padx=10, pady=(8, 3))
            return box

        def entry(parent, label, value):
            row = tk.Frame(parent, bg=PANEL_DARK)
            row.pack(fill='x', padx=10, pady=3)
            tk.Label(row, text=label, bg=PANEL_DARK, fg=TEXT, width=21, anchor='w').pack(side='left')
            variable = tk.StringVar(value=str(value))
            tk.Entry(row, textvariable=variable, bg=BG, fg=TEXT, insertbackground=TEXT,
                     relief='flat').pack(side='left', fill='x', expand=True)
            return variable

        def toggle(parent, label, value):
            variable = tk.BooleanVar(value=bool(value))
            tk.Checkbutton(parent, text=label, variable=variable, bg=PANEL_DARK, fg=TEXT,
                           activebackground=PANEL_DARK, activeforeground=TEXT,
                           selectcolor=BG, anchor='w').pack(fill='x', padx=10, pady=2)
            return variable

        branding = section(left, 'BRANDING')
        nickname = entry(branding, 'Bot nickname', config['branding']['bot_nickname'])
        currency_name = entry(branding, 'Currency name', config['branding']['currency_name'])
        currency_emoji = entry(branding, 'Currency emoji', config['branding']['currency_emoji'])
        event_name = entry(branding, 'Event name', config['branding']['event_name'])

        rewards = section(left, 'REWARDS & VOICE')
        interval = entry(rewards, 'Interval minutes', config['rewards']['interval_minutes'])
        points = entry(rewards, 'Points per interval', config['rewards']['points_per_interval'])
        first_bonus = entry(rewards, 'First join bonus', config['rewards']['first_join_bonus'])
        first_bonus_enabled = toggle(rewards, 'Enable first join bonus', config['rewards']['first_join_bonus_enabled'])
        minimum_members = entry(rewards, 'Minimum voice members', config['voice']['minimum_members_in_channel'])
        ignore_afk = toggle(rewards, 'Ignore AFK channel', config['voice']['ignore_afk_channel'])
        ignore_deafened = toggle(rewards, 'Ignore deafened users', config['voice']['ignore_deafened_users'])

        event = section(right, 'EVENT SCHEDULE')
        event_enabled = toggle(event, 'Enable event multiplier', config['event']['enabled'])
        multiplier = entry(event, 'Multiplier', config['event']['multiplier'])
        start_day = entry(event, 'Start day', config['event']['start_day'])
        start_time = entry(event, 'Start time (HH:MM)', config['event']['start_time'])
        end_day = entry(event, 'End day', config['event']['end_day'])
        end_time = entry(event, 'End time (HH:MM)', config['event']['end_time'])

        channels = section(right, 'CHANNELS & ADMIN')
        reward_channel = entry(channels, 'Reward channel ID', config['channels']['reward_channel'])
        announcement_channel = entry(channels, 'Announcement channel ID', config['channels']['announcement_channel'])
        admin_role = entry(channels, 'Admin role ID', config['admin_role_id'])
        timezone = entry(channels, 'Timezone', config['timezone'])

        schedule = section(right, 'WEEKLY & MONTHLY')
        weekly_enabled = toggle(schedule, 'Enable weekly leaderboard', config['weekly_announcement']['enabled'])
        weekly_day = entry(schedule, 'Weekly day', config['weekly_announcement']['day'])
        weekly_time = entry(schedule, 'Weekly time (HH:MM)', config['weekly_announcement']['time'])
        monthly_enabled = toggle(schedule, 'Enable monthly winner', config['monthly']['enabled'])
        monthly_winners = entry(schedule, 'Monthly winners (1 or 3)', config['monthly']['winner_count'])

        def refresh_status(saved=None):
            current = setup_status(saved or load_config(guild_id))
            missing = ', '.join(current['missing']) if current['missing'] else 'none'
            state = 'Complete' if current['complete'] else f"{current['done']}/{current['total']} sections complete"
            status_label.configure(text=f'Setup status: {state} · Missing: {missing}')

        def save_setup():
            try:
                updated = load_config(guild_id)
                updated['branding'].update({
                    'bot_nickname': nickname.get().strip() or 'Activity Rewards',
                    'currency_name': currency_name.get().strip() or 'Points',
                    'currency_emoji': currency_emoji.get().strip() or '⭐',
                    'event_name': event_name.get().strip() or 'Rush Hour',
                })
                updated['rewards'].update({
                    'interval_minutes': interval.get(), 'points_per_interval': points.get(),
                    'first_join_bonus': first_bonus.get(), 'first_join_bonus_enabled': first_bonus_enabled.get(),
                })
                updated['voice'].update({
                    'minimum_members_in_channel': minimum_members.get(), 'ignore_afk_channel': ignore_afk.get(),
                    'ignore_deafened_users': ignore_deafened.get(),
                })
                updated['event'].update({
                    'enabled': event_enabled.get(), 'multiplier': multiplier.get(),
                    'start_day': start_day.get(), 'start_time': start_time.get(),
                    'end_day': end_day.get(), 'end_time': end_time.get(),
                })
                updated['channels'].update({
                    'reward_channel': reward_channel.get() or 0,
                    'announcement_channel': announcement_channel.get() or 0,
                })
                updated['admin_role_id'] = admin_role.get() or 0
                updated['timezone'] = timezone.get().strip() or 'Asia/Beirut'
                updated['weekly_announcement'].update({
                    'enabled': weekly_enabled.get(), 'day': weekly_day.get(), 'time': weekly_time.get(),
                })
                updated['monthly'].update({
                    'enabled': monthly_enabled.get(), 'winner_count': monthly_winners.get(),
                })
                for name in ('branding', 'rewards', 'event', 'voice', 'weekly', 'monthly'):
                    mark_setup_section(updated, name)
                if reward_channel.get().strip() and announcement_channel.get().strip():
                    mark_setup_section(updated, 'channels')
                saved = save_config(guild_id, updated)
            except (OSError, ValueError, TypeError) as exc:
                messagebox.showerror('Setup not saved', str(exc), parent=tab.winfo_toplevel())
                return
            refresh_status(saved)
            messagebox.showinfo('Guild setup saved', 'Guild configuration was saved. The Discord /setup command remains available.', parent=tab.winfo_toplevel())

        refresh_status(config)
        modern_button(content, 'Save Guild Setup', save_setup, 'accent', anchor='w', padx=16, pady=(0, 12))

    def close_guild(self, gid):
        item = self.windows.pop(gid, None)
        if item:
            item[0].destroy()
            self.summary_widgets.pop(gid, None)
            self.detail_tabs.pop(gid, None)
            self.analytics_canvases.pop(gid, None)
            self.analytics_ranges.pop(gid, None)
            self.analytics_summaries.pop(gid, None)
            self._analytics_last_draw.pop(gid, None)

    def update_guild(self, gid, metrics=None):
        if gid not in self.windows:
            return
        item = self.guilds[gid]
        win, label, detail, output = self.windows[gid]
        status = "LEFT SERVER" if item.get("removed") else "KNOWN GUILD"
        win.title(f"Activity Rewards — {item['name']}")
        label.config(text=item['name'])
        if metrics is None:
            metrics = guild_metrics(gid)
        members = metrics['members'] if metrics['members'] is not None else item['members']
        voice = metrics['voice_users'] if metrics['voice_users'] is not None else '—'
        online = 'ONLINE' if metrics['online'] else 'No live heartbeat'
        detail.config(text=f"Members: {members}     In voice: {voice}     {online}")
        top = metrics['top']
        rank = '\n'.join(f"{i}. {name[:26]} — {score} points" for i, (name, score) in enumerate(top, 1)) or "No recorded scores"
        event = f"Event configured: {'enabled' if metrics['event_enabled'] else 'disabled'} | x{metrics['multiplier_configured']} (schedule may be inactive)"
        uptime = f"Bot uptime: {metrics['uptime_seconds']} sec" if metrics['online'] else 'Bot uptime: unavailable'
        schedule = metrics.get('schedule', {})
        if schedule.get('next'):
            remain = schedule.get('seconds_left', 0)
            countdown = f"{'ACTIVE' if schedule['active'] else 'INACTIVE'} x{schedule['multiplier']} · next {'end' if schedule['active'] else 'start'} in {remain // 3600}h {(remain % 3600) // 60}m · {schedule['next']}"
        else:
            countdown = schedule.get('reason', 'Event schedule unavailable')
        self.summary_widgets[gid].config(text=f"{event}    |    {uptime}\nEVENT COUNTDOWN: {countdown}\nTOP 5 MEMBERS\n{rank}")
        if gid in self.detail_tabs:
            voice_display, reward_display = self.detail_tabs[gid]
            now = time.time()
            sessions = metrics.get('sessions', [])
            voice_lines = [f"{v.get('name','Unknown')} | {v.get('channel','Unknown')} | {max(0,int(now - float(v.get('started_at', now)))) // 60} min tracked" for v in sessions]
            if not metrics['online']:
                voice_lines = ['No recent bot heartbeat; live session details unavailable.']
            elif not voice_lines:
                voice_lines = ['No eligible voice sessions currently tracked.']
            refresh_text(voice_display, voice_lines)
            reward_lines = recent_reward_activity(gid)
            refresh_text(reward_display, reward_lines or ['No recent reward delivery entries in available logs.'])
        refresh_text(output, item['lines'])
        if time.monotonic() - self._analytics_last_draw.get(gid, 0) > 10:
            self.draw_analytics(gid)

    def draw_analytics(self, gid):
        canvas = self.analytics_canvases.get(gid)
        if canvas is None or not canvas.winfo_exists():
            return
        self._analytics_last_draw[gid] = time.monotonic()
        canvas.delete("all")
        width, height = max(200, canvas.winfo_width()), max(130, canvas.winfo_height())
        period = self.analytics_ranges[gid].get() if gid in self.analytics_ranges else '24 hours'
        points = load_samples(ROOT, gid, period)
        stats = summarize(points)
        label = self.analytics_summaries.get(gid)
        if label is not None:
            label.config(text=(f"Samples: {stats['samples']}  ·  Avg voice: {stats['avg_voice']:.1f}  ·  "
                               f"Peak voice: {stats['peak_voice']}  ·  Latest members: {stats['latest_members']}"
                               if points else 'No recorded samples in selected period'))
        if not points:
            canvas.create_text(width//2, height//2, text="Collecting history · one sample every 5 minutes", fill=MUTED, font=("Segoe UI", 11))
            return
        left, top, right, bottom = 45, 20, width-18, height-34
        max_voice = max(1, max(row[1] for row in points))
        start = points[0][0]
        end = max(start + 1, points[-1][0])
        for index in range(5):
            y = bottom - (bottom-top) * index / 4
            canvas.create_line(left, y, right, y, fill=BORDER)
            canvas.create_text(left-8, y, text=str(round(max_voice * index / 4)), fill=MUTED, anchor="e", font=("Segoe UI", 9))
        coords = []
        for stamp, count, members in points:
            x = left + (right-left) * (stamp-start) / (end-start)
            y = bottom - (bottom-top) * count / max_voice
            coords.extend((x, y))
        if len(coords) >= 4:
            canvas.create_line(*coords, fill=ACCENT, width=2, smooth=False)
        if coords:
            canvas.create_oval(coords[-2]-3, coords[-1]-3, coords[-2]+3, coords[-1]+3, fill=GOOD, outline="")
        canvas.create_text(left, height-12, text=time.strftime("%H:%M", time.localtime(start)), fill=MUTED, anchor="w")
        canvas.create_text(right, height-12, text=time.strftime("%H:%M", time.localtime(end)), fill=MUTED, anchor="e")
        canvas.create_text(right, top, text=f"Voice now: {points[-1][1]}  ·  Members: {points[-1][2]}", fill=TEXT, anchor="ne")

    def export_guild_analytics(self, gid):
        """Export independent monitoring snapshots; never modify guild data."""
        period = self.analytics_ranges[gid].get() if gid in self.analytics_ranges else '24 hours'
        rows = load_samples(ROOT, gid, period)
        if not rows:
            messagebox.showinfo('Analytics report', 'No analytics samples in this period yet.')
            return
        filename = f'activity_analytics_{gid}_{time.strftime("%Y%m%d")}.csv'
        target = filedialog.asksaveasfilename(parent=self.windows[gid][0], title='Save analytics CSV',
                                              defaultextension='.csv', initialfile=filename,
                                              filetypes=[('CSV report', '*.csv')])
        if not target:
            return
        try:
            export_csv(target, rows, guild_name=self.guilds[gid]['name'], guild_id=gid, period=period)
        except OSError as exc:
            messagebox.showerror('Export failed', str(exc))
        else:
            messagebox.showinfo('Analytics report', f'Saved report to:\n{target}')

    def tick(self, schedule_next=True):
        if supervisor_db.get_status(ROOT).get('shutdown_requested'):
            self.root.destroy()
            return
        new_guild_ids = set()
        for line in self.tail.fetch():
            previous = set(self.guilds)
            parse_line(line, self.guilds, self.recent)
            new_guild_ids.update(set(self.guilds) - previous)
        self.update_health()
        self.status.config(text=f"Guilds seen: {len(self.guilds)} · Windows open: {len(self.windows)}")
        existing = set(self.table.get_children())
        for gid, item in sorted(self.guilds.items(), key=lambda pair: pair[1]['name'].casefold()):
            metrics = guild_metrics(gid)
            if metrics['name'] and item['name'].startswith('Guild '):
                item['name'] = metrics['name']
            members = metrics['members'] if metrics['members'] is not None else item['members']
            voice = metrics['voice_users'] if metrics['voice_users'] is not None else '—'
            state = 'Left server' if item.get('removed') else ('Online' if metrics['online'] else 'Unverified')
            values = (item['name'], members, voice, state)
            if gid in existing:
                self.table.item(gid, values=values)
            else:
                self.table.insert("", "end", iid=gid, values=values)
            if gid in self.windows:
                self.update_guild(gid, metrics=metrics)
        # Default remains main-only. Optional auto-open is capped to avoid UI overload.
        if self.preferences["startup_windows"] == "Open guild windows":
            for gid in sorted(self.guilds):
                if len(self.auto_opened) >= MAX_AUTO_WINDOWS:
                    break
                if gid not in self.auto_opened:
                    self.auto_opened.add(gid)
                    self.open_guild(gid)
        refresh_text(self.global_log, self.recent)
        if schedule_next:
            self.root.after(self.poll_delay_ms(), self.tick)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Dashboard().run()
