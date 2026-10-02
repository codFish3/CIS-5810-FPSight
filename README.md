# FPSight — Baseline Demo

A demo for the CIS 5810 final-project.

## What this demo shows

The demo processes recorded FPS gameplay and:

- detects `person` instances using a generic YOLO model;
- treats the screen center as the player's crosshair position;
- measures crosshair-to-target distance;
- checks whether the crosshair lies inside a detected target box;
- estimates a simple reaction time from target appearance to crosshair overlap;
- exports an annotated video and per-frame CSV metrics.

## Run

Put a short CS2 / Valorant / other FPS recording in this folder, for example:

`gameplay.mp4`

Then run:

```bash
python fpsight_demo.py gameplay.mp4
```

For a faster sharing-session demo:

```bash
python fpsight_demo.py gameplay.mp4 --max-seconds 15
```

Outputs:

- `fpsight_output.mp4`
- `fpsight_metrics.csv`
