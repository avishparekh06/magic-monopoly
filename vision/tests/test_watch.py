"""End-to-end: synthetic video through the roll and card state machines."""
import sys, os
import cv2
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from vision import synth
from vision.cards import CardMatcher
from zones import Zones                    # noqa: E402  (vision/ is on sys.path)
from watch import DiceWatcher, CardWatcher  # noqa: E402


def run(path, cards_dir):
    z = Zones({})
    events = []
    dw = DiceWatcher(z.px_per_mm, z.d["die_mm"], events.append)
    cw = CardWatcher(CardMatcher(cards_dir), events.append)
    cap = cv2.VideoCapture(path)
    n = 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        n += 1
        mat = z.warp(f)
        dw.step(z.crop(mat, "dice_zone_mm"), n / 30)
        cw.step(z.crop(mat, "card_zone_mm"))
    return [(e["type"], e.get("values") and sorted(e["values"]), e.get("card_id")) for e in events]


def test_full_sequence(tmp_path):
    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    for t in ["fairy godmother", "golden goose"]:
        cv2.imwrite(str(cards_dir / (t.replace(" ", "_") + ".png")), synth.card(t))
    clip = str(tmp_path / "clip.mp4")
    synth.roll_video(clip, [
        ("empty", 1.0, None),
        ("roll", 1.0, [3, 5]), ("still", 1.5, [3, 5]),
        ("hand", 0.6, [3, 5]), ("still", 1.5, [3, 5]),          # hand passes: must not re-emit
        ("card", 1.5, synth.card("golden goose")),
        ("empty", 1.0, None),
        ("roll", 1.0, [6, 6]), ("still", 1.5, [6, 6]),
        ("empty", 1.0, None),
    ])
    ev = run(clip, str(cards_dir))
    dice = [e for e in ev if e[0].startswith("dice")]
    card = [e for e in ev if e[0].startswith("card")]
    assert dice == [("dice_rolled", [3, 5], None), ("dice_cleared", None, None),
                    ("dice_rolled", [6, 6], None), ("dice_cleared", None, None)], ev
    assert card == [("card_drawn", None, "golden_goose"), ("card_removed", None, None)], ev
    # the card is drawn while the first roll is still on the table, and removed before the second roll
    assert ev.index(card[0]) > ev.index(dice[0]) and ev.index(card[1]) < ev.index(dice[2]), ev
