"""Read dice from a top-down image of the tray. Pure function, no camera.

read_dice(tray_bgr, px_per_mm, die_mm=19, expected=2) -> DiceResult
  values: list of 1-6 per die found (empty if tray is empty)
  reason: None when clean, else "empty" | "touching" | "count_mismatch" | "unreadable"
"""
from dataclasses import dataclass, field
import cv2
import numpy as np

DIE_WARP = 120          # px, each die is warped to this square for pip counting
BORDER = 0.10           # fraction of the die square ignored around the edge (hides edges and shadows)
MIN_DIE_BRIGHTNESS = 50 # threshold must be at least this, else the tray is just felt with sensor noise
MIN_CONTRAST = 50       # mean(bright) - mean(dark) must be at least this


@dataclass
class DiceResult:
    values: list
    reason: str | None = None
    boxes: list = field(default_factory=list)      # minAreaRect per die, for the overlay
    debug: dict = field(default_factory=dict)

    @property
    def ok(self):
        return self.reason is None

    @property
    def total(self):
        return sum(self.values)

    @property
    def doubles(self):
        return len(self.values) == 2 and self.values[0] == self.values[1]


def find_dice(tray_bgr, px_per_mm, die_mm):
    """White blobs on dark felt -> list of (rotated rect, area) plus a 'touching' flag."""
    gray = cv2.cvtColor(tray_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    # Dice are the brightest thing in the tray but only a few % of its pixels, so Otsu would split the
    # background texture instead. Threshold halfway between the background (median) and the brightest pixels.
    t = (np.percentile(gray, 99.8) + np.median(gray)) / 2
    _, bw = cv2.threshold(gray, int(t), 255, cv2.THRESH_BINARY)
    if t < MIN_DIE_BRIGHTNESS or not (bw > 0).any() or gray[bw > 0].mean() - gray[bw == 0].mean() < MIN_CONTRAST:
        return [], False, np.zeros_like(bw), 0            # nothing white enough to be a die
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, k)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, k)      # fill the pips so each die is one blob
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    die_px = die_mm * px_per_mm
    one = die_px * die_px
    dice, touching, noise = [], False, 0
    for c in contours:
        a = cv2.contourArea(c)
        if a < 0.4 * one:
            noise += 1             # glare, a pip reflection, a die cut off at the zone edge
            continue
        rect = cv2.minAreaRect(c)
        (w, h) = rect[1]
        aspect = max(w, h) / max(min(w, h), 1)
        if a <= 1.6 * one and aspect < 1.35:
            dice.append(rect)
        else:
            touching = True        # too big or too elongated: two dice in one blob, or a hand
    return dice, touching, bw, noise


def count_pips(tray_bgr, rect):
    """Warp one die to a square and count dark round pips inside it."""
    box = cv2.boxPoints(rect).astype(np.float32)
    # order points: use the rect as-is; orientation does not matter for counting
    dst = np.float32([[0, 0], [DIE_WARP, 0], [DIE_WARP, DIE_WARP], [0, DIE_WARP]])
    M = cv2.getPerspectiveTransform(box, dst)
    die = cv2.warpPerspective(tray_bgr, M, (DIE_WARP, DIE_WARP))
    g = cv2.cvtColor(die, cv2.COLOR_BGR2GRAY)
    m = int(DIE_WARP * BORDER)
    inner = g[m:-m, m:-m]
    # pips are much darker than the die face; threshold relative to the face brightness
    face = np.percentile(inner, 75)
    _, dark = cv2.threshold(inner, int(face * 0.6), 255, cv2.THRESH_BINARY_INV)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    side = DIE_WARP * (1 - 2 * BORDER)
    pip_d = 0.18 * DIE_WARP                     # typical pip diameter ~ 18% of the die edge
    a_min, a_max = 0.25 * np.pi * (pip_d / 2) ** 2, 3.0 * np.pi * (pip_d / 2) ** 2
    pips = 0
    for c in contours:
        a = cv2.contourArea(c)
        p = cv2.arcLength(c, True)
        if a < a_min or a > a_max or p == 0:
            continue
        circ = 4 * np.pi * a / (p * p)
        x, y, w, h = cv2.boundingRect(c)
        # round-ish and not a sliver of a side face; pips may touch the inner crop edge on real dice
        if circ > 0.55 and 0.6 < w / max(h, 1) < 1.6 and w < 0.4 * side and h < 0.4 * side:
            pips += 1
    return pips, die


def read_dice(tray_bgr, px_per_mm, die_mm=19.0, expected=2):
    dice, touching, bw, noise = find_dice(tray_bgr, px_per_mm, die_mm)
    debug = {"mask": bw, "noise_blobs": noise}
    if touching:
        return DiceResult([], "touching", [d for d in dice], debug)
    if not dice:
        return DiceResult([], "empty", [], debug)
    values, crops = [], []
    for rect in dice:
        v, crop = count_pips(tray_bgr, rect)
        values.append(v)
        crops.append(crop)
    debug["crops"] = crops
    if len(dice) != expected:
        return DiceResult(values, "count_mismatch", dice, debug)
    if any(not 1 <= v <= 6 for v in values):
        return DiceResult(values, "unreadable", dice, debug)
    return DiceResult(values, None, dice, debug)
