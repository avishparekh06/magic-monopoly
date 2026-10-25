"""Synthetic dice-tray and card images for tests and dry runs (no camera needed)."""
import zlib
import cv2
import numpy as np

PIPS = {  # positions on a 3x3 grid, (col, row)
    1: [(1, 1)], 2: [(0, 0), (2, 2)], 3: [(0, 0), (1, 1), (2, 2)],
    4: [(0, 0), (2, 0), (0, 2), (2, 2)], 5: [(0, 0), (2, 0), (1, 1), (0, 2), (2, 2)],
    6: [(0, 0), (0, 1), (0, 2), (2, 0), (2, 1), (2, 2)],
}


def felt(w, h, rng, shade=28):
    img = np.full((h, w, 3), shade, np.uint8)
    noise = rng.normal(0, 6, (h, w, 1)).astype(np.int16)
    return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def draw_die(img, center, side_px, value, angle_deg, rng, face=235):
    """Draw a rotated white die with black pips. Returns nothing; draws in place."""
    half = side_px / 2
    canvas = np.zeros((int(side_px * 1.6), int(side_px * 1.6), 3), np.uint8)
    cx = cy = canvas.shape[0] // 2
    r = int(side_px * 0.1)
    cv2.rectangle(canvas, (int(cx - half), int(cy - half)), (int(cx + half), int(cy + half)), (face, face, face), -1)
    # slight face shading to look real
    pip_r = max(2, int(side_px * 0.09))
    for col, row in PIPS[value]:
        px = int(cx - half + side_px * (0.25 + 0.25 * col))
        py = int(cy - half + side_px * (0.25 + 0.25 * row))
        cv2.circle(canvas, (px, py), pip_r, (15, 15, 15), -1, cv2.LINE_AA)
    M = cv2.getRotationMatrix2D((cx, cy), angle_deg, 1.0)
    rot = cv2.warpAffine(canvas, M, (canvas.shape[1], canvas.shape[0]), flags=cv2.INTER_LINEAR)
    mask = cv2.warpAffine(np.full(canvas.shape[:2], 255, np.uint8) * (canvas.sum(2) > 0), M,
                          (canvas.shape[1], canvas.shape[0]), flags=cv2.INTER_NEAREST)
    x0, y0 = int(center[0] - cx), int(center[1] - cy)
    h, w = rot.shape[:2]
    H, W = img.shape[:2]
    xs, ys = max(0, -x0), max(0, -y0)
    xe, ye = min(w, W - x0), min(h, H - y0)
    if xe <= xs or ye <= ys:
        return
    region = img[y0 + ys:y0 + ye, x0 + xs:x0 + xe]
    m = mask[ys:ye, xs:xe] > 0
    region[m] = rot[ys:ye, xs:xe][m]


def tray(values, px_per_mm=3.0, die_mm=19.0, size_mm=(140, 180), seed=0, touching=False, angles=None):
    """Dark felt tray with the given dice values. touching=True puts two dice edge to edge."""
    rng = np.random.default_rng(seed)
    w, h = int(size_mm[0] * px_per_mm), int(size_mm[1] * px_per_mm)
    img = felt(w, h, rng)
    side = die_mm * px_per_mm
    if touching and len(values) == 2:
        c = (w / 2, h / 2)
        centers = [(c[0] - side / 2 - 1, c[1]), (c[0] + side / 2 + 1, c[1])]
        angles = angles or [0, 0]
    else:
        centers = []
        tries = 0
        while len(centers) < len(values) and tries < 500:
            tries += 1
            p = (rng.uniform(side, w - side), rng.uniform(side, h - side))
            if all(np.hypot(p[0] - q[0], p[1] - q[1]) > side * 1.8 for q in centers):
                centers.append(p)
        angles = angles or list(rng.uniform(0, 90, len(values)))
    for v, c, a in zip(values, centers, angles):
        draw_die(img, c, side, v, a, rng)
    return cv2.GaussianBlur(img, (3, 3), 0)


def card(title, w=250, h=350, seed=0, color=(230, 215, 180)):
    """A fake fairy-tale card: coloured face, title text, and a unique squiggle so ORB has features."""
    rng = np.random.default_rng(zlib.crc32(title.encode()) + seed)
    img = np.full((h, w, 3), color, np.uint8)
    cv2.rectangle(img, (8, 8), (w - 9, h - 9), (60, 40, 20), 3)
    for i, word in enumerate(title.split()):
        cv2.putText(img, word, (20, 50 + 34 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (40, 20, 10), 2, cv2.LINE_AA)
    for _ in range(14):      # random shapes, deterministic per title
        x, y = int(rng.integers(20, w - 20)), int(rng.integers(140, h - 20))
        r = int(rng.integers(6, 26))
        c = tuple(int(v) for v in rng.integers(0, 200, 3))
        if rng.random() < 0.5:
            cv2.circle(img, (x, y), r, c, -1, cv2.LINE_AA)
        else:
            cv2.rectangle(img, (x - r, y - r), (x + r, y + r), c, -1)
    return img


def card_with_marker(title, marker_id, w=250, h=350, seed=0, size=70):
    """A synthetic card with an ArUco marker (DICT_4X4_250) near the top and bottom edges, as the print spec says."""
    img = card(title, w, h, seed)
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_250)
    m = cv2.cvtColor(cv2.aruco.generateImageMarker(d, marker_id, size), cv2.COLOR_GRAY2BGR)
    pad = 8
    for y in (h - size - 2 * pad - 10, 10):
        x = (w - size) // 2
        img[y:y + size + 2 * pad, x - pad:x + size + pad] = 255            # white quiet zone
        img[y + pad:y + pad + size, x:x + size] = m
    return img


def card_zone(card_img=None, px_per_mm=3.0, size_mm=(120, 180), card_mm=(63, 88), angle=4.0, seed=0):
    """Dark card zone with an optional card placed in it at a slight angle."""
    rng = np.random.default_rng(seed)
    w, h = int(size_mm[0] * px_per_mm), int(size_mm[1] * px_per_mm)
    img = felt(w, h, rng, shade=35)
    if card_img is None:
        return img
    cw, ch = int(card_mm[0] * px_per_mm), int(card_mm[1] * px_per_mm)
    face = cv2.resize(card_img, (cw, ch))
    src = np.float32([[0, 0], [cw, 0], [cw, ch], [0, ch]])
    cx, cy = w / 2, h / 2
    M = cv2.getRotationMatrix2D((cw / 2, ch / 2), angle, 1.0)
    dst = cv2.transform(src.reshape(1, -1, 2), M).reshape(-1, 2) + np.float32([cx - cw / 2, cy - ch / 2])
    P = cv2.getPerspectiveTransform(src, dst.astype(np.float32))
    warped = cv2.warpPerspective(face, P, (w, h))
    mask = cv2.warpPerspective(np.full((ch, cw), 255, np.uint8), P, (w, h)) > 0
    img[mask] = warped[mask]
    return cv2.GaussianBlur(img, (3, 3), 0)


def roll_video(path, sequence, px_per_mm=3.0, mat_mm=(300, 200), fps=30, seed=0):
    """Write a synthetic action-mat video. sequence is a list of (kind, seconds, payload):
         ("empty", 1.0, None)             empty tray
         ("roll", 1.2, [3, 5])            dice tumbling (random positions each frame), ending on the given values
         ("still", 2.0, [3, 5])           dice at rest showing the values
         ("hand", 0.5, [3, 5])            a dark 'hand' blob sweeps over the still dice
         ("card", 2.0, card_img)          card placed in the card zone (dice as before)
       Zones follow vision.zones.DEFAULTS: dice zone (10,10)-(150,190) mm, card zone (170,10)-(290,190) mm."""
    rng = np.random.default_rng(seed)
    W, H = int(mat_mm[0] * px_per_mm), int(mat_mm[1] * px_per_mm)
    out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    dice_vals, card_img = [], None
    still_tray = None
    for kind, secs, payload in sequence:
        for i in range(int(secs * fps)):
            frame = felt(W, H, rng, shade=30)
            if kind == "empty":
                dice_vals, still_tray = [], None
            elif kind == "roll":
                dice_vals = payload
                t = tray(payload, px_per_mm, seed=int(rng.integers(1 << 30)))
                still_tray = None
            elif kind in ("still", "hand"):
                dice_vals = payload
                if still_tray is None:
                    still_tray = tray(payload, px_per_mm, seed=seed + 7)
                t = still_tray.copy()
                if kind == "hand":
                    x = int(t.shape[1] * i / max(int(secs * fps) - 1, 1))
                    cv2.circle(t, (x, t.shape[0] // 2), int(25 * px_per_mm), (70, 60, 55), -1)
            elif kind == "card":
                card_img = payload
                t = still_tray if still_tray is not None else tray(dice_vals, px_per_mm, seed=seed + 7)
                still_tray = t
            if dice_vals or kind == "card":
                x0, y0 = int(10 * px_per_mm), int(10 * px_per_mm)
                frame[y0:y0 + t.shape[0], x0:x0 + t.shape[1]] = t
            if kind == "card" and card_img is not None:
                cz = card_zone(card_img, px_per_mm, angle=3.0, seed=seed)
                x0, y0 = int(170 * px_per_mm), int(10 * px_per_mm)
                frame[y0:y0 + cz.shape[0], x0:x0 + cz.shape[1]] = cz
            out.write(frame)
        if kind != "card":
            card_img = None
    out.release()
    return path
