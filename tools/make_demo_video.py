"""Make a demo video to try the app without a camera.
   python tools/make_demo_video.py demo_collision.mp4 collision      (scenes: collision, pass, obstacle)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utwatch.demo_scenes import write_video

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "demo_collision.mp4"
    scene = sys.argv[2] if len(sys.argv) > 2 else "collision"
    write_video(out, scene)
    print("Wrote", out, "- add it as a camera source in UTWatch (Cameras, Add camera).")
