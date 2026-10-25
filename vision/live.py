"""The camera process: one window that reads the dice tray and, when the game is waiting for a drawn
card, the card zone. Start it once and leave it running next to the game server.

  .venv/bin/python vision/live.py 0 --post                 follow the game: dice by default; while the game waits
                                                           for a card, read ArUco card markers instead, then back to dice
  .venv/bin/python vision/live.py 0 --post --game <id>     follow one specific game instead of "the game being played"
  .venv/bin/python vision/live.py [camera index]           dice only, no server (default index: zones.json, else 0)
  .venv/bin/python vision/live.py --source clip.mp4
  .venv/bin/python vision/live.py 0 --post --server http://127.0.0.1:8000
  .venv/bin/python vision/live.py 0 --dice --post          dice only, never switch
  .venv/bin/python vision/live.py 0 --cards --post         cards only (ids from server/cards.json)
  .venv/bin/python vision/live.py 0 --belt                 hardware test: each confirmed roll goes straight to the
                                                           belt step service (BELT_URL/BELT_KEY from .env), no game engine

Mouse:  drag a box around the dice area (green). In card mode the drag sets the card area (orange) instead.
        Both default to the whole frame and are remembered in vision/live.json.
Slider: "die px" = how wide one die looks in pixels at this camera height (match the yellow reference square)
Keys:   m  force a mode (auto -> dice -> card -> auto)   r reset the current box   s save frame   q quit

How the switch works: with --post the window asks the server GET /vision/mode a few times a second. The server
answers "card" while the engine is in WAITING_FOR_CARD (someone landed on Crystal Ball / Chest of Spells) and
"dice" otherwise. A card that was already lying in the card area when the switch happens is ignored until it
is replaced, so last turn's card is never read as this turn's draw. Dice keep being tracked in card mode but
their reads are not sent until the game wants dice again.
"""
import json
import os
import sys
import threading
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dice import read_dice            # noqa: E402
from watch import CardWatcher, DiceWatcher, Emitter, jpeg_b64  # noqa: E402
from cards import CardMatcher         # noqa: E402
from zones import Zones               # noqa: E402

SETTINGS = os.path.join(HERE, "live.json")
WIN = "magic mirror camera"
MODE_POLL_SECONDS = 0.4
GREEN, ORANGE, YELLOW, GREY, RED, WHITE = (0, 255, 0), (0, 165, 255), (0, 255, 255), (200, 200, 200), (0, 0, 255), (255, 255, 255)


def load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(s):
    with open(SETTINGS, "w") as f:
        json.dump(s, f)


def flag_value(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def dotenv():
    env = {}
    try:
        for line in open(os.path.join(os.path.dirname(HERE), ".env")):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v.strip().strip('"')
    except OSError:
        pass
    return env


class ModePoller:
    """Tells the game server what the camera is looking at and asks what it should look for.
    Runs in a thread so the window never waits; the board page shows the report live."""

    def __init__(self, server, game=None):
        import requests
        self.url = f"{server}/vision/sync"
        self.params = {"game": game} if game else {}
        self.session = requests.Session()
        self.info = {"mode": "dice", "detail": "asking the server…"}
        self.report = {"mode": "dice"}           # replaced by the main loop every frame
        self.ok = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                r = self.session.post(self.url, params=self.params, json=self.report, timeout=1.5)
                if r.status_code == 200:
                    self.info, self.ok = r.json(), True
                else:
                    self.info, self.ok = {"mode": "dice", "detail": f"server said {r.status_code}: {r.text[:60]}"}, False
            except Exception as ex:                      # keep the last answer; show that the server is gone
                self.ok = False
                self.info = {**self.info, "detail": f"server unreachable ({type(ex).__name__})"}
            time.sleep(MODE_POLL_SECONDS)


def main():
    argv = sys.argv[1:]
    source = flag_value(argv, "--source")
    server = flag_value(argv, "--server", "http://127.0.0.1:8000")
    game = flag_value(argv, "--game")
    post = "--post" in argv
    frames = "--frames" in argv                   # send a picture of the table with each roll
    belt = "--belt" in argv                       # bypass the game: POST <BELT_URL>/step?squares=<total>&key=<BELT_KEY>
    forced = "card" if "--cards" in argv else "dice" if "--dice" in argv else None
    belt_url = belt_key = None
    if belt:
        env = dotenv()
        belt_url = flag_value(argv, "--belt-url", os.environ.get("BELT_URL") or env.get("BELT_URL"))
        belt_key = flag_value(argv, "--belt-key", os.environ.get("BELT_KEY") or env.get("BELT_KEY"))
        if not belt_url or not belt_key:
            sys.exit("--belt needs BELT_URL and BELT_KEY (in .env) or --belt-url/--belt-key")
        print(f"hardware test: confirmed rolls go straight to {belt_url}/step (no game engine)")
    skip = {source, server, game, belt_url, belt_key}
    args = [a for a in argv if not a.startswith("--") and a not in skip]
    z = Zones.load()
    idx = int(args[0]) if args else z.d["camera"]["index"]
    die_mm = float(z.d["die_mm"])

    if source:
        cap = cv2.VideoCapture(source)
    else:
        cap = cv2.VideoCapture(idx, cv2.CAP_AVFOUNDATION)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    if not cap.isOpened():
        sys.exit(f"could not open camera {idx}. Run: .venv/bin/python vision/probe.py --list")

    s = load_settings()
    boxes = {"dice": s.get("box"), "card": s.get("card_box")}     # [x0, y0, x1, y1] in frame pixels, or None = whole frame
    drag = {"start": None, "cur": None}
    mode = {"active": forced or "dice", "override": forced}      # what we are reading right now / what the user forced

    def on_mouse(ev, x, y, flags, _):
        if ev == cv2.EVENT_LBUTTONDOWN:
            drag["start"] = drag["cur"] = (x, y)
        elif ev == cv2.EVENT_MOUSEMOVE and drag["start"]:
            drag["cur"] = (x, y)
        elif ev == cv2.EVENT_LBUTTONUP and drag["start"]:
            (x0, y0), (x1, y1) = drag["start"], (x, y)
            drag["start"] = None
            if abs(x1 - x0) > 20 and abs(y1 - y0) > 20:
                boxes[mode["active"]] = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
                s["box" if mode["active"] == "dice" else "card_box"] = boxes[mode["active"]]
                save_settings(s)

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WIN, on_mouse)
    cv2.createTrackbar("die px", WIN, int(s.get("die_px", 70)), 250, lambda v: None)

    events = []
    emitter = Emitter(server, post=post)          # prints JSON lines; POSTs to the game server with --post
    latest = {}

    def step_belt(n):
        import requests
        try:
            r = requests.post(f"{belt_url.rstrip('/')}/step", params={"squares": n, "key": belt_key}, timeout=120)
            print(f"belt /step?squares={n} -> {r.status_code} {r.text[:120]}", flush=True)
        except Exception as ex:
            print(f"belt /step?squares={n} -> FAILED {ex}", flush=True)

    def on_event(e):
        events.append((time.time(), e))
        if frames and e.get("type") == "dice_rolled" and "img" in latest:
            e = {**e, "frame_b64": jpeg_b64(latest["img"])}
        emitter(e)
        if belt and e.get("type") == "dice_rolled":
            threading.Thread(target=step_belt, args=(e["total"],), daemon=True).start()   # don't freeze the window

    def on_dice_event(e):
        # In card mode the tray is still tracked (so nothing is replayed later) but reads are not sent:
        # the engine is not waiting for dice, and a roll made early is nobody's roll.
        if mode["active"] == "card":
            events.append((time.time(), {**e, "held": "card mode"}))
            return
        on_event(e)

    dw = DiceWatcher(1.0, die_mm, on_dice_event)

    matcher = CardMatcher(os.path.join(os.path.dirname(HERE), "cards"))
    names = {}
    try:
        data = json.load(open(os.path.join(os.path.dirname(HERE), "server", "cards.json"), encoding="utf-8"))
        names = {c["id"]: f"{data['decks'][c['deck']]['name']}: {c['title']}" for c in data["cards"]}
    except OSError:
        pass
    cw = None                                     # a CardWatcher only while reading cards
    ignored_card = {"id": None}                   # a card already lying there when card mode began

    def region_of(kind, frame):
        H, W = frame.shape[:2]
        x0, y0, x1, y1 = boxes[kind] or [0, 0, W, H]
        return (x0, y0, x1, y1), frame[y0:y1, x0:x1]

    def enter_card_mode(frame):
        nonlocal cw
        cw = CardWatcher(matcher, on_event)
        _, zone = region_of("card", frame)
        already = matcher.identify(zone)          # whatever is in the zone now is last turn's card, not this draw
        ignored_card["id"] = already.card_id if already is not None and already.card_id else None
        if ignored_card["id"]:
            cw.current = ignored_card["id"]       # the watcher reports a change only when a different card appears
        print(json.dumps({"type": "mode", "mode": "card", "ignoring": ignored_card["id"]}), flush=True)

    def leave_card_mode():
        nonlocal cw
        cw = None
        ignored_card["id"] = None
        print(json.dumps({"type": "mode", "mode": "dice"}), flush=True)

    poller = ModePoller(server, game) if post and forced is None else None
    if poller:
        print(f"following the game at {server}: dice until it waits for a card, then card markers, then dice again")
    elif forced == "card":
        print(f"card mode: {len(matcher.markers)} marker ids known; hold a card still in the box")
    if post:
        print(f"posting confirmed reads to {server}/vision/events")
    os.makedirs(os.path.join(HERE, "samples"), exist_ok=True)
    t0, n = time.time(), 0
    while True:
        ok, frame = cap.read()
        if not ok:
            if source:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            print("frame grab failed")
            break
        n += 1
        raw = frame
        latest["img"] = raw
        H, W = frame.shape[:2]
        die_px = max(20, cv2.getTrackbarPos("die px", WIN))
        if die_px != s.get("die_px"):
            s["die_px"] = die_px
            save_settings(s)
        px_per_mm = die_px / die_mm
        dw.px_per_mm = px_per_mm

        # which mode: the user's override, else what the server wants, else dice
        wanted = mode["override"] or (poller.info.get("mode", "dice") if poller else "dice")
        if wanted != mode["active"]:
            mode["active"] = wanted
            enter_card_mode(raw) if wanted == "card" else leave_card_mode()

        # dice are always tracked (the watcher needs continuity); cards only in card mode
        (dx0, dy0, dx1, dy1), tray = region_of("dice", raw)
        r = read_dice(tray, px_per_mm, die_mm)
        dw.step(tray, time.time())
        res = None
        if cw is not None:
            (cx0, cy0, cx1, cy1), zone = region_of("card", raw)
            cw.step(zone)
            res = cw.last

        view = frame.copy()
        active = mode["active"]
        # both areas, the active one bold
        cv2.rectangle(view, (dx0, dy0), (dx1, dy1), GREEN, 3 if active == "dice" else 1)
        if boxes["card"] or active == "card":
            (cx0, cy0, cx1, cy1), _ = region_of("card", raw)
            cv2.rectangle(view, (cx0, cy0), (cx1, cy1), ORANGE, 3 if active == "card" else 1)
        if active == "dice":
            cv2.rectangle(view, (W - die_px - 20, 20), (W - 20, 20 + die_px), YELLOW, 2)
            cv2.putText(view, "1 die", (W - die_px - 20, 20 + die_px + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, YELLOW, 2)
            for rect, v in zip(r.boxes, r.values or [None] * len(r.boxes)):
                pts = cv2.boxPoints(rect).astype(int) + (dx0, dy0)
                cv2.polylines(view, [pts], True, YELLOW, 2)
                if v is not None:
                    cx, cy = int(rect[0][0]) + dx0, int(rect[0][1]) + dy0
                    cv2.putText(view, str(v), (cx - 12, cy + 12), cv2.FONT_HERSHEY_SIMPLEX, 1.2, RED, 3)
        elif res is not None and res.quad is not None:
            (cx0, cy0, _, _), _ = region_of("card", raw)
            cv2.polylines(view, [np.int32(res.quad) + np.int32([cx0, cy0])], True, YELLOW, 3)
        if drag["start"]:
            cv2.rectangle(view, drag["start"], drag["cur"], (255, 255, 0), 2)

        # banner: what we see, what we last sent, what the game is doing
        cv2.rectangle(view, (0, 0), (W, 140), (0, 0, 0), -1)
        if active == "dice":
            if r.ok:
                txt, col = f"{r.values[0]} + {r.values[1]} = {r.total}" + ("  DOUBLES" if r.doubles else ""), GREEN
            elif r.reason == "empty":
                txt, col = "no dice", GREY
            else:
                txt, col = f"{r.reason}  seen {r.values}", ORANGE
        else:
            if res is not None and res.card_id and res.card_id == ignored_card["id"] and cw.current == res.card_id:
                txt, col = f"{res.card_id} is last turn's card - replace it", ORANGE
            elif res is not None and res.card_id:
                txt, col = f"{res.card_id}  {names.get(res.card_id, '')}", GREEN
            elif res is not None and res.reason == "no_card":
                txt, col = "no card - place the drawn card in the orange box", GREY
            else:
                txt, col = f"card seen but not identified ({res.reason if res else '-'})", ORANGE
        cv2.putText(view, txt, (16, 48), cv2.FONT_HERSHEY_SIMPLEX, 1.3, col, 3)
        if poller:
            seeing = (f"{r.values[0]} + {r.values[1]}" if r.ok else r.reason) if active == "dice" else \
                     (res.card_id if res is not None and res.card_id else (res.reason if res is not None else None))
            poller.report = {"mode": active, "dice_state": dw.state, "seeing": seeing, "forced": bool(mode["override"]),
                             "card": (res.card_id if res is not None and res.card_id and res.card_id != ignored_card["id"] else None),
                             "fps": round(n / max(time.time() - t0, 1e-6))}
        sent = [e for _, e in events if "held" not in e]
        last = sent[-1] if sent else None
        if last is None:
            lt = "last sent: -"
        elif last["type"] == "dice_rolled":
            lt = f"last sent: roll {last['values']} = {last['total']}"
        else:
            lt = f"last sent: {last['type']} {last.get('card_id') or last.get('reason') or ''}"
        fps = n / max(time.time() - t0, 1e-6)
        cv2.putText(view, f"{lt}    dice state {dw.state}    {fps:.0f} fps", (16, 88), cv2.FONT_HERSHEY_SIMPLEX, 0.8, WHITE, 2)
        if poller:
            detail = poller.info.get("detail") or poller.info.get("phase") or ""
            src = "forced" if mode["override"] else "auto"
            gcol = WHITE if poller.ok else ORANGE
            cv2.putText(view, f"mode {active.upper()} ({src})    game: {detail}", (16, 124), cv2.FONT_HERSHEY_SIMPLEX, 0.8, gcol, 2)
        else:
            cv2.putText(view, f"mode {active.upper()}" + ("" if post else "    (not posting: add --post)"), (16, 124),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, WHITE, 2)
        hint = "drag = dice area" if active == "dice" else "drag = card area"
        cv2.putText(view, f"{hint}   m = force mode   r = reset box   s = save frame   q = quit", (16, H - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, WHITE, 2)

        cv2.imshow(WIN, view)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        if k == ord("m"):
            if poller is None and forced is None:         # no server to follow: just flip
                mode["override"] = "card" if active == "dice" else "dice"
            else:
                mode["override"] = {None: "dice", "dice": "card", "card": None}[mode["override"]]
                if mode["override"] is None and poller is None:
                    mode["override"] = forced
        if k == ord("r"):
            boxes[active] = None
            s.pop("box" if active == "dice" else "card_box", None)
            save_settings(s)
        if k == ord("s"):
            path = os.path.join(HERE, "samples", f"frame_{int(time.time())}.jpg")
            cv2.imwrite(path, raw)
            print("saved", path)
    for t, e in events:
        print(json.dumps(e))
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
