"""Watch the action zone: detect dice rolls and cards, emit events to the game server, draw a debug overlay.

  .venv/bin/python vision/watch.py                       live camera (index from zones.json)
  .venv/bin/python vision/watch.py --source clip.mp4     recorded video
  .venv/bin/python vision/watch.py --no-post --no-display --source clip.mp4   headless dry run, prints JSON lines

Events (JSON, one per line on stdout, and POSTed to --server/vision/events unless --no-post):
  {"type":"dice_rolled","values":[3,5],"total":8,"doubles":false}
  {"type":"dice_unclear","reason":"touching|count_mismatch|unreadable","seen":[...]}
  {"type":"dice_cleared"}                      tray emptied (dice picked up)
  {"type":"card_drawn","card_id":"...","score":n}
  {"type":"card_unknown","reason":"no_match|ambiguous","score":n}
  {"type":"card_removed"}
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zones import Zones, DEFAULT_PATH            # noqa: E402
from dice import read_dice                       # noqa: E402
from cards import CardMatcher                    # noqa: E402

MOTION_THRESH = 0.5      # changed area, in die areas, that counts as motion (independent of region size)
PIXEL_DELTA = 28         # a pixel counts as changed when it differs this much (0-255) from the last frame
STILL_SECONDS = 0.6      # dice must be still this long before reading
AGREE_FRAMES = 3         # consecutive identical readings required
SAME_POS_FRAC = 0.4      # dice within this many die-widths of the last emitted ones count as the same roll
CARD_STABLE_FRAMES = 4   # same card id this many frames in a row -> card_drawn
CARD_GONE_FRAMES = 6     # no card this many frames in a row -> card_removed


def jpeg_b64(frame, width=800, quality=75):
    """Small JPEG of the camera frame as base64, for the Mirror's scene description."""
    import base64
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (width, int(h * width / w))) if w > width else frame
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf.tobytes()).decode() if ok else ""


class Emitter:
    def __init__(self, server, post=True):
        self.server, self.post = server, post
        self.session = None
        if post:
            import requests
            self.session = requests.Session()

    def __call__(self, event):
        event = {"t": round(time.time(), 3), **event}
        print(json.dumps(event), flush=True)
        if self.session:
            try:
                self.session.post(f"{self.server}/vision/events", json=event, timeout=1.5)
            except Exception as e:                           # the game must not die if the server is down
                print(json.dumps({"type": "post_failed", "error": str(e)[:120]}), file=sys.stderr, flush=True)


class DiceWatcher:
    """States: EMPTY -> ROLLING (motion) -> SETTLING (still timer) -> READ (emitted). Motion from READ -> ROLLING."""

    def __init__(self, px_per_mm, die_mm, emit):
        self.px_per_mm, self.die_mm, self.emit = px_per_mm, die_mm, emit
        self.state = "EMPTY"
        self.prev = None
        self.still_since = None
        self.readings = []
        self.last_emitted = None    # DiceResult we last reported, to ignore a hand passing over unchanged dice
        self.last = None            # last DiceResult for the overlay

    def step(self, tray_bgr, now):
        gray = cv2.cvtColor(tray_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (7, 7), 0)
        if self.prev is None or self.prev.shape != gray.shape:
            motion = 0.0
        else:
            # How much of the picture changed, measured in die areas, so a small die tumbling in a big
            # region still counts and sensor noise over a big region does not.
            changed = int(cv2.countNonZero((cv2.absdiff(gray, self.prev) > PIXEL_DELTA).view(np.uint8)))
            die_area = max((self.die_mm * self.px_per_mm) ** 2, 1.0)
            motion = changed / die_area
        self.prev = gray

        if motion > MOTION_THRESH:
            self.state = "ROLLING"
            self.still_since = None
            self.readings = []
            return motion
        if self.still_since is None:
            self.still_since = now
        if self.state in ("ROLLING", "SETTLING") and now - self.still_since >= STILL_SECONDS:
            self.state = "SETTLING"
            r = read_dice(tray_bgr, self.px_per_mm, self.die_mm)
            self.last = r
            key = (tuple(sorted(r.values)), r.reason)
            self.readings.append(key)
            if len(self.readings) >= AGREE_FRAMES and len(set(self.readings[-AGREE_FRAMES:])) == 1:
                if not self._same_as_last(r):   # a hand passing over unchanged dice settles to the same reading
                    self._emit(r)
                    self.last_emitted = r
                self.state = "READ"
                self.readings = []
        return motion

    def _same_as_last(self, r):
        p = self.last_emitted
        if p is None or p.reason != r.reason or sorted(p.values) != sorted(r.values):
            return False
        if not r.boxes and not p.boxes:
            return True                          # e.g. empty -> empty
        if len(r.boxes) != len(p.boxes):
            return False
        tol = SAME_POS_FRAC * self.die_mm * self.px_per_mm
        new = [np.array(b[0]) for b in r.boxes]
        old = [np.array(b[0]) for b in p.boxes]
        for c in new:                            # every new die must sit where an old die was
            if not any(np.linalg.norm(c - o) < tol for o in old):
                return False
        return True

    def _emit(self, r):
        if r.reason == "empty":
            self.emit({"type": "dice_cleared"})
        elif r.ok:
            self.emit({"type": "dice_rolled", "values": r.values, "total": r.total, "doubles": r.doubles})
        else:
            self.emit({"type": "dice_unclear", "reason": r.reason, "seen": r.values})


class CardWatcher:
    def __init__(self, matcher, emit):
        self.matcher, self.emit = matcher, emit
        self.current = None         # card_id currently reported, or "?" for unknown
        self.stable = []            # recent ids (None = no card)
        self.last = None

    def step(self, zone_bgr):
        r = self.matcher.identify(zone_bgr)
        self.last = r
        seen = None if r.reason == "no_card" else (r.card_id or f"?{r.reason}")
        self.stable.append(seen)
        self.stable = self.stable[-max(CARD_STABLE_FRAMES, CARD_GONE_FRAMES):]
        if seen is None:
            if self.current is not None and self.stable[-CARD_GONE_FRAMES:].count(None) == CARD_GONE_FRAMES:
                self.emit({"type": "card_removed"})
                self.current = None
        elif seen != self.current and self.stable[-CARD_STABLE_FRAMES:].count(seen) == CARD_STABLE_FRAMES:
            self.current = seen
            if r.card_id:
                self.emit({"type": "card_drawn", "card_id": r.card_id, "score": r.score})
            else:
                self.emit({"type": "card_unknown", "reason": r.reason, "score": r.score})


def open_source(src, z):
    if src is None:
        cam = z.d["camera"]
        cap = cv2.VideoCapture(cam["index"], cv2.CAP_AVFOUNDATION)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam["width"])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam["height"])
    else:
        cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        sys.exit(f"could not open {'camera' if src is None else src}")
    return cap


def draw_overlay(mat, z, dw, cw, motion, fps):
    for key, col in (("dice_zone_mm", (0, 255, 0)), ("card_zone_mm", (255, 160, 0))):
        x0, y0, x1, y1 = z.rect_px(key)
        cv2.rectangle(mat, (x0, y0), (x1, y1), col, 2)
    x0, y0, _, _ = z.rect_px("dice_zone_mm")
    if dw.last:
        for rect in dw.last.boxes:
            box = cv2.boxPoints(rect).astype(np.int32) + np.int32([x0, y0])
            cv2.polylines(mat, [box], True, (0, 255, 255), 2)
        txt = f"dice {dw.last.values} {dw.last.reason or ''}"
    else:
        txt = "dice -"
    cv2.putText(mat, f"{dw.state}  motion {motion:.2f} dice-areas  {txt}", (x0 + 6, y0 + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    cx0, cy0, _, _ = z.rect_px("card_zone_mm")
    if cw.last and cw.last.quad is not None:
        cv2.polylines(mat, [np.int32(cw.last.quad) + np.int32([cx0, cy0])], True, (255, 160, 0), 2)
    cv2.putText(mat, f"card {cw.current or '-'}", (cx0 + 6, cy0 + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 160, 0), 2)
    cv2.putText(mat, f"{fps:.0f} fps  q=quit", (6, mat.shape[0] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    return mat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="video file instead of the camera")
    ap.add_argument("--zones", default=DEFAULT_PATH)
    ap.add_argument("--cards", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cards"))
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--no-post", action="store_true")
    ap.add_argument("--no-display", action="store_true")
    ap.add_argument("--realtime", action="store_true", help="with --source, pace playback at the video's fps")
    ap.add_argument("--frames", action="store_true", help="attach a small JPEG of the table to each roll (the Mirror describes it for remote players)")
    a = ap.parse_args()

    z = Zones.load(a.zones)
    if not z.calibrated:
        print("warning: zones.json has no corners; using the whole frame as the mat (run calibrate.py)", file=sys.stderr)
    emit = Emitter(a.server, post=not a.no_post)
    dw = DiceWatcher(z.px_per_mm, z.d["die_mm"], emit)
    cw = CardWatcher(CardMatcher(a.cards), emit)
    if not cw.matcher.refs:
        print(f"warning: no reference cards in {a.cards}", file=sys.stderr)

    cap = open_source(a.source, z)
    if a.frames:
        last_frame = {}
        base_emit = emit

        def emit_with_frame(ev):                    # noqa: E306
            if ev.get("type") == "dice_rolled" and "img" in last_frame:
                ev = {**ev, "frame_b64": jpeg_b64(last_frame["img"])}
            base_emit(ev)
        dw.emit = emit_with_frame
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30
    t0, n = time.time(), 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        n += 1
        now = n / src_fps if a.source else time.time()     # video time for files, wall clock for the camera
        if a.frames:
            last_frame["img"] = frame
        mat = z.warp(frame)
        motion = dw.step(z.crop(mat, "dice_zone_mm"), now)
        cw.step(z.crop(mat, "card_zone_mm"))
        if not a.no_display:
            fps = n / max(time.time() - t0, 1e-6)
            cv2.imshow("vision", draw_overlay(mat, z, dw, cw, motion, fps))
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        if a.source and a.realtime:
            time.sleep(1 / src_fps)
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
