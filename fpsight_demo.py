#!/usr/bin/env python3
"""
FPSight baseline demo

What it does:
1. Reads an FPS gameplay video.
2. Runs a generic YOLO detector and keeps person detections.
3. Assumes the crosshair is at the screen center (a simple baseline).
4. Measures distance from crosshair to the nearest detected target.
5. Estimates a simple reaction time:
   - target becomes visible
   - first later frame where crosshair enters that target's box
6. Writes an annotated video + CSV metrics.

"""

import argparse
import csv
import math
from pathlib import Path

import cv2
from ultralytics import YOLO


def point_in_box(px, py, box):
    x1, y1, x2, y2 = box
    return x1 <= px <= x2 and y1 <= py <= y2


def box_center(box):
    x1, y1, x2, y2 = box
    return (0.5 * (x1 + x2), 0.5 * (y1 + y2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("video", help="Path to gameplay video")
    parser.add_argument("--model", default="yolo11n.pt",
                        help="Ultralytics model path/name (default: yolo11n.pt)")
    parser.add_argument("--conf", type=float, default=0.25,
                        help="Detection confidence threshold")
    parser.add_argument("--output", default="fpsight_output.mp4")
    parser.add_argument("--csv", default="fpsight_metrics.csv")
    parser.add_argument("--max-seconds", type=float, default=30.0,
                        help="Process only first N seconds for a quick demo")
    args = parser.parse_args()

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise SystemExit(f"Could not open video: {args.video}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames_limit = int(args.max_seconds * fps)

    out = cv2.VideoWriter(
        args.output,
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )

    model = YOLO(args.model)

    # simple state
    target_first_seen_frame = None
    reaction_time_ms = None

    rows = []
    frame_idx = 0

    while True:
        ok, frame = cap.read()
        if not ok or frame_idx >= total_frames_limit:
            break

        t_sec = frame_idx / fps
        cx, cy = width // 2, height // 2

        # detect person
        result = model.predict(frame, conf=args.conf, verbose=False)[0]

        detections = []
        if result.boxes is not None:
            for b in result.boxes:
                cls_id = int(b.cls[0].item())
                conf = float(b.conf[0].item())
                if cls_id != 0:
                    continue
                x1, y1, x2, y2 = map(float, b.xyxy[0].tolist())
                detections.append((x1, y1, x2, y2, conf))

        # crosshair marker at screen center
        cv2.drawMarker(
            frame, (cx, cy), (255, 255, 255),
            markerType=cv2.MARKER_CROSS, markerSize=24, thickness=2
        )

        nearest_dist = None
        nearest_box = None
        on_target = False

        for x1, y1, x2, y2, conf in detections:
            box = (x1, y1, x2, y2)
            tx, ty = box_center(box)
            dist = math.hypot(tx - cx, ty - cy)

            if nearest_dist is None or dist < nearest_dist:
                nearest_dist = dist
                nearest_box = box

            color = (0, 255, 0) if point_in_box(cx, cy, box) else (0, 0, 255)
            cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
            cv2.putText(
                frame, f"target {conf:.2f}",
                (int(x1), max(20, int(y1) - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2
            )

        if detections and target_first_seen_frame is None:
            target_first_seen_frame = frame_idx

        if nearest_box is not None:
            on_target = point_in_box(cx, cy, nearest_box)

            tx, ty = box_center(nearest_box)
            cv2.line(frame, (cx, cy), (int(tx), int(ty)), (255, 255, 0), 2)

            if on_target and target_first_seen_frame is not None and reaction_time_ms is None:
                reaction_time_ms = (
                    (frame_idx - target_first_seen_frame) / fps * 1000.0
                )

        # reset simple reaction event when no targets visible
        if not detections:
            target_first_seen_frame = None
            reaction_time_ms = None

        # normalize aim error
        diag = math.hypot(width, height)
        aim_error_norm = (nearest_dist / diag) if nearest_dist is not None else None

        # HUD
        cv2.rectangle(frame, (12, 12), (470, 125), (0, 0, 0), -1)
        cv2.putText(frame, "FPSight Baseline", (24, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.putText(frame, f"Detected targets: {len(detections)}", (24, 68),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        cv2.putText(frame, f"Crosshair on target: {on_target}", (24, 94),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (0, 255, 0) if on_target else (0, 0, 255), 1)

        rt_text = "-" if reaction_time_ms is None else f"{reaction_time_ms:.0f} ms"
        cv2.putText(frame, f"Reaction time: {rt_text}", (250, 94),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

        out.write(frame)

        rows.append({
            "frame": frame_idx,
            "time_sec": round(t_sec, 4),
            "num_targets": len(detections),
            "nearest_target_distance_px": (
                "" if nearest_dist is None else round(nearest_dist, 2)
            ),
            "normalized_aim_error": (
                "" if aim_error_norm is None else round(aim_error_norm, 6)
            ),
            "crosshair_on_target": int(on_target),
            "reaction_time_ms": (
                "" if reaction_time_ms is None else round(reaction_time_ms, 2)
            ),
        })

        frame_idx += 1

    cap.release()
    out.release()

    with open(args.csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else [
            "frame", "time_sec", "num_targets", "nearest_target_distance_px",
            "normalized_aim_error", "crosshair_on_target", "reaction_time_ms"
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Done.")
    print(f"Annotated video: {args.output}")
    print(f"Metrics CSV:     {args.csv}")


if __name__ == "__main__":
    main()
