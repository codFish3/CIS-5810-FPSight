#!/bin/zsh
# Double-click in Finder, or run ./start.command in a terminal.
set -eu
cd "${0:A:h}"

export PYTHONDONTWRITEBYTECODE=1
export MPLCONFIGDIR="$PWD/.runtime/mpl"
export YOLO_CONFIG_DIR="$PWD/.runtime/yolo"
mkdir -p "$MPLCONFIGDIR" "$YOLO_CONFIG_DIR/Ultralytics"

task_python=""
for task_candidate in \
  "$PWD/.venv/bin/python" \
  "/Users/codfish/Desktop/CIS 5810/final_proj/FPSight_demo/fpsight/bin/python"; do
  if [[ -x "$task_candidate" ]]; then
    task_python="$task_candidate"
    break
  fi
done
if [[ -z "$task_python" ]]; then
  task_python="$(command -v python3 || true)"
fi
if [[ -z "$task_python" ]]; then
  print "Python 3 is required to run FPSight. Install Python 3, then launch this file again."
  exit 1
fi
if ! "$task_python" -c 'import cv2, numpy, ultralytics' 2>/dev/null; then
  print "Sample mode is available. Video analysis needs the packages in requirements.txt."
  print "To enable uploads, run these commands from this folder:"
  print "  python3 -m venv .venv"
  print "  .venv/bin/python -m pip install -r requirements.txt"
fi
print "Open FPSight at http://127.0.0.1:5810"
exec "$task_python" server.py "$@"
