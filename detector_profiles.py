"""Detector presets shared by the local API and video analyzer."""

DETECTOR_PROFILES = {
    "proposal": {"label": "I1 · Original ROI", "imgsz": 1280, "conf": 0.12},
    "i2": {"label": "I2 · Higher-resolution ROI", "imgsz": 2240, "conf": 0.22722969949245453},
    "i3": {"label": "I3 · Small-box rescoring", "imgsz": 2240, "conf": 0.22722969949245453},
    "i4": {"label": "I4 · Tile agreement", "imgsz": 2240, "conf": 0.29818594455718994},
}


def resolve_detector_options(options):
    resolved = dict(options or {})
    detector = resolved.setdefault("detector", "proposal")
    if detector not in DETECTOR_PROFILES:
        raise ValueError("detector must be proposal, i2, i3, or i4.")
    profile = DETECTOR_PROFILES[detector]
    resolved.setdefault("conf", profile["conf"])
    if detector != "proposal":
        if resolved.get("hud") is False:
            raise ValueError("The iteration detectors require the gameplay ROI (hud=true).")
        resolved.update(imgsz=profile["imgsz"], hud=True)
    return resolved
