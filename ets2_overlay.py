"""
ETS2 Monitor Overlay
====================
Zeigt CPU/GPU/RAM + ETS2 Telemetry als transparentes Always-on-top Overlay.
Voraussetzungen: pip install psutil pynvml
ETS2 Telemetry Plugin muss aktiv sein (scs-telemetry.dll im plugins-Ordner).
Log-CSV landet auf dem Desktop: ets2_monitor_log_DATUM.csv
"""

import tkinter as tk
import ctypes
import threading
import time
import math
import psutil
import csv
import json
import os
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

try:
    import pynvml
    NVML_AVAILABLE = True
    pynvml.nvmlInit()
    gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
except Exception:
    NVML_AVAILABLE = False

try:
    import truck_telemetry
    TELEMETRY_AVAILABLE = True
except Exception:
    TELEMETRY_AVAILABLE = False

# ──────────────────────────────────────────────
# ETS2 Telemetry (via scs-sdk-plugin / truck-telemetry)
# Parst das reale SDK-Struct statt fester Byte-Offsets,
# da sich das Speicherlayout zwischen SDK-Versionen ändert.
# ──────────────────────────────────────────────
_telemetry_initialized = False

def _ensure_telemetry_init():
    global _telemetry_initialized
    if not TELEMETRY_AVAILABLE:
        return False
    if _telemetry_initialized:
        return True
    try:
        truck_telemetry.init()
        _telemetry_initialized = True
    except Exception:
        return False
    return True

def read_telemetry():
    """Liest ETS2 Telemetrie aus. Gibt dict zurück oder None wenn nicht verfügbar."""
    global _telemetry_initialized
    if not _ensure_telemetry_init():
        return None
    try:
        data = truck_telemetry.get_data()
        if not data.get("sdkActive"):
            return None

        speed_kmh = abs(data["speed"]) * 3.6
        gear      = data["gear"]
        gear_str  = "R" if gear < 0 else ("N" if gear == 0 else str(gear))
        fuel      = data["fuel"]
        fuel_cap  = data["fuelCapacity"]
        fuel_pct  = (fuel / fuel_cap * 100) if fuel_cap > 0 else 0
        wear_avg  = (data["wearEngine"] + data["wearTransmission"] + data["wearCabin"]
                     + data["wearChassis"] + data["wearWheels"]) / 5

        # Ankunftszeit (Spielzeit): routeTime (Sekunden Restfahrzeit) auf
        # time_abs (aktuelle Spielzeit in Minuten) aufaddieren
        on_job       = bool(data.get("onJob"))
        remaining_s  = data["routeTime"] if on_job else 0.0
        eta_game_str = "--"
        remaining_str = "--"
        if on_job and remaining_s > 0:
            remaining_min = remaining_s / 60.0
            remaining_str = f"{int(remaining_min // 60)}:{int(remaining_min % 60):02d} h"

            time_abs = data["time_abs"]
            eta_abs  = time_abs + remaining_min
            day_offset = int(eta_abs // 1440) - int(time_abs // 1440)
            tod = eta_abs % 1440
            eta_game_str = f"{int(tod // 60):02d}:{int(tod % 60):02d}"
            if day_offset > 0:
                eta_game_str += f" +{day_offset}T"

        return {
            "speed":             speed_kmh,
            "rpm":               data["engineRpm"],
            "gear":              gear_str,
            "fuel":              fuel,
            "fuel_pct":          fuel_pct,
            "truck_dmg":         wear_avg * 100,
            "engine":            bool(data["engineEnabled"]),
            "paused":            bool(data["paused"]),
            "on_job":            on_job,
            "remaining_sec":     remaining_s,
            "eta_game":          eta_game_str,
            "remaining":         remaining_str,
            "pos_x":             data["coordinateX"],
            "pos_z":             data["coordinateZ"],
        }
    except Exception:
        # Shared Memory evtl. verschwunden (Spiel beendet) -> beim naechsten Mal neu verbinden
        _telemetry_initialized = False
        return None


def get_hw_stats():
    """CPU/GPU/RAM Stats auslesen."""
    stats = {}

    # CPU
    stats["cpu_pct"]  = psutil.cpu_percent(interval=None)
    cpu_temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
    # Windows: cpu_thermal oder coretemp
    temp_val = None
    for key in ("coretemp", "cpu_thermal", "k10temp", "zenpower"):
        if key in cpu_temps:
            entries = cpu_temps[key]
            # Tdie oder Package bevorzugen
            for e in entries:
                if "tdie" in e.label.lower() or "package" in e.label.lower():
                    temp_val = e.current
                    break
            if temp_val is None and entries:
                temp_val = entries[0].current
            break
    stats["cpu_temp"] = temp_val

    # RAM
    ram = psutil.virtual_memory()
    stats["ram_used_gb"] = ram.used / 1e9
    stats["ram_total_gb"] = ram.total / 1e9
    stats["ram_pct"] = ram.percent

    # GPU (NVIDIA via pynvml)
    if NVML_AVAILABLE:
        try:
            util       = pynvml.nvmlDeviceGetUtilizationRates(gpu_handle)
            mem_info   = pynvml.nvmlDeviceGetMemoryInfo(gpu_handle)
            temp_gpu   = pynvml.nvmlDeviceGetTemperature(gpu_handle, pynvml.NVML_TEMPERATURE_GPU)
            stats["gpu_pct"]     = util.gpu
            stats["vram_used"]   = mem_info.used / 1e9
            stats["vram_total"]  = mem_info.total / 1e9
            stats["gpu_temp"]    = temp_gpu
        except Exception:
            stats["gpu_pct"] = stats["vram_used"] = stats["vram_total"] = stats["gpu_temp"] = None
    else:
        stats["gpu_pct"] = stats["vram_used"] = stats["vram_total"] = stats["gpu_temp"] = None

    return stats


# ──────────────────────────────────────────────
# Farb-Logik
# ──────────────────────────────────────────────
def color_pct(val, warn=70, crit=90):
    if val is None: return "#888888"
    if val >= crit: return "#FF4444"
    if val >= warn: return "#FFAA00"
    return "#44FF88"

def color_temp(val, warn=75, crit=90):
    if val is None: return "#888888"
    if val >= crit: return "#FF4444"
    if val >= warn: return "#FFAA00"
    return "#44FF88"

def color_dmg(val):
    if val is None: return "#888888"
    if val >= 50:  return "#FF4444"
    if val >= 20:  return "#FFAA00"
    return "#44FF88"

def color_fuel(pct):
    if pct is None: return "#888888"
    if pct <= 10:  return "#FF4444"
    if pct <= 25:  return "#FFAA00"
    return "#44FF88"


# ──────────────────────────────────────────────
# Anzeige-Konfiguration: welche Werte es gibt, und
# Persistenz für Sichtbarkeit/Losgelöst-Status/Position/Skalierung
# ──────────────────────────────────────────────
METRIC_DEFS = [
    # (id, Sektion, Anzeige-Label)
    ("cpu_pct",    "CPU",  "Auslastung"),
    ("cpu_temp",   "CPU",  "Temperatur"),
    ("gpu_pct",    "GPU",  "Auslastung"),
    ("gpu_temp",   "GPU",  "Temperatur"),
    ("vram",       "GPU",  "VRAM"),
    ("ram",        "RAM",  "Belegung"),
    ("ets_status", "ETS2", "Status"),
    ("speed",      "ETS2", "Tempo"),
    ("gear",       "ETS2", "Gang"),
    ("rpm",        "ETS2", "Drehzahl"),
    ("fuel",       "ETS2", "Kraftstoff"),
    ("dmg",        "ETS2", "Schaden"),
    ("eta_real",   "ETS2", "Ankunft"),
    ("eta_game",   "ETS2", "  (Spielzeit)"),
    ("remaining",  "ETS2", "Restzeit"),
]

SETTINGS_PATH = Path.home() / ".ets2_monitor_settings.json"

def _default_metric_settings():
    return {"visible": True, "detached": False, "x": None, "y": None, "scale": 1.0}

def load_settings():
    settings = {
        "main_window": {"x": 10, "y": 10},
        "metrics": {mid: _default_metric_settings() for mid, _, _ in METRIC_DEFS},
        "trail_widget": {"enabled": False, "x": None, "y": None, "meters_per_px": 4.0},
    }
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        settings["main_window"].update(saved.get("main_window", {}))
        settings["trail_widget"].update(saved.get("trail_widget", {}))
        for mid, cfg in saved.get("metrics", {}).items():
            if mid in settings["metrics"]:
                settings["metrics"][mid].update(cfg)
    except Exception:
        pass
    return settings

def save_settings(settings):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass


# ──────────────────────────────────────────────
# CSV Logger
# ──────────────────────────────────────────────
LOG_INTERVAL_SEC = 5   # Alle N Sekunden einen Datensatz schreiben

CSV_COLUMNS = [
    "timestamp",
    "cpu_pct", "cpu_temp",
    "gpu_pct", "gpu_temp",
    "vram_used_gb", "vram_total_gb",
    "ram_pct", "ram_used_gb",
    "speed_kmh", "gear", "rpm",
    "fuel_pct", "fuel_liter",
    "truck_dmg_pct",
    "engine_on", "paused",
]

class CSVLogger:
    def __init__(self):
        desktop   = Path.home() / "Desktop"
        date_str  = datetime.now().strftime("%Y-%m-%d_%H-%M")
        self.path = desktop / f"ets2_monitor_log_{date_str}.csv"
        self._lock = threading.Lock()
        self._init_file()

    def _init_file(self):
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()

    def write(self, hw: dict, ets):
        row = {
            "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "cpu_pct":      round(hw.get("cpu_pct") or 0, 1),
            "cpu_temp":     round(hw.get("cpu_temp") or 0, 1) if hw.get("cpu_temp") else "",
            "gpu_pct":      hw.get("gpu_pct") or "",
            "gpu_temp":     hw.get("gpu_temp") or "",
            "vram_used_gb": round(hw.get("vram_used") or 0, 2) if hw.get("vram_used") else "",
            "vram_total_gb":round(hw.get("vram_total") or 0, 2) if hw.get("vram_total") else "",
            "ram_pct":      round(hw.get("ram_pct") or 0, 1),
            "ram_used_gb":  round(hw.get("ram_used_gb") or 0, 2),
            "speed_kmh":    round(ets["speed"], 1)      if ets else "",
            "gear":         ets["gear"]                  if ets else "",
            "rpm":          round(ets["rpm"], 0)         if ets else "",
            "fuel_pct":     round(ets["fuel_pct"], 1)   if ets else "",
            "fuel_liter":   round(ets["fuel"], 1)       if ets else "",
            "truck_dmg_pct":round(ets["truck_dmg"], 2)  if ets else "",
            "engine_on":    int(ets["engine"])           if ets else "",
            "paused":       int(ets["paused"])           if ets else "",
        }
        with self._lock:
            with open(self.path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                writer.writerow(row)

    @property
    def filename(self):
        return self.path.name


# ──────────────────────────────────────────────
# Losgelöstes Einzel-Widget (frei positionierbar/skalierbar)
# ──────────────────────────────────────────────
class DetachedWidget(tk.Toplevel):
    BG_COLOR = "#0D0D0D"

    def __init__(self, master, metric_id, label_text, settings, on_change):
        super().__init__(master)
        self.metric_id = metric_id
        self.settings  = settings
        self.on_change = on_change

        self.overrideredirect(True)
        self.wm_attributes("-topmost", True)
        self.configure(bg=self.BG_COLOR)

        cfg = settings["metrics"][metric_id]
        x = cfg["x"] if cfg["x"] is not None else 300
        y = cfg["y"] if cfg["y"] is not None else 300
        self.geometry(f"+{x}+{y}")
        self.scale = cfg.get("scale", 1.0)

        frame = tk.Frame(self, bg=self.BG_COLOR, highlightbackground="#4FC3F7", highlightthickness=1)
        frame.pack()
        self._lbl_title = tk.Label(frame, text=label_text.strip(), bg=self.BG_COLOR, fg="#4FC3F7")
        self._lbl_title.pack(anchor=tk.W, padx=5, pady=(3, 0))
        self._lbl_value = tk.Label(frame, text="--", bg=self.BG_COLOR, fg="#CCCCCC")
        self._lbl_value.pack(padx=7, pady=(0, 5))
        self._apply_scale()

        for w in (self, frame, self._lbl_title, self._lbl_value):
            w.bind("<ButtonPress-1>",   self._drag_start)
            w.bind("<B1-Motion>",       self._drag_motion)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<MouseWheel>",      self._on_scroll)
            w.bind("<ButtonPress-3>",   self._show_menu)
        self._drag_x = self._drag_y = 0

    def _apply_scale(self):
        self._lbl_title.config(font=("Consolas", max(6, round(7 * self.scale))))
        self._lbl_value.config(font=("Consolas", max(8, round(15 * self.scale)), "bold"))

    def set_value(self, text, color):
        self._lbl_value.config(text=text, fg=color)

    def _drag_start(self, event):
        self._drag_x, self._drag_y = event.x, event.y

    def _drag_motion(self, event):
        x = self.winfo_x() + (event.x - self._drag_x)
        y = self.winfo_y() + (event.y - self._drag_y)
        self.geometry(f"+{x}+{y}")

    def _drag_end(self, event):
        cfg = self.settings["metrics"][self.metric_id]
        cfg["x"], cfg["y"] = self.winfo_x(), self.winfo_y()
        self.on_change()

    def _on_scroll(self, event):
        factor = 1.1 if event.delta > 0 else (1 / 1.1)
        self.scale = min(max(self.scale * factor, 0.5), 3.0)
        self._apply_scale()
        self.settings["metrics"][self.metric_id]["scale"] = self.scale
        self.on_change()

    def _show_menu(self, event):
        menu = tk.Menu(self, tearoff=0, bg="#1A1A2E", fg="#CCCCCC",
                        activebackground="#4FC3F7", activeforeground="#000000")
        menu.add_command(label="Anheften (zurück ins Panel)", command=self._dock_back)
        menu.add_command(label="Ausblenden", command=self._hide)
        menu.tk_popup(event.x_root, event.y_root)

    def _dock_back(self):
        self.settings["metrics"][self.metric_id]["detached"] = False
        self.on_change(relayout=True)

    def _hide(self):
        self.settings["metrics"][self.metric_id]["visible"] = False
        self.on_change(relayout=True)


# ──────────────────────────────────────────────
# Spur-Karte: gefahrene Strecke als Linie + Richtungspfeil,
# ganz ohne Kartenbild -- rein aus den worldX/worldZ-Positionen
# der Telemetrie. Nord oben, Zoom per Mausrad.
# ──────────────────────────────────────────────
class TrailWidget(tk.Toplevel):
    BG_COLOR   = "#0D0D0D"
    LINE_COLOR = "#4FC3F7"
    NOW_COLOR  = "#44FF88"
    SIZE       = 220

    def __init__(self, master, settings, on_change):
        super().__init__(master)
        self.settings = settings
        self.on_change = on_change

        self.overrideredirect(True)
        self.wm_attributes("-topmost", True)
        self.configure(bg=self.BG_COLOR)

        cfg = settings["trail_widget"]
        x = cfg["x"] if cfg["x"] is not None else 300
        y = cfg["y"] if cfg["y"] is not None else 300
        self.geometry(f"+{x}+{y}")
        self.meters_per_px = cfg.get("meters_per_px", 4.0)

        frame = tk.Frame(self, bg=self.BG_COLOR, highlightbackground=self.LINE_COLOR, highlightthickness=1)
        frame.pack()
        title = tk.Frame(frame, bg="#1A1A2E")
        title.pack(fill=tk.X)
        tk.Label(title, text="SPUR", font=("Consolas", 7, "bold"), fg=self.LINE_COLOR,
                 bg="#1A1A2E").pack(side=tk.LEFT, padx=4)
        self._lbl_scale = tk.Label(title, text="", font=("Consolas", 7), fg="#666666", bg="#1A1A2E")
        self._lbl_scale.pack(side=tk.RIGHT, padx=4)

        self.canvas = tk.Canvas(frame, width=self.SIZE, height=self.SIZE,
                                 bg=self.BG_COLOR, highlightthickness=0)
        self.canvas.pack()

        for w in (self, frame, title, self.canvas):
            w.bind("<ButtonPress-1>",   self._drag_start)
            w.bind("<B1-Motion>",       self._drag_motion)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<MouseWheel>",      self._on_scroll)
            w.bind("<ButtonPress-3>",   self._show_menu)
        self._drag_x = self._drag_y = 0
        self._update_scale_label()

    def _update_scale_label(self):
        self._lbl_scale.config(text=f"{self.meters_per_px * self.SIZE / 1000:.1f} km")

    def redraw(self, points, current_pos, heading_deg):
        self.canvas.delete("all")
        if current_pos is None:
            self.canvas.create_text(self.SIZE / 2, self.SIZE / 2, text="--",
                                     fill="#888888", font=("Consolas", 10))
            return

        cx, cz = current_pos
        c = self.SIZE / 2
        mpp = self.meters_per_px

        def to_screen(px, pz):
            return (c + (px - cx) / mpp, c - (pz - cz) / mpp)

        if len(points) >= 2:
            coords = []
            for px, pz in points:
                sx, sy = to_screen(px, pz)
                coords.extend([sx, sy])
            self.canvas.create_line(*coords, fill=self.LINE_COLOR, width=2, smooth=True)

        # Pfeil (Fahrtrichtung) am aktuellen Standort
        ang = math.radians(heading_deg)
        size = 7
        tip   = (c + size * math.sin(ang),         c - size * math.cos(ang))
        left  = (c - size * 0.6 * math.cos(ang),    c - size * 0.6 * math.sin(ang))
        right = (c + size * 0.6 * math.cos(ang),    c + size * 0.6 * math.sin(ang))
        self.canvas.create_polygon(*tip, *left, *right, fill=self.NOW_COLOR, outline="")

    def _drag_start(self, event):
        self._drag_x, self._drag_y = event.x, event.y

    def _drag_motion(self, event):
        x = self.winfo_x() + (event.x - self._drag_x)
        y = self.winfo_y() + (event.y - self._drag_y)
        self.geometry(f"+{x}+{y}")

    def _drag_end(self, event):
        self.settings["trail_widget"]["x"] = self.winfo_x()
        self.settings["trail_widget"]["y"] = self.winfo_y()
        self.on_change()

    def _on_scroll(self, event):
        factor = 1.2 if event.delta > 0 else (1 / 1.2)
        self.meters_per_px = min(max(self.meters_per_px * factor, 0.5), 200.0)
        self._update_scale_label()
        self.settings["trail_widget"]["meters_per_px"] = self.meters_per_px
        self.on_change()

    def _show_menu(self, event):
        menu = tk.Menu(self, tearoff=0, bg="#1A1A2E", fg="#CCCCCC",
                        activebackground="#4FC3F7", activeforeground="#000000")
        menu.add_command(label="Spur zurücksetzen", command=lambda: self.on_change(clear_trail=True))
        menu.add_command(label="Ausblenden", command=self._hide)
        menu.tk_popup(event.x_root, event.y_root)

    def _hide(self):
        self.settings["trail_widget"]["enabled"] = False
        self.on_change(relayout=True)


# ──────────────────────────────────────────────
# Einstellungsfenster: Sichtbarkeit / Losgelöst pro Wert
# ──────────────────────────────────────────────
class SettingsWindow(tk.Toplevel):
    def __init__(self, master, settings, on_change):
        super().__init__(master)
        self.settings  = settings
        self.on_change = on_change
        self.title("ETS2 Monitor – Anzeige anpassen")
        self.configure(bg="#0D0D0D")
        self.attributes("-topmost", True)

        tk.Label(self, text="Sichtbar / Losgelöst pro Wert", font=("Consolas", 9, "bold"),
                 fg="#4FC3F7", bg="#0D0D0D").pack(padx=8, pady=(8, 4), anchor=tk.W)

        container = tk.Frame(self, bg="#0D0D0D")
        container.pack(padx=8, pady=4)

        self._vis_vars = {}
        self._det_vars = {}
        last_section = None
        for mid, sect, label in METRIC_DEFS:
            r = container.grid_size()[1]
            if sect != last_section:
                tk.Label(container, text=sect, font=("Consolas", 8, "bold"), fg="#666666",
                         bg="#0D0D0D").grid(row=r, column=0, columnspan=3, sticky=tk.W, pady=(6, 0))
                last_section = sect
                r += 1

            cfg = self.settings["metrics"][mid]
            tk.Label(container, text=label.strip(), font=("Consolas", 8), fg="#CCCCCC",
                     bg="#0D0D0D", width=14, anchor=tk.W).grid(row=r, column=0, sticky=tk.W)

            vis_var = tk.BooleanVar(value=cfg["visible"])
            det_var = tk.BooleanVar(value=cfg["detached"])
            self._vis_vars[mid] = vis_var
            self._det_vars[mid] = det_var

            tk.Checkbutton(container, text="Sichtbar", variable=vis_var, bg="#0D0D0D", fg="#CCCCCC",
                            selectcolor="#1A1A2E", activebackground="#0D0D0D",
                            command=lambda m=mid: self._on_toggle(m)).grid(row=r, column=1, sticky=tk.W, padx=4)
            tk.Checkbutton(container, text="Losgelöst", variable=det_var, bg="#0D0D0D", fg="#CCCCCC",
                            selectcolor="#1A1A2E", activebackground="#0D0D0D",
                            command=lambda m=mid: self._on_toggle(m)).grid(row=r, column=2, sticky=tk.W, padx=4)

        tk.Label(self, text="Losgelöste Widgets: Ziehen zum Verschieben, Mausrad zum\n"
                             "Skalieren, Rechtsklick zum Anheften/Ausblenden.",
                 font=("Consolas", 7), fg="#666666", bg="#0D0D0D", justify=tk.LEFT
                 ).pack(padx=8, pady=(4, 8), anchor=tk.W)

        tk.Button(self, text="Schließen", command=self.destroy).pack(pady=(0, 8))

    def _on_toggle(self, mid):
        cfg = self.settings["metrics"][mid]
        cfg["visible"]  = self._vis_vars[mid].get()
        cfg["detached"] = self._det_vars[mid].get()
        self.on_change(relayout=True)


# ──────────────────────────────────────────────
# Overlay Window
# ──────────────────────────────────────────────
class ETS2Overlay:
    REFRESH_HW  = 1000   # ms – Hardware-Stats
    REFRESH_ETS = 500    # ms – Telemetry

    BG_COLOR    = "#0D0D0D"
    TEXT_COLOR  = "#CCCCCC"
    LABEL_COLOR = "#666666"
    FONT_MONO   = ("Consolas", 9)
    FONT_TITLE  = ("Consolas", 8, "bold")
    ALPHA       = 0.82   # Transparenz (0.0–1.0)
    WIDTH       = 220

    def __init__(self, root):
        self.root = root
        self.settings = load_settings()

        self.root.title("ETS2 Monitor")
        self.root.overrideredirect(True)           # kein Fensterrahmen
        self.root.wm_attributes("-topmost", True)  # immer oben
        self.root.wm_attributes("-alpha", self.ALPHA)
        self.root.configure(bg=self.BG_COLOR)
        mw = self.settings["main_window"]
        self.root.geometry(f"+{mw.get('x', 10)}+{mw.get('y', 10)}")

        # Drag-Support
        self.root.bind("<ButtonPress-1>",   self._drag_start)
        self.root.bind("<B1-Motion>",       self._drag_motion)
        self.root.bind("<ButtonRelease-1>", self._drag_end)
        self.root.bind("<ButtonPress-3>",   self._show_menu)
        self._drag_x = self._drag_y = 0

        # Doppelklick zum Schließen
        self.root.bind("<Double-Button-1>", lambda e: self._close_app())

        self._detached      = {}   # metric_id -> DetachedWidget
        self._value_labels  = {}   # metric_id -> Label (nur wenn inline im Panel)
        self._trail_widget  = None
        self._trail_points  = deque(maxlen=2000)  # ~16 Min. Spur bei 500ms Polling
        self._build_ui()
        self._sync_detached_widgets()
        self._sync_trail_widget()

        self._hw_stats   = {}
        self._ets_data   = None
        self._lock       = threading.Lock()
        self._logger     = CSVLogger()
        self._log_rows   = 0
        self._logging_on = True
        # EMA-Zustand zur Schätzung der tatsächlichen Ankunftszeit
        self._eta_rate_ema    = None
        self._eta_last_sample = None
        # Aus Positionsänderung abgeleitete Fahrtrichtung (kein rotationX
        # noetig -- selbstkalibrierend, unabhaengig von SCS-Achsenkonvention)
        self._heading_deg   = 0.0
        self._last_pos      = None

        # Background-Threads für Stats
        threading.Thread(target=self._hw_loop,   daemon=True).start()
        threading.Thread(target=self._ets_loop,  daemon=True).start()
        threading.Thread(target=self._log_loop,  daemon=True).start()

        self._update_ui()

    # ── UI aufbauen ──────────────────────────
    def _build_ui(self):
        pad = dict(padx=6, pady=1)

        # Titelzeile
        title_frame = tk.Frame(self.root, bg="#1A1A2E")
        title_frame.pack(fill=tk.X)
        tk.Label(title_frame, text="● ETS2 MONITOR", font=("Consolas", 8, "bold"),
                 fg="#4FC3F7", bg="#1A1A2E").pack(side=tk.LEFT, **pad)
        tk.Label(title_frame, text="[RMB=Menü] [2×=Schließen]", font=("Consolas", 7),
                 fg="#444444", bg="#1A1A2E").pack(side=tk.RIGHT, **pad)

        sep = tk.Frame(self.root, bg="#222233", height=1)
        sep.pack(fill=tk.X)

        # Container
        main = tk.Frame(self.root, bg=self.BG_COLOR)
        main.pack(fill=tk.BOTH, padx=4, pady=2)

        def section(text):
            f = tk.Frame(main, bg=self.BG_COLOR)
            f.pack(fill=tk.X, pady=(4, 0))
            tk.Label(f, text=f"── {text} ──", font=self.FONT_TITLE,
                     fg="#4FC3F7", bg=self.BG_COLOR).pack(anchor=tk.W)

        def row(label):
            f = tk.Frame(main, bg=self.BG_COLOR)
            f.pack(fill=tk.X)
            tk.Label(f, text=f"{label:<12}", font=self.FONT_MONO,
                     fg=self.LABEL_COLOR, bg=self.BG_COLOR, width=12, anchor=tk.W).pack(side=tk.LEFT)
            val = tk.Label(f, text="--", font=self.FONT_MONO,
                           fg=self.TEXT_COLOR, bg=self.BG_COLOR, anchor=tk.W)
            val.pack(side=tk.LEFT)
            return val

        # Nur Werte anzeigen, die sichtbar und NICHT losgelöst sind;
        # Sektionsüberschrift nur, wenn darin mindestens eine Zeile folgt
        self._value_labels = {}
        last_section = None
        for mid, sect, label in METRIC_DEFS:
            cfg = self.settings["metrics"][mid]
            if not cfg["visible"] or cfg["detached"]:
                continue
            if sect != last_section:
                section(sect)
                last_section = sect
            self._value_labels[mid] = row(label)

        # Footer
        tk.Frame(self.root, bg="#222233", height=1).pack(fill=tk.X, pady=(4, 0))
        footer = tk.Frame(self.root, bg=self.BG_COLOR)
        footer.pack(fill=tk.X, padx=4, pady=2)
        tk.Label(footer, text="ETS2 Monitor v1.6.1-beta", font=("Consolas", 7),
                 fg="#333344", bg=self.BG_COLOR).pack(side=tk.LEFT)
        self.lbl_log_status = tk.Label(footer, text="● LOG", font=("Consolas", 7),
                                       fg="#44FF88", bg=self.BG_COLOR)
        self.lbl_log_status.pack(side=tk.RIGHT)

    def _rebuild_layout(self):
        for child in self.root.winfo_children():
            child.destroy()
        self._build_ui()
        self._sync_detached_widgets()
        self._sync_trail_widget()

    def _sync_detached_widgets(self):
        for mid, sect, label in METRIC_DEFS:
            cfg = self.settings["metrics"][mid]
            want_detached = cfg["visible"] and cfg["detached"]
            have = mid in self._detached
            if want_detached and not have:
                self._detached[mid] = DetachedWidget(
                    self.root, mid, label, self.settings, self._on_widget_change)
            elif not want_detached and have:
                self._detached[mid].destroy()
                del self._detached[mid]

    def _sync_trail_widget(self):
        want = self.settings["trail_widget"]["enabled"]
        have = self._trail_widget is not None
        if want and not have:
            self._trail_widget = TrailWidget(self.root, self.settings, self._on_widget_change)
        elif not want and have:
            self._trail_widget.destroy()
            self._trail_widget = None

    def _toggle_trail_widget(self):
        self.settings["trail_widget"]["enabled"] = not self.settings["trail_widget"]["enabled"]
        self._on_widget_change(relayout=True)

    def _on_widget_change(self, relayout=False, clear_trail=False):
        if clear_trail:
            self._trail_points.clear()
        save_settings(self.settings)
        if relayout:
            self._rebuild_layout()

    def _open_settings_window(self):
        SettingsWindow(self.root, self.settings, self._on_widget_change)

    # ── Background Threads ────────────────────
    def _hw_loop(self):
        # Einmal CPU-Interval "aufwärmen"
        psutil.cpu_percent(interval=1)
        while True:
            stats = get_hw_stats()
            with self._lock:
                self._hw_stats = stats
            time.sleep(self.REFRESH_HW / 1000)

    def _ets_loop(self):
        while True:
            data = read_telemetry()
            if data is not None:
                data["eta_real"] = self._estimate_real_eta(data)
                self._track_position(data["pos_x"], data["pos_z"])
            with self._lock:
                self._ets_data = data
            time.sleep(self.REFRESH_ETS / 1000)

    TRAIL_MIN_MOVE_M = 3.0  # Mindestbewegung (Meter), bevor ein neuer Punkt/Heading gilt

    def _track_position(self, x, z):
        """Sammelt die Spur-Punkte und leitet die Fahrtrichtung direkt aus
        der Positionsänderung ab (kein rotationX nötig -- funktioniert
        unabhängig von SCS' Achsenkonvention, da rein aus Ist-Bewegung)."""
        if self._last_pos is not None:
            dx = x - self._last_pos[0]
            dz = z - self._last_pos[1]
            dist = math.hypot(dx, dz)
            if dist >= self.TRAIL_MIN_MOVE_M:
                self._heading_deg = math.degrees(math.atan2(dx, dz))
                self._trail_points.append((x, z))
                self._last_pos = (x, z)
        else:
            self._trail_points.append((x, z))
            self._last_pos = (x, z)

    ETA_RATE_EMA_ALPHA = 0.10  # Anpassgeschwindigkeit an Zonenwechsel Stadt/Autobahn
    ETA_RATE_DEFAULT   = 15.0  # Startwert bis zur ersten Messung (Mix aus 3x Stadt/19x Autobahn)
    ETA_RATE_MIN       = 2.0   # Plausibilitätsgrenzen gegen Messrauschen
    ETA_RATE_MAX       = 25.0

    def _estimate_real_eta(self, ets):
        """Schätzt die reale (Wanduhr-)Ankunftszeit aus routeTime -- ETS2s
        eigenem Navi-Wert, der die GESAMTE Reststrecke bereits Stadt-/
        Autobahn-gewichtet berücksichtigt (verifiziert: time_abs + routeTime
        ergibt exakt die im Spiel angezeigte Navi-Ankunftszeit). Offizielle
        SCS-Zeitraffer-Werte: ca. 19x außerorts, nur ca. 3x innerorts --
        eine feste Konstante wäre also falsch, sobald eine Stadt auf der
        Strecke liegt. Die tatsächliche Rate wird deshalb live gemessen,
        aber mit einem Startwert vorbelegt, damit sofort etwas angezeigt
        wird, statt erst zu 'lernen'."""
        if not ets["on_job"]:
            self._eta_rate_ema    = None
            self._eta_last_sample = None
            return "--"

        remaining_s = ets["remaining_sec"]
        if remaining_s <= 0:
            return "--"

        now = time.monotonic()
        if self._eta_rate_ema is None:
            self._eta_rate_ema = self.ETA_RATE_DEFAULT

        if ets["paused"] or not ets["engine"]:
            self._eta_last_sample = None
        else:
            if self._eta_last_sample is not None:
                last_t, last_remaining = self._eta_last_sample
                dt          = now - last_t
                d_remaining = last_remaining - remaining_s

                if remaining_s > last_remaining + 30:
                    # Neuer Job / Umleitung -> Startwert statt verzerrter Messung
                    self._eta_rate_ema = self.ETA_RATE_DEFAULT
                elif dt > 0 and d_remaining > 0:
                    instant_rate = min(max(d_remaining / dt, self.ETA_RATE_MIN),
                                        self.ETA_RATE_MAX)
                    self._eta_rate_ema = (self.ETA_RATE_EMA_ALPHA * instant_rate
                        + (1 - self.ETA_RATE_EMA_ALPHA) * self._eta_rate_ema)
            self._eta_last_sample = (now, remaining_s)

        real_seconds_left = remaining_s / self._eta_rate_ema
        arrival = datetime.now() + timedelta(seconds=real_seconds_left + 30)
        arrival = arrival.replace(second=0, microsecond=0)  # auf Minute runden
        return arrival.strftime("%H:%M")

    def _log_loop(self):
        while True:
            time.sleep(LOG_INTERVAL_SEC)
            if not self._logging_on:
                continue
            with self._lock:
                hw  = self._hw_stats.copy()
                ets = self._ets_data
            try:
                self._logger.write(hw, ets)
                self._log_rows += 1
            except Exception:
                pass

    # ── UI Update (Main Thread) ───────────────
    def _compute_values(self, hw, ets):
        """Berechnet (Text, Farbe) für jeden bekannten Messwert -- unabhängig
        davon, ob er gerade inline im Panel oder als losgelöstes Widget
        angezeigt wird (oder auch gar nicht sichtbar ist)."""
        values = {}

        cpu_pct = hw.get("cpu_pct")
        cpu_tmp = hw.get("cpu_temp")
        values["cpu_pct"]  = (f"{cpu_pct:.0f} %" if cpu_pct is not None else "--", color_pct(cpu_pct))
        values["cpu_temp"] = (f"{cpu_tmp:.0f} °C" if cpu_tmp is not None else "n/a (WMI)", color_temp(cpu_tmp))

        gpu_pct = hw.get("gpu_pct")
        gpu_tmp = hw.get("gpu_temp")
        vram_u  = hw.get("vram_used")
        vram_t  = hw.get("vram_total")
        values["gpu_pct"]  = (f"{gpu_pct} %" if gpu_pct is not None else "--", color_pct(gpu_pct))
        values["gpu_temp"] = (f"{gpu_tmp} °C" if gpu_tmp is not None else "--", color_temp(gpu_tmp))
        values["vram"]     = (f"{vram_u:.1f} / {vram_t:.0f} GB" if vram_u is not None else "--",
                               color_pct((vram_u / vram_t * 100) if vram_t else None, warn=75, crit=90))

        ram_u = hw.get("ram_used_gb")
        ram_t = hw.get("ram_total_gb")
        ram_p = hw.get("ram_pct")
        values["ram"] = (f"{ram_u:.1f} / {ram_t:.0f} GB ({ram_p:.0f}%)" if ram_u else "--", color_pct(ram_p))

        if ets is None:
            values["ets_status"] = ("Warten...", "#888888")
            for mid in ("speed", "gear", "rpm", "fuel", "dmg", "eta_real", "eta_game", "remaining"):
                values[mid] = ("--", "#888888")
        else:
            status = "⏸ Pause" if ets["paused"] else ("▶ Läuft" if ets["engine"] else "Motor aus")
            values["ets_status"] = (status, "#44FF88" if not ets["paused"] else "#FFAA00")
            values["speed"]      = (f"{ets['speed']:.0f} km/h", "#FFFFFF")
            values["gear"]       = (ets["gear"], "#4FC3F7")
            values["rpm"]        = (f"{ets['rpm']:.0f}", "#FFFFFF")
            values["fuel"]       = (f"{ets['fuel']:.0f} L ({ets['fuel_pct']:.0f}%)",
                                     color_fuel(ets["fuel_pct"]))
            values["dmg"]        = (f"{ets['truck_dmg']:.1f} %", color_dmg(ets["truck_dmg"]))
            values["eta_real"]   = (ets["eta_real"], "#44FF88" if ets["eta_real"] != "--" else "#888888")
            values["eta_game"]   = (ets["eta_game"], "#4FC3F7" if ets["eta_game"] != "--" else "#888888")
            values["remaining"]  = (ets["remaining"], "#FFFFFF" if ets["remaining"] != "--" else "#888888")

        return values

    def _update_ui(self):
        with self._lock:
            hw  = self._hw_stats.copy()
            ets = self._ets_data

        values = self._compute_values(hw, ets)
        for mid, (text, color) in values.items():
            lbl = self._value_labels.get(mid)
            if lbl is not None:
                lbl.config(text=text, fg=color)
            widget = self._detached.get(mid)
            if widget is not None:
                widget.set_value(text, color)

        if self._trail_widget is not None:
            current_pos = self._last_pos if ets is not None else None
            self._trail_widget.redraw(list(self._trail_points), current_pos, self._heading_deg)

        # Log-Status Footer aktualisieren
        if self._logging_on:
            self.lbl_log_status.config(
                text=f"● LOG {self._log_rows} Zeilen",
                fg="#44FF88")
        else:
            self.lbl_log_status.config(text="○ LOG (aus)", fg="#666666")

        self.root.after(500, self._update_ui)

    # ── Drag ─────────────────────────────────
    def _drag_start(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _drag_motion(self, event):
        x = self.root.winfo_x() + (event.x - self._drag_x)
        y = self.root.winfo_y() + (event.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    def _drag_end(self, event):
        self.settings["main_window"]["x"] = self.root.winfo_x()
        self.settings["main_window"]["y"] = self.root.winfo_y()
        save_settings(self.settings)

    # ── Rechtsklick-Menü ─────────────────────
    def _show_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0, bg="#1A1A2E", fg="#CCCCCC",
                       activebackground="#4FC3F7", activeforeground="#000000")
        menu.add_command(label="Transparenz: 50%",  command=lambda: self.root.wm_attributes("-alpha", 0.5))
        menu.add_command(label="Transparenz: 75%",  command=lambda: self.root.wm_attributes("-alpha", 0.75))
        menu.add_command(label="Transparenz: 90%",  command=lambda: self.root.wm_attributes("-alpha", 0.9))
        menu.add_separator()
        menu.add_command(label="Anzeige anpassen...", command=self._open_settings_window)
        trail_label = ("Spur-Karte ausblenden" if self.settings["trail_widget"]["enabled"]
                       else "Spur-Karte anzeigen")
        menu.add_command(label=trail_label, command=self._toggle_trail_widget)
        menu.add_separator()
        log_label = "Logging PAUSIEREN" if self._logging_on else "Logging STARTEN"
        menu.add_command(label=log_label, command=self._toggle_logging)
        menu.add_command(label=f"Log öffnen ({self._logger.filename})",
                         command=lambda: os.startfile(str(self._logger.path)))
        menu.add_separator()
        menu.add_command(label="Schließen", command=self._close_app)
        menu.tk_popup(event.x_root, event.y_root)

    def _toggle_logging(self):
        self._logging_on = not self._logging_on

    def _close_app(self):
        save_settings(self.settings)
        self.root.destroy()


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
if __name__ == "__main__":
    # DPI-Aware machen (Windows Skalierung)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    app  = ETS2Overlay(root)
    root.mainloop()
