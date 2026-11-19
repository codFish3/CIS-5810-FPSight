"""Rebuild the follow-up holdout images from their recorded source indices."""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import cv2


def extract_clip(path, pending, dimensions, output):
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot decode {path}")
    index = 0
    try:
        while pending:
            ok, image = capture.read()
            if not ok:
                raise RuntimeError(f"{path} ended at frame {index}")
            relative_path = pending.pop(index, None)
            if relative_path:
                resized = cv2.resize(image, dimensions, interpolation=cv2.INTER_AREA)
                destination = output / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(
                    str(destination), resized, [cv2.IMWRITE_JPEG_QUALITY, 97]
                ):
                    raise RuntimeError(f"Cannot write {destination}")
            index += 1
    finally:
        capture.release()


def main():
    folder = Path(__file__).resolve().parent
    manifest = json.loads((folder / "annotations.json").read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output", type=Path, default=folder)
    args = parser.parse_args()

    frames_by_source = defaultdict(dict)
    for frame in manifest["frames"]:
        frames_by_source[frame["source"]][frame["source_frame"]] = frame["image"]

    for source_id, pending in frames_by_source.items():
        spec = manifest["sources"][source_id]
        path = folder / spec["path"]
        if args.source_dir is not None:
            path = args.source_dir / path.name
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != spec["sha256"]:
            parser.error(f"Input video does not match the SHA-256 for {source_id}: {path}")
        extract_clip(path, pending, tuple(manifest["dimensions"]), args.output)


if __name__ == "__main__":
    main()
