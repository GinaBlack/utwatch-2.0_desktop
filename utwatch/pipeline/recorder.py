"""In-app recording: a ring buffer of compressed frames, event clips and manual recordings."""
from __future__ import annotations
import json
import os
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

MAX_MANUAL_SECONDS = 600
EVENT_RECORDING_SECONDS = 60.0


def _jpeg(frame, quality=75) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""


class _Clip:
    def __init__(self, incident_id, camera_id, camera_name, t_start, frames):
        self.incident_id, self.camera_id, self.camera_name = incident_id, camera_id, camera_name
        self.t_start = t_start
        self.frames = list(frames)          # (t, jpeg bytes)
        self.close_at: float | None = None
        self.outcome = "pending"


class EventRecorder:
    def __init__(self, camera_id, camera_name: str, folder: Path, settings: dict):
        self.camera_id, self.camera_name, self.folder = camera_id, camera_name, Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.s = settings
        self.ring: deque = deque()
        self.clips: dict[str, _Clip] = {}
        self._finished: list[dict] = []
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._manual = None                 # (VideoWriter, path, t_start, frame_count, size)
        self._last_push = -1e9

    def update_settings(self, settings: dict):
        self.s = settings

    @property
    def recording_manually(self) -> bool:
        return self._manual is not None

    # ---------- frame input
    def push(self, t: float, frame_bgr: np.ndarray) -> None:
        """Offer a frame; it is kept only at record_fps."""
        if t - self._last_push < 1.0 / self.s["record_fps"] - 1e-6:
            return
        self._last_push = t
        data = _jpeg(frame_bgr)
        if not data:
            return
        self.ring.append((t, data))
        horizon = t - EVENT_RECORDING_SECONDS / 2 - 1.0
        while self.ring and self.ring[0][0] < horizon:
            self.ring.popleft()
        for clip in self.clips.values():
            if clip.close_at is None or t <= clip.close_at:
                clip.frames.append((t, data))
        if self._manual is not None:
            self._write_manual(t, frame_bgr)

    # ---------- event clips
    def start_event(self, incident_id: str, t: float) -> None:
        if incident_id in self.clips:
            return
        start = t - EVENT_RECORDING_SECONDS / 2
        pre = [(ft, d) for (ft, d) in self.ring if ft >= start]
        clip = _Clip(incident_id, self.camera_id, self.camera_name, start, pre)
        clip.close_at = t + EVENT_RECORDING_SECONDS / 2
        self.clips[incident_id] = clip

    def end_event(self, incident_id: str, t: float, outcome: str) -> None:
        clip = self.clips.get(incident_id)
        if clip is not None and clip.close_at is None:
            clip.close_at = t + EVENT_RECORDING_SECONDS / 2
            clip.outcome = outcome
        elif clip is not None:
            clip.outcome = outcome

    def tick(self, t: float) -> None:
        for iid, clip in list(self.clips.items()):
            if clip.close_at is not None and t >= clip.close_at:
                del self.clips[iid]
                th = threading.Thread(target=self._finalise, args=(clip,), daemon=True)
                self._threads.append(th)
                th.start()
        self._threads = [th for th in self._threads if th.is_alive()]

    def flush(self) -> None:
        """Close every open clip now and wait for writers (used on shutdown and in tests)."""
        for iid, clip in list(self.clips.items()):
            del self.clips[iid]
            if clip.close_at is None:
                clip.outcome = clip.outcome if clip.outcome != "pending" else "interrupted"
            self._finalise(clip)
        for th in self._threads:
            th.join(timeout=30)
        self.stop_manual()

    def _finalise(self, clip: _Clip) -> None:
        if len(clip.frames) < 2:
            return
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(max(0.0, clip.t_start)))
        base = self.folder / f"{_safe(self.camera_name)}_{stamp}_{_safe(clip.incident_id)}"
        path = str(base) + ".mp4"
        first = cv2.imdecode(np.frombuffer(clip.frames[0][1], np.uint8), cv2.IMREAD_COLOR)
        h, w = first.shape[:2]
        duration = clip.frames[-1][0] - clip.frames[0][0]
        fps = max(1.0, min(30.0, (len(clip.frames) - 1) / duration)) if duration > 0 else self.s["record_fps"]
        wr = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        if not wr.isOpened():
            return
        mid = len(clip.frames) // 2
        thumb = None
        for i, (_t, d) in enumerate(clip.frames):
            img = cv2.imdecode(np.frombuffer(d, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            wr.write(img)
            if i == mid:
                thumb = img
        wr.release()
        thumb_path = str(base) + ".jpg"
        if thumb is not None:
            cv2.imwrite(thumb_path, cv2.resize(thumb, (320, int(320 * h / w))))
        meta = {"incident_id": clip.incident_id, "camera": self.camera_name, "start": clip.t_start,
                "duration": duration, "frames": len(clip.frames), "fps": fps, "outcome": clip.outcome}
        Path(path + ".json").write_text(json.dumps(meta), encoding="utf-8")
        rec = {"camera_id": self.camera_id, "camera_name": self.camera_name, "path": path, "thumb": thumb_path,
               "ts": clip.t_start, "duration": duration, "size": os.path.getsize(path),
               "incident_id": clip.incident_id, "kind": "event"}
        with self._lock:
            self._finished.append(rec)

    # ---------- manual recording (written straight to disk)
    def start_manual(self, frame_bgr: np.ndarray, t: float) -> None:
        if self._manual is not None:
            return
        h, w = frame_bgr.shape[:2]
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(t))
        path = str(self.folder / f"{_safe(self.camera_name)}_{stamp}_manual.mp4")
        wr = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), self.s["record_fps"], (w, h))
        if not wr.isOpened():
            raise RuntimeError("Could not start recording: the video encoder is not available")
        self._manual = [wr, path, t, 0, (w, h), frame_bgr.copy()]

    def _write_manual(self, t, frame):
        wr, path, t0, n, size, _ = self._manual
        if t - t0 > MAX_MANUAL_SECONDS:
            self.stop_manual()
            return
        if (frame.shape[1], frame.shape[0]) != size:
            frame = cv2.resize(frame, size)
        wr.write(frame)
        self._manual[3] = n + 1

    def stop_manual(self) -> None:
        if self._manual is None:
            return
        wr, path, t0, n, size, thumb = self._manual
        self._manual = None
        wr.release()
        if n < 1 or not os.path.exists(path):
            return
        thumb_path = path[:-4] + ".jpg"
        h, w = thumb.shape[:2]
        cv2.imwrite(thumb_path, cv2.resize(thumb, (320, int(320 * h / w))))
        rec = {"camera_id": self.camera_id, "camera_name": self.camera_name, "path": path, "thumb": thumb_path,
               "ts": t0, "duration": n / self.s["record_fps"], "size": os.path.getsize(path),
               "incident_id": None, "kind": "manual"}
        with self._lock:
            self._finished.append(rec)

    def pop_finished(self) -> list[dict]:
        with self._lock:
            out, self._finished = self._finished, []
        return out


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(name))
