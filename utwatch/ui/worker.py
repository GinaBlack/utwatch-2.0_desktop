"""One QThread per camera: read, analyse, annotate and hand results to the interface."""
from __future__ import annotations
import queue
import time

import cv2
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from ..pipeline.calibration import Calibration
from ..pipeline.engine import CameraEngine
from ..pipeline.source import VideoSource

DISPLAY_FPS = 20.0


def to_qimage(bgr) -> QImage:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


class CameraWorker(QThread):
    frame = Signal(int, QImage)
    alert = Signal(dict)
    bucket = Signal(int, dict)
    recording = Signal(dict)
    status = Signal(int, dict)
    snapshot = Signal(int, object)          # a raw frame, for the calibration dialog

    def __init__(self, camera: dict, settings: dict, rec_dir, parent=None):
        super().__init__(parent)
        self.camera = camera
        self.cam_id = camera["id"]
        self.settings = dict(settings)
        self.rec_dir = rec_dir
        self.cmds: queue.Queue = queue.Queue()
        self._stop = False
        self.last_raw = None

    # ---- commands from the interface thread
    def command(self, name: str, payload=None):
        self.cmds.put((name, payload))

    def stop(self):
        self._stop = True
        self.cmds.put(("stop", None))

    def run(self):
        engine = CameraEngine(self.camera, self.settings, self.rec_dir)
        src = VideoSource(self.camera["source"])
        t_wall0, t_media = time.time(), None
        last_display, frames, fps_t0, fps = 0.0, 0, time.time(), 0.0
        last_status = 0.0
        manual_stop = False
        while not self._stop:
            self._drain(engine)
            ok, raw = src.read()
            if not ok:
                self._emit_status(engine, src, fps, "reconnecting" if src.reconnecting else "stopped", True)
                self.msleep(250)
                continue
            if src.wrapped:
                src.wrapped = False
                engine.reset_scene()
            self.last_raw = raw
            now = time.time()
            res = engine.process(now, raw)
            frames += 1
            if now - fps_t0 >= 1.0:
                fps, frames, fps_t0 = frames / (now - fps_t0), 0, now
            if now - last_display >= 1.0 / DISPLAY_FPS:
                last_display = now
                self.frame.emit(self.cam_id, to_qimage(res["frame"]))
            for a in res["alerts"]:
                self.alert.emit(a)
            if res["bucket"]:
                self.bucket.emit(self.cam_id, res["bucket"])
            for r in res["recordings"]:
                self.recording.emit(r)
            if now - last_status >= 1.0:
                last_status = now
                self._emit_status(engine, src, fps, res["stage"], False, res)
            # pace files and webcams to their native rate so playback runs at real-time speed
            if src.kind == "file":
                target = 1.0 / src.fps
                spent = time.time() - now
                if spent < target:
                    self.msleep(int((target - spent) * 1000))
            else:
                self.msleep(1)
        engine.recorder.stop_manual()
        b = engine.close()
        if b:
            self.bucket.emit(self.cam_id, b)
        for r in engine.recorder.pop_finished():
            self.recording.emit(r)
        src.release()

    def _drain(self, engine: CameraEngine):
        while True:
            try:
                name, payload = self.cmds.get_nowait()
            except queue.Empty:
                return
            if name == "settings":
                self.settings, self.camera = dict(payload[0]), payload[1]
                engine.update_settings(*payload)
            elif name == "calibration":
                engine.set_calibration(Calibration.from_json(payload) if payload else None)
            elif name == "audio":
                engine.audio_input(time.time(), payload)
            elif name == "manual_start" and self.last_raw is not None:
                try:
                    engine.recorder.start_manual(self.last_raw, time.time())
                except RuntimeError as exc:
                    self.status.emit(self.cam_id, {"state": "error", "message": str(exc)})
            elif name == "manual_stop":
                engine.recorder.stop_manual()
            elif name == "snapshot" and self.last_raw is not None:
                self.snapshot.emit(self.cam_id, self.last_raw.copy())
            elif name == "stop":
                self._stop = True

    def _emit_status(self, engine, src, fps, state, failed, res=None):
        self.status.emit(self.cam_id, {
            "state": state, "fps": round(fps, 1), "message": src.last_error if failed else "",
            "timings": {k: round(v, 1) for k, v in engine.timings.items()},
            "analysed": engine.analysed, "vehicles": engine.stats.current, "level": engine.stats.level,
            "recording": engine.recorder.recording_manually, "notes": list(engine.notes),
            "detector": engine.detector.name, "temporal": engine.temporal.name,
            "tracks": len(engine.last_tracks)})
