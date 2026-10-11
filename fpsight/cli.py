"""Command line entry point: python -m fpsight.cli gameplay.mp4."""

import argparse
from pathlib import Path

from .analysis import analyze_video


def main():
    parser = argparse.ArgumentParser(description="Offline CS2 video-only CV analysis")
    root = Path(__file__).resolve().parent.parent
    parser.add_argument("video", type=Path)
    parser.add_argument("--output", type=Path, default=root / "data" / "analysis")
    parser.add_argument("--model", type=Path, default=root / "models" / "yolo11n.pt")
    parser.add_argument("--conf", type=float, default=0.12)
    parser.add_argument("--sample-fps", type=float, default=10.0)
    parser.add_argument("--max-seconds", type=float, default=24.0)
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--crosshair", choices=["auto", "center"], default="auto")
    parser.add_argument("--no-tracking", action="store_true")
    parser.add_argument("--no-hud", action="store_true")
    args = parser.parse_args()
    result = analyze_video(
        args.video,
        args.output,
        args.model,
        {
            "conf": args.conf,
            "sample_fps": args.sample_fps,
            "max_seconds": args.max_seconds,
            "imgsz": args.imgsz,
            "crosshair": args.crosshair,
            "tracking": not args.no_tracking,
            "hud": not args.no_hud,
        },
        lambda fraction, message: print(
            "[%3d%%] %s" % (fraction * 100, message), flush=True
        ),
    )
    print("Analysis saved to %s" % (args.output / "analysis.json"))
    print(
        "Confirmed tracks: %s; sampled frames: %s"
        % (result["summary"]["confirmed_tracks"], result["summary"]["total_frames"])
    )


if __name__ == "__main__":
    main()
