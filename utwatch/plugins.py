"""Slots for the models that are added later. The app already runs with the built-in fallbacks.

Detector:   detect(frame_bgr) -> list[Detection]            (boxes in the pixels of the frame it receives)
Temporal:   predict(frames_bgr: list[ndarray]) -> float     (accident score 0..1 for one analysis window)
Audio:      predict(samples: ndarray, sample_rate) -> float (crash score 0..1 for one window)

Register a new model with register_*() and choose it by name in Settings.
"""
from __future__ import annotations
import threading
from pathlib import Path

import cv2
import numpy as np

from . import config
from .models import Detection
from .pipeline.motion_detector import MotionDetector

COCO_TO_ROAD = {
    "car": "car", "truck": "truck", "bus": "bus", "motorcycle": "motorcycle", "motocycle": "motorcycle",
    "bicycle": "motorcycle", "person": "person", "bird": "animal", "cat": "animal", "dog": "animal",
    "horse": "animal", "sheep": "animal", "cow": "animal", "vehicle": "vehicle", "accident": "accident",
    "obstacle": "object",
}

_DETECTORS: dict = {}
_TEMPORAL: dict = {}
_AUDIO: dict = {}
_MODEL_CACHE: dict = {}
_MODEL_CACHE_LOCK = threading.Lock()


def _cached_model(kind: str, weights: str, factory):
    key = (kind, str(weights or ""))
    with _MODEL_CACHE_LOCK:
        if key not in _MODEL_CACHE:
            _MODEL_CACHE[key] = factory()
        return _MODEL_CACHE[key]


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
    """UTWatch fine-tuned YOLO detector from models/best.pt."""
    name = "utwatch_yolo"

    def __init__(self, weights="", conf=0.35, imgsz=640):
        from ultralytics import YOLO
        path = weights or config.model_path("best.pt")
        if not Path(path).is_file():
            raise FileNotFoundError(f"Object detector weights not found: {path}")
        self.model = YOLO(path)
        if self.model.task != "detect":
            raise ValueError(f"Expected an object-detection model, got task {self.model.task!r}")
        self.conf, self.imgsz = conf, imgsz
        self._lock = threading.Lock()

    def detect(self, frame_bgr):
        with self._lock:
            res = self.model.predict(frame_bgr, conf=self.conf, imgsz=self.imgsz, verbose=False, device="cpu")[0]
        names, out = res.names, []
        for b in res.boxes:
            label = COCO_TO_ROAD.get(names[int(b.cls)])
            if label is None:
                continue
            x1, y1, x2, y2 = map(float, b.xyxy[0])
            out.append(Detection(x1, y1, x2, y2, label, float(b.conf)))
        return out


def _accident_network(torch):
    nn = torch.nn

    class BasicBlock(nn.Module):
        expansion = 1

        def __init__(self, in_channels, channels, stride=1):
            super().__init__()
            self.conv1 = nn.Conv2d(in_channels, channels, kernel_size=3, stride=stride, padding=1, bias=False)
            self.bn1 = nn.BatchNorm2d(channels)
            self.relu = nn.ReLU(inplace=True)
            self.conv2 = nn.Conv2d(channels, channels, kernel_size=3, padding=1, bias=False)
            self.bn2 = nn.BatchNorm2d(channels)
            self.downsample = (nn.Sequential(nn.Conv2d(in_channels, channels, kernel_size=1, stride=stride, bias=False),
                                             nn.BatchNorm2d(channels)) if stride != 1 or in_channels != channels else None)

        def forward(self, x):
            identity = x
            out = self.relu(self.bn1(self.conv1(x)))
            out = self.bn2(self.conv2(out))
            if self.downsample is not None:
                identity = self.downsample(x)
            return self.relu(out + identity)

    class ConvLSTMCell(nn.Module):
        def __init__(self, input_channels, hidden_channels):
            super().__init__()
            self.hidden_channels = hidden_channels
            self.conv = nn.Conv2d(input_channels + hidden_channels, 4 * hidden_channels, 3, padding=1)

        def forward(self, x, state):
            h, c = state
            gates = self.conv(torch.cat((x, h), dim=1))
            i, f, o, g = torch.chunk(gates, 4, dim=1)
            c = torch.sigmoid(f) * c + torch.sigmoid(i) * torch.tanh(g)
            h = torch.sigmoid(o) * torch.tanh(c)
            return h, c

    class AccidentConvLSTM(nn.Module):
        def __init__(self):
            super().__init__()
            self.cnn = nn.Sequential(
                nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3, bias=False),
                nn.BatchNorm2d(64),
                nn.ReLU(inplace=True),
                nn.MaxPool2d(kernel_size=3, stride=2, padding=1),
                nn.Sequential(BasicBlock(64, 64), BasicBlock(64, 64)),
                nn.Sequential(BasicBlock(64, 128, 2), BasicBlock(128, 128)),
                nn.Sequential(BasicBlock(128, 256, 2), BasicBlock(256, 256)),
                nn.Sequential(BasicBlock(256, 512, 2), BasicBlock(512, 512)),
                nn.AdaptiveAvgPool2d((1, 1)),
            )
            self.convlstm = nn.Module()
            self.convlstm.cell_list = nn.ModuleList((
                ConvLSTMCell(512, 64),
                ConvLSTMCell(64, 64),
            ))
            self.classifier = nn.Sequential(
                nn.AdaptiveAvgPool2d((1, 1)),
                nn.Flatten(),
                nn.Dropout(0.5),
                nn.Linear(64, 64),
                nn.ReLU(inplace=True),
                nn.Linear(64, 1),
            )

        def forward(self, frames):
            batch, steps, channels, height, width = frames.shape
            features = self.cnn(frames.reshape(batch * steps, channels, height, width))
            _, feature_channels, feature_height, feature_width = features.shape
            sequence = features.reshape(batch, steps, feature_channels, feature_height, feature_width)
            for cell in self.convlstm.cell_list:
                h = features.new_zeros(batch, cell.hidden_channels, feature_height, feature_width)
                c = torch.zeros_like(h)
                outputs = []
                for step in range(steps):
                    h, c = cell(sequence[:, step], (h, c))
                    outputs.append(h)
                sequence = torch.stack(outputs, dim=1)
            return self.classifier(sequence[:, -1])

    return AccidentConvLSTM()


class TorchAccidentClassifier:
    """ResNet18 + ConvLSTM accident classifier from models/accident_classifier(1).pth."""

    name = "accident_convlstm"

    def __init__(self, weights=""):
        import torch
        path = weights or config.model_path("accident_classifier(1).pth")
        if not Path(path).is_file():
            raise FileNotFoundError(f"Accident classifier weights not found: {path}")
        self.torch = torch
        self.device = torch.device("cpu")
        self.model = _accident_network(torch)
        state = torch.load(path, map_location=self.device, weights_only=True)
        self.model.load_state_dict(state, strict=True)
        self.model.to(self.device).eval()
        self.mean = torch.tensor((0.485, 0.456, 0.406), device=self.device).view(1, 3, 1, 1)
        self.std = torch.tensor((0.229, 0.224, 0.225), device=self.device).view(1, 3, 1, 1)

    def predict(self, frames_bgr: list[np.ndarray]) -> float:
        if not frames_bgr:
            return 0.0
        torch = self.torch
        tensors = []
        for frame in frames_bgr:
            rgb = frame[..., ::-1].copy()
            resized = cv2.resize(rgb, (224, 224), interpolation=cv2.INTER_AREA)
            tensor = torch.from_numpy(resized).to(device=self.device, dtype=torch.float32).permute(2, 0, 1) / 255.0
            tensors.append(tensor)
        batch = torch.stack(tensors).unsqueeze(0)
        batch = (batch - self.mean.unsqueeze(1)) / self.std.unsqueeze(1)
        with torch.inference_mode():
            logit = self.model(batch)
            return float(torch.sigmoid(logit).item())


class TrainedAudioClassifier:
    """40-coefficient MFCC MLP using the supplied audio model and StandardScaler."""

    name = "trained_audio"

    def __init__(self, weights="", scaler_weights=""):
        import joblib
        import torch
        model_path = weights or config.model_path("audio_model.pth")
        scaler_path = scaler_weights or config.model_path("scaler.pkl")
        if not Path(model_path).is_file():
            raise FileNotFoundError(f"Audio model weights not found: {model_path}")
        if not Path(scaler_path).is_file():
            raise FileNotFoundError(f"Audio feature scaler not found: {scaler_path}")
        self.torch = torch
        self.scaler = joblib.load(scaler_path)
        if getattr(self.scaler, "n_features_in_", None) != 40:
            raise ValueError("The audio feature scaler must accept exactly 40 features")
        self.model = torch.nn.Sequential(
            torch.nn.Linear(40, 128), torch.nn.ReLU(), torch.nn.Dropout(0.3),
            torch.nn.Linear(128, 64), torch.nn.ReLU(), torch.nn.Dropout(0.3),
            torch.nn.Linear(64, 1),
        )
        state = torch.load(model_path, map_location="cpu", weights_only=True)
        self.model.load_state_dict({key.removeprefix("net."): value for key, value in state.items()}, strict=True)
        self.model.eval()

    def predict(self, samples: np.ndarray, sample_rate: int) -> float:
        import librosa
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if x.size < 512:
            return 0.0
        if float(np.sqrt(np.mean(np.square(x)))) < 0.005:
            return 0.0
        mfcc = librosa.feature.mfcc(y=x, sr=sample_rate, n_mfcc=40)
        features = mfcc.mean(axis=1, dtype=np.float64).reshape(1, -1)
        normalized = self.scaler.transform(features).astype(np.float32)
        with self.torch.inference_mode():
            logits = self.model(self.torch.from_numpy(normalized))
            return float(self.torch.sigmoid(logits).item())

register_detector("motion", lambda weights="": MotionDetector())
register_detector("ultralytics", lambda weights="": _cached_model(
    "ultralytics", weights or config.model_path("best.pt"), lambda: UltralyticsDetector(weights)))
register_temporal("none", lambda weights="": NoTemporal())
register_temporal("accident_convlstm", lambda weights="": _cached_model(
    "accident_convlstm", weights or config.model_path("accident_classifier(1).pth"),
    lambda: TorchAccidentClassifier(weights)))
register_audio("none", lambda weights="": NoAudio())
register_audio("rms_transient", lambda weights="": RmsTransientAudio())
register_audio("trained_audio", lambda weights="": _cached_model(
    "trained_audio", weights or config.model_path("audio_model.pth"),
    lambda: TrainedAudioClassifier(weights)))


def _make(registry, name, weights, fallback):
    try:
        return registry[name](weights), None
    except Exception as exc:                         # missing package, missing weights, not implemented yet
        return registry[fallback](""), f"{name} unavailable ({exc}); using {fallback}"


def create_detector(name, weights=""): return _make(_DETECTORS, name, weights, "motion")
def create_temporal(name, weights=""): return _make(_TEMPORAL, name, weights, "none")
def create_audio(name, weights="", scaler_weights=""):
    if name == "trained_audio":
        try:
            return _cached_model("trained_audio", (weights or config.model_path("audio_model.pth"),
                                                    scaler_weights or config.model_path("scaler.pkl")),
                                 lambda: TrainedAudioClassifier(weights, scaler_weights)), None
        except Exception as exc:
            return _AUDIO["none"](""), f"{name} unavailable ({exc}); using none"
    return _make(_AUDIO, name, weights, "none")


def available():
    return {"detector": sorted(_DETECTORS), "temporal": sorted(_TEMPORAL), "audio": sorted(_AUDIO)}
