"""Rebuild the reference frames from the recorded source frame indices."""

import argparse
import hashlib
import json
from pathlib import Path

import cv2


def main():
    folder = Path(__file__).resolve().parent
    manifest = json.loads((folder / "annotations.json").read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source", type=Path, nargs="?", default=folder / manifest["source"]
    )
    parser.add_argument("--output", type=Path, default=folder)
    args = parser.parse_args()

    with args.source.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != manifest["source_sha256"]:
        parser.error("Input video does not match the reference recording's SHA-256.")

    capture = cv2.VideoCapture(str(args.source))
    if not capture.isOpened():
        parser.error(f"Cannot decode {args.source}.")
    pending = {frame["source_frame"]: frame["image"] for frame in manifest["frames"]}
    index = 0
    try:
        while pending:
            ok, image = capture.read()
            if not ok:
                raise RuntimeError(f"Input ended at frame {index}.")
            relative_path = pending.pop(index, None)
            if relative_path:
                resized = cv2.resize(
                    image, tuple(manifest["dimensions"]), interpolation=cv2.INTER_AREA
                )
                destination = args.output / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(
                    str(destination), resized, [cv2.IMWRITE_JPEG_QUALITY, 97]
                ):
                    raise RuntimeError(f"Cannot write {destination}.")
            index += 1
    finally:
        capture.release()


if __name__ == "__main__":
    main()
