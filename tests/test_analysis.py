import unittest

import cv2
import numpy as np

from fpsight.analysis import TargetTracker, filter_detections, locate_crosshair


def det(box, confidence=0.7):
    return {"box": list(box), "confidence": confidence}


class TrackerTests(unittest.TestCase):
    def test_identity_survives_order_switch_and_confirmation(self):
        tracker = TargetTracker(sample_interval=0.1)
        a, b = det((10, 10, 30, 60)), det((160, 10, 180, 60))
        first = tracker.update([a, b], 0, (100, 30))
        second = tracker.update([b, a], 0.1, (100, 30))
        third = tracker.update([b, a], 0.2, (100, 30))
        self.assertEqual(first[0]["id"], second[1]["id"])
        self.assertEqual(first[1]["id"], second[0]["id"])
        self.assertFalse(second[0]["confirmed"])
        self.assertTrue(third[0]["confirmed"])

    def test_acquisition_is_per_track_not_nearest_switch(self):
        tracker = TargetTracker(sample_interval=0.1)
        a, b = det((10, 10, 30, 60)), det((160, 10, 180, 60))
        tracker.update([a, b], 0, (100, 30))
        tracker.update([a, b], 0.1, (100, 30))
        tracker.update([a, b], 0.2, (170, 30))
        events = tracker.finish()
        acquired = [e for e in events if e["acquisition_ms"] is not None]
        self.assertEqual(len(acquired), 1)
        self.assertEqual(acquired[0]["id"], 2)
        self.assertEqual(acquired[0]["acquisition_ms"], 200.0)

    def test_missing_observation_censors_latency_even_if_id_recovers(self):
        tracker = TargetTracker(sample_interval=0.1, max_gap=0.3)
        target = det((10, 10, 30, 60))
        for timestamp in (0, 0.1, 0.2):
            tracker.update([target], timestamp, (100, 30))
        tracker.update([], 0.3, (100, 30))
        recovered = tracker.update([target], 0.4, (20, 30))
        tracker.update([target], 0.5, (20, 30))
        tracker.update([target], 0.6, (20, 30))
        events = tracker.finish()
        self.assertEqual(recovered[0]["id"], 1)
        self.assertEqual(events[0]["censored_reason"], "unobserved_gap")
        self.assertIsNone(events[0]["acquisition_ms"])
        self.assertEqual(events[1]["acquisition_ms"], 0.0)
        self.assertTrue(events[1]["already_aligned"])
        self.assertEqual(events[1]["segment"], 2)

    def test_long_gap_allocates_new_id(self):
        tracker = TargetTracker(sample_interval=0.1, max_gap=0.3)
        target = det((10, 10, 30, 60))
        first = tracker.update([target], 0, (100, 30))[0]
        later = tracker.update([target], 0.5, (100, 30))[0]
        self.assertNotEqual(first["id"], later["id"])

    def test_aligned_onset_is_zero_and_explicit(self):
        tracker = TargetTracker(sample_interval=0.1)
        target = det((10, 10, 30, 60))
        for timestamp in (0, 0.1, 0.2):
            tracker.update([target], timestamp, (20, 30))
        event = tracker.finish()[0]
        self.assertEqual(event["acquisition_ms"], 0.0)
        self.assertTrue(event["already_aligned"])


class VisualHeuristicTests(unittest.TestCase):
    def test_green_crosshair_and_center_fallback_are_distinguishable(self):
        image = np.zeros((100, 200, 3), dtype=np.uint8)
        cv2.line(image, (101, 50), (107, 50), (0, 255, 0), 1)
        result = locate_crosshair(image)
        self.assertEqual(result["source"], "hsv")
        self.assertAlmostEqual(result["x"], 104.0)
        blank = locate_crosshair(np.zeros_like(image))
        self.assertEqual(blank["source"], "center_fallback")
        self.assertEqual((blank["x"], blank["y"]), (100.0, 50.0))
        self.assertEqual(locate_crosshair(image, "center")["source"], "center_config")

    def test_geometry_removes_hands_large_and_crop_edge_boxes(self):
        targets = [
            det((10, 300, 350, 700)),
            det((300, 90, 1000, 600)),
            det((256, 200, 300, 300)),
            det((600, 200, 650, 340)),
        ]
        kept = filter_detections(targets, 1280, 720, (256, 86, 1280, 605), True)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["box"], [600, 200, 650, 340])


if __name__ == "__main__":
    unittest.main()
