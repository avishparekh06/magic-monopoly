"""Calibration data: where the mat is in the camera image and where the zones are on the mat.

zones.json (all mm unless noted):
  mat_mm        [w, h]            size of the action mat
  corners_px    [[x,y] x4]        mat corners in the camera image: TL, TR, BR, BL
  px_per_mm     float             resolution of the top-down warp
  dice_zone_mm  [x0, y0, x1, y1]  dice tray rectangle on the mat
  card_zone_mm  [x0, y0, x1, y1]  card rectangle on the mat
  die_mm        float             die edge length
  card_mm       [w, h]            card size (portrait)
  camera        {index, width, height}
"""
import json
import os
import cv2
import numpy as np

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "zones.json")

DEFAULTS = {
    "mat_mm": [300, 200],
    "corners_px": None,
    "px_per_mm": 3.0,
    "dice_zone_mm": [10, 10, 150, 190],
    "card_zone_mm": [170, 10, 290, 190],
    "die_mm": 19.0,
    "card_mm": [63, 88],
    "camera": {"index": 0, "width": 1920, "height": 1080},
}


class Zones:
    def __init__(self, data):
        self.d = {**DEFAULTS, **data}
        self.H = None
        if self.d["corners_px"]:
            w, h = self.mat_px
            dst = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
            self.H = cv2.getPerspectiveTransform(np.float32(self.d["corners_px"]), dst)

    @classmethod
    def load(cls, path=DEFAULT_PATH):
        if os.path.exists(path):
            with open(path) as f:
                return cls(json.load(f))
        return cls({})

    def save(self, path=DEFAULT_PATH):
        with open(path, "w") as f:
            json.dump(self.d, f, indent=2)

    @property
    def px_per_mm(self):
        return float(self.d["px_per_mm"])

    @property
    def mat_px(self):
        return int(round(self.d["mat_mm"][0] * self.px_per_mm)), int(round(self.d["mat_mm"][1] * self.px_per_mm))

    @property
    def calibrated(self):
        return self.H is not None

    def warp(self, frame):
        """Camera frame -> top-down mat image. Uncalibrated: returns the frame resized to the mat size (for dry runs)."""
        w, h = self.mat_px
        if self.H is None:
            return cv2.resize(frame, (w, h))
        return cv2.warpPerspective(frame, self.H, (w, h))

    def rect_px(self, key):
        x0, y0, x1, y1 = self.d[key]
        s = self.px_per_mm
        return int(x0 * s), int(y0 * s), int(x1 * s), int(y1 * s)

    def crop(self, mat_img, key):
        x0, y0, x1, y1 = self.rect_px(key)
        return mat_img[y0:y1, x0:x1]
