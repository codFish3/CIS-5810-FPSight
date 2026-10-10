# FPSight

A local computer vision prototype for reviewing CS2 gameplay recordings.

## Run

Use Python 3.10 or 3.11. From the project folder:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

On Windows, activate the environment with `.venv\Scripts\Activate.ps1`.

Open **http://127.0.0.1:5810**. On macOS, after setup, you can also double-click `start.command`. Keep the terminal open; press `Ctrl+C` to stop.

Video analysis requires the bundled `models/yolo11n.pt`. If exported videos do not play in your browser, install the optional H.264 encoder:

```sh
python -m pip install imageio-ffmpeg
```

## Core Features

- Import recorded gameplay and analyze it locally.
- Detect character candidates, associate target IDs, and locate the crosshair.
- Review detection overlays, aim-distance curves, and target acquisition events.
- Compare baseline and improved detection views; export CSV, JSON, and annotated video.

## Baseline: Original FPSight_demo

The original `FPSight_demo/fpsight_demo.py` is a command-line script using pretrained YOLO11n COCO `person` detection on full frames. It assumes a screen-center crosshair, measures distance to the nearest box center, and uses one global acquisition timer that resets when no characters are detected. It exports an annotated video and frame-level CSV.

## Iteration 1: Changes from the Demo

| Area | Original demo | Current version |
| --- | --- | --- |
| Detection | Full-frame person detection | Gameplay-region crop and geometry filters for HUD/weapon candidates |
| Crosshair | Fixed screen center | Green HSV localization with an explicit center fallback |
| Target identity | Nearest target per frame; no persistent IDs | Lightweight IoU/distance association and confirmation after 3 consecutive samples |
| Acquisition | One global timer | Per-target episodes; missing observations end episodes, initially aligned cases are separate, and unavailable delays remain null |
| Aim reference | Box center | Geometric anchor 15% down the box |
| Review | Annotated video and CSV | Local web replay, event seeking, synchronized distance curves, and baseline overlays |
| Experiment outputs | Frame-level CSV | Frame/event CSV, analysis JSON, saved configuration, and baseline/iteration comparison |
| Structure | Single script | Separate analyzer, CLI, local server, interface, and rule tests |
