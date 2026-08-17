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
import psutil
import csv
import os
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
            "route_distance_km": data["routeDistance"] if on_job else 0.0,
            "eta_game":          eta_game_str,
            "remaining":         remaining_str,
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
        self.root.title("ETS2 Monitor")
        self.root.overrideredirect(True)           # kein Fensterrahmen
        self.root.wm_attributes("-topmost", True)  # immer oben
        self.root.wm_attributes("-alpha", self.ALPHA)
        self.root.configure(bg=self.BG_COLOR)
        self.root.geometry(f"+10+10")             # Oben links

        # Drag-Support
        self.root.bind("<ButtonPress-1>",   self._drag_start)
        self.root.bind("<B1-Motion>",       self._drag_motion)
        self.root.bind("<ButtonPress-3>",   self._show_menu)
        self._drag_x = self._drag_y = 0

        # Doppelklick zum Schließen
        self.root.bind("<Double-Button-1>", lambda e: self.root.destroy())

        self._build_ui()
        self._hw_stats   = {}
        self._ets_data   = None
        self._lock       = threading.Lock()
        self._logger     = CSVLogger()
        self._log_rows   = 0
        self._logging_on = True
        # Geglättete Geschwindigkeit für die reale Ankunftszeitschätzung
        # (Restdistanz ÷ Tempo -- wie ein normales Fahrzeug-Navi)
        self._eta_speed_ema = None

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
            return f

        def row(parent, label):
            f = tk.Frame(parent, bg=self.BG_COLOR)
            f.pack(fill=tk.X)
            tk.Label(f, text=f"{label:<12}", font=self.FONT_MONO,
                     fg=self.LABEL_COLOR, bg=self.BG_COLOR, width=12, anchor=tk.W).pack(side=tk.LEFT)
            val = tk.Label(f, text="--", font=self.FONT_MONO,
                           fg=self.TEXT_COLOR, bg=self.BG_COLOR, anchor=tk.W)
            val.pack(side=tk.LEFT)
            return val

        # ── CPU ──
        section("CPU")
        self.lbl_cpu_pct  = row(main, "Auslastung")
        self.lbl_cpu_temp = row(main, "Temperatur")

        # ── GPU ──
        section("GPU")
        self.lbl_gpu_pct  = row(main, "Auslastung")
        self.lbl_gpu_temp = row(main, "Temperatur")
        self.lbl_vram     = row(main, "VRAM")

        # ── RAM ──
        section("RAM")
        self.lbl_ram      = row(main, "Belegung")

        # ── ETS2 ──
        section("ETS2")
        self.lbl_ets_status = row(main, "Status")
        self.lbl_speed      = row(main, "Tempo")
        self.lbl_gear       = row(main, "Gang")
        self.lbl_rpm        = row(main, "Drehzahl")
        self.lbl_fuel       = row(main, "Kraftstoff")
        self.lbl_dmg        = row(main, "Schaden")
        self.lbl_eta_real   = row(main, "Ankunft")
        self.lbl_eta_game   = row(main, "  (Spielzeit)")
        self.lbl_remaining  = row(main, "Restzeit")

        # Footer
        tk.Frame(self.root, bg="#222233", height=1).pack(fill=tk.X, pady=(4, 0))
        footer = tk.Frame(self.root, bg=self.BG_COLOR)
        footer.pack(fill=tk.X, padx=4, pady=2)
        tk.Label(footer, text="ETS2 Monitor v1.1", font=("Consolas", 7),
                 fg="#333344", bg=self.BG_COLOR).pack(side=tk.LEFT)
        self.lbl_log_status = tk.Label(footer, text="● LOG", font=("Consolas", 7),
                                       fg="#44FF88", bg=self.BG_COLOR)
        self.lbl_log_status.pack(side=tk.RIGHT)

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
            with self._lock:
                self._ets_data = data
            time.sleep(self.REFRESH_ETS / 1000)

    ETA_SPEED_EMA_ALPHA = 0.15  # leichte Glättung gegen kurze Bremsvorgänge/Ampeln
    ETA_MIN_SPEED_KMH   = 3     # darunter gilt der Truck als stehend

    def _estimate_real_eta(self, ets):
        """Schätzt die reale (Wanduhr-)Ankunftszeit direkt aus Restdistanz
        (routeDistance, vom Navi bereits vorgegeben) und aktueller
        Geschwindigkeit -- genau wie ein normales Fahrzeug-Navi rechnet.
        Dadurch sofort verfügbar, ohne erst eine Zeitraffer-Rate lernen zu
        müssen. Bei Stillstand (Ampel, Stau) bleibt der letzte Wert stehen,
        statt auf '--' zurückzufallen."""
        if not ets["on_job"]:
            self._eta_speed_ema = None
            return "--"

        speed_kmh   = ets["speed"]
        distance_km = ets["route_distance_km"]

        if speed_kmh >= self.ETA_MIN_SPEED_KMH:
            if self._eta_speed_ema is None:
                self._eta_speed_ema = speed_kmh
            else:
                self._eta_speed_ema = (self.ETA_SPEED_EMA_ALPHA * speed_kmh
                    + (1 - self.ETA_SPEED_EMA_ALPHA) * self._eta_speed_ema)

        if not self._eta_speed_ema or distance_km <= 0:
            return "--"

        hours_left = distance_km / self._eta_speed_ema
        arrival = datetime.now() + timedelta(hours=hours_left, seconds=30)
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
    def _update_ui(self):
        with self._lock:
            hw  = self._hw_stats.copy()
            ets = self._ets_data

        # CPU
        cpu_pct = hw.get("cpu_pct")
        cpu_tmp = hw.get("cpu_temp")
        self.lbl_cpu_pct.config(
            text=f"{cpu_pct:.0f} %" if cpu_pct is not None else "--",
            fg=color_pct(cpu_pct))
        self.lbl_cpu_temp.config(
            text=f"{cpu_tmp:.0f} °C" if cpu_tmp is not None else "n/a (WMI)",
            fg=color_temp(cpu_tmp))

        # GPU
        gpu_pct  = hw.get("gpu_pct")
        gpu_tmp  = hw.get("gpu_temp")
        vram_u   = hw.get("vram_used")
        vram_t   = hw.get("vram_total")
        self.lbl_gpu_pct.config(
            text=f"{gpu_pct} %" if gpu_pct is not None else "--",
            fg=color_pct(gpu_pct))
        self.lbl_gpu_temp.config(
            text=f"{gpu_tmp} °C" if gpu_tmp is not None else "--",
            fg=color_temp(gpu_tmp))
        self.lbl_vram.config(
            text=f"{vram_u:.1f} / {vram_t:.0f} GB" if vram_u is not None else "--",
            fg=color_pct((vram_u / vram_t * 100) if vram_t else None, warn=75, crit=90))

        # RAM
        ram_u = hw.get("ram_used_gb")
        ram_t = hw.get("ram_total_gb")
        ram_p = hw.get("ram_pct")
        self.lbl_ram.config(
            text=f"{ram_u:.1f} / {ram_t:.0f} GB ({ram_p:.0f}%)" if ram_u else "--",
            fg=color_pct(ram_p))

        # ETS2
        if ets is None:
            self.lbl_ets_status.config(text="Warten...", fg="#888888")
            for lbl in (self.lbl_speed, self.lbl_gear, self.lbl_rpm,
                        self.lbl_fuel, self.lbl_dmg, self.lbl_eta_real,
                        self.lbl_eta_game, self.lbl_remaining):
                lbl.config(text="--", fg="#888888")
        else:
            status = "⏸ Pause" if ets["paused"] else ("▶ Läuft" if ets["engine"] else "Motor aus")
            self.lbl_ets_status.config(text=status, fg="#44FF88" if not ets["paused"] else "#FFAA00")
            self.lbl_speed.config(text=f"{ets['speed']:.0f} km/h", fg="#FFFFFF")
            self.lbl_gear.config(text=ets["gear"], fg="#4FC3F7")
            self.lbl_rpm.config(text=f"{ets['rpm']:.0f}", fg="#FFFFFF")
            self.lbl_fuel.config(
                text=f"{ets['fuel']:.0f} L ({ets['fuel_pct']:.0f}%)",
                fg=color_fuel(ets["fuel_pct"]))
            self.lbl_dmg.config(
                text=f"{ets['truck_dmg']:.1f} %",
                fg=color_dmg(ets["truck_dmg"]))
            self.lbl_eta_real.config(
                text=ets["eta_real"], fg="#44FF88" if ets["eta_real"] != "--" else "#888888")
            self.lbl_eta_game.config(
                text=ets["eta_game"], fg="#4FC3F7" if ets["eta_game"] != "--" else "#888888")
            self.lbl_remaining.config(
                text=ets["remaining"], fg="#FFFFFF" if ets["remaining"] != "--" else "#888888")

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

    # ── Rechtsklick-Menü ─────────────────────
    def _show_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0, bg="#1A1A2E", fg="#CCCCCC",
                       activebackground="#4FC3F7", activeforeground="#000000")
        menu.add_command(label="Transparenz: 50%",  command=lambda: self.root.wm_attributes("-alpha", 0.5))
        menu.add_command(label="Transparenz: 75%",  command=lambda: self.root.wm_attributes("-alpha", 0.75))
        menu.add_command(label="Transparenz: 90%",  command=lambda: self.root.wm_attributes("-alpha", 0.9))
        menu.add_separator()
        log_label = "Logging PAUSIEREN" if self._logging_on else "Logging STARTEN"
        menu.add_command(label=log_label, command=self._toggle_logging)
        menu.add_command(label=f"Log öffnen ({self._logger.filename})",
                         command=lambda: os.startfile(str(self._logger.path)))
        menu.add_separator()
        menu.add_command(label="Schließen", command=self.root.destroy)
        menu.tk_popup(event.x_root, event.y_root)

    def _toggle_logging(self):
        self._logging_on = not self._logging_on


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
