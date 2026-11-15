"""Rule-based incident cues from tracked objects."""
from __future__ import annotations
import itertools

from ..models import Cue, Severity, VEHICLE_LABELS
from .tracker import iou


class RuleEngine:
    def __init__(self, settings: dict, speed_limit_kmh: float | None = None, ignore_zones=None):
        self.s = settings
        self.speed_limit = speed_limit_kmh
        self.zones = ignore_zones or []         # list of (x1,y1,x2,y2) in fractions of the frame
        self._contact: dict[tuple, int] = {}    # pair -> consecutive frames in contact
        self._reported_pairs: dict[tuple, float] = {}
        self._stopped: set[int] = set()
        self._stationary: set[int] = set()
        self._over: dict[int, int] = {}
        self._over_reported: dict[int, float] = {}
        self._merged: dict[int, int] = {}       # lost track id -> visible track id that swallowed it

    def update_settings(self, settings: dict, speed_limit_kmh=None):
        self.s = settings
        self.speed_limit = speed_limit_kmh

    def _in_zone(self, tr, frame_size) -> bool:
        cx, cy = tr.center
        fx, fy = cx / frame_size[0], cy / frame_size[1]
        return any(z[0] <= fx <= z[2] and z[1] <= fy <= z[3] for z in self.zones)

    def update(self, t: float, tracks: list, frame_size: tuple[int, int], lost: list | None = None) -> list[Cue]:
        """tracks: visible tracks. lost: recently vanished tracks, used to spot two blobs merging into one."""
        s, cues = self.s, []
        live = {tr.id for tr in tracks}
        self._stopped &= live
        self._stationary &= live
        for k in list(self._over):
            if k not in live:
                del self._over[k]

        vehicles = [tr for tr in tracks if tr.label in VEHICLE_LABELS]
        # collision: sustained overlap where at least one object had been moving
        seen_pairs = set()
        for a, b in itertools.combinations(vehicles, 2):
            key = (min(a.id, b.id), max(a.id, b.id))
            if iou(a.box, b.box) >= s["collision_iou"]:
                seen_pairs.add(key)
                self._contact[key] = self._contact.get(key, 0) + 1
                moving_before = max(a.max_speed_seen, b.max_speed_seen) >= s["moving_speed"]
                if (self._contact[key] >= s["collision_persist_frames"] and moving_before
                        and t - self._reported_pairs.get(key, -1e9) > 10.0):
                    self._reported_pairs[key] = t
                    cues.append(Cue("collision", Severity.HIGH, t, key, {"iou": round(iou(a.box, b.box), 2)}))
        # merge: a moving object vanishes inside another one (two blobs became one when they touched).
        # Remember which visible object swallowed it, so the contact keeps counting while the other stays missing.
        by_id = {tr.id: tr for tr in vehicles}
        lost_ids = set()
        for lo in (lost or []):
            lost_ids.add(lo.id)
            if lo.label not in VEHICLE_LABELS or lo.max_speed_seen < s["moving_speed"]:
                continue
            host = by_id.get(self._merged.get(lo.id))
            if host is None and lo.missed <= 2:
                area = max(1.0, (lo.box[2] - lo.box[0]) * (lo.box[3] - lo.box[1]))
                for v in vehicles:
                    iw = max(0.0, min(lo.box[2], v.box[2]) - max(lo.box[0], v.box[0]))
                    ih = max(0.0, min(lo.box[3], v.box[3]) - max(lo.box[1], v.box[1]))
                    if iw * ih / area >= 0.5:
                        host = v
                        self._merged[lo.id] = v.id
                        break
            if host is None:
                continue
            key = (min(lo.id, host.id), max(lo.id, host.id))
            seen_pairs.add(key)
            self._contact[key] = self._contact.get(key, 0) + 1
            if (self._contact[key] >= s["collision_persist_frames"]
                    and t - self._reported_pairs.get(key, -1e9) > 10.0):
                self._reported_pairs[key] = t
                cues.append(Cue("collision", Severity.HIGH, t, key, {"merge": True}))
        for k in list(self._merged):
            if k not in lost_ids:
                del self._merged[k]
        for key in list(self._contact):
            if key not in seen_pairs:
                self._contact[key] = 0

        for tr in tracks:
            # sudden stop: strong speed before, almost none now
            hist = list(tr.speed_hist)
            if len(hist) >= 3:
                before = [v for (tt, v) in hist if t - 1.5 <= tt <= t - 0.5]
                now = [v for (tt, v) in hist if tt > t - 0.5]
                if before and now:
                    peak, cur = max(before), sum(now) / len(now)
                    if peak >= s["moving_speed"] and cur <= (1 - s["sudden_stop_drop"]) * peak:
                        if tr.id not in self._stopped and tr.label in VEHICLE_LABELS:
                            self._stopped.add(tr.id)
                            cues.append(Cue("sudden_stop", Severity.MEDIUM, t, (tr.id,), {"peak": round(peak, 3)}))
                    elif cur >= s["moving_speed"]:
                        self._stopped.discard(tr.id)
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
