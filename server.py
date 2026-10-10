#!/usr/bin/env python3
"""FPSight's local, dependency-light web server. No external service is used."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import mimetypes
import re
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi"}
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
DEFAULT_OPTIONS = {
    "conf": 0.12,
    "sample_fps": 10.0,
    "max_seconds": 30.0,
    "crosshair": "auto",
    "tracking": True,
    "hud": True,
    "imgsz": 1280,
}


def dump_json(path: Path, value: dict) -> None:
    """Replace an on-disk status atomically so readers see a full JSON file."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    temporary.replace(path)


def parse_options(query: dict[str, list[str]]) -> dict:
    allowed = set(DEFAULT_OPTIONS) | {"filename"}
    unknown = set(query) - allowed
    if unknown:
        raise ValueError("Unknown option: " + ", ".join(sorted(unknown)))
    if any(len(value) != 1 for value in query.values()):
        raise ValueError("Each query parameter must occur only once.")
    options = dict(DEFAULT_OPTIONS)
    bounds = {
        "conf": (0.01, 0.95),
        "sample_fps": (1.0, 30.0),
        "max_seconds": (1.0, 300.0),
    }
    for key, (minimum, maximum) in bounds.items():
        if key in query:
            try:
                value = float(query[key][0])
            except ValueError:
                raise ValueError(f"{key} must be a number.") from None
            if not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError(f"{key} must be between {minimum:g} and {maximum:g}.")
            options[key] = value
    if "crosshair" in query:
        value = query["crosshair"][0]
        if value not in {"auto", "center"}:
            raise ValueError("crosshair must be auto or center.")
        options["crosshair"] = value
    for key in ("tracking", "hud"):
        if key in query:
            value = query[key][0].lower()
            if value not in {"true", "false"}:
                raise ValueError(f"{key} must be true or false.")
            options[key] = value == "true"
    if "imgsz" in query:
        try:
            value = int(query["imgsz"][0])
        except ValueError:
            raise ValueError("imgsz must be an integer.") from None
        if not 320 <= value <= 1280 or value % 32:
            raise ValueError("imgsz must be a multiple of 32 between 320 and 1280.")
        options["imgsz"] = value
    return options


def rewrite_artifact_urls(result: dict, run_dir: Path, job_id: str) -> dict:
    """Make output paths consumable by the UI without exposing arbitrary files."""
    prefix = f"/runs/{job_id}/"

    # Analysts can return an artifacts mapping or explicit *_url fields.
    def local_url(value):
        if not isinstance(value, str) or not value:
            return value
        if value.startswith(("http://", "https://", "data:", "/runs/", "/data/")):
            return value
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = run_dir / candidate
        try:
            relative = candidate.resolve().relative_to(run_dir.resolve())
        except ValueError:
            return value
        return prefix + relative.as_posix()

    def transform(value):
        if isinstance(value, dict):
            mapped = {}
            for key, child in value.items():
                if key.endswith("_url"):
                    mapped[key] = local_url(child)
                elif key == "artifacts" and isinstance(child, dict):
                    mapped[key] = {
                        name: local_url(artifact) for name, artifact in child.items()
                    }
                else:
                    mapped[key] = transform(child)
            return mapped
        if isinstance(value, list):
            return [transform(child) for child in value]
        return value

    transformed = transform(result)
    transformed["video_url"] = prefix + "recording.mp4"
    transformed["analysis_url"] = prefix + "analysis.json"
    return transformed


class JobStore:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.active: str | None = None

    def reserve(self) -> dict | None:
        with self.lock:
            if self.active:
                return None
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "status": "queued",
                "progress": 0.0,
                "message": "Receiving video…",
            }
            self.jobs[job_id] = job
            self.active = job_id
            return dict(job)

    def update(self, job_id: str, **changes) -> dict:
        with self.lock:
            self.jobs[job_id].update(changes)
            value = dict(self.jobs[job_id])
        try:
            dump_json(self.root / "runs" / job_id / "job.json", value)
        except (OSError, ValueError, TypeError):
            pass
        return value

    def release(self, job_id: str) -> None:
        with self.lock:
            if self.active == job_id:
                self.active = None

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            if job_id in self.jobs:
                return dict(self.jobs[job_id])
        try:
            value = json.loads(
                (self.root / "runs" / job_id / "job.json").read_text(encoding="utf-8")
            )
            if value.get("status") in {"queued", "running"}:
                value.update(
                    status="failed",
                    error="The server stopped before this analysis completed.",
                    message="Interrupted analysis.",
                )
            return value
        except (OSError, ValueError, AttributeError):
            return None

    def run(self, job_id: str, input_path: Path, run_dir: Path, options: dict) -> None:
        try:
            self.update(
                job_id,
                status="running",
                progress=0.01,
                message="Loading the local computer vision model…",
            )
            model = self.root / "models" / "yolo11n.pt"
            if not model.is_file():
                raise RuntimeError(
                    "The bundled model is missing: models/yolo11n.pt. No automatic download was attempted."
                )
            from fpsight.analysis import analyze_video

            last_write = [0.0]

            def progress(fraction, message):
                try:
                    value = float(fraction)
                except (TypeError, ValueError):
                    value = 0.0
                if not math.isfinite(value):
                    value = 0.0
                now = time.monotonic()
                if now - last_write[0] > 0.2 or value >= 1:
                    self.update(
                        job_id,
                        progress=min(0.99, max(0.01, value)),
                        message=str(message),
                    )
                    last_write[0] = now

            result = analyze_video(
                input_path, run_dir, model, options, progress_callback=progress
            )
            if not isinstance(result, dict):
                raise RuntimeError("The analyzer returned an invalid result.")
            result = rewrite_artifact_urls(result, run_dir, job_id)
            dump_json(run_dir / "analysis.json", result)
            self.update(
                job_id,
                status="complete",
                progress=1.0,
                message="Analysis complete.",
                result=result,
            )
        except Exception as error:
            # Tracebacks stay in the local terminal; the UI receives a useful message.
            import traceback

            traceback.print_exc()
            detail = str(error) or type(error).__name__
            self.update(
                job_id, status="failed", message="Analysis failed.", error=detail
            )
        finally:
            self.release(job_id)


class FPSightHandler(BaseHTTPRequestHandler):
    server_version = "FPSightLocal/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def jobs(self) -> JobStore:
        return self.server.jobs

    def json_response(self, value: dict, status: int = 200) -> None:
        try:
            payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode(
                "utf-8"
            )
        except (ValueError, TypeError):
            payload = b'{"error":"Invalid response data."}'
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def error_response(self, message: str, status: int = 400) -> None:
        self.json_response({"error": message}, status)

    def local_request_allowed(self) -> bool:
        # A localhost service should not accept cross-origin writes from websites.
        host = self.headers.get("Host", "")
        parsed_host = urlsplit("http://" + host)
        if parsed_host.hostname not in {"127.0.0.1", "localhost", "::1"}:
            self.error_response("This service accepts localhost requests only.", 403)
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed_origin = urlsplit(origin)
                origin_port = parsed_origin.port or (
                    443 if parsed_origin.scheme == "https" else 80
                )
                host_port = parsed_host.port or 80
            except ValueError:
                self.error_response("Invalid request origin.", 403)
                return False
            if (
                parsed_origin.hostname not in {"127.0.0.1", "localhost", "::1"}
                or origin_port != host_port
            ):
                self.error_response("Use this page's local URL to upload a video.", 403)
                return False
        return True

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            split = urlsplit(self.path)
            path = unquote(split.path)
        except (ValueError, UnicodeError):
            return self.error_response("Invalid URL.")
        if path == "/api/health":
            dependencies = {
                name: importlib.util.find_spec(name) is not None
                for name in ("cv2", "numpy", "ultralytics")
            }
            model_available = (ROOT / "models" / "yolo11n.pt").is_file()
            analyzer_available = (ROOT / "fpsight" / "analysis.py").is_file()
            with self.jobs.lock:
                active = self.jobs.active
            return self.json_response(
                {
                    "ready": True,
                    "model_available": model_available,
                    "analyzer_available": analyzer_available,
                    "analysis_ready": model_available
                    and analyzer_available
                    and all(dependencies.values()),
                    "dependencies": dependencies,
                    "active_job": active,
                }
            )
        if path == "/api/sample":
            sample = ROOT / "data" / "sample" / "analysis.json"
            try:
                return self.json_response(
                    json.loads(sample.read_text(encoding="utf-8"))
                )
            except FileNotFoundError:
                return self.error_response(
                    "The sample analysis is not available yet.", 404
                )
            except (OSError, ValueError):
                return self.error_response("Unable to read the sample analysis.", 500)
        if path.startswith("/api/jobs/"):
            job_id = path.removeprefix("/api/jobs/")
            if not JOB_ID.fullmatch(job_id):
                return self.error_response("Invalid job ID.", 404)
            job = self.jobs.get(job_id)
            if job is None:
                return self.error_response("Analysis job not found.", 404)
            return self.json_response(job)
        if path.startswith("/api/"):
            return self.error_response("Unknown API endpoint.", 404)
        return self.serve_file(path)

    def serve_file(self, url_path: str):
        if "\x00" in url_path or "\\" in url_path:
            return self.error_response("Invalid path.", 400)
        if url_path == "/":
            candidate, base = ROOT / "web" / "index.html", ROOT / "web"
        elif url_path.startswith("/web/"):
            base = ROOT / "web"
            candidate = base / url_path.removeprefix("/web/")
        elif url_path.startswith("/data/sample/"):
            base = ROOT / "data" / "sample"
            candidate = base / url_path.removeprefix("/data/sample/")
        elif url_path.startswith("/runs/"):
            parts = url_path.split("/")
            if len(parts) < 4 or not JOB_ID.fullmatch(parts[2]):
                return self.error_response("File not found.", 404)
            base = ROOT / "runs" / parts[2]
            candidate = base / "/".join(parts[3:])
        elif url_path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        else:
            return self.error_response("File not found.", 404)
        try:
            candidate = candidate.resolve()
            candidate.relative_to(base.resolve())
            if not candidate.is_file():
                raise FileNotFoundError
            size = candidate.stat().st_size
            start, end = 0, size - 1
            partial = False
            requested_range = self.headers.get("Range")
            if requested_range:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested_range.strip())
                if match and size:
                    first, last = match.groups()
                    if first:
                        start = int(first)
                        end = min(int(last), size - 1) if last else size - 1
                    elif last and int(last) > 0:
                        start = max(0, size - int(last))
                    else:
                        match = None
                if not match or not size or start >= size or start > end:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                partial = True
            mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            if mime.startswith("text/") or mime == "application/javascript":
                mime += "; charset=utf-8"
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(max(0, end - start + 1)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-cache")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command != "HEAD":
                with candidate.open("rb") as handle:
                    handle.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        chunk = handle.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
        except (FileNotFoundError, ValueError):
            self.error_response("File not found.", 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError:
            self.error_response("Unable to read this file.", 500)

    def do_POST(self):
        self.close_connection = True
        if not self.local_request_allowed():
            return
        split = urlsplit(self.path)
        if split.path != "/api/analyze":
            return self.error_response("Unknown API endpoint.", 404)
        if self.headers.get("Transfer-Encoding"):
            return self.error_response(
                "Send the video with a Content-Length header.", 411
            )
        try:
            query = parse_qs(split.query, keep_blank_values=True, max_num_fields=20)
            options = parse_options(query)
            filename = query.get("filename", ["recording.mp4"])[0]
            if "/" in filename or "\\" in filename or "\x00" in filename:
                raise ValueError("filename must be a file name, without a directory.")
            extension = Path(filename).suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                raise ValueError("Supported video formats: MP4, MOV, MKV, AVI.")
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                return self.error_response("A Content-Length header is required.", 411)
            length = int(raw_length)
            if length <= 0:
                raise ValueError("Choose a non-empty video.")
            if length > MAX_UPLOAD_BYTES:
                return self.error_response(
                    "The video must be no larger than 500 MB.", 413
                )
        except (ValueError, TypeError) as error:
            return self.error_response(str(error))
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("multipart/"):
            return self.error_response(
                "Send raw video bytes; multipart uploads are not supported."
            )
        job = self.jobs.reserve()
        if job is None:
            return self.error_response(
                "An analysis is already running. Wait for it to finish before uploading another video.",
                409,
            )
        job_id = job["id"]
        run_dir = ROOT / "runs" / job_id
        input_path = run_dir / ("input" + extension)
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            self.connection.settimeout(120)
            remaining = length
            with input_path.open("wb") as handle:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError(
                            "The upload ended before the complete video was received."
                        )
                    handle.write(chunk)
                    remaining -= len(chunk)
            job = self.jobs.update(job_id, message="Video received. Starting analysis…")
            self.json_response({"id": job_id, "status": "queued"}, 202)
            threading.Thread(
                target=self.jobs.run,
                args=(job_id, input_path, run_dir, options),
                daemon=True,
            ).start()
        except (OSError, ValueError) as error:
            self.jobs.update(
                job_id, status="failed", message="Upload failed.", error=str(error)
            )
            self.jobs.release(job_id)
            input_path.unlink(missing_ok=True)
            try:
                self.error_response("Upload failed: " + str(error), 400)
            except (BrokenPipeError, ConnectionResetError):
                pass


def main():
    parser = argparse.ArgumentParser(
        description="Run the FPSight CS2 demo on localhost."
    )
    parser.add_argument(
        "--port", type=int, default=5810, help="Local port (default: 5810)"
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), FPSightHandler)
    except OSError as error:
        parser.exit(
            1,
            f"Unable to open local port {args.port}: {error}. Try python server.py --port 5811\n",
        )
    server.daemon_threads = True
    server.jobs = JobStore(ROOT)
    print(f"FPSight is ready: http://127.0.0.1:{args.port}", flush=True)
    print(
        "Videos and analysis stay on this computer. Press Ctrl+C to stop.", flush=True
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nFPSight stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
