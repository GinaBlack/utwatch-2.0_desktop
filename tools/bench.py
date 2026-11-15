"""Time the analysis pipeline on THIS computer (this is the measurement the plan needs from your laptop).

   python tools/bench.py path\to\video.mp4 [--detector motion|ultralytics] [--weights yolo11n.pt] [--seconds 60]

It feeds the video through the same engine the app uses, as fast as the CPU allows, and prints milliseconds per stage,
frames analysed per second, and memory. Run it with the laptop plugged in and other programs closed."""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv2
from utwatch import config
from utwatch.pipeline.engine import CameraEngine
import tempfile


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--detector", default="motion")
    ap.add_argument("--weights", default="")
    ap.add_argument("--seconds", type=float, default=60.0, help="video time to process")
    ap.add_argument("--width", type=int, default=480, help="analysis width in pixels")
    a = ap.parse_args()
    s = config.defaults()
    s.update(detector=a.detector, detector_weights=a.weights, analysis_fps=1000, analysis_width=a.width, record_fps=1)
    cam = {"id": 1, "name": "bench", "calibration": None, "speed_limit_kmh": None}
    eng = CameraEngine(cam, s, Path(tempfile.mkdtemp()))
    for n in eng.notes: print("note:", n)
    cap = cv2.VideoCapture(a.source)
    if not cap.isOpened():
        sys.exit("Could not open " + a.source)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    n, t0, per_frame = 0, time.perf_counter(), []
    while n / fps < a.seconds:
        ok, f = cap.read()
        if not ok: break
        t1 = time.perf_counter()
        eng.process(n / fps, f)
        per_frame.append((time.perf_counter() - t1) * 1000)
        n += 1
    wall = time.perf_counter() - t0
    per_frame.sort()
    print(f"detector: {eng.detector.name}   analysis width: {a.width}")
    print(f"frames processed: {n} in {wall:.1f} s  ->  {n / wall:.1f} frames/s (the video is {fps:.0f} frames/s)")
    print(f"per-frame ms  median {per_frame[len(per_frame)//2]:.1f}   95th percentile {per_frame[int(len(per_frame)*0.95)]:.1f}")
    print("smoothed stage ms:", {k: round(v, 1) for k, v in eng.timings.items()})
    try:
        import psutil
        print(f"memory of this process: {psutil.Process().memory_info().rss / 1e6:.0f} MB")
    except ImportError:
        pass
    print("The SRS wants the detector at 500 ms per frame or less, and 2 or more analysed frames per second per camera.")


if __name__ == "__main__":
    main()
