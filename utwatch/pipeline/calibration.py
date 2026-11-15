"""Per-camera ground-plane calibration (pixels to metres) from a known rectangle."""
from __future__ import annotations
import json
import math
import numpy as np
import cv2


class Calibration:
    def __init__(self, H: np.ndarray, image_size: tuple[int, int]):
        self.H = np.asarray(H, dtype=np.float64)
        self.image_size = (int(image_size[0]), int(image_size[1]))   # size of the frame the points were clicked on

    @classmethod
    def from_points(cls, pixel_points, width_m: float, length_m: float, image_size):
        """pixel_points: four clicks in order top-left, top-right, bottom-right, bottom-left of a road rectangle
        whose real width and length (metres) are known."""
        pts = np.asarray(pixel_points, dtype=np.float32)
        if pts.shape != (4, 2):
            raise ValueError("Click exactly four points")
        if width_m <= 0 or length_m <= 0:
            raise ValueError("Width and length must be greater than zero")
        dst = np.array([[0, 0], [width_m, 0], [width_m, length_m], [0, length_m]], dtype=np.float32)
        H = cv2.getPerspectiveTransform(pts, dst)
        return cls(H, image_size)

    def to_ground(self, x: float, y: float, frame_size: tuple[int, int] | None = None):
        """Map a pixel in a frame of frame_size (default: the calibration frame) to ground metres."""
        if frame_size and tuple(frame_size) != self.image_size:
            x = x * self.image_size[0] / frame_size[0]
            y = y * self.image_size[1] / frame_size[1]
        v = self.H @ np.array([x, y, 1.0])
        if abs(v[2]) < 1e-9:
            return (float("nan"), float("nan"))
        return (float(v[0] / v[2]), float(v[1] / v[2]))

    def distance_m(self, p1, p2, frame_size=None) -> float:
        a, b = self.to_ground(*p1, frame_size), self.to_ground(*p2, frame_size)
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def to_json(self) -> dict:
        return {"H": self.H.tolist(), "image_size": list(self.image_size)}

    @classmethod
    def from_json(cls, data) -> "Calibration":
        if isinstance(data, str):
            data = json.loads(data)
        return cls(np.array(data["H"]), tuple(data["image_size"]))
