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

## Midterm detector iterations

[Detection benchmark](data/eval/README.md) contains the 51-frame pilot, three new inference iterations, their saved predictions, and the commands to reproduce the recall, precision, AP50 and CPU latency comparisons.

[Follow-up benchmark](data/eval_followup/README.md) adds a cumulative three-iteration sequence with locked Recall, F1, and AP50 evaluation on separate recordings.


## Live detector versions

Run `./start.command` on macOS, then open http://127.0.0.1:5810.
The sidebar **Demo mode** selector loads the same 24-second clip analyzed
with Standard, High resolution, Small targets or Multi-view settings, including tracking, events, distance curves and exports.
The bundled iteration demos are in `data/demos/`. Switching versions keeps
the replay timestamp. I1 is the default.

For your own video, choose **Import Video → Analysis mode**. Selecting I2,
I3 or I4 fills its full-precision development threshold automatically.
The score threshold can also be edited before analysis.

| Version | Video detector | Preset score threshold |
| --- | --- | ---: |
| Standard (I1) | Original gameplay ROI and geometry filters, size 1280 | 0.12 |
| High resolution (I2) | Same ROI and filters, size 2240 | 0.22722969949245453 |
| Small targets (I3) | I2 plus small-box rescoring | 0.22722969949245453 |
| Multi-view (I4) | I3 plus overlapping-tile agreement | 0.29818594455718994 |

The new modes reuse `predict_progression.run_frame`, then apply the selected
threshold and feed its boxes into the existing tracker and aim measurements.
I3/I4 scores are adjusted rankings. The independent-recording benchmark found
more false positives, so compare overlays and events on your recording.

CLI example:

```sh
.venv/bin/python -m fpsight.cli data/source.mp4 --detector i4 --output runs/i4-review
```

Use `--detector i2`, `i3` or `proposal` to run other versions, or `--conf`
to override the preset threshold. I2–I4 use the gameplay ROI and size 2240.
Their saved analysis records the selected version, effective threshold, input
size and detector latency. The full-frame overlay uses confidence 0.12 and size
1280 as the reference for new-mode runs. The Parameters & Results page reports the
selected version's measured output counts and aim proxies.
