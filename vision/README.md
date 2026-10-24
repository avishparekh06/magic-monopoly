# Vision: dice and card reading

One overhead camera (Logitech C920) looks down at an action mat with a felt dice tray and a card zone.
This process reads dice rolls and card IDs and sends events to the game server. It knows nothing about game state.

## Setup
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    # macOS: System Settings > Privacy & Security > Camera > allow your terminal app

## Steps
1. `vision/probe.py [index]` — check the camera opens, see fps, save sample frames with `s`.
2. `vision/make_aruco_sheet.py` → print `vision/aruco_sheet.png`, tape markers 0–3 at the mat corners (TL, TR, BR, BL).
3. `vision/calibrate.py --aruco` (or without the flag and click the 4 corners) → writes `zones.json`.
   Edit `dice_zone_mm`, `card_zone_mm`, `die_mm`, `card_mm`, `mat_mm` in `zones.json` to match the real mat.
4. Put reference card images in `cards/<card_id>.png` (portrait, straight-on photo or the print file).
5. `vision/watch.py` — live overlay + events POSTed to `http://127.0.0.1:8000/vision/events`.
   `--source clip.mp4` replays a recording; `--no-post` prints only; `--no-display` runs headless.

## Events
    {"type":"dice_rolled","values":[3,5],"total":8,"doubles":false}
    {"type":"dice_unclear","reason":"touching|count_mismatch|unreadable","seen":[...]}
    {"type":"dice_cleared"}
    {"type":"card_drawn","card_id":"golden_goose","score":171}
    {"type":"card_unknown","reason":"no_match|ambiguous","score":12}
    {"type":"card_removed"}

## Tuning knobs (top of each file)
- `watch.py`: `MOTION_THRESH` (motion = rolling), `STILL_SECONDS`, `AGREE_FRAMES`, `CARD_STABLE_FRAMES`.
- `dice.py`: die area window (0.5–1.6× expected), pip size (18% of die edge), `BORDER`.
- `cards.py`: `MIN_GOOD` matches, `MIN_LEAD` over second best, `MIN_CARD_BRIGHTNESS`.

## Tests (no camera)
    .venv/bin/pytest vision/tests
Synthetic trays, cards and a synthetic video come from `vision/synth.py`.
