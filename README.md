# 🚛 Ets2_Monitor

A lightweight always-on-top overlay for **Euro Truck Simulator 2** that combines real-time hardware monitoring with ETS2 telemetry data — all in one transparent HUD.

---

## ✨ Features

- **CPU** — Usage % + Temperature
- **GPU** — Usage % + Temperature + VRAM usage (NVIDIA via NVML)
- **RAM** — Used / Total + Percentage
- **ETS2 Telemetry** (via SCS Telemetry SDK shared memory)
  - Speed (km/h), Gear, RPM
  - Fuel level (liters + %)
  - Truck damage %
  - Engine & pause state
- **CSV Logging** — Every 5 seconds to `Desktop\ets2_monitor_log_DATE.csv`
- Color-coded values (green / orange / red thresholds)
- Draggable, transparent, always-on-top — no taskbar clutter
- Right-click menu: transparency toggle, log pause/resume, open log file

---

## 📋 Requirements

- Windows 10/11
- Python 3.10+
- ETS2 with [SCS Telemetry Plugin](https://github.com/nlhans/ets2-sdk-plugin) active (`scs-telemetry.dll` in `<ETS2>\bin\win_x64\plugins\`)

### Python dependencies

```bash
pip install psutil pynvml
```

> **Note:** `pynvml` requires an NVIDIA GPU. On AMD systems, GPU stats will show as unavailable.

---

## 🚀 Usage

```bash
python ets2_overlay.py
```

- Start the overlay **before or after** launching ETS2 — it waits for the telemetry connection automatically
- **Left-click + drag** → move the overlay
- **Double-click** → close
- **Right-click** → menu (transparency, logging, exit)

---

## 📂 CSV Log Format

Log files are saved to your Desktop as `ets2_monitor_log_YYYY-MM-DD_HH-MM.csv`.

| Column | Description |
|---|---|
| `timestamp` | Date and time of record |
| `cpu_pct` | CPU usage in % |
| `cpu_temp` | CPU temperature in °C |
| `gpu_pct` | GPU usage in % |
| `gpu_temp` | GPU temperature in °C |
| `vram_used_gb` | VRAM used in GB |
| `vram_total_gb` | Total VRAM in GB |
| `ram_pct` | RAM usage in % |
| `ram_used_gb` | RAM used in GB |
| `speed_kmh` | Truck speed in km/h |
| `gear` | Current gear (N / R / 1–12) |
| `rpm` | Engine RPM |
| `fuel_pct` | Fuel level in % |
| `fuel_liter` | Fuel level in liters |
| `truck_dmg_pct` | Truck damage in % |
| `engine_on` | Engine state (1 = on) |
| `paused` | Pause state (1 = paused) |

---

## 🔧 Configuration

At the top of `ets2_overlay.py`:

```python
LOG_INTERVAL_SEC = 5    # CSV write interval in seconds
ALPHA            = 0.82 # Overlay transparency (0.0 – 1.0)
REFRESH_HW       = 1000 # Hardware stats refresh in ms
REFRESH_ETS      = 500  # Telemetry refresh in ms
```

---

## 📜 License

MIT — do whatever you want with it.
