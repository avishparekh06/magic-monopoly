"""Markers to print on the game cards: one PNG per card plus a labelled contact sheet.

    .venv/bin/python vision/make_card_markers.py          -> vision/card_markers/<card_id>.png (+ p1..p3) and sheet.png

Print spec (see CLAUDE.md §3): DICT_4X4_250, about 18 mm wide with a 3 mm white quiet zone, the same marker twice
per card (near the top and bottom edges), matte stock. Ids come from server/cards.json so they always match the game.
"""
import json
import os

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "vision", "card_markers")
data = json.load(open(os.path.join(ROOT, "server", "cards.json"), encoding="utf-8"))
d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_250)
os.makedirs(OUT, exist_ok=True)

items = [
    (c["marker"], c["id"], f"{data['decks'][c['deck']]['name']}: {c['title']}"
     + (f" — {c['text']}" if c["effect"]["kind"] == "forward" else ""))
    for c in data["cards"]
]
items += [(m, pid, f"player card {pid}") for pid, m in data["player_card_markers"].items()]

SIZE, PAD = 300, 50          # marker pixels and quiet zone; print at ~18 mm for the marker itself
tiles = []
for marker, cid, label in items:
    m = cv2.aruco.generateImageMarker(d, marker, SIZE)
    tile = np.full((SIZE + 2 * PAD + 40, SIZE + 2 * PAD), 255, np.uint8)
    tile[PAD:PAD + SIZE, PAD:PAD + SIZE] = m
    cv2.imwrite(os.path.join(OUT, f"{cid}.png"), tile[: SIZE + 2 * PAD])
    cv2.putText(tile, f"{marker}  {cid}", (6, SIZE + 2 * PAD + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 0, 2)
    tiles.append((tile, label))

cols = 6
rows = (len(tiles) + cols - 1) // cols
th, tw = tiles[0][0].shape
sheet = np.full((rows * th, cols * tw), 255, np.uint8)
for k, (tile, _) in enumerate(tiles):
    r, c = divmod(k, cols)
    sheet[r * th:(r + 1) * th, c * tw:(c + 1) * tw] = tile
cv2.imwrite(os.path.join(OUT, "sheet.png"), sheet)
with open(os.path.join(OUT, "index.txt"), "w", encoding="utf-8") as f:
    for marker, cid, label in items:
        f.write(f"{marker:>3}  {cid:<12} {label}\n")
print(f"wrote {len(items)} markers to {OUT} (sheet.png, index.txt)")


# ---- print-ready PDF (vector): US Letter, card markers 18 mm, mat corners 50 mm ----
# Drawn as filled rectangles (one per run of black cells), so it is tiny and prints crisp at any resolution.
import pymupdf

PT = 72 / 25.4                                   # points per mm


def draw_marker(page, mid, x_mm, y_mm, size_mm):
    bits = cv2.aruco.generateImageMarker(d, mid, 6)          # 6x6 cells including the black border
    cell = size_mm / 6
    for r in range(6):
        c = 0
        while c < 6:
            if bits[r, c] == 0:
                c0 = c
                while c < 6 and bits[r, c] == 0:
                    c += 1
                page.draw_rect(pymupdf.Rect((x_mm + c0 * cell) * PT, (y_mm + r * cell) * PT,
                                            (x_mm + c * cell) * PT, (y_mm + (r + 1) * cell) * PT),
                               color=None, fill=(0, 0, 0), width=0)
            else:
                c += 1


doc = pymupdf.open()
page = doc.new_page(width=612, height=792)
cols, cw, ch, x0, y0 = 7, 28, 34, 10, 12
for k, (mid, cid, label) in enumerate(items):
    r, c = divmod(k, cols)
    x, y = x0 + c * cw + 3, y0 + r * ch + 3
    draw_marker(page, mid, x, y, 18)
    page.insert_text(((x - 1) * PT, (y + 18 + 5) * PT), f"{mid} {cid}", fontsize=6, fontname="helv")
page.insert_text((10 * PT, 270 * PT), "Magic Mirror Monopoly card markers (DICT_4X4_250). Print at 100% / actual size: "
                 "each square is 18 mm. Keep the white border.", fontsize=8, fontname="helv")
page2 = doc.new_page(width=612, height=792)
corner_labels = {200: "200  TOP-LEFT of the mat", 201: "201  TOP-RIGHT", 202: "202  BOTTOM-RIGHT", 203: "203  BOTTOM-LEFT"}
for k, mid in enumerate((200, 201, 202, 203)):
    r, c = divmod(k, 2)
    x, y = 25 + c * 100, 25 + r * 105
    draw_marker(page2, mid, x, y, 50)
    page2.insert_text((x * PT, (y + 50 + 8) * PT), corner_labels[mid], fontsize=11, fontname="helv")
page2.insert_text((15 * PT, 255 * PT), "Action-mat corner markers (DICT_4X4_250). Print at 100%: each square is 50 mm. "
                  "Keep white space around them.", fontsize=9, fontname="helv")
page2.insert_text((15 * PT, 262 * PT), "Tape them flat at the four corners of the dice/card mat as labelled, as seen from the camera.",
                  fontsize=9, fontname="helv")
pdf = os.path.join(OUT, "markers_letter.pdf")
doc.save(pdf, garbage=4, deflate=True, clean=True)

# self-check: render and detect every marker back
det = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters())
found = []
for pg in pymupdf.open(pdf):
    pix = pg.get_pixmap(dpi=150)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    corners, ids, _ = det.detectMarkers(cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if pix.n == 3 else img)
    found += ids.ravel().tolist() if ids is not None else []
expected = sorted(m for m, _, _ in items) + [200, 201, 202, 203]
assert sorted(found) == expected, (sorted(found), expected)
print(f"wrote {pdf} ({os.path.getsize(pdf)} bytes); all {len(expected)} markers detected from the render")
