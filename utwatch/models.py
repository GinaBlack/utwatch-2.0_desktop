"""Shared data types."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @property
    def label(self) -> str:
        return self.name.capitalize()


STAGE_INFO = "info"
STAGE_SUSPECTED = "suspected"
STAGE_CONFIRMED = "confirmed"
STAGE_EXPIRED = "expired"

VEHICLE_LABELS = {"car", "truck", "bus", "motorcycle", "vehicle", "object"}

ROLES = ("administrator", "officer", "responder")
# permission name -> roles allowed
PERMISSIONS = {
    "view": ("administrator", "officer", "responder"),
    "acknowledge": ("administrator", "officer"),
    "record_manual": ("administrator", "officer"),
    "export": ("administrator", "officer"),
    "protect_clip": ("administrator", "officer"),
    "delete_clip": ("administrator",),
    "manage_cameras": ("administrator",),
    "manage_settings": ("administrator",),
    "manage_users": ("administrator",),
    "view_audit": ("administrator",),
}


def can(role: str, permission: str) -> bool:
    return role in PERMISSIONS.get(permission, ())


@dataclass
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    label: str = "object"
    conf: float = 1.0

    @property
    def box(self):
        return (self.x1, self.y1, self.x2, self.y2)


@dataclass
class Cue:
    """One piece of evidence produced by the rules."""
    kind: str                      # stationary, overspeed
    severity: Severity
    t: float
    track_ids: tuple = ()
    info: dict = field(default_factory=dict)
