# Sample Source and Reproduction

Local origin: the supplied `FPSight_demo/twistzz.mp4` (33.91 seconds, 3022×1698, approximately 57.18 container FPS). No new online footage was obtained.

The original analysis input was the first 24 seconds, resized to 1280×720, converted to constant 30 FPS H.264 without audio. It contained 721 frames; analysis used the first 720. Original SHA-256: `a741d01c91b35816f80f1642649ff66d8b32ce9794fe4289dda29508ca2ba12b`.

Spectator footage contains x-ray, HUD, and embedded player elements. It is not truth for player visibility or reaction time. Publisher rights were not verified; use only for local course experiments.

Original analysis command:

```sh
.venv/bin/python -m fpsight.cli data/source.mp4 --output data/sample --model models/yolo11n.pt --conf 0.12 --sample-fps 10 --max-seconds 24 --imgsz 1280 --crosshair auto
```

Do not substitute recompressed replay exports when reproducing original results. During English localization, baked-in game-interface text is masked in bundled media. Historical metrics and coordinates are retained, not recomputed from presentation-masked input. Consequently the original hash describes the pre-localization input, and running inference on the masked source may change results. Current media hashes are in `media_manifest.json`.
