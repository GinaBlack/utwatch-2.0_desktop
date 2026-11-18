"""Microphone monitor. Uses the optional 'sounddevice' package; without it the app runs with audio off."""
from __future__ import annotations
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from . import plugins

SAMPLE_RATE = 16000


class AudioMonitor(QObject):
    score = Signal(float)          # crash score 0..1 for the latest window
    status = Signal(str, bool)     # message, healthy

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.s = dict(settings)
        self.classifier = None
        self.stream = None
        self._buf = np.zeros(0, dtype=np.float32)
        self._lock = threading.Lock()
        self._last_sample = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._evaluate)
        self.healthy = False

    def configure(self, settings: dict):
        self.s = dict(settings)
        self.classifier, warn = plugins.create_audio(
            self.s["audio"], self.s["audio_weights"], self.s["audio_scaler_weights"])
        if warn:
            self.status.emit(warn, False)

    def start(self, device=None):
        self.configure(self.s)
        if self.classifier.name == "none":
            self.healthy = False
            self.status.emit("Audio off (no audio model selected)", False)
            return
        try:
            import sounddevice as sd
        except ImportError:
            self.status.emit("Audio unavailable: install the sounddevice package", False)
            return
        try:
            self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=device,
                                         callback=self._callback)
            self.stream.start()
        except Exception as exc:
            self.status.emit(f"Microphone unavailable: {exc}", False)
            return
        self._timer.start()
        self.healthy = True
        self.status.emit("Microphone listening", True)

    def stop(self):
        self._timer.stop()
        if self.stream is not None:
            try:
                self.stream.stop(); self.stream.close()
            except Exception:
                pass
            self.stream = None

    def _callback(self, indata, frames, time_info, status):
        with self._lock:
            self._buf = np.concatenate([self._buf, indata[:, 0].copy()])[-int(SAMPLE_RATE * max(8.0, self.s["window_seconds"] + 1)):]
            self._last_sample = time.time()

    def _evaluate(self):
        if self.stream is None:
            return
        if time.time() - self._last_sample > 5.0:
            if self.healthy:
                self.healthy = False
                self.status.emit("No audio samples for 5 s", False)
            return
        if not self.healthy:
            self.healthy = True
            self.status.emit("Microphone listening", True)
        with self._lock:
            samples = self._buf[-int(SAMPLE_RATE * self.s["window_seconds"]):].copy()
        self.score.emit(float(self.classifier.predict(samples, SAMPLE_RATE)))

    def simulate(self, value: float = 0.9):
        """Pretend a crash sound was heard (used by the Tools menu and by tests)."""
        self.score.emit(float(value))
