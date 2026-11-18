"""Video sources: files, webcams and network streams, with reconnect for streams."""
from __future__ import annotations
import os
import time

import cv2


def classify_source(source: str) -> str:
    s = str(source).strip()
    if s.isdigit():
        return "webcam"
    if s.lower().startswith(("rtsp://", "rtmp://", "http://", "https://")):
        return "stream"
    return "file"


def is_nearly_black_frame(frame) -> bool:
    """Detect an almost uniformly black image without treating it as a disconnection."""
    probe = frame[::32, ::32]
    mean_luma = sum(cv2.mean(probe)[:3]) / 3
    deviation = float(cv2.meanStdDev(probe)[1].mean())
    return mean_luma < 3 and deviation < 2


class VideoSource:
    def __init__(self, source: str, loop_files: bool = True):
        self.source = str(source).strip()
        self.kind = classify_source(self.source)
        self.loop_files = loop_files
        self.cap = None
        self.fps = 25.0
        self.reconnecting = False
        self._retry_at = 0.0
        self._backoff = 1.0
        self.last_error = ""
        self.wrapped = False          # set when a looped file starts over, so callers can reset their state

    def open(self) -> bool:
        if self.cap is not None:
            self.cap.release()
        if self.kind == "webcam":
            idx = int(self.source)
            self.cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(idx)
        else:
            self.cap = cv2.VideoCapture(self.source)
        ok = bool(self.cap and self.cap.isOpened())
        if ok:
            fps = self.cap.get(cv2.CAP_PROP_FPS)
            self.fps = fps if fps and 1 <= fps <= 120 else 25.0
            self.reconnecting = False
            self._backoff = 1.0
            self.last_error = ""
        else:
            self.last_error = {"file": "Could not open the video file",
                               "webcam": "Could not open the webcam",
                               "stream": "Could not connect to the stream"}[self.kind]
        return ok

    def read(self):
        """Return (ok, frame). On failure streams and webcams retry with back-off; files loop or end."""
        if self.cap is None or not self.cap.isOpened():
            if time.time() < self._retry_at:
                return False, None
            if not self.open():
                self.reconnecting = True
                self._retry_at = time.time() + self._backoff
                self._backoff = min(30.0, self._backoff * 2)
                return False, None
        ok, frame = self.cap.read()
        if ok:
            return True, frame
        if self.kind == "file":
            if self.loop_files:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self.cap.read()
                self.wrapped = True
                return (True, frame) if ok else (False, None)
            return False, None
        self.cap.release()
        self.cap = None
        self.reconnecting = True
        self.last_error = "Lost the connection; reconnecting"
        self._retry_at = time.time() + self._backoff
        return False, None

    def release(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None
