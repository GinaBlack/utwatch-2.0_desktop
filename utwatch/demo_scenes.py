"""Synthetic fixed-camera scenes: lets you try the whole app without real footage or models."""
from __future__ import annotations
import cv2
import numpy as np

W, H, FPS = 640, 360, 25


def road_background(seed=1):
    rng = np.random.default_rng(seed)
    bg = np.full((H, W, 3), (92, 96, 100), np.uint8)
    cv2.rectangle(bg, (0, 60), (W, 300), (70, 72, 76), -1)
    for x in range(0, W, 60):
        cv2.line(bg, (x, 180), (x + 30, 180), (220, 220, 220), 2)
    bg = np.clip(bg.astype(np.int16) + rng.integers(-6, 7, bg.shape), 0, 255).astype(np.uint8)
    return bg


_TEX = {}


def _vehicle(frame, x, y, w=72, h=38, colour=(40, 60, 200)):
    """A textured box (real vehicles are not flat colour, and flat boxes fade into the background model)."""
    key = (w, h, colour)
    if key not in _TEX:
        rng = np.random.default_rng(sum(colour))
        tex = np.full((h, w, 3), colour, np.int16) + rng.integers(-45, 46, (h, w, 1))
        tex = np.clip(tex, 0, 255).astype(np.uint8)
        cv2.rectangle(tex, (8, 6), (w - 8, h - 6), (230, 230, 230), 2)
        cv2.line(tex, (w // 2, 6), (w // 2, h - 6), (30, 30, 30), 2)
        _TEX[key] = tex
    x, y = int(x), int(y)
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, W), min(y + h, H)
    if x1 > x0 and y1 > y0:
        frame[y0:y1, x0:x1] = _TEX[key][y0 - y:y1 - y, x0 - x:x1 - x]


LEAD = 3.5        # seconds of empty road so the background model can settle


def frames(scenario: str, seconds: float = 17.0):
    """Yield (t, frame). Scenarios: collision, pass, obstacle, empty."""
    bg = road_background()
    rng = np.random.default_rng(7)
    n = int(seconds * FPS)
    for i in range(n):
        t = i / FPS
        f = bg.copy()
        f = np.clip(f.astype(np.int16) + rng.integers(-2, 3, f.shape), 0, 255).astype(np.uint8)
        tau = t - LEAD
        if scenario == "collision":
            # two cars in the same lane head toward each other; they overlap from about t=7.4 s and both stop
            if tau > 0:
                _vehicle(f, min(-60 + 100 * tau, 280), 150, colour=(40, 60, 200))
                _vehicle(f, max(700 - 100 * tau, 300), 150, colour=(200, 120, 40))
        elif scenario == "pass":
            if tau > 0:
                _vehicle(f, -60 + 100 * tau, 90, colour=(40, 60, 200))
                _vehicle(f, 700 - 100 * tau, 230, colour=(200, 120, 40))
        elif scenario == "obstacle":
            if t > 4.0:
                _vehicle(f, 300, 170, 50, 30, colour=(30, 160, 60))
        yield t, f


def write_video(path, scenario: str, seconds: float = 17.0):
    """Write a synthetic scene to an mp4 file so the whole app can be run against it."""
    wr = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for _t, f in frames(scenario, seconds):
        wr.write(f)
    wr.release()
    return path
