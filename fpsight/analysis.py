"""Video-only baseline: geometric aim metrics, not enemy or hit classification.

The improved arm uses a gameplay ROI, geometry filtering and persistent IDs.
Acquisition episodes require uninterrupted sampled observations. A missed
observation ends an episode even when a track ID survives the short gap.
"""

import csv
import json
import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median

import cv2
import numpy as np

DEFAULT_OPTIONS = {
    "conf": 0.12,
    "sample_fps": 10.0,
    "max_seconds": 24.0,
    "crosshair": "auto",
    "tracking": True,
    "hud": True,
    "imgsz": 1280,
}


def point_in_box(point, box):
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def box_center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def aim_point(box):
    # A fixed geometric anchor, deliberately not a detected anatomical head.
    return ((box[0] + box[2]) / 2, box[1] + 0.15 * (box[3] - box[1]))


def box_iou(a, b):
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )
    aa = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    bb = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = aa + bb - intersection
    return intersection / union if union > 0 else 0.0


def locate_crosshair(frame, mode="auto"):
    """Local green HSV heuristic; exact screen center is an explicit fallback."""
    height, width = frame.shape[:2]
    center = (width / 2, height / 2)
    if mode == "center":
        return {"x": center[0], "y": center[1], "source": "center_config"}
    radius = max(10, round(width * 0.012))
    cx, cy = round(center[0]), round(center[1])
    x0, y0 = max(0, cx - radius), max(0, cy - radius)
    roi = frame[y0 : min(height, cy + radius + 1), x0 : min(width, cx + radius + 1)]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    # Compression and motion blur lower saturation of this pale green crosshair.
    mask = cv2.inRange(hsv, np.array([38, 45, 105]), np.array([98, 255, 255]))
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
    candidates = []
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        bw, bh = stats[label, cv2.CC_STAT_WIDTH : cv2.CC_STAT_HEIGHT + 1]
        px, py = centroids[label]
        distance = math.hypot(px + x0 - center[0], py + y0 - center[1])
        if (
            2 <= area <= 100
            and bw <= radius
            and bh <= radius
            and distance <= radius * 0.7
        ):
            candidates.append((distance, px + x0, py + y0))
    if candidates:
        _, x, y = min(candidates)
        return {"x": round(float(x), 2), "y": round(float(y), 2), "source": "hsv"}
    return {"x": center[0], "y": center[1], "source": "center_fallback"}


def gameplay_roi(width, height, enabled=True):
    # Source-specific preset. Left minimap and bottom weapon/HUD are excluded.
    return (
        (round(width * 0.20), round(height * 0.12), width, round(height * 0.84))
        if enabled
        else (0, 0, width, height)
    )


def filter_detections(detections, width, height, roi=None, hud=True):
    kept = []
    for det in detections:
        box = det["box"]
        x1, y1, x2, y2 = box
        bw, bh = x2 - x1, y2 - y1
        if bw < 5 or bh < 10 or bw * bh > width * height * 0.20:
            continue
        if bw / bh > 1.5:
            continue
        # First-person hands and weapon are a frequent COCO-person false positive.
        if x1 < width * 0.36 and y2 > height * 0.75:
            continue
        if hud and roi:
            # Reject boxes truncated at crop's left/top edges; right edge can
            # contain an entering target, so it is retained.
            if x1 <= roi[0] + 2 or y1 <= roi[1] + 2:
                continue
        kept.append(det)
    return kept


@dataclass
class Track:
    id: int
    box: list
    confidence: float
    last_time: float
    observations: int = 1
    consecutive: int = 1
    confirmed: bool = False
    segment: int = 1
    episode: dict = field(default_factory=dict)


class TargetTracker:
    """Greedy one-to-one IoU/distance association with short-gap persistence."""

    def __init__(self, sample_interval=0.1, max_gap=0.3, min_confirm=3):
        self.sample_interval = sample_interval
        self.max_gap = max_gap
        self.min_confirm = min_confirm
        self.tracks = {}
        self.next_id = 1
        self.closed = []
        self.confirmed_ids = set()

    def _new_episode(self, track, timestamp, crosshair):
        error = math.dist(crosshair, aim_point(track.box))
        aligned = point_in_box(crosshair, track.box)
        track.episode = {
            "id": track.id,
            "segment": track.segment,
            "start": timestamp,
            "end": timestamp,
            "acquisition_ms": 0.0 if aligned else None,
            "initial_error_px": error,
            "already_aligned": aligned,
            "observations": 1,
            "confidence": track.confidence,
            "censored_reason": None,
            "confirmed": False,
        }

    def _close_episode(self, track, reason):
        if not track.episode:
            return
        episode = dict(track.episode)
        if episode["acquisition_ms"] is None:
            episode["censored_reason"] = reason
        self.closed.append(episode)
        track.episode = {}

    def update(self, detections, timestamp, crosshair):
        for track_id, track in list(self.tracks.items()):
            if timestamp - track.last_time > self.max_gap + 1e-6:
                self._close_episode(track, "track_lost")
                del self.tracks[track_id]

        pairs = []
        for di, det in enumerate(detections):
            for track_id, track in self.tracks.items():
                iou = box_iou(det["box"], track.box)
                distance = math.dist(box_center(det["box"]), box_center(track.box))
                scale = max(
                    30,
                    math.hypot(
                        track.box[2] - track.box[0], track.box[3] - track.box[1]
                    ),
                )
                if iou >= 0.12 or distance <= scale * 0.55:
                    pairs.append((iou * 2 - distance / scale, di, track_id))
        matched, used_tracks = {}, set()
        for _, di, track_id in sorted(pairs, reverse=True):
            if di not in matched and track_id not in used_tracks:
                matched[di] = track_id
                used_tracks.add(track_id)
        current = []
        for di, det in enumerate(detections):
            if di in matched:
                track = self.tracks[matched[di]]
                uninterrupted = (
                    timestamp - track.last_time <= self.sample_interval * 1.6 + 1e-6
                )
                track.box, track.confidence = list(det["box"]), det["confidence"]
                track.observations += 1
                if uninterrupted and track.episode:
                    track.consecutive += 1
                    ep = track.episode
                    ep["end"] = timestamp
                    ep["observations"] += 1
                    ep["confidence"] = max(ep["confidence"], track.confidence)
                    if ep["acquisition_ms"] is None and point_in_box(
                        crosshair, track.box
                    ):
                        ep["acquisition_ms"] = round(
                            (timestamp - ep["start"]) * 1000, 2
                        )
                else:
                    self._close_episode(track, "unobserved_gap")
                    track.segment += 1
                    track.consecutive = 1
                    self._new_episode(track, timestamp, crosshair)
                track.last_time = timestamp
            else:
                track = Track(
                    self.next_id, list(det["box"]), det["confidence"], timestamp
                )
                self.next_id += 1
                self.tracks[track.id] = track
                self._new_episode(track, timestamp, crosshair)
            if track.consecutive >= self.min_confirm:
                track.confirmed = True
                track.episode["confirmed"] = True
                self.confirmed_ids.add(track.id)
            current.append(
                {
                    "id": track.id,
                    "box": [round(float(x), 2) for x in track.box],
                    "confidence": round(track.confidence, 4),
                    "confirmed": track.consecutive >= self.min_confirm,
                    "aim": [round(x, 2) for x in aim_point(track.box)],
                    "on_target": point_in_box(crosshair, track.box),
                }
            )
        # Close an episode as soon as a sampled observation is absent. Keep the
        # ID in memory to reconnect, but never measure latency through the gap.
        for track_id, track in self.tracks.items():
            if track_id not in {d["id"] for d in current} and track.episode:
                self._close_episode(track, "unobserved_gap")
                track.consecutive = 0
        return current

    def finish(self):
        for track in self.tracks.values():
            self._close_episode(track, "clip_end")
        return [ep for ep in self.closed if ep["confirmed"]]


def _get_detections(model, frame, options, offset=(0, 0)):
    result = model.predict(
        frame,
        imgsz=options["imgsz"],
        conf=options["conf"],
        classes=[0],
        verbose=False,
        device="cpu",
    )[0]
    detections = []
    if result.boxes is not None:
        for box in result.boxes:
            coordinates = box.xyxy[0].tolist()
            coordinates = [
                coordinates[0] + offset[0],
                coordinates[1] + offset[1],
                coordinates[2] + offset[0],
                coordinates[3] + offset[1],
            ]
            detections.append({"box": coordinates, "confidence": float(box.conf[0])})
    return detections


def _write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _annotate(frame, data, roi, hud):
    frame = frame.copy()
    point = (round(data["crosshair"]["x"]), round(data["crosshair"]["y"]))
    if hud:
        cv2.rectangle(frame, (roi[0], roi[1]), (roi[2] - 1, roi[3]), (95, 85, 60), 1)
    for det in data["detections"]:
        x1, y1, x2, y2 = [round(v) for v in det["box"]]
        color = (73, 226, 140) if det["confirmed"] else (90, 172, 245)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = "ID %s %.2f%s" % (
            det["id"],
            det["confidence"],
            "" if det["confirmed"] else " candidate",
        )
        cv2.putText(
            frame,
            label,
            (x1, max(18, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            color,
            1,
            cv2.LINE_AA,
        )
        cv2.circle(frame, tuple(round(v) for v in det["aim"]), 3, color, -1)
        if det["id"] == data["selected_id"]:
            cv2.line(
                frame,
                point,
                tuple(round(v) for v in det["aim"]),
                (230, 178, 75),
                1,
                cv2.LINE_AA,
            )
    cv2.drawMarker(frame, point, (255, 255, 255), cv2.MARKER_CROSS, 16, 1)
    cv2.rectangle(
        frame, (12, 12), (min(frame.shape[1] - 12, 585), 78), (20, 24, 33), -1
    )
    cv2.putText(
        frame,
        "FPSIGHT | CS2 VIDEO-ONLY CV BASELINE",
        (24, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52,
        (245, 245, 245),
        1,
        cv2.LINE_AA,
    )
    error = "--" if data["aim_error_px"] is None else "%.1f px" % data["aim_error_px"]
    text = "t %.2fs  tracks %d  anchor error %s  aligned %s" % (
        data["time"],
        data["confirmed_count"],
        error,
        data["on_target"],
    )
    cv2.putText(
        frame,
        text,
        (24, 59),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.43,
        (181, 219, 223),
        1,
        cv2.LINE_AA,
    )
    return frame


def _ffmpeg_path():
    configured = os.environ.get("FPSIGHT_FFMPEG")
    if configured and Path(configured).is_file():
        return configured
    path = shutil.which("ffmpeg")
    if path:
        return path
    # Optional dependency bundled with the existing user's prototype.
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return None


def _browser_video(raw, final, fps=None, already_h264=False):
    if already_h264:
        raw.replace(final)
        return True
    executable = _ffmpeg_path()
    if executable:
        command = [
            executable,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(raw),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(final),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            raw.unlink(missing_ok=True)
            return True
    raw.replace(final)
    return False


def analyze_video(
    input_path, output_dir, model_path, options=None, progress_callback=None
):
    """Analyze one clip and save reproducible data and two presentation videos."""
    started = time.monotonic()
    opts = dict(DEFAULT_OPTIONS)
    opts.update(options or {})
    opts["conf"] = max(0.01, min(0.95, float(opts["conf"])))
    opts["sample_fps"] = max(1.0, min(30.0, float(opts["sample_fps"])))
    opts["max_seconds"] = max(0.1, min(300.0, float(opts["max_seconds"])))
    opts["imgsz"] = max(320, min(1920, int(opts["imgsz"])))
    input_path, output_dir, model_path = (
        Path(input_path),
        Path(output_dir),
        Path(model_path),
    )
    if not model_path.is_file():
        raise FileNotFoundError("Local YOLO weights do not exist: %s" % model_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise ValueError("Cannot decode video: %s" % input_path)
    native_fps = cap.get(cv2.CAP_PROP_FPS)
    if not math.isfinite(native_fps) or native_fps <= 0:
        native_fps = 30.0
    native_width, native_height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(
        cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    )
    if native_width <= 0 or native_height <= 0:
        cap.release()
        raise ValueError("Video has no valid image dimensions")
    scale = min(1.0, 1280 / native_width, 720 / native_height)
    width, height = (
        int(native_width * scale) // 2 * 2,
        int(native_height * scale) // 2 * 2,
    )
    source_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_limit = (
        min(opts["max_seconds"], source_frames / native_fps)
        if source_frames
        else opts["max_seconds"]
    )
    analysis_fps = min(native_fps, opts["sample_fps"])
    frame_count_limit = max(1, math.ceil(duration_limit * native_fps))
    roi = gameplay_roi(width, height, opts["hud"])
    if progress_callback:
        progress_callback(0.01, "Loading local YOLO model")
    from ultralytics import YOLO

    model = YOLO(str(model_path))
    tracker = TargetTracker(1 / analysis_fps, max(0.25, 2.5 / analysis_fps), 3)
    recorded_raw = output_dir / "recording_raw.mp4"
    annotated_raw = output_dir / "annotated_raw.mp4"
    record_writer = cv2.VideoWriter(
        str(recorded_raw), cv2.VideoWriter_fourcc(*"avc1"), native_fps, (width, height)
    )
    recording_codec_h264 = record_writer.isOpened()
    if not recording_codec_h264:
        record_writer = cv2.VideoWriter(
            str(recorded_raw),
            cv2.VideoWriter_fourcc(*"mp4v"),
            native_fps,
            (width, height),
        )
    annotated_writer = cv2.VideoWriter(
        str(annotated_raw),
        cv2.VideoWriter_fourcc(*"avc1"),
        analysis_fps,
        (width, height),
    )
    annotated_codec_h264 = annotated_writer.isOpened()
    if not annotated_codec_h264:
        annotated_writer = cv2.VideoWriter(
            str(annotated_raw),
            cv2.VideoWriter_fourcc(*"mp4v"),
            analysis_fps,
            (width, height),
        )
    if not record_writer.isOpened() or not annotated_writer.isOpened():
        cap.release()
        raise RuntimeError("OpenCV could not create output video")
    frames, baseline_latencies, provisional_id = [], [], 1
    naive_onset, naive_recorded = None, False
    baseline_overlap_frames = 0
    next_sample, frame_idx = 0.0, 0
    fallback_frames = 0
    try:
        while frame_idx < frame_count_limit:
            ok, frame = cap.read()
            if not ok:
                break
            if frame.shape[1] != width or frame.shape[0] != height:
                frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
            record_writer.write(frame)
            timestamp = frame_idx / native_fps
            frame_idx += 1
            if timestamp + 0.5 / native_fps < next_sample:
                continue
            next_sample += 1 / analysis_fps
            crosshair = locate_crosshair(frame, opts["crosshair"])
            point = (crosshair["x"], crosshair["y"])
            fallback_frames += int(crosshair["source"] == "center_fallback")
            baseline = _get_detections(model, frame, opts)
            if opts["hud"]:
                improved_raw = _get_detections(
                    model,
                    frame[roi[1] : roi[3], roi[0] : roi[2]],
                    opts,
                    (roi[0], roi[1]),
                )
            else:
                improved_raw = baseline
            filtered = filter_detections(improved_raw, width, height, roi, opts["hud"])
            if opts["tracking"]:
                detections = tracker.update(filtered, timestamp, point)
            else:
                detections = []
                for det in filtered:
                    detections.append(
                        {
                            "id": provisional_id,
                            "box": [round(x, 2) for x in det["box"]],
                            "confidence": round(det["confidence"], 4),
                            "confirmed": True,
                            "aim": [round(v, 2) for v in aim_point(det["box"])],
                            "on_target": point_in_box(point, det["box"]),
                        }
                    )
                    provisional_id += 1
            confirmed = [det for det in detections if det["confirmed"]]
            selected = (
                min(confirmed, key=lambda d: math.dist(point, d["aim"]))
                if confirmed
                else None
            )
            error = math.dist(point, selected["aim"]) if selected else None
            baseline_point = (width / 2, height / 2)
            baseline_nearest = (
                min(
                    baseline,
                    key=lambda d: math.dist(baseline_point, box_center(d["box"])),
                )
                if baseline
                else None
            )
            baseline_error = (
                math.dist(baseline_point, box_center(baseline_nearest["box"]))
                if baseline_nearest
                else None
            )
            baseline_on = (
                point_in_box(baseline_point, baseline_nearest["box"])
                if baseline_nearest
                else False
            )
            baseline_overlap_frames += int(baseline_on)
            if baseline:
                if naive_onset is None:
                    naive_onset, naive_recorded = timestamp, False
                if baseline_on and not naive_recorded:
                    baseline_latencies.append(
                        round((timestamp - naive_onset) * 1000, 2)
                    )
                    naive_recorded = True
            else:
                naive_onset, naive_recorded = None, False
            data = {
                "time": round(timestamp, 4),
                "frame": frame_idx - 1,
                "crosshair": crosshair,
                "detections": detections,
                "selected_id": selected["id"] if selected else None,
                "aim_error_px": round(error, 2) if error is not None else None,
                "aim_error_norm": (
                    round(error / math.hypot(width, height), 6)
                    if error is not None
                    else None
                ),
                "on_target": any(d["on_target"] for d in confirmed),
                "confirmed_count": len(confirmed),
                "baseline_error_px": (
                    round(baseline_error, 2) if baseline_error is not None else None
                ),
                "baseline_count": len(baseline),
                "baseline_detections": [
                    {
                        "box": [round(float(v), 2) for v in d["box"]],
                        "confidence": round(d["confidence"], 4),
                    }
                    for d in baseline
                ],
                "raw_roi_count": len(improved_raw),
            }
            frames.append(data)
            annotated = _annotate(frame, data, roi, opts["hud"])
            annotated_writer.write(annotated)
            if len(frames) == 1:
                cv2.imwrite(str(output_dir / "preview.jpg"), annotated)
            if progress_callback and (len(frames) % 10 == 0):
                progress_callback(
                    min(0.91, 0.03 + 0.86 * timestamp / duration_limit),
                    "Analyzing %.1f / %.1f seconds: detection, tracking, and crosshair geometry"
                    % (timestamp, duration_limit),
                )
    finally:
        cap.release()
        record_writer.release()
        annotated_writer.release()
    if not frames:
        raise ValueError("No frames could be decoded")
    encounters = tracker.finish() if opts["tracking"] else []
    for ep in encounters:
        for key in ("start", "end", "initial_error_px", "confidence"):
            ep[key] = round(ep[key], 4 if key != "initial_error_px" else 2)
        ep.pop("confirmed", None)
    if progress_callback:
        progress_callback(0.93, "Creating browser-playable videos and analysis report")
    recording_h264 = _browser_video(
        recorded_raw, output_dir / "recording.mp4", already_h264=recording_codec_h264
    )
    annotated_h264 = _browser_video(
        annotated_raw, output_dir / "annotated.mp4", already_h264=annotated_codec_h264
    )
    valid = [f for f in frames if f["aim_error_px"] is not None]
    acquired = [
        ep["acquisition_ms"]
        for ep in encounters
        if ep["acquisition_ms"] is not None and not ep["already_aligned"]
    ]
    detected_frames = sum(bool(f["detections"]) for f in frames)
    baseline_frames = sum(f["baseline_count"] > 0 for f in frames)
    duration = frame_idx / native_fps
    summary = {
        "mean_error_px": (
            round(mean(f["aim_error_px"] for f in valid), 2) if valid else None
        ),
        "alignment_pct": (
            round(100 * sum(f["on_target"] for f in valid) / len(valid), 2)
            if valid
            else None
        ),
        "median_acquisition_ms": round(median(acquired), 2) if acquired else None,
        "confirmed_tracks": len(tracker.confirmed_ids) if opts["tracking"] else None,
        "detected_frames": detected_frames,
        "total_frames": len(frames),
        "confirmed_frames": len(valid),
        "duration": round(duration, 3),
        "processing_seconds": round(time.monotonic() - started, 2),
        "fps": native_fps,
        "analysis_fps": analysis_fps,
        "width": width,
        "height": height,
        "crosshair_fallback_frames": fallback_frames,
        "acquired_encounters": len(acquired),
        "already_aligned_encounters": sum(ep["already_aligned"] for ep in encounters),
    }
    limitations = [
        "The generic COCO person detector identifies character candidates, without distinguishing enemies, teammates, bodies, or weapon false positives. It has not been fine-tuned on CS2.",
        "The sample is spectator footage with x-ray. The algorithm may detect occluded outlines that the player cannot see. Detection onset does not establish enemy visibility.",
        "The anchor is fixed at 15% down the box, a geometric approximation rather than head detection. Box alignment is not hit accuracy.",
        "Acquisition delay is a proxy from first continuous detection to crosshair entry, not human reaction time. Missing frames, missed detections, or tracking gaps end the event.",
        "HSV searches only for small green components near screen center. Failure falls back to center and records the source. Other colors, resolutions, and spectator layouts may require calibration.",
        "ROI and hand filters use a layout preset for this recording and may discard edge or occluded characters. Different HUD layouts require recalibration.",
        "Shots, damage, kills, and game state are not detected. No accuracy evaluation against manual ground truth is available.",
        "Timestamps use frame index and container-average FPS. Constant-frame-rate input is recommended; variable-frame-rate recordings may introduce timing error.",
    ]
    if not recording_h264 or not annotated_h264:
        limitations.append(
            "No H.264 encoder is available. MP4 uses OpenCV MPEG-4; some browsers may require downloading the video."
        )
    feedback = [
        "Of %d sampled frames, %d contain filtered character candidates and %d contain continuously confirmed targets."
        % (len(frames), detected_frames, len(valid))
    ]
    if summary["mean_error_px"] is not None:
        feedback.append(
            "For confirmed targets, mean distance to the upper-box anchor is %.1f pixels. Review the annotated footage for vertical and horizontal aim offsets."
            % summary["mean_error_px"]
        )
        feedback.append(
            "In frames with confirmed targets, the crosshair lies inside at least one target box in %.1f%% of frames. This measures overlap, not bullet hits."
            % summary["alignment_pct"]
        )
    if acquired:
        feedback.append(
            "%d events not initially aligned yield an acquisition proxy with a median of %.0f ms, for visual review of this clip only."
            % (len(acquired), summary["median_acquisition_ms"])
        )
    else:
        feedback.append(
            "No eligible continuously observed events were acquired after onset. Median acquisition delay remains null."
        )
    if fallback_frames:
        feedback.append(
            "Green crosshair localization failed in %d / %d sampled frames; screen center was used. Check each frame source field."
            % (fallback_frames, len(frames))
        )
    experiment = {
        "protocol": "Same local YOLO weights, confidence threshold, imgsz and sample timestamps. Baseline: full frame / fixed screen-center crosshair / nearest bbox center / no identity. Improved: local HSV crosshair (or configured center), optional gameplay crop + geometry filters + temporal confirmation; fixed upper-bbox anchor. Crosshair and geometry anchors differ, so mean errors are descriptive and cannot establish accuracy gains.",
        "model_filename": model_path.name,
        "ground_truth_available": False,
        "config": {
            k: opts[k] for k in ("conf", "sample_fps", "imgsz", "hud", "tracking")
        },
        "sampled_frames": len(frames),
        "baseline": {
            "total_detections": sum(f["baseline_count"] for f in frames),
            "detected_frames": baseline_frames,
            "coverage_pct": round(100 * baseline_frames / len(frames), 2),
            "mean_error_px": (
                round(
                    mean(
                        f["baseline_error_px"]
                        for f in frames
                        if f["baseline_error_px"] is not None
                    ),
                    2,
                )
                if baseline_frames
                else None
            ),
            "alignment_pct": (
                round(100 * baseline_overlap_frames / baseline_frames, 2)
                if baseline_frames
                else None
            ),
            "naive_latency_events": len(baseline_latencies),
            "naive_latency_median_ms": (
                median(baseline_latencies) if baseline_latencies else None
            ),
            "naive_zero_latency_events": sum(v == 0 for v in baseline_latencies),
        },
        "improved": {
            "raw_roi_detections": sum(f["raw_roi_count"] for f in frames),
            "total_detections": sum(len(f["detections"]) for f in frames),
            "geometry_rejections": sum(
                f["raw_roi_count"] - len(f["detections"]) for f in frames
            ),
            "detected_frames": detected_frames,
            "confirmed_frames": len(valid),
            "coverage_pct": round(100 * detected_frames / len(frames), 2),
            "confirmed_tracks": summary["confirmed_tracks"],
            "mean_error_px": summary["mean_error_px"],
            "alignment_pct": summary["alignment_pct"],
            "acquisition_events": len(acquired),
            "median_acquisition_ms": summary["median_acquisition_ms"],
            "already_aligned_encounters": summary["already_aligned_encounters"],
        },
        "interpretation": "Counts and proxy distributions only. No precision, recall, mAP, hit accuracy or human reaction-time claim without annotated ground truth.",
    }
    analysis = {
        "version": "1.0",
        "video": {
            "width": width,
            "height": height,
            "fps": native_fps,
            "analysis_fps": analysis_fps,
            "duration": round(duration, 3),
            "source": input_path.name,
            "recording": "recording.mp4",
            "annotated": "annotated.mp4",
        },
        "options": opts,
        "frames": frames,
        "summary": summary,
        "encounters": encounters,
        "feedback": feedback,
        "limitations": limitations,
        "methodology": {
            "detector": "%s / class 0 person; local weights (COCO-compatible class mapping required)"
            % model_path.name,
            "roi": list(roi),
            "crosshair": "Center-localized green HSV component, center fallback flagged",
            "tracking": "Greedy IoU/distance association; 3 consecutive sampled observations to confirm; gap closes acquisition episode",
            "anchor": "bbox top + 0.15 * height (geometric proxy, not anatomical head)",
            "alignment_denominator": "Frames with at least one currently confirmed track",
            "acquisition_denominator": "Confirmed uninterrupted encounters that began unaligned and later aligned",
            "timestamp_resolution_ms": round(1000 / analysis_fps, 2),
        },
        "artifacts": {
            "recording": "recording.mp4",
            "annotated": "annotated.mp4",
            "analysis": "analysis.json",
            "frame_metrics": "frame_metrics.csv",
            "encounters": "encounters.csv",
            "experiment": "experiment.json",
            "preview": "preview.jpg",
        },
    }
    flattened = [
        {
            "time": f["time"],
            "frame": f["frame"],
            "crosshair_x": f["crosshair"]["x"],
            "crosshair_y": f["crosshair"]["y"],
            "crosshair_source": f["crosshair"]["source"],
            "detections": len(f["detections"]),
            **{
                k: f[k]
                for k in (
                    "confirmed_count",
                    "selected_id",
                    "aim_error_px",
                    "aim_error_norm",
                    "on_target",
                    "baseline_count",
                    "baseline_error_px",
                )
            },
        }
        for f in frames
    ]
    _write_csv(output_dir / "frame_metrics.csv", flattened, list(flattened[0]))
    _write_csv(
        output_dir / "encounters.csv",
        encounters,
        [
            "id",
            "segment",
            "start",
            "end",
            "acquisition_ms",
            "initial_error_px",
            "already_aligned",
            "observations",
            "confidence",
            "censored_reason",
        ],
    )
    (output_dir / "experiment.json").write_text(
        json.dumps(experiment, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if progress_callback:
        progress_callback(
            1.0,
            "Analysis complete: videos, frame metrics, and experiment comparison exported",
        )
    return analysis
