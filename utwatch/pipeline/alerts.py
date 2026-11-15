"""Two-stage alerting: a suspected accident first, then a confirmed one (or an expiry)."""
from __future__ import annotations

from ..models import Severity, STAGE_CONFIRMED, STAGE_EXPIRED, STAGE_INFO, STAGE_SUSPECTED
from . import fusion

CUE_MESSAGES = {
    "sudden_stop": "Sudden stop",
    "stationary": "Stationary object or vehicle",
    "overspeed": "Overspeeding vehicle",
}


class Incident:
    def __init__(self, incident_id: str, t0: float, involved: set, reasons: set):
        self.id = incident_id
        self.t0 = t0
        self.involved = set(involved)
        self.reasons = set(reasons)      # modalities that have flagged: video, temporal, audio
        self.confirmed = False


class AlertManager:
    def __init__(self, camera_id, camera_name: str, settings: dict):
        self.camera_id, self.camera_name, self.s = camera_id, camera_name, settings
        self.incident: Incident | None = None
        self._last_alert: dict[str, float] = {}
        self._cooldown_until = -1e9
        self._level = "low"
        self._count = 0

    def update_settings(self, settings: dict):
        self.s = settings

    def _mk(self, t, type_, severity, stage, message, incident_id=None, **details):
        return {"ts": t, "camera_id": self.camera_id, "camera_name": self.camera_name, "type": type_,
                "severity": int(severity), "stage": stage, "incident_id": incident_id,
                "message": message, "details": details}

    def _cooled(self, key: str, t: float) -> bool:
        if t - self._last_alert.get(key, -1e9) < self.s["alert_cooldown_seconds"]:
            return False
        self._last_alert[key] = t
        return True

    def process(self, t: float, cues: list, tracks: list, audio_flag=False, temporal_flag=False, congestion="low"):
        """Return a list of alert dicts produced at time t."""
        out = []
        collision_ids, video_flag = set(), False
        for c in cues:
            if c.kind == "collision":
                video_flag = True
                collision_ids |= set(c.track_ids)
            elif self._cooled(c.kind, t):
                out.append(self._mk(t, c.kind, c.severity, STAGE_INFO, CUE_MESSAGES.get(c.kind, c.kind), **c.info))
        if congestion == "high" and self._level != "high" and self._cooled("congestion", t):
            out.append(self._mk(t, "congestion", Severity.LOW, STAGE_INFO, "Heavy congestion"))
        self._level = congestion

        inc = self.incident
        flags = {m for m, v in (("video", video_flag), ("temporal", temporal_flag), ("audio", audio_flag)) if v}
        if inc is None and t >= self._cooldown_until:
            if fusion.decide(self.s["fusion_rule"], video_flag, temporal_flag, audio_flag):
                iid = f"{self.camera_id}-{int(t * 1000)}"
                inc = self.incident = Incident(iid, t, collision_ids, flags)
                out.append(self._mk(t, "accident", Severity.HIGH, STAGE_SUSPECTED, "Suspected accident", iid,
                                    evidence=sorted(flags)))
        elif inc is not None:
            inc.reasons |= flags
            inc.involved |= collision_ids
            window = self.s["window_seconds"]
            if not inc.confirmed:
                by_tracks = self._aftermath(inc, t, tracks)
                by_modalities = len(inc.reasons) >= 2
                if by_tracks or by_modalities:
                    inc.confirmed = True
                    out.append(self._mk(t, "accident", Severity.CRITICAL, STAGE_CONFIRMED, "Accident confirmed", inc.id,
                                        evidence=sorted(inc.reasons), basis="aftermath" if by_tracks else "two modalities"))
                    self._finish(t)
                elif t - inc.t0 > window:
                    out.append(self._mk(t, "accident", Severity.LOW, STAGE_EXPIRED,
                                        "Suspected accident not confirmed", inc.id, evidence=sorted(inc.reasons)))
                    self._finish(t)
        return out

    def _aftermath(self, inc: Incident, t: float, tracks: list) -> bool:
        """Confirmed when every involved object that is still tracked has stayed still long enough."""
        if not inc.involved or t - inc.t0 < self.s["aftermath_seconds"]:
            return False
        by_id = {tr.id: tr for tr in tracks}
        present = [by_id[i] for i in inc.involved if i in by_id]
        if not present:
            return False
        return all(tr.still_for(t) >= self.s["aftermath_seconds"] for tr in present)

    def _finish(self, t: float):
        self.incident = None
        self._cooldown_until = t + self.s["alert_cooldown_seconds"]
