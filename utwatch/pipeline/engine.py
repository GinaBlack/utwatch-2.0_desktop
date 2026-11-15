"""CameraEngine: everything that happens to one camera's frames, with no Qt in it (so it can be tested)."""
from __future__ import annotations
import math
import time
from collections import deque

import cv2
import numpy as np

from .. import plugins
from ..models import STAGE_CONFIRMED, STAGE_EXPIRED, STAGE_SUSPECTED, Severity
from .alerts import AlertManager
from .analytics import StatsCollector
from .calibration import Calibration
from .recorder import EventRecorder
from .rules import RuleEngine
from .tracker import IoUTracker

DISPLAY_WIDTH = 640
STAGE_COLOURS = {"ok": (80, 175, 39), "suspected": (34, 126, 230), "confirmed": (60, 76, 231)}   # BGR


class CameraEngine:
    def __init__(self, camera: dict, settings: dict, recordings_dir):
        self.camera = camera
        self.cam_id, self.cam_name = camera["id"], camera["name"]
        self.s = dict(settings)
        self.calibration = Calibration.from_json(camera["calibration"]) if camera.get("calibration") else None
        self.zones = [tuple(z) for z in (camera.get("zones") or [])]
        self.notes: list[str] = []
        self.detector, w = plugins.create_detector(self.s["detector"], self.s["detector_weights"]); self._note(w)
        self.temporal, w = plugins.create_temporal(self.s["temporal"], self.s["temporal_weights"]); self._note(w)
        self.tracker = IoUTracker(stationary_speed=self.s["stationary_speed"])
        self.rules = RuleEngine(self.s, camera.get("speed_limit_kmh"), self.zones)
        self.alerts = AlertManager(self.cam_id, self.cam_name, self.s)
        self.stats = StatsCollector(self.s["congestion_medium"], self.s["congestion_high"])
        self.recorder = EventRecorder(self.cam_id, self.cam_name, recordings_dir, self.s)
        self.window: deque = deque()          # (t, small frame) for the temporal model
        self._last_analysis = -1e9
        self._last_window_eval = -1e9
        self._audio_t, self._audio_score = -1e9, 0.0
        self._temporal_t, self._temporal_score = -1e9, 0.0
        self.timings = {"detect": 0.0, "track_rules": 0.0, "total": 0.0}
        self.analysed = 0
        self.last_tracks: list = []
        self.last_boxes_scale = 1.0
        self.active_stage = "ok"

    def _note(self, w):
        if w:
            self.notes.append(w)

    # ---------- configuration
    def update_settings(self, settings: dict, camera: dict | None = None):
        self.s = dict(settings)
        if camera:
            self.camera = camera
        self.rules.update_settings(self.s, self.camera.get("speed_limit_kmh"))
        self.alerts.update_settings(self.s)
        self.recorder.update_settings(self.s)
        self.stats.set_thresholds(self.s["congestion_medium"], self.s["congestion_high"])

    def set_calibration(self, cal: Calibration | None):
        self.calibration = cal

    def reset_scene(self):
        """Forget the background, tracks and open incident (a looped file started over, or the camera view changed)."""
        self.detector, _ = plugins.create_detector(self.s["detector"], self.s["detector_weights"])
        self.tracker = IoUTracker(stationary_speed=self.s["stationary_speed"])
        self.rules = RuleEngine(self.s, self.camera.get("speed_limit_kmh"), self.zones)
        self.alerts = AlertManager(self.cam_id, self.cam_name, self.s)
        self.window.clear()
        self.last_tracks = []
        self.active_stage = "ok"

    def audio_input(self, t: float, score: float):
        """Called with a crash score (0..1) from the audio classifier."""
        self._audio_t, self._audio_score = t, float(score)

    # ---------- main entry
    def process(self, t: float, frame: np.ndarray) -> dict:
        t0 = time.perf_counter()
        h, w = frame.shape[:2]
        dscale = DISPLAY_WIDTH / w if w > DISPLAY_WIDTH else 1.0
        display = cv2.resize(frame, (int(w * dscale), int(h * dscale))) if dscale != 1.0 else frame.copy()
        alerts, finished_stats = [], None

        if t - self._last_analysis >= 1.0 / self.s["analysis_fps"] - 1e-6:
            self._last_analysis = t
            alerts, finished_stats = self._analyse(t, frame)

        self.recorder.push(t, display)
        self.recorder.tick(t)
        for a in alerts:
            if a["stage"] == STAGE_SUSPECTED:
                self.recorder.start_event(a["incident_id"], t)
            elif a["stage"] in (STAGE_CONFIRMED, STAGE_EXPIRED):
                self.recorder.end_event(a["incident_id"], t, a["stage"])
        self.active_stage = ("confirmed" if self.alerts.incident and self.alerts.incident.confirmed
                             else "suspected" if self.alerts.incident else self.active_stage if self.active_stage == "confirmed" and t - getattr(self, "_conf_t", -1e9) < 8 else "ok")
        if any(a["stage"] == STAGE_CONFIRMED for a in alerts):
            self._conf_t, self.active_stage = t, "confirmed"
        annotated = self._draw(display, dscale, t)
        self.timings["total"] = 0.8 * self.timings["total"] + 0.2 * (time.perf_counter() - t0) * 1000
        return {"frame": annotated, "alerts": alerts, "bucket": finished_stats,
                "recordings": self.recorder.pop_finished(), "stage": self.active_stage,
                "vehicles": self.stats.current, "level": self.stats.level}

    def _analyse(self, t: float, frame: np.ndarray):
        h, w = frame.shape[:2]
        aw = min(self.s["analysis_width"], w)
        scale = aw / w
        small = cv2.resize(frame, (aw, int(h * scale))) if scale != 1.0 else frame
        size = (small.shape[1], small.shape[0])
        self.last_boxes_scale = scale

        a0 = time.perf_counter()
        dets = self.detector.detect(small)
        a1 = time.perf_counter()
        visible = self.tracker.update(dets, t, size, self.calibration)
        lost = [tr for tr in self.tracker.tracks.values() if tr.missed > 0]
        cues = self.rules.update(t, visible, size, lost)
        vehicles = sum(1 for tr in visible if tr.label in ("car", "truck", "bus", "motorcycle", "vehicle", "object"))
        bucket = self.stats.add(t, vehicles)
        self._temporal_step(t, small)
        audio_flag = (t - self._audio_t) <= self.s["window_seconds"] and self._audio_score >= 0.5
        temporal_flag = (t - self._temporal_t) <= self.s["window_seconds"] and self._temporal_score >= 0.5
        alerts = self.alerts.process(t, cues, visible, audio_flag, temporal_flag, self.stats.level)
        a2 = time.perf_counter()

        self.last_tracks = visible
        self.analysed += 1
        self.timings["detect"] = 0.8 * self.timings["detect"] + 0.2 * (a1 - a0) * 1000
        self.timings["track_rules"] = 0.8 * self.timings["track_rules"] + 0.2 * (a2 - a1) * 1000
        return alerts, bucket

    def _temporal_step(self, t: float, small: np.ndarray):
        if self.temporal.name == "none":
            return
        self.window.append((t, small))
        while self.window and self.window[0][0] < t - self.s["window_seconds"]:
            self.window.popleft()
        if t - self._last_window_eval >= self.s["window_hop_seconds"] and len(self.window) >= 4:
            self._last_window_eval = t
            frames = [f for (_t, f) in self.window]
            idx = np.linspace(0, len(frames) - 1, 40).astype(int)      # sample to 40 frames
            score = float(self.temporal.predict([frames[i] for i in idx]))
            if score >= 0.5:
                self._temporal_t = t
            self._temporal_score = score

    # ---------- drawing
    def _draw(self, display: np.ndarray, dscale: float, t: float) -> np.ndarray:
        out = display
        k = self.last_boxes_scale and (dscale / self.last_boxes_scale)
        colour = STAGE_COLOURS.get(self.active_stage, STAGE_COLOURS["ok"])
        for tr in self.last_tracks:
            x1, y1, x2, y2 = [int(v * k) for v in tr.box]
            cv2.rectangle(out, (x1, y1), (x2, y2), colour, 2)
            label = f"#{tr.id}"
            if tr.speed_kmh is not None:
                label += f" {tr.speed_kmh:.0f} km/h"
            cv2.putText(out, label, (x1, max(12, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)
        if self.active_stage != "ok":
            text = "Accident confirmed" if self.active_stage == "confirmed" else "Suspected accident"
            cv2.rectangle(out, (0, 0), (out.shape[1], 24), colour, -1)
            cv2.putText(out, text, (8, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        if self.recorder.recording_manually:
            cv2.circle(out, (out.shape[1] - 18, 16), 6, (60, 76, 231), -1)
            cv2.putText(out, "REC", (out.shape[1] - 56, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 76, 231), 1, cv2.LINE_AA)
        return out

    def close(self):
        b = self.stats.flush()
        self.recorder.flush()
        return b
