# CS2 player detection pilot

The 51 evaluation images come from the local `FPSight_demo/twistzz.mp4` spectator recording. Source indices are zero-based. At nominal times `0.25 + 0.5 × k` seconds, OpenCV 4.12.0 selected the nearest frame index using the container's nominal frame rate (57.18 FPS). The manifest records that index and its estimated timestamp. Images retain the original composition at 1280 × 720. Local course-use restrictions in [SOURCE.md](../SOURCE.md) apply.

| Split | Sample IDs | Frames | Frames with targets | Reference boxes |
| --- | --- | ---: | ---: | ---: |
| Development | f007–f032 | 26 | 11 | 13 |
| Test | f000–f003, f034–f054 | 25 | 12 | 15 |

The split uses contiguous time blocks within one round on Inferno. It skips f004–f006 and f033 as guard frames and omits f055–f067 because they show the broadcast player camera. The two splits still share the same round, map, broadcast overlay and player perspective. Every reference box represents a character instance in one frame, so adjacent boxes often refer to the same character.

Draw one tight `[x_min, y_min, x_max, y_max]` box around the visible body of each living rendered character from either team. Coordinates are in image pixels and allow the right or bottom edge to meet the frame boundary. Skip detached weapons, first-person hands, corpses, HUD elements and outlines where scenery fully obscures the character. For partly obscured characters, use the visible portion even when it appears inside a spectator outline. The manifest records difficult visibility decisions and includes gameplay frames with no visible characters.

Codex boxed the frames visually using enlarged crops of the images before viewing model predictions. The pilot has one annotator and is pending independent human review. The tiny, partly hidden targets make IoU sensitive to a shift of a few pixels. The footage supports a within-round comparison of visible character detections, with generalization to new recordings still to be measured.

From the project root, regenerate the included JPEGs on Python 3.11+ with OpenCV installed:

```sh
python data/eval/extract_frames.py
```

The optional source argument accepts a relocated copy of the original. Use `--output /path/to/directory` to regenerate frames in a separate folder.

## Run the detector iterations

Use the project environment from the top-level README. The measured run used Ultralytics 8.4.168, OpenCV 4.11.0.86, Torch 2.8.0, and the bundled YOLO11n weights on a Darwin arm64 CPU with four Torch threads. The cache records runtime and image/weight hashes.

| Proposal sequence | Experiment ID | Detector change |
| --- | --- | --- |
| Baseline | `v0` | Full-frame YOLO11n at size 1280 |
| Iteration 1 | `proposal` | Proposal gameplay crop and geometry filters, measured before tracking |
| Iteration 2 | `v1`, `v1_roi` | Full-frame size 1920 and controlled proposal crop at size 2240 |
| Iteration 3 | `v2` | Four overlapping crops at size 1280, mapped back and deduplicated |
| Iteration 4 | `v3`, `v4` | Full-frame and tile fusion, with a geometry score penalty tested as a refinement |

From the project root:

```sh
python -m fpsight.predict_eval --annotations data/eval/annotations.json --output data/eval/predictions.json
python fpsight/evaluate.py
```

Run only one variant by adding `--variant v1`, `--variant v1_roi`, `--variant v2`, `--variant v3`, or `--variant v4` to the inference command and choosing a separate output path. For a new frame or image directory, replace `--annotations ...` with `--images PATH`. The output contains pixel boxes and their scores. For an operating point, add `--confidence 0.12` or use the full-precision calibrated threshold in `results.json`. The packaged cache can be scored with Python's standard library without model dependencies.

`results.csv` contains split-level counts, Recall, Precision, AP50 and latency. Use the `historical_fixed` and `common_fixed_0.12` rows together to compare all detectors at confidence 0.12. The other rows use thresholds selected only on development data. The precision target is 0.80 on development, with maximum F1 as the fallback. See `results.json` for the full-precision thresholds and frame IDs of false positives and misses. AP50 is score-ranked single-class AP at IoU 0.50 using candidates with scores at or above 0.01. Latency excludes decoding and model loading. Fusion times sum the separate passes and merging.

For the proposal crop, development AP50 across input sizes 1280, 1600, 1920, and 2240 was 22.6%, 23.3%, 28.4%, and 35.8%, respectively. The reported crop refinement uses 2240, chosen on development. Iteration 4 likewise selected the geometry score multiplier (0.05) on development, with the unpenalized fusion cached as an ablation.
