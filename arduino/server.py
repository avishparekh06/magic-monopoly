import collections
import json
import os
import random
import threading
import time
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from control import Board

SQUARE_MM = float(os.environ.get("SQUARE_MM", 30))   # belt travel per Monopoly square, tune on the real board
SPEED_MM_S = float(os.environ.get("SPEED_MM_S", 20))
DRY_RUN = os.environ.get("DRY_RUN") == "1"            # skip the Arduino and just return rolls
MOTOR_AXIS = int(os.environ.get("MOTOR_AXIS", 1))     # motor the board endpoints drive (0 = X, 1 = Y, 2 = Z)
# Motor steps per (normal square, square before a corner -> corner middle, corner middle -> square after it).
# Track 1 is smaller, so its values are hand-tuned and differ from track 0's.
AXIS_STEPS = {0: (225, 300, 500), 1: (195, 199, 361), 2: (225, 300, 500)}
STEPS = dict(AXIS_STEPS)                                  # the live values; /tune/apply changes them
_n, _i, _o = AXIS_STEPS[MOTOR_AXIS]                       # env vars override the default motor's values
STEPS[MOTOR_AXIS] = (int(os.environ.get("NORMAL_STEPS", _n)),   # normal square
                     int(os.environ.get("CORNER_IN", _i)),      # square before a corner -> middle of the corner
                     int(os.environ.get("CORNER_OUT", _o)))     # middle of the corner -> square after it
# Step period in microseconds (smaller = faster) per motor, for (plain squares, corner legs). The belt needs a
# faster burst to get through a corner. 3000 is the firmware's own default speed. 1500 is a first guess: tune it.
PERIODS = {0: (3000, 1500), 1: (3000, 1500), 2: (3000, 1500)}
# Per-corner overrides of the shared corner-in / corner-out values: {axis: {corner square: (in, out)}}. A corner that
# isn't listed uses the motor's shared values. The guided run measures every corner on its own, because corners
# don't all behave alike (Free Parking is the odd one).
CORNERS = {0: {}, 1: {}, 2: {}}
SQUARES = 40                                           # standard Monopoly board, square 0 = GO
CORNER_SQUARES = {0, 10, 20, 30}                       # GO, Jail, Free Parking, Go To Jail
SQUARE_NAMES = {0: "GO", 10: "Jail", 20: "Free Parking", 30: "Go To Jail"}
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "board_state.json")
TUNING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tuning.json")   # values saved by the bench tool's Tune


def load_tuning():
    """Step values and speeds saved by /tune/apply and /tune/speed win over the defaults above."""
    try:
        with open(TUNING_FILE) as f:
            saved = json.load(f)
        for a, v in saved.get("steps", {}).items():
            if int(a) in STEPS and len(v) == 3:
                STEPS[int(a)] = tuple(int(x) for x in v)
        for a, v in saved.get("periods", {}).items():
            if int(a) in PERIODS and len(v) == 2:
                PERIODS[int(a)] = tuple(int(x) for x in v)
        for a, per_corner in saved.get("corners", {}).items():
            if int(a) in CORNERS:
                CORNERS[int(a)] = {int(c): tuple(int(x) for x in v) for c, v in per_corner.items()
                                   if int(c) in CORNER_SQUARES and len(v) == 2}
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def save_tuning():
    with open(TUNING_FILE, "w") as f:
        json.dump({"steps": {str(a): list(v) for a, v in STEPS.items()},
                   "periods": {str(a): list(v) for a, v in PERIODS.items()},
                   "corners": {str(a): {str(c): list(v) for c, v in per.items()} for a, per in CORNERS.items()}}, f)


load_tuning()


LANES = 3                                           # player N drives axis N (0 = X, 1 = Y, 2 = Z)


def load_squares():
    """Saved square of every axis. An old file with only {"square": n} belongs to MOTOR_AXIS."""
    squares = {a: 0 for a in range(LANES)}               # no saved position, assume GO
    try:
        with open(STATE_FILE) as f:
            saved = json.load(f)
        if "axes" in saved:
            for a, s in saved["axes"].items():
                if int(a) in squares:
                    squares[int(a)] = int(s) % SQUARES
        else:
            squares[MOTOR_AXIS] = int(saved["square"]) % SQUARES
    except (OSError, ValueError, KeyError):
        pass
    return squares


def save_square(*_):
    with open(STATE_FILE, "w") as f:
        json.dump({"square": current_square, "axes": {str(a): get_square(a) for a in range(LANES)}}, f)


_loaded = load_squares()
current_square = _loaded[MOTOR_AXIS]                    # square of the MOTOR_AXIS token (the game's)
other_squares = {a: s for a, s in _loaded.items() if a != MOTOR_AXIS}


def pick_axis(axis):
    """The axis a request means: MOTOR_AXIS unless the caller (the bench tool) chose another."""
    axis = MOTOR_AXIS if axis is None else axis
    if not 0 <= axis < LANES:
        raise HTTPException(422, f"axis must be 0-{LANES - 1}")
    return axis


def get_square(axis):
    return current_square if axis == MOTOR_AXIS else other_squares[axis]


def put_square(axis, square):
    global current_square
    if axis == MOTOR_AXIS:
        current_square = square
    else:
        other_squares[axis] = square
    save_square()


def steps_for(axis):
    """(normal square, corner in, corner out) in motor steps for this motor."""
    return STEPS[axis]

app = FastAPI(title="Belt Dice")
lock = threading.Lock()
stop_requested = threading.Event()                   # set by /stop so a move in several pieces doesn't carry on
board = None


def get_board():
    global board
    if board is None:
        board = Board()
    return board


def roll_dice(rng=random):
    d1, d2 = rng.randint(1, 6), rng.randint(1, 6)
    return {"die1": d1, "die2": d2, "total": d1 + d2, "doubles": d1 == d2}


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html"))


@app.post("/roll/{player}")
def roll(player: int, move: bool = True):
    """Roll two dice and move that player's token by the total, in squares."""
    if not 0 <= player < LANES:
        raise HTTPException(404, f"player must be 0-{LANES - 1}")
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A token is still moving")
    try:
        result = roll_dice()
        result.update(player=player, mm=result["total"] * SQUARE_MM, moved=False)
        if move and not DRY_RUN:
            try:
                get_board().move_mm(player, result["mm"], SPEED_MM_S)
            except Exception as e:
                raise HTTPException(503, f"Motor move failed: {e}")
            result["moved"] = True
        return result
    finally:
        lock.release()


@app.post("/move")
def move(steps: int = 0, corners: int = 0, axis: Optional[int] = None):
    """Move by `steps` normal steps plus `corners` corner steps (motor steps), on `axis` (default MOTOR_AXIS). Negative goes back."""
    axis = pick_axis(axis)
    normal, _, corner_out = steps_for(axis)
    motor_steps = steps * normal + corners * corner_out
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A token is still moving")
    try:
        stop_requested.clear()
        drive(axis, motor_steps, PERIODS[axis][0])
        return {"axis": axis, "steps": steps, "corners": corners,
                "motor_steps": motor_steps, "moved": bool(motor_steps) and not DRY_RUN}
    finally:
        lock.release()


def drive(axis, motor_steps, period_us=None):
    """Send a move to the Arduino (caller holds the lock). Skipped in dry run.

    The firmware keeps the last step period it was given, so callers always pass one."""
    if motor_steps and not DRY_RUN:
        try:
            get_board().move(axis, motor_steps, period_us)
        except Exception as e:
            raise HTTPException(503, f"Motor move failed: {e}")


def board_state(square):
    return {"square": square, "name": SQUARE_NAMES.get(square, f"Square {square}"),
            "is_corner": square in CORNER_SQUARES}


@app.get("/axes")
def axes():
    """The motors the bench tool can pick, with each one's step values and saved square."""
    return {"default": MOTOR_AXIS,
            "axes": [{"axis": a, "normal": steps_for(a)[0], "corner_in": steps_for(a)[1],
                      "corner_out": steps_for(a)[2], "period_plain": PERIODS[a][0],
                      "period_corner": PERIODS[a][1], "square": get_square(a),
                      "corners": {str(c): list(v) for c, v in CORNERS[a].items()}} for a in range(LANES)]}


@app.get("/board")
def board_pos(axis: Optional[int] = None):
    """Where the token is on the Monopoly board."""
    return board_state(get_square(pick_axis(axis)))


@app.post("/board/set")
def board_set(square: int, axis: Optional[int] = None):
    """Tell the server where the token physically is (no motor move). Use after moving it by hand."""
    axis = pick_axis(axis)
    if not 0 <= square < SQUARES:
        raise HTTPException(422, f"square must be 0-{SQUARES - 1}")
    put_square(axis, square)
    return board_state(square)


def hop_info(start, squares):
    """The hops a move of `squares` (negative = back) from `start` crosses, in the order the magnet travels, as
    (kind, corner): "in" = the square before a corner into it, "out" = a corner to the square after it (corner =
    that corner's square number), else ("plain", None)."""
    n = abs(squares)
    if squares >= 0:
        hops = [((start + i - 1) % SQUARES, (start + i) % SQUARES) for i in range(1, n + 1)]
    else:
        hops = [((start - i) % SQUARES, (start - i + 1) % SQUARES) for i in range(1, n + 1)]
    # each hop is a forward hop a -> b, whichever way the magnet is going
    return [("in", b) if b in CORNER_SQUARES else ("out", a) if a in CORNER_SQUARES else ("plain", None)
            for a, b in hops]


def hop_kinds(start, squares):
    return [kind for kind, _ in hop_info(start, squares)]


def hop_cost(axis, kind, corner=None):
    """Motor steps for one hop of this kind on this motor (a corner's own values win over the shared ones)."""
    normal, c_in, c_out = STEPS[axis]
    if kind == "plain":
        return normal
    own = CORNERS[axis].get(corner)
    return (own[0] if own else c_in) if kind == "in" else (own[1] if own else c_out)


def pos_cost(axis, x):
    """Motor steps from the middle of square 0 forward to position `x` (squares; may have a fraction, any size)."""
    laps = int(x // SQUARES)
    r = x - laps * SQUARES
    whole = int(r)
    total = laps * sum(hop_cost(axis, *hop_info(k, 1)[0]) for k in range(SQUARES))
    total += sum(hop_cost(axis, *hop_info(k, 1)[0]) for k in range(whole))
    if r - whole > 1e-12:
        total += (r - whole) * hop_cost(axis, *hop_info(whole, 1)[0])
    return total


def centre_steps_for(axis, actual, target=None):
    """Motor steps (signed) from position `actual` to the middle of `target` (default: the nearest square)."""
    target = round(actual) if target is None else target
    d = ((target - actual + SQUARES / 2) % SQUARES) - SQUARES / 2        # the short way round
    return round(pos_cost(axis, actual + d) - pos_cost(axis, actual))


def hop_counts(start, squares):
    """(plain hops, hops into a corner, hops out of a corner) in a move."""
    kinds = hop_kinds(start, squares)
    return kinds.count("plain"), kinds.count("in"), kinds.count("out")


def hop_weights(start, squares):
    """Like hop_counts, but `squares` can end part way along a hop: 7.3 squares counts seven whole hops plus
    0.3 of the next one. Returns (plain, in, out) as floats."""
    sign = 1 if squares >= 0 else -1
    whole = int(abs(squares))
    frac = abs(squares) - whole
    kinds = hop_kinds(start, sign * whole)
    weights = {k: float(kinds.count(k)) for k in ("plain", "in", "out")}
    if frac > 1e-9:
        weights[hop_kinds(start, sign * (whole + 1))[-1]] += frac
    return weights["plain"], weights["in"], weights["out"]


def plan_segments(start, squares, axis=None):
    """A move as a list of (signed motor steps, step period in microseconds), in travel order.

    Corner legs get the motor's faster corner period, plain squares the normal one. Neighbouring hops at the
    same speed are joined, so a move with no corner is a single piece. The steps add up to plan_move's total."""
    axis = pick_axis(axis)
    p_plain, p_corner = PERIODS[axis]
    pieces = []
    for kind, corner in hop_info(start, squares):
        steps, period = hop_cost(axis, kind, corner), (p_plain if kind == "plain" else p_corner)
        if pieces and pieces[-1][1] == period:
            pieces[-1][0] += steps
        else:
            pieces.append([steps, period])
    sign = 1 if squares >= 0 else -1
    return [(sign * steps, period) for steps, period in pieces]


def plan_move(start, squares, axis=None):
    """Cost of moving `squares` (negative = back) from `start`.

    A hop costs the same in both directions, so forward then back returns to the same motor position.
    Into a corner costs the motor's corner-in value, out of one its corner-out value, every other hop its normal value.
    """
    axis = pick_axis(axis)
    hops = hop_info(start, squares)
    total = sum(hop_cost(axis, kind, corner) for kind, corner in hops)
    plain = sum(1 for kind, _ in hops if kind == "plain")
    return plain, len(hops) - plain, (total if squares >= 0 else -total)


def run_board_move(squares, axis=None):
    """Move the token `squares` squares (negative = back), save and report where it ended up."""
    axis = pick_axis(axis)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A token is still moving")
    try:
        stop_requested.clear()
        start = get_square(axis)
        steps, corners, motor_steps = plan_move(start, squares, axis)
        segments = plan_segments(start, squares, axis)
        for seg_steps, period in segments:                # corner legs go faster than plain squares
            if stop_requested.is_set():
                break
            drive(axis, seg_steps, period)
        if stop_requested.is_set():                       # not 409: the game bridge retries those as "busy"
            raise HTTPException(503, "Stopped part way. Use Set position to say where the magnet is.")
        end = (start + squares) % SQUARES
        put_square(axis, end)
        return {"from": start, "to": board_state(end), "squares": squares, "axis": axis,
                "steps": steps, "corners": corners, "motor_steps": motor_steps, "segments": segments,
                "passed_go": not 0 <= start + squares < SQUARES,
                "moved": bool(motor_steps) and not DRY_RUN}
    finally:
        lock.release()


@app.post("/board/move")
def board_move(squares: int, axis: Optional[int] = None):
    """Move the token by `squares` squares around the board (negative goes back)."""
    return run_board_move(squares, axis)


@app.post("/board/goto")
def board_goto(square: int, direction: str = "auto", axis: Optional[int] = None):
    """Move the token to `square`. direction: auto (shortest way), forward, or back."""
    if not 0 <= square < SQUARES:
        raise HTTPException(422, f"square must be 0-{SQUARES - 1}")
    if direction not in ("auto", "forward", "back"):
        raise HTTPException(422, "direction must be auto, forward or back")
    axis = pick_axis(axis)
    ahead = (square - get_square(axis)) % SQUARES        # squares to go forward
    behind = ahead - SQUARES if ahead else 0              # squares to go back, as a negative
    if direction == "forward" or (direction == "auto" and ahead <= -behind):
        squares = ahead
    else:
        squares = behind
    return run_board_move(squares, axis)


@app.get("/tune/suggest")
def tune_suggest(start: int, squares: int, motor_steps: int, actual: float,
                 axis: Optional[int] = None, basis: str = "auto"):
    """Work out better step values from one test move.

    The move was commanded as `squares` squares from `start` and sent `motor_steps` to the motor, and the
    magnet really stopped at `actual`: a square number, or a point between squares (11.3 = 0.3 of the way
    from square 11 to 12). basis: auto (plain stretch tunes the normal value, a move with corners tunes the
    two corner values), normal, corners, or all (scale all three together).
    """
    axis = pick_axis(axis)
    if not (0 <= start < SQUARES and 0 <= actual < SQUARES):
        raise HTTPException(422, f"squares must be 0-{SQUARES - 1}")
    if basis not in ("auto", "normal", "corners", "all"):
        raise HTTPException(422, "basis must be auto, normal, corners or all")
    d = (actual - start) % SQUARES
    moved = d + SQUARES * round((squares - d) / SQUARES)               # the whole number of laps closest to the command
    if abs(moved) < 0.005:
        raise HTTPException(422, "The magnet is still on the start square, so there is nothing to tune from.")
    if motor_steps == 0 or (moved > 0) != (motor_steps > 0):
        raise HTTPException(422, "The magnet went the opposite way to the command. That is the direction "
                                 "wiring, not the step values.")
    plain, n_in, n_out = hop_weights(start, moved)
    normal, c_in, c_out = steps_for(axis)
    steps = abs(motor_steps)
    if basis == "auto":
        basis = "normal" if n_in + n_out < 1e-9 else "corners"
    if basis in ("corners", "all") and CORNERS[axis]:
        raise HTTPException(422, "This motor has its own value for each corner, so corners can't be tuned "
                                 "together. Use the guided run, or clear them with /tune/corners/clear.")
    if basis == "normal":
        if plain < 1e-9:
            raise HTTPException(422, "That move crossed only corners. Tune the corners instead.")
        suggested = (round(steps / plain), c_in, c_out)
    elif basis == "corners":
        corner_cost = n_in * c_in + n_out * c_out
        if corner_cost < 1e-9:
            raise HTTPException(422, "That move crossed no corners. Pick a move that goes through one.")
        k = (steps - plain * normal) / corner_cost
        if k <= 0:
            raise HTTPException(422, "The normal value is too big to fit that move. Tune the normal value first.")
        suggested = (normal, round(c_in * k), round(c_out * k))
    else:
        k = steps / (plain * normal + n_in * c_in + n_out * c_out)
        suggested = (round(normal * k), round(c_in * k), round(c_out * k))
    return {"axis": axis, "basis": basis, "commanded_squares": squares, "moved_squares": round(moved, 3),
            "off_by": round(moved - squares, 3), "current": [normal, c_in, c_out], "suggested": list(suggested)}


@app.post("/tune/apply")
def tune_apply(normal: int, corner_in: int, corner_out: int, axis: Optional[int] = None,
               actual: Optional[float] = None):
    """Use these step values for this motor from now on (saved in tuning.json). If `actual` is given (a square
    number, or a point between squares), also record that the magnet is on the nearest square, and report how
    many steps would put it exactly in the middle of that square (`centre_steps`)."""
    axis = pick_axis(axis)
    if not all(1 <= v <= 5000 for v in (normal, corner_in, corner_out)):
        raise HTTPException(422, "step values must be 1-5000")
    if actual is not None and not 0 <= actual < SQUARES:
        raise HTTPException(422, f"actual must be 0-{SQUARES - 1}")
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A token is still moving")
    try:
        STEPS[axis] = (normal, corner_in, corner_out)
        save_tuning()
        centre_steps = 0
        if actual is not None:
            put_square(axis, round(actual) % SQUARES)
            centre_steps = centre_steps_for(axis, actual)
        return {"axis": axis, "steps": [normal, corner_in, corner_out], "square": get_square(axis),
                "centre_steps": centre_steps}
    finally:
        lock.release()


@app.get("/tune/centre")
def tune_centre(actual: float, target: Optional[int] = None, axis: Optional[int] = None):
    """Motor steps that move the magnet from `actual` (a square number, or a point between squares) to the
    middle of `target` (default: the nearest square)."""
    axis = pick_axis(axis)
    if not 0 <= actual < SQUARES or (target is not None and not 0 <= target < SQUARES):
        raise HTTPException(422, f"positions must be 0-{SQUARES - 1}")
    return {"axis": axis, "steps": centre_steps_for(axis, actual, target)}


@app.post("/tune/guided")
def tune_guided(body: dict):
    """Turn the legs of a guided run into step values. Each leg is {start, squares, steps}: the magnet started
    dead centre on `start`, was sent `squares` forward, and `steps` is how many motor steps it really took to end
    dead centre on the target. A leg that is one hop into or out of a corner sets that corner's own value; a
    leg of plain squares gives a sample for the normal value (the average of the samples is used)."""
    axis = pick_axis(body.get("axis"))
    legs = body.get("legs")
    if not isinstance(legs, list) or not legs:
        raise HTTPException(422, "legs must be a list")
    normal, c_in, c_out = steps_for(axis)
    corners = {c: list(v) for c, v in CORNERS[axis].items()}
    before = {c: tuple(corners.get(c, (c_in, c_out))) for c in CORNER_SQUARES}
    samples, touched = [], set()
    for leg in legs:
        try:
            start, squares, steps = int(leg["start"]), int(leg["squares"]), abs(float(leg["steps"]))
        except (KeyError, TypeError, ValueError):
            raise HTTPException(422, "each leg needs start, squares and steps")
        if not 0 <= start < SQUARES or squares == 0 or steps <= 0:
            raise HTTPException(422, "each leg needs a start square, some squares and some steps")
        hops = hop_info(start, squares)
        if len(hops) == 1 and hops[0][0] != "plain":
            kind, corner = hops[0]
            pair = corners.setdefault(corner, [c_in, c_out])
            pair[0 if kind == "in" else 1] = round(steps)
            touched.add(corner)
        elif all(kind == "plain" for kind, _ in hops):
            samples.append(steps / len(hops))
    if not samples and not touched:
        raise HTTPException(422, "Those legs don't isolate a corner or a plain stretch, so there is nothing to tune.")
    new_normal = round(sum(samples) / len(samples)) if samples else normal
    values = [new_normal] + [v for pair in corners.values() for v in pair]
    if not all(1 <= v <= 5000 for v in values):
        raise HTTPException(422, "step values must be 1-5000")
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A token is still moving")
    try:
        STEPS[axis] = (new_normal, c_in, c_out)
        CORNERS[axis] = {c: tuple(v) for c, v in corners.items()}
        save_tuning()
    finally:
        lock.release()
    return {"axis": axis, "normal": [normal, new_normal], "edges": [round(x) for x in samples],
            "corners": {str(c): {"in": [before[c][0], CORNERS[axis][c][0]], "out": [before[c][1], CORNERS[axis][c][1]]}
                        for c in sorted(touched)}}


@app.post("/tune/corners/clear")
def tune_corners_clear(axis: Optional[int] = None):
    """Forget the per-corner values for this motor, so every corner uses the shared ones again."""
    axis = pick_axis(axis)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A token is still moving")
    try:
        CORNERS[axis] = {}
        save_tuning()
        return {"axis": axis, "corners": {}}
    finally:
        lock.release()


@app.post("/jog")
def jog(steps: int, axis: Optional[int] = None):
    """Nudge the motor by raw motor steps (negative goes back) without touching the saved square.
    For correcting a move by hand while tuning."""
    axis = pick_axis(axis)
    if steps == 0 or abs(steps) > 20000:
        raise HTTPException(422, "steps must be 1-20000 either way")
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A token is still moving")
    try:
        stop_requested.clear()
        drive(axis, steps, PERIODS[axis][0])
        return {"axis": axis, "steps": steps, "moved": not DRY_RUN}
    finally:
        lock.release()


@app.post("/tune/speed")
def tune_speed(plain_us: int, corner_us: int, axis: Optional[int] = None):
    """Set the step period (microseconds, smaller = faster) for plain squares and for corner legs, saved in tuning.json."""
    axis = pick_axis(axis)
    if not all(200 <= v <= 20000 for v in (plain_us, corner_us)):
        raise HTTPException(422, "periods must be 200-20000 microseconds")
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A token is still moving")
    try:
        PERIODS[axis] = (plain_us, corner_us)
        save_tuning()
        return {"axis": axis, "plain_us": plain_us, "corner_us": corner_us}
    finally:
        lock.release()


@app.post("/stop")
def stop():
    stop_requested.set()
    get_board().stop()
    return {"stopped": True}


@app.get("/pos")
def pos():
    return {"steps": get_board().pos()}


# --- vision events (from vision/watch.py). For now: log and keep the recent ones; the rules engine will consume these. ---
vision_events = collections.deque(maxlen=200)


@app.post("/vision/events")
def vision_event(event: dict):
    """Receive one event from the vision process: dice_rolled, dice_unclear, dice_cleared, card_drawn, card_unknown, card_removed."""
    event.setdefault("t", time.time())
    event["received"] = time.time()
    vision_events.append(event)
    print("vision:", json.dumps(event), flush=True)
    return {"ok": True, "queued": len(vision_events)}


@app.get("/vision/events")
def vision_recent(n: int = 20):
    """The most recent vision events, newest last."""
    return list(vision_events)[-n:]
