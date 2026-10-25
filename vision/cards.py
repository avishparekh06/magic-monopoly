"""Find and identify a card in the top-down card zone. Pure functions, no camera.

CardMatcher(cards_dir, markers).identify(zone_bgr) -> CardResult(card_id, score, second, quad)
  card_id None  : no card found, or no confident match (reason says which)

Two ways to identify a card, tried in order:
  1. ArUco marker printed on the card (DICT_4X4_250; ids from server/cards.json: Crystal Ball 0-15,
     Chest of Spells 16-31, player cards 100-102). Exact, rotation-proof, no reference images needed.
  2. ORB feature match against reference images cards/<card_id>.png (upright and rotated 180 degrees),
     for cards without a visible marker.
"""
from dataclasses import dataclass
import glob
import json
import os
import cv2
import numpy as np

ARUCO_DICT = cv2.aruco.DICT_4X4_250
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS_JSON = os.path.join(ROOT, "server", "cards.json")


def load_marker_map(path=CARDS_JSON):
    """marker id -> card id (game cards) or player id (player cards), from the game's card data."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return {}
    m = {int(c["marker"]): c["id"] for c in data.get("cards", []) if "marker" in c}
    m.update({int(v): k for k, v in data.get("player_card_markers", {}).items()})
    return m

CARD_W, CARD_H = 250, 350
MIN_GOOD = 18           # minimum good ORB matches to accept
MIN_LEAD = 1.4          # best must beat second-best by this ratio
MIN_CARD_BRIGHTNESS = 70    # Otsu split point must be at least this (0-255), else the zone is just felt
MIN_CARD_CONTRAST = 50      # mean(bright) - mean(dark) must be at least this


@dataclass
class CardResult:
    card_id: str | None
    score: int = 0
    second: int = 0
    quad: np.ndarray | None = None
    reason: str | None = None       # "no_card" | "no_match" | "ambiguous"
    warped: np.ndarray | None = None


def find_card(zone_bgr, min_area_frac=0.08):
    """Largest bright 4-corner contour in the zone -> 4 points (TL, TR, BR, BL) or None."""
    gray = cv2.cvtColor(zone_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    t, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if t < MIN_CARD_BRIGHTNESS or gray[bw > 0].mean() - gray[bw == 0].mean() < MIN_CARD_CONTRAST:
        return None                              # nothing bright enough to be a card on the dark zone
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    H, W = gray.shape
    best, best_a = None, 0
    for c in contours:
        a = cv2.contourArea(c)
        if a < min_area_frac * W * H or a <= best_a:
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.04 * peri, True)
        if len(approx) == 4:
            best, best_a = approx.reshape(4, 2).astype(np.float32), a
        else:                                   # fall back to the min-area rectangle of the blob
            rect = cv2.minAreaRect(c)
            if cv2.contourArea(c) / max(rect[1][0] * rect[1][1], 1) > 0.85:
                best, best_a = cv2.boxPoints(rect).astype(np.float32), a
    return order_quad(best) if best is not None else None


def order_quad(pts):
    s = pts.sum(1)
    d = np.diff(pts, axis=1).ravel()
    tl, br = pts[np.argmin(s)], pts[np.argmax(s)]
    tr, bl = pts[np.argmin(d)], pts[np.argmax(d)]
    return np.float32([tl, tr, br, bl])


def warp_card(zone_bgr, quad):
    """Warp to portrait CARD_W x CARD_H regardless of how the card lies."""
    tl, tr, br, bl = quad
    w = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    h = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    if w > h:                                    # landscape: rotate the point order so the long side is vertical
        quad = np.float32([tr, br, bl, tl])
    dst = np.float32([[0, 0], [CARD_W, 0], [CARD_W, CARD_H], [0, CARD_H]])
    M = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(zone_bgr, M, (CARD_W, CARD_H))


class CardMatcher:
    def __init__(self, cards_dir, markers=None, nfeatures=800):
        self.markers = load_marker_map() if markers is None else markers
        self.detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(ARUCO_DICT),
                                                cv2.aruco.DetectorParameters())
        self.orb = cv2.ORB_create(nfeatures=nfeatures)
        self.bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        self.refs = {}       # card_id -> list of (kp, des) for 0 and 180 degrees
        for path in sorted(glob.glob(os.path.join(cards_dir, "*.png")) + glob.glob(os.path.join(cards_dir, "*.jpg"))):
            cid = os.path.splitext(os.path.basename(path))[0]
            img = cv2.imread(path)
            if img is None:
                continue
            img = cv2.resize(img, (CARD_W, CARD_H))
            self.refs[cid] = [self._feats(img), self._feats(cv2.rotate(img, cv2.ROTATE_180))]

    def _feats(self, bgr):
        g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        return self.orb.detectAndCompute(g, None)

    def _good_matches(self, des_q, des_r):
        if des_q is None or des_r is None or len(des_q) < 2 or len(des_r) < 2:
            return 0
        pairs = self.bf.knnMatch(des_q, des_r, k=2)
        return sum(1 for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance)

    def match(self, card_bgr):
        """Warped card image -> sorted [(card_id, good_matches)]."""
        _, des_q = self._feats(card_bgr)
        scores = []
        for cid, variants in self.refs.items():
            scores.append((cid, max(self._good_matches(des_q, des_r) for _, des_r in variants)))
        return sorted(scores, key=lambda s: -s[1])

    def read_marker(self, zone_bgr):
        """(card_id, marker corners) for the largest known marker in view, else (None, None)."""
        corners, ids, _ = self.detector.detectMarkers(zone_bgr)
        if ids is None or not self.markers:
            return None, None
        best = None
        for c, i in zip(corners, ids.ravel()):
            cid = self.markers.get(int(i))
            if cid is None:
                continue
            area = cv2.contourArea(c.reshape(4, 2))
            if best is None or area > best[2]:
                best = (cid, c.reshape(4, 2), area)
        return (best[0], best[1]) if best else (None, None)

    def identify(self, zone_bgr):
        cid, mquad = self.read_marker(zone_bgr)
        if cid is not None:
            quad = find_card(zone_bgr)
            return CardResult(cid, 1000, 0, quad if quad is not None else mquad, None, None)
        quad = find_card(zone_bgr)
        if quad is None:
            return CardResult(None, reason="no_card")
        warped = warp_card(zone_bgr, quad)
        if not self.refs:
            return CardResult(None, quad=quad, reason="no_match", warped=warped)
        ranked = self.match(warped)
        best, second = ranked[0], (ranked[1] if len(ranked) > 1 else (None, 0))
        if best[1] < MIN_GOOD:
            return CardResult(None, best[1], second[1], quad, "no_match", warped)
        if second[1] and best[1] < MIN_LEAD * second[1]:
            return CardResult(None, best[1], second[1], quad, "ambiguous", warped)
        return CardResult(best[0], best[1], second[1], quad, None, warped)
