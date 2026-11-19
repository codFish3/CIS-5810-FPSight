"""Regression checks for detector presets and the live video pipeline."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from detector_profiles import DETECTOR_PROFILES
from fpsight.analysis import analyze_video
from server import parse_options


class DetectorIntegrationTests(unittest.TestCase):
    def test_api_defaults_and_selected_presets(self):
        self.assertEqual(parse_options({})["conf"], 0.12)
        for detector in ("i2", "i3", "i4"):
            options = parse_options({"detector": [detector]})
            self.assertEqual(options["conf"], DETECTOR_PROFILES[detector]["conf"])
            self.assertEqual(options["imgsz"], 2240)
            self.assertTrue(options["hud"])
        options = parse_options({"detector": ["i4"], "conf": ["0.12"]})
        self.assertEqual(options["conf"], 0.12)
        for query in ({"detector": ["bad"]}, {"detector": ["i4"], "hud": ["false"]}):
            with self.assertRaises(ValueError):
                parse_options(query)

    def test_score_promotion_reaches_tracking_and_exports(self):
        def fake_detections(model, frame, options, offset=(0, 0)):
            if options["conf"] > 0.08:
                return []
            return [{"box": [600, 300, 620, 320], "confidence": 0.08}]

        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            video = folder / "clip.avi"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (1280, 720))
            self.assertTrue(writer.isOpened())
            for _ in range(3):
                writer.write(np.zeros((720, 1280, 3), dtype=np.uint8))
            writer.release()
            for detector, expected_score in (("i2", None), ("i3", 0.32), ("i4", 0.64)):
                output = folder / detector
                with patch("ultralytics.YOLO", return_value=object()), patch(
                    "fpsight.analysis._get_detections", side_effect=fake_detections
                ), patch("fpsight.predict_eval._get_detections", side_effect=fake_detections):
                    result = analyze_video(
                        video, output, Path(__file__).resolve().parents[1] / "models/yolo11n.pt",
                        {"detector": detector, "max_seconds": 0.3},
                    )
                self.assertEqual(len(result["frames"]), 3)
                self.assertEqual(result["options"]["imgsz"], 2240)
                self.assertEqual(result["detector"]["id"], detector)
                self.assertEqual(result["detector"]["baseline_conf"], 0.12)
                if expected_score is None:
                    self.assertEqual(result["summary"]["total_detections"], 0)
                else:
                    last = result["frames"][-1]["detections"][0]
                    self.assertTrue(last["confirmed"])
                    self.assertAlmostEqual(last["confidence"], expected_score)
                    self.assertEqual(last["box"], [600, 300, 620, 320])
                    self.assertEqual(result["summary"]["confirmed_frames"], 1)
                disk = json.loads((output / "analysis.json").read_text())
                self.assertEqual(disk["detector"], result["detector"])
                self.assertIn("detector_ms", (output / "frame_metrics.csv").read_text())
                cap = cv2.VideoCapture(str(output / "annotated.mp4"))
                self.assertTrue(cap.read()[0])
                cap.release()


if __name__ == "__main__":
    unittest.main()
