"""Model-free moving-object detector (background subtraction) so the system runs end to end today.

It finds moving blobs from a fixed camera. It does not know what they are, so labels are 'object'.
Replace it with the YOLO plug-in later by changing the detector setting.
"""
from __future__ import annotations
import cv2
import numpy as np

from ..models import Detection


class MotionDetector:
    name = "motion"

    def __init__(self, min_area_fraction=0.002, warmup_frames=12, history=200, var_threshold=28, learning_rate=0.002):
        self.min_area_fraction = min_area_fraction
        self.learning_rate = learning_rate        # slow, so a stopped vehicle stays in the foreground for minutes
        self.warmup = warmup_frames
        self.bg = cv2.createBackgroundSubtractorMOG2(history=history, varThreshold=var_threshold, detectShadows=True)
        self.k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self.k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
        self.frames = 0

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        lr = -1 if self.frames < self.warmup else self.learning_rate     # learn fast at first, then slowly
        fg = self.bg.apply(frame_bgr, learningRate=lr)
        self.frames += 1
        if self.frames <= self.warmup:
            return []
        fg = (fg == 255).astype(np.uint8) * 255          # drop shadow pixels (value 127)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, self.k_open)
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, self.k_close)
        h, w = fg.shape[:2]
        min_area = self.min_area_fraction * w * h
        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out = []
        for c in contours:
            if cv2.contourArea(c) < min_area:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            out.append(Detection(x, y, x + bw, y + bh, "object", 1.0))
        return out
