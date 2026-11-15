"""Small IoU tracker with smoothed speed, stillness timing and optional ground-plane speed."""
from __future__ import annotations
from collections import deque
import math

from ..models import Detection


def iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


class Track:
    def __init__(self, tid: int, det: Detection, t: float):
        self.id = tid
        self.label = det.label
        self.box = det.box
        self.hits = 1
        self.missed = 0
        self.created = t
        self.last_t = t
        self.speed_n = 0.0           # frame diagonals per second, smoothed
        self.speed_ms = None         # metres per second when calibrated
        self.ground = None
        self.speed_hist: deque = deque(maxlen=64)   # (t, speed_n)
        self.still_since: float | None = t
        self.max_speed_seen = 0.0

    @property
    def center(self):
        return ((self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2)

    @property
    def foot(self):
        return ((self.box[0] + self.box[2]) / 2, self.box[3])

    def still_for(self, t: float) -> float:
        return 0.0 if self.still_since is None else max(0.0, t - self.still_since)

    @property
    def speed_kmh(self):
        return None if self.speed_ms is None else self.speed_ms * 3.6


class IoUTracker:
    def __init__(self, iou_threshold=0.15, max_missed=6, min_hits=2, stationary_speed=0.008, ema=0.5):
        self.iou_threshold = iou_threshold
        self.max_missed = max_missed
        self.min_hits = min_hits
        self.stationary_speed = stationary_speed
        self.ema = ema
        self.tracks: dict[int, Track] = {}
        self._next = 1

    def reset(self):
        self.tracks.clear()

    def update(self, detections: list[Detection], t: float, frame_size: tuple[int, int], calibration=None):
        diag = math.hypot(*frame_size) or 1.0
        pairs = []
        for tid, tr in self.tracks.items():
            for di, d in enumerate(detections):
                v = iou(tr.box, d.box)
                if v >= self.iou_threshold:
                    pairs.append((v, tid, di))
        pairs.sort(reverse=True)
        used_t, used_d = set(), set()
        for v, tid, di in pairs:
            if tid in used_t or di in used_d:
                continue
            used_t.add(tid); used_d.add(di)
            self._apply(self.tracks[tid], detections[di], t, diag, frame_size, calibration)
        for tid, tr in list(self.tracks.items()):
            if tid not in used_t:
                tr.missed += 1
                if tr.missed > self.max_missed:
                    del self.tracks[tid]
        for di, d in enumerate(detections):
            if di not in used_d:
                tr = Track(self._next, d, t)
                self._next += 1
                if calibration is not None:
                    tr.ground = calibration.to_ground(*tr.foot, frame_size)
                self.tracks[tr.id] = tr
        return [tr for tr in self.tracks.values() if tr.hits >= self.min_hits and tr.missed == 0]

    def _apply(self, tr: Track, det: Detection, t: float, diag: float, frame_size, calibration):
        dt = max(t - tr.last_t, 1e-3)
        cx0, cy0 = tr.center
        tr.box = det.box
        if det.label != "object":
            tr.label = det.label
        cx1, cy1 = tr.center
        inst = math.hypot(cx1 - cx0, cy1 - cy0) / dt / diag
        tr.speed_n = inst if tr.hits <= 1 else self.ema * inst + (1 - self.ema) * tr.speed_n
        if calibration is not None:
            g = calibration.to_ground(*tr.foot, frame_size)
            if tr.ground is not None and not any(math.isnan(v) for v in g):
                inst_ms = math.hypot(g[0] - tr.ground[0], g[1] - tr.ground[1]) / dt
                tr.speed_ms = inst_ms if tr.speed_ms is None else self.ema * inst_ms + (1 - self.ema) * tr.speed_ms
            tr.ground = g
        tr.hits += 1
        tr.missed = 0
        tr.last_t = t
        tr.speed_hist.append((t, tr.speed_n))
        tr.max_speed_seen = max(tr.max_speed_seen, tr.speed_n)
        if tr.speed_n <= self.stationary_speed:
            if tr.still_since is None:
                tr.still_since = t
        else:
            tr.still_since = None
