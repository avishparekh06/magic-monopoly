"""Live dice test window: camera feed + what the reader sees, every frame.

  .venv/bin/python vision/live.py [camera index]        (default: camera.index from zones.json, else 0)
  .venv/bin/python vision/live.py --source clip.mp4
  .venv/bin/python vision/live.py 0 --post                 also send confirmed rolls to the game server
  .venv/bin/python vision/live.py 0 --post --server http://127.0.0.1:8000

Mouse:  drag a box around the dice area (defaults to the whole frame)
Slider: "die px" = how wide one die looks in pixels at this camera height (match the yellow reference square)
Keys:   r reset box   s save frame to vision/samples/   q quit
The box and slider are remembered in vision/live.json.
"""
import json
import os
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dice import read_dice            # noqa: E402
from watch import DiceWatcher, Emitter, jpeg_b64  # noqa: E402
from zones import Zones               # noqa: E402

SETTINGS = os.path.join(HERE, "live.json")
WIN = "dice live test"


def load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(s):
    with open(SETTINGS, "w") as f:
        json.dump(s, f)


def main():
    argv = sys.argv[1:]
    source = argv[argv.index("--source") + 1] if "--source" in argv else None
    server = argv[argv.index("--server") + 1] if "--server" in argv else "http://127.0.0.1:8000"
    post = "--post" in argv
    frames = "--frames" in argv                   # send a picture of the table with each roll
    skip = {source, server}
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
    box = s.get("box")            # [x0, y0, x1, y1] in frame pixels
    drag = {"start": None, "cur": None}

    def on_mouse(ev, x, y, flags, _):
        nonlocal box
        if ev == cv2.EVENT_LBUTTONDOWN:
            drag["start"] = drag["cur"] = (x, y)
        elif ev == cv2.EVENT_MOUSEMOVE and drag["start"]:
            drag["cur"] = (x, y)
        elif ev == cv2.EVENT_LBUTTONUP and drag["start"]:
            (x0, y0), (x1, y1) = drag["start"], (x, y)
            drag["start"] = None
            if abs(x1 - x0) > 20 and abs(y1 - y0) > 20:
                box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
                s["box"] = box
                save_settings(s)

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WIN, on_mouse)
    cv2.createTrackbar("die px", WIN, int(s.get("die_px", 70)), 250, lambda v: None)

    events = []
    emitter = Emitter(server, post=post)          # prints JSON lines; POSTs to the game server with --post

    latest = {}

    def on_event(e):
        events.append((time.time(), e))
        if frames and e.get("type") == "dice_rolled" and "img" in latest:
            e = {**e, "frame_b64": jpeg_b64(latest["img"])}
        emitter(e)

    dw = DiceWatcher(1.0, die_mm, on_event)
    if post:
        print(f"posting confirmed rolls to {server}/vision/events")
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

        x0, y0, x1, y1 = box if box else [0, 0, W, H]
        region = raw[y0:y1, x0:x1]
        r = read_dice(region, px_per_mm, die_mm)
        dw.step(region, time.time())

        view = frame.copy()
        # region box, reference die square, per-die boxes with counts
        cv2.rectangle(view, (x0, y0), (x1, y1), (0, 255, 0), 2)
        cv2.rectangle(view, (W - die_px - 20, 20), (W - 20, 20 + die_px), (0, 255, 255), 2)
        cv2.putText(view, "1 die", (W - die_px - 20, 20 + die_px + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        for rect, v in zip(r.boxes, r.values or [None] * len(r.boxes)):
            pts = cv2.boxPoints(rect).astype(int) + (x0, y0)
            cv2.polylines(view, [pts], True, (0, 255, 255), 2)
            if v is not None:
                cx, cy = int(rect[0][0]) + x0, int(rect[0][1]) + y0
                cv2.putText(view, str(v), (cx - 12, cy + 12), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
        if drag["start"]:
            cv2.rectangle(view, drag["start"], drag["cur"], (255, 255, 0), 2)

        # readout banner
        if r.ok:
            txt, col = f"{r.values[0]} + {r.values[1]} = {r.total}" + ("  DOUBLES" if r.doubles else ""), (0, 255, 0)
        elif r.reason == "empty":
            txt, col = "no dice", (200, 200, 200)
        else:
            txt, col = f"{r.reason}  seen {r.values}", (0, 165, 255)
        cv2.rectangle(view, (0, 0), (W, 110), (0, 0, 0), -1)
        cv2.putText(view, txt, (16, 48), cv2.FONT_HERSHEY_SIMPLEX, 1.4, col, 3)
        last = events[-1][1] if events else None
        if last:
            if last["type"] == "dice_rolled":
                lt = f"last confirmed roll: {last['values']} = {last['total']}"
            else:
                lt = f"last event: {last['type']} {last.get('reason', '')}"
        else:
            lt = "last confirmed roll: -"
        fps = n / max(time.time() - t0, 1e-6)
        cv2.putText(view, f"{lt}    state {dw.state}    {fps:.0f} fps", (16, 92), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.putText(view, "drag = dice area   r = reset   s = save   q = quit", (16, H - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

        cv2.imshow(WIN, view)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        if k == ord("r"):
            box = None
            s.pop("box", None)
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
