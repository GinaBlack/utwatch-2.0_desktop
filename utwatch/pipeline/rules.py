"""Rule-based incident cues from tracked objects."""
from __future__ import annotations
from ..models import Cue, Severity, VEHICLE_LABELS


class RuleEngine:
    def __init__(self, settings: dict, speed_limit_kmh: float | None = None, ignore_zones=None):
        self.s = settings
        self.speed_limit = speed_limit_kmh
        self.zones = ignore_zones or []         # list of (x1,y1,x2,y2) in fractions of the frame
        self._stationary: set[int] = set()
        self._over: dict[int, int] = {}
        self._over_reported: dict[int, float] = {}

    def update_settings(self, settings: dict, speed_limit_kmh=None):
        self.s = settings
        self.speed_limit = speed_limit_kmh

    def _in_zone(self, tr, frame_size) -> bool:
        cx, cy = tr.center
        fx, fy = cx / frame_size[0], cy / frame_size[1]
        return any(z[0] <= fx <= z[2] and z[1] <= fy <= z[3] for z in self.zones)

    def update(self, t: float, tracks: list, frame_size: tuple[int, int]) -> list[Cue]:
        s, cues = self.s, []
        live = {tr.id for tr in tracks}
        self._stationary &= live
        for k in list(self._over):
            if k not in live:
                del self._over[k]

        for tr in tracks:
            # stationary object
            if tr.still_for(t) >= s["stationary_seconds"] and not self._in_zone(tr, frame_size):
                if tr.id not in self._stationary:
                    self._stationary.add(tr.id)
                    cues.append(Cue("stationary", Severity.MEDIUM, t, (tr.id,),
                                    {"seconds": round(tr.still_for(t), 1), "label": tr.label}))
            elif tr.still_since is None:
                self._stationary.discard(tr.id)
            # overspeed (needs calibration)
            if self.speed_limit and tr.speed_kmh is not None and tr.label in VEHICLE_LABELS:
                if tr.speed_kmh > self.speed_limit:
                    self._over[tr.id] = self._over.get(tr.id, 0) + 1
                    if (self._over[tr.id] >= s["overspeed_frames"]
                            and t - self._over_reported.get(tr.id, -1e9) > 10.0):
                        self._over_reported[tr.id] = t
                        cues.append(Cue("overspeed", Severity.HIGH, t, (tr.id,),
                                        {"kmh": round(tr.speed_kmh, 1), "limit": self.speed_limit}))
                else:
                    self._over[tr.id] = 0
        return cues
