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

items = [(c["marker"], c["id"], f"{data['decks'][c['deck']]['name']}: {c['title']}") for c in data["cards"]]
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


# ---- print-ready page: US Letter at 300 dpi, every marker exactly 18 mm wide with a 3 mm quiet zone ----
DPI = 300
MM = DPI / 25.4
MARK = int(18 * MM)
QUIET = int(3 * MM)
CELL_W, CELL_H = int(28 * MM), int(34 * MM)          # marker + quiet zone + label
page = np.full((int(11 * DPI), int(8.5 * DPI)), 255, np.uint8)
cols = int((8.5 * DPI - 0.5 * DPI) // CELL_W)
x0 = y0 = int(0.25 * DPI)
for k, (marker, cid, label) in enumerate(items):
    r, c = divmod(k, cols)
    x, y = x0 + c * CELL_W, y0 + r * CELL_H
    if y + CELL_H > page.shape[0]:
        break
    m = cv2.aruco.generateImageMarker(d, marker, MARK)
    page[y + QUIET:y + QUIET + MARK, x + QUIET:x + QUIET + MARK] = m
    cv2.putText(page, f"{marker} {cid}", (x, y + 2 * QUIET + MARK + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, 0, 1, cv2.LINE_AA)
cv2.putText(page, "Magic Mirror Monopoly card markers - print at 100% (no scaling): each square is 18 mm",
            (x0, page.shape[0] - int(0.3 * DPI)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 0, 2, cv2.LINE_AA)
cv2.imwrite(os.path.join(OUT, "print_letter_300dpi.png"), page)
print("wrote print_letter_300dpi.png (US Letter, print at 100%)")
