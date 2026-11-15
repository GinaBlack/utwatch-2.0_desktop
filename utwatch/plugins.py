"""Slots for the models that are added later. The app already runs with the built-in fallbacks.

Detector:   detect(frame_bgr) -> list[Detection]            (boxes in the pixels of the frame it receives)
Temporal:   predict(frames_bgr: list[ndarray]) -> float     (accident score 0..1 for one analysis window)
Audio:      predict(samples: ndarray, sample_rate) -> float (crash score 0..1 for one window)

Register a new model with register_*() and choose it by name in Settings.
"""
from __future__ import annotations
import numpy as np

from .models import Detection
from .pipeline.motion_detector import MotionDetector

COCO_TO_ROAD = {"car": "car", "truck": "truck", "bus": "bus", "motorcycle": "motorcycle", "bicycle": "motorcycle",
                "person": "person", "bird": "animal", "cat": "animal", "dog": "animal", "horse": "animal",
                "sheep": "animal", "cow": "animal"}

_DETECTORS: dict = {}
_TEMPORAL: dict = {}
_AUDIO: dict = {}


def register_detector(name, factory): _DETECTORS[name] = factory
def register_temporal(name, factory): _TEMPORAL[name] = factory
def register_audio(name, factory): _AUDIO[name] = factory


# ---------------- built-in fallbacks
class NoTemporal:
    name = "none"
    def predict(self, frames): return 0.0


class NoAudio:
    name = "none"
    def predict(self, samples, sample_rate): return 0.0


class RmsTransientAudio:
    """Placeholder crash-sound cue: a sudden jump in loudness. Replace with YAMNet later."""
    name = "rms_transient"

    def __init__(self, ratio=6.0, floor=0.02):
        self.ratio, self.floor = ratio, floor

    def predict(self, samples: np.ndarray, sample_rate: int) -> float:
        x = np.asarray(samples, dtype=np.float32)
        if x.size < sample_rate // 4:
            return 0.0
        n = max(1, sample_rate // 20)                      # 50 ms blocks
        blocks = x[: (x.size // n) * n].reshape(-1, n)
        rms = np.sqrt((blocks ** 2).mean(axis=1)) + 1e-9
        base = np.median(rms)
        peak = rms.max()
        if peak < self.floor:
            return 0.0
        return float(min(1.0, (peak / base) / (self.ratio * 2)))


# ---------------- model plug-ins (written, not yet tested without the model packages)
class UltralyticsDetector:
    """YOLO11n pretrained on COCO. Needs: pip install ultralytics. Weights path from Settings, or 'yolo11n.pt'."""
    name = "ultralytics"

    def __init__(self, weights="", conf=0.35, imgsz=480):
        from ultralytics import YOLO          # imported late so the app starts without it
        self.model = YOLO(weights or "yolo11n.pt")
        self.conf, self.imgsz = conf, imgsz

    def detect(self, frame_bgr):
        res = self.model.predict(frame_bgr, conf=self.conf, imgsz=self.imgsz, verbose=False, device="cpu")[0]
        names, out = res.names, []
        for b in res.boxes:
            label = COCO_TO_ROAD.get(names[int(b.cls)])
            if label is None:
                continue
            x1, y1, x2, y2 = map(float, b.xyxy[0])
            out.append(Detection(x1, y1, x2, y2, label, float(b.conf)))
        return out


class TorchConvLSTM:
    """Slot for the retrained ConvLSTM. Implement load/predict when the new weights exist."""
    name = "convlstm_torch"

    def __init__(self, weights=""):
        raise NotImplementedError("Add the retrained ConvLSTM here: load the weights, resize the window to 40 frames, return a 0..1 score.")


class YamnetAudio:
    """Slot for YAMNet. Sum the scores of crash-related AudioSet classes and return 0..1."""
    name = "yamnet"

    def __init__(self, weights=""):
        raise NotImplementedError("Add YAMNet here: load the model, score the window, map class scores to a crash score.")


register_detector("motion", lambda weights="": MotionDetector())
register_detector("ultralytics", lambda weights="": UltralyticsDetector(weights))
register_temporal("none", lambda weights="": NoTemporal())
register_temporal("convlstm_torch", lambda weights="": TorchConvLSTM(weights))
register_audio("none", lambda weights="": NoAudio())
register_audio("rms_transient", lambda weights="": RmsTransientAudio())
register_audio("yamnet", lambda weights="": YamnetAudio(weights))


def _make(registry, name, weights, fallback):
    try:
        return registry[name](weights), None
    except Exception as exc:                         # missing package, missing weights, not implemented yet
        return registry[fallback](""), f"{name} unavailable ({exc}); using {fallback}"


def create_detector(name, weights=""): return _make(_DETECTORS, name, weights, "motion")
def create_temporal(name, weights=""): return _make(_TEMPORAL, name, weights, "none")
def create_audio(name, weights=""): return _make(_AUDIO, name, weights, "none")


def available():
    return {"detector": sorted(_DETECTORS), "temporal": sorted(_TEMPORAL), "audio": sorted(_AUDIO)}
