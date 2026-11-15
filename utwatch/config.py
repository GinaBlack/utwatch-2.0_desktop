"""Paths, brand tokens and validated settings."""
from __future__ import annotations
import json
import os
from pathlib import Path

APP_NAME = "UTWatch"

# Brand palette from the UTWatch brand sheet, plus the two severity colours the sheet lacks.
BRAND = {
    "navy": "#1E2A3A",
    "blue": "#3498DB",
    "orange": "#E67E22",
    "green": "#27AE60",
    "light": "#ECF0F1",
    "red": "#E74C3C",
    "amber": "#F1C40F",
}


def data_dir() -> Path:
    """Folder for the database, recordings, reports and logs."""
    env = os.environ.get("UTWATCH_HOME")
    if env:
        base = Path(env)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME
    else:
        base = Path.home() / ".utwatch"
    base.mkdir(parents=True, exist_ok=True)
    return base


def sub_dir(name: str) -> Path:
    p = data_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


# key: (type, minimum, maximum, default, description)
SETTING_SPECS: dict[str, tuple] = {
    "analysis_fps": (float, 0.5, 15.0, 5.0, "Frames analysed per second"),
    "analysis_width": (int, 160, 1280, 480, "Width used for analysis (pixels)"),
    "window_seconds": (float, 2.0, 8.0, 5.0, "Analysis window (seconds); pilot decides 4 or 5"),
    "window_hop_seconds": (float, 0.25, 4.0, 1.0, "Hop between window evaluations (seconds)"),
    "alert_cooldown_seconds": (float, 1.0, 120.0, 5.0, "Cooldown per camera and alert type"),
    "collision_iou": (float, 0.05, 0.95, 0.40, "Overlap that counts as contact"),
    "collision_persist_frames": (int, 1, 20, 3, "Consecutive analysed frames of contact"),
    "moving_speed": (float, 0.001, 1.0, 0.03, "Moving threshold (frame diagonals per second)"),
    "stationary_speed": (float, 0.0, 0.2, 0.008, "Stationary threshold (frame diagonals per second)"),
    "stationary_seconds": (float, 0.5, 120.0, 2.0, "Seconds stationary before an obstacle warning"),
    "sudden_stop_drop": (float, 0.3, 0.99, 0.80, "Speed drop that counts as a sudden stop"),
    "aftermath_seconds": (float, 0.5, 6.0, 2.0, "Seconds of stillness that confirm an accident"),
    "overspeed_frames": (int, 1, 30, 3, "Consecutive frames above the limit"),
    "congestion_medium": (int, 1, 200, 6, "Vehicles for medium congestion"),
    "congestion_high": (int, 2, 400, 12, "Vehicles for high congestion"),
    "fusion_rule": (str, None, None, "or", "or, and, majority or weighted"),
    "pre_roll_seconds": (float, 1.0, 60.0, 10.0, "Recording before an alert"),
    "post_roll_seconds": (float, 1.0, 60.0, 10.0, "Recording after confirmation or expiry"),
    "record_fps": (float, 1.0, 30.0, 10.0, "Frames per second kept for recordings"),
    "storage_quota_gb": (float, 0.1, 500.0, 5.0, "Recording quota (gigabytes)"),
    "retention_days": (int, 1, 3650, 30, "Days to keep unprotected recordings"),
    "mute_alerts": (int, 0, 1, 0, "Mute audible alerts"),
    "detector": (str, None, None, "motion", "Object detector plug-in"),
    "temporal": (str, None, None, "none", "Temporal classifier plug-in"),
    "audio": (str, None, None, "none", "Audio classifier plug-in"),
    "detector_weights": (str, None, None, "", "Path to detector weights"),
    "temporal_weights": (str, None, None, "", "Path to temporal model weights"),
    "audio_weights": (str, None, None, "", "Path to audio model weights"),
    "cloud_sync": (int, 0, 1, 0, "Send non-personal metadata to the cloud"),
}

FUSION_RULES = ("or", "and", "majority", "weighted")


def defaults() -> dict:
    return {k: v[3] for k, v in SETTING_SPECS.items()}


def validate_setting(key: str, value):
    """Return the value converted to its type, or raise ValueError with a plain message."""
    if key not in SETTING_SPECS:
        raise ValueError(f"Unknown setting: {key}")
    typ, lo, hi, _default, desc = SETTING_SPECS[key]
    try:
        v = typ(value)
    except (TypeError, ValueError):
        raise ValueError(f"{desc}: expected a {typ.__name__}")
    if lo is not None and v < lo:
        raise ValueError(f"{desc}: must be at least {lo}")
    if hi is not None and v > hi:
        raise ValueError(f"{desc}: must be at most {hi}")
    if key == "fusion_rule" and v not in FUSION_RULES:
        raise ValueError(f"{desc}: choose one of {', '.join(FUSION_RULES)}")
    return v


def validate_latlon(lat, lon):
    """Return (lat, lon) as floats or raise ValueError."""
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        raise ValueError("Latitude and longitude must be numbers")
    if not -90 <= la <= 90:
        raise ValueError("Latitude must be between -90 and 90")
    if not -180 <= lo <= 180:
        raise ValueError("Longitude must be between -180 and 180")
    return la, lo


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)
