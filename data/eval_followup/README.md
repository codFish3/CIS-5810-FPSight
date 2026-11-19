# Cumulative detector iterations

This follows the original midterm benchmark with one sequence of changes to the proposal's gameplay-region detector. The three tracked metrics are box-level Recall (primary), F1 and AP50. Precision and CPU time accompany them. The original benchmark and its results are in [data/eval](../eval/README.md).

| Stage | What changes |
| --- | --- |
| `v0` | Original full-frame detector, included as a secondary reference |
| `proposal` | Proposal gameplay crop and geometry filter at inference size 1280 |
| `i2` | Same crop and filter at inference size 2240, selected by the old development sweep |
| `i3` | Keep I2 coordinates. For predicted height at most 32 pixels per 720 image rows, multiply score by 4, capped at 1 |
| `i4` | Keep I3. For ROI boxes at most 48 pixels per 720 rows with a tile box at IoU ≥ 0.50, use 8 times the original score instead, capped at 1. The tile pass confirms ROI candidates without adding boxes |

Each stage uses the local YOLO11n COCO person weights. The original 26 Inferno development frames informed design and threshold selection. The remaining 25 Inferno frames do not participate in the follow-up. The holdout imagery comes from different Dust II and Nuke replays. The holdout uses 55 frames from seven clips across three replays, including 21 negative frames and 48 reference boxes. Overlapping footage from clip 6 was excluded. Its JPEGs, sources and visual annotation decisions are documented in `frames/` and `annotations.json`. Codex drew the visible-body reference boxes without seeing predictions. Independent human review is pending.

The operating point selects the highest development Recall at Precision ≥ 0.80, or maximum development F1 if no threshold meets the floor. It is then frozen for the new recordings. The result also includes a common-confidence `0.12` control. All stages use score-ordered one-to-one matches at IoU ≥ 0.50. AP50 uses cached scores down to 0.01. Median CPU time excludes video or image decoding and model loading. The new clips have a different aspect ratio and replay layout. Repeated appearances from the same clips count as frame instances.

## Reproduction

Run from the project root in the inference environment. Existing prediction caches can be scored using standard-library Python.

```sh
python -m fpsight.predict_progression --annotations data/eval/annotations.json --output data/eval_followup/development_predictions.json
python fpsight/evaluate_progression.py calibrate --predictions data/eval_followup/development_predictions.json --output data/eval_followup/lock.json

python -m fpsight.predict_progression --images data/eval_followup/frames --output data/eval_followup/holdout_predictions.json
python fpsight/evaluate_progression.py score --predictions data/eval_followup/holdout_predictions.json --lock data/eval_followup/lock.json --output data/eval_followup/results.json
```

The first predictor call caches all legacy images for audit. Calibration uses only rows explicitly marked `dev`. The scorer validates image SHA-256s, disjoint sources, and the stage and runtime settings from the lock before applying its thresholds. Preserve `lock.json` when evaluating the held-out data. To run a single detector, pass `--variant i2`, `--variant i3`, or `--variant i4` and use a separate output path. `results.csv` gives both splits and both operating points. `results.json` includes exact thresholds, failed precision-floor checks and the identities of false positives and misses.

To rebuild the included holdout JPEGs from the source MP4s, use `python data/eval_followup/extract_frames.py` with Python 3.11+ and OpenCV. The script checks source hashes and supports `--source-dir` for relocated recordings.
