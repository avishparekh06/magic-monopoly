import collections
import json
import math
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
# Lane 1 is hand-tuned. Lanes 0 and 2 are ESTIMATES from lane 1's numbers, assuming the lanes sit 20 mm apart
# (lane 0 outside lane 1, lane 2 inside): a lane moved out or in by 20 mm changes a square by 20 steps and a corner
# crossing by 220 steps (5 steps per mm), split in:out like lane 1's 36:64. Tune them with the bench tool.
AXIS_STEPS = {0: (215, 277, 503), 1: (195, 199, 361), 2: (175, 121, 219)}
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
# Per-corner step periods in microseconds: {axis: {corner square: (period into the corner, period out of it)}}. A corner
# that isn't listed uses the motor's shared corner period (PERIODS). Free Parking needs a much harder burst.
CORNER_PERIODS = {0: {}, 1: {}, 2: {}}
# A fast zone per motor: {axis: (first square, number of hops, step period in microseconds)} or None. Every hop from
# `first square` forward (square, square+1, ...) for that many hops runs at that period, in either direction, instead of
# its normal or corner period. For the stretch around Free Parking and Kentucky Avenue where the belt catches.
ZONES = {0: None, 1: None, 2: None}
# The zone's own step counts: {axis: {square a: steps for the hop a -> a+1}}. Inside the fast zone these replace the
# shared normal / corner values, so tuning the zone never moves the rest of the board (and the reverse). Entries are
# copied from the shared values when a zone is set; they are kept if the zone is cleared, for when it comes back.
ZONE_COSTS = {0: {}, 1: {}, 2: {}}
SQUARES = 40                                           # standard Monopoly board, square 0 = GO
CORNER_SQUARES = {0, 10, 20, 30}                       # GO, Jail, Free Parking, Go To Jail
# Free Parking zone: the hops 19->20 (into it) and 20->21 (out of it, ending on Kentucky Avenue). Free Parking and
# Kentucky Avenue behave unlike the rest of the belt, so a leg whose path touches these hops must not leak into the
# shared values: only the "in:20" / "out:20" primaries may be solved from it, anything else is skipped.
FP_CORNER = 20
FP_ZONE_HOPS = {(19, 20), (20, 21)}
SQUARE_NAMES = {0: "GO", 10: "Jail", 20: "Free Parking", 30: "Go To Jail"}
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "board_state.json")
TUNING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tuning.json")   # values saved by the bench tool's Tune


def write_json(path, data):
    """Save a settings file all at once: write a temp file, then swap it in, so a kill mid-save can't leave half a file."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def warn_unreadable(path, err):
    print(f"WARNING: {path} could not be read ({err}); using the defaults for what it held.", flush=True)


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
        for a, per_corner in saved.get("corner_periods", {}).items():
            if int(a) in CORNER_PERIODS:
                CORNER_PERIODS[int(a)] = {int(c): tuple(int(x) for x in v) for c, v in per_corner.items()
                                          if int(c) in CORNER_SQUARES and len(v) == 2}
        for a, z in saved.get("zones", {}).items():
            if int(a) in ZONES and z and len(z) == 3:
                ZONES[int(a)] = tuple(int(x) for x in z)
        for a, costs in saved.get("zone_costs", {}).items():
            if int(a) in ZONE_COSTS:
                ZONE_COSTS[int(a)] = {int(k): int(v) for k, v in costs.items() if 0 <= int(k) < SQUARES}
    except FileNotFoundError:
        pass                                                  # first run: nothing saved yet
    except (OSError, ValueError, TypeError, AttributeError) as e:
        warn_unreadable(TUNING_FILE, e)


def save_tuning():
    write_json(TUNING_FILE, {"steps": {str(a): list(v) for a, v in STEPS.items()},
                   "periods": {str(a): list(v) for a, v in PERIODS.items()},
                   "corners": {str(a): {str(c): list(v) for c, v in per.items()} for a, per in CORNERS.items()},
                   "corner_periods": {str(a): {str(c): list(v) for c, v in per.items()}
                                      for a, per in CORNER_PERIODS.items()},
                   "zones": {str(a): list(z) for a, z in ZONES.items() if z},
                   "zone_costs": {str(a): {str(k): v for k, v in c.items()} for a, c in ZONE_COSTS.items()}})


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
    except FileNotFoundError:
        pass                                                  # first run: nothing saved yet
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        warn_unreadable(STATE_FILE, e)
    return squares


def load_offsets():
    """Saved offset of every axis: how far past the middle of its square the magnet is (squares, about -0.5..0.5)."""
    out = {a: 0.0 for a in range(LANES)}
    try:
        with open(STATE_FILE) as f:
            for a, o in json.load(f).get("offsets", {}).items():
                if int(a) in out:
                    out[int(a)] = max(-0.5, min(0.5, float(o)))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return out


def save_square(*_):
    write_json(STATE_FILE, {"square": current_square, "axes": {str(a): get_square(a) for a in range(LANES)},
                   "offsets": {str(a): offsets[a] for a in range(LANES)}})


_loaded = load_squares()
current_square = _loaded[MOTOR_AXIS]                    # square of the MOTOR_AXIS token (the game's)
other_squares = {a: s for a, s in _loaded.items() if a != MOTOR_AXIS}
offsets = load_offsets()                                # position = square + offset


def pick_axis(axis):
    """The axis a request means: MOTOR_AXIS unless the caller (the bench tool) chose another."""
    axis = MOTOR_AXIS if axis is None else axis
    if not 0 <= axis < LANES:
        raise HTTPException(422, f"axis must be 0-{LANES - 1}")
    return axis


def get_square(axis):
    return current_square if axis == MOTOR_AXIS else other_squares[axis]


def get_position(axis):
    """Where the magnet is in squares: its saved square plus the offset (an int when the offset is 0)."""
    return get_square(axis) + offsets[axis] if offsets[axis] else get_square(axis)


def put_square(axis, square):
    global current_square
    if axis == MOTOR_AXIS:
        current_square = square
    else:
        other_squares[axis] = square
    offsets[axis] = 0.0
    save_square()


def put_position(axis, position):
    """Record the magnet at `position` (0 <= position < 40): the nearest square plus the offset from its middle."""
    square = math.ceil(position - 0.5)
    off = round(position - square, 6)
    put_square(axis, square % SQUARES)
    offsets[axis] = off
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


def board_state(square, offset=0):
    state = {"square": square, "name": SQUARE_NAMES.get(square, f"Square {square}"),
             "is_corner": square in CORNER_SQUARES}
    if offset:
        state["offset"] = offset
    return state


@app.get("/axes")
def axes():
    """The motors the bench tool can pick, with each one's step values and saved square."""
    return {"default": MOTOR_AXIS,
            "axes": [{"axis": a, "normal": steps_for(a)[0], "corner_in": steps_for(a)[1],
                      "corner_out": steps_for(a)[2], "period_plain": PERIODS[a][0],
                      "period_corner": PERIODS[a][1], "square": get_square(a), "offset": offsets[a],
                      "corners": {str(c): list(v) for c, v in CORNERS[a].items()},
                      "zone": list(ZONES[a]) if ZONES[a] else None,
                      "zone_costs": {str(k): ZONE_COSTS[a][k] for k in sorted(zone_starts(a)) if k in ZONE_COSTS[a]},
                      "corner_periods": {str(c): list(v) for c, v in CORNER_PERIODS[a].items()}}
                     for a in range(LANES)]}


@app.get("/board")
def board_pos(axis: Optional[int] = None):
    """Where the token is on the Monopoly board."""
    axis = pick_axis(axis)
    return board_state(get_square(axis), offsets[axis])


@app.post("/board/set")
def board_set(square: int, axis: Optional[int] = None):
    """Tell the server where the token physically is (no motor move). Use after moving it by hand."""
    axis = pick_axis(axis)
    if not 0 <= square < SQUARES:
        raise HTTPException(422, f"square must be 0-{SQUARES - 1}")
    put_square(axis, square)
    return board_state(square)


@app.post("/board/at")
def board_at(position: float, axis: Optional[int] = None):
    """Tell the server exactly where the magnet is, between squares too (11.3 = 0.3 of the way from 11 to 12)."""
    axis = pick_axis(axis)
    if not 0 <= position <= SQUARES:
        raise HTTPException(422, f"position must be 0-{SQUARES}")
    put_position(axis, position % SQUARES)
    return board_state(get_square(axis), offsets[axis])


def hop_kind(a, b):
    """(kind, corner) of the forward hop a -> b."""
    return ("in", b) if b in CORNER_SQUARES else ("out", a) if a in CORNER_SQUARES else ("plain", None)


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
    return [hop_kind(a, b) for a, b in hops]


def hop_kinds(start, squares):
    return [kind for kind, _ in hop_info(start, squares)]


def hop_cost(axis, kind, corner=None, a=None):
    """Motor steps for one hop of this kind on this motor (a corner's own values win over the shared ones). For the
    hop that starts on square `a`: the fast zone's own count if it lies in the zone."""
    if a is not None and a in ZONE_COSTS[axis] and a in zone_starts(axis):
        return ZONE_COSTS[axis][a]
    normal, c_in, c_out = STEPS[axis]
    if kind == "plain":
        return normal
    own = CORNERS[axis].get(corner)
    return (own[0] if own else c_in) if kind == "in" else (own[1] if own else c_out)


def fwd_cost(axis, k):
    """Motor steps for the forward hop k -> k+1."""
    k %= SQUARES
    kind, corner = hop_kind(k, (k + 1) % SQUARES)
    return hop_cost(axis, kind, corner, k)


def pos_cost(axis, x):
    """Motor steps from the middle of square 0 forward to position `x` (squares; may have a fraction, any size)."""
    laps = int(x // SQUARES)
    r = x - laps * SQUARES
    whole = int(r)
    total = laps * sum(fwd_cost(axis, k) for k in range(SQUARES))
    total += sum(fwd_cost(axis, k) for k in range(whole))
    if r - whole > 1e-12:
        total += (r - whole) * fwd_cost(axis, whole)
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


def path_hops(x0, x1):
    """The hops between positions x0 and x1 (squares, may be fractional or past 40), in the order the magnet
    travels, as (kind, corner, weight, a, b): weight is the part of the hop covered (1 = all of it), a -> b the
    forward hop."""
    lo, hi = min(x0, x1), max(x0, x1)
    hops = []
    for k in range(math.floor(lo), math.ceil(hi)):
        w = min(hi, k + 1) - max(lo, k)
        if w > 1e-9:
            a, b = k % SQUARES, (k + 1) % SQUARES
            hops.append((*hop_kind(a, b), w, a, b))
    return hops[::-1] if x1 < x0 else hops


def zone_starts(axis):
    """The squares whose forward hop (square -> square+1) lies in this motor's fast zone."""
    z = ZONES[axis]
    return {(z[0] + i) % SQUARES for i in range(z[1])} if z else set()


def fill_zone_costs(axis):
    """Give every hop in the axis's fast zone its own step count, copied from what it costs now if it has none."""
    for a in zone_starts(axis):
        if a not in ZONE_COSTS[axis]:
            kind, corner = hop_kind(a, (a + 1) % SQUARES)
            ZONE_COSTS[axis][a] = hop_cost(axis, kind, corner)               # no `a`: the shared cost


for _axis in range(LANES):
    fill_zone_costs(_axis)


def hop_period(axis, kind, corner, a=None):
    """Step period (microseconds) for the hop that starts on square `a`: the fast zone's if it lies in it, else plain
    squares', or a corner leg's (the corner's own period if it has one)."""
    if a is not None and a in zone_starts(axis) and ZONES[axis][2]:        # a zone period of 0 means no speed change
        return ZONES[axis][2]
    p_plain, p_corner = PERIODS[axis]
    if kind == "plain":
        return p_plain
    own = CORNER_PERIODS[axis].get(corner)
    return (own[0] if kind == "in" else own[1]) if own else p_corner


def move_ends(start, squares):
    """(from, to) positions of a move of `squares` from position `start`: it ends in the middle of a square."""
    return start, math.ceil(start - 0.5) + squares


def plan_segments(start, squares, axis=None):
    """A move as a list of (signed motor steps, step period in microseconds), in travel order.

    `start` is a position (a fractional one starts part way along a hop). Corner legs get the corner's period,
    plain squares the normal one. Neighbouring hops at the same speed are joined, so a move with no corner is a
    single piece. The steps add up to plan_move's total."""
    axis = pick_axis(axis)
    x0, x1 = move_ends(start, squares)
    pieces = []
    for kind, corner, w, a, _ in path_hops(x0, x1):
        steps, period = w * hop_cost(axis, kind, corner, a), hop_period(axis, kind, corner, a)
        if pieces and pieces[-1][1] == period:
            pieces[-1][0] += steps
        else:
            pieces.append([steps, period])
    sign = -1 if x1 < x0 else 1
    out, done = [], 0                                      # round the running total, so the pieces add up exactly
    cum = 0.0
    for steps, period in pieces:
        cum += steps
        out.append((sign * (round(cum) - done), period))
        done = round(cum)
    return out


def plan_move(start, squares, axis=None):
    """Cost of moving `squares` (negative = back) from position `start` (a square, or a point between squares).

    A hop costs the same in both directions, so forward then back returns to the same motor position.
    Into a corner costs the motor's corner-in value, out of one its corner-out value, every other hop its normal value.
    """
    axis = pick_axis(axis)
    x0, x1 = move_ends(start, squares)
    hops = path_hops(x0, x1)
    total = round(sum(w * hop_cost(axis, kind, corner, a) for kind, corner, w, a, _ in hops))
    plain = sum(1 for h in hops if h[0] == "plain")
    return plain, len(hops) - plain, (-total if x1 < x0 else total)


def run_board_move(squares, axis=None):
    """Move the token `squares` squares (negative = back), save and report where it ended up."""
    axis = pick_axis(axis)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A token is still moving")
    try:
        stop_requested.clear()
        start = get_position(axis)                        # may be part way between squares
        steps, corners, motor_steps = plan_move(start, squares, axis)
        segments = plan_segments(start, squares, axis)
        for seg_steps, period in segments:                # corner legs go faster than plain squares
            if stop_requested.is_set():
                break
            drive(axis, seg_steps, period)
        if stop_requested.is_set():                       # not 409: the game bridge retries those as "busy"
            raise HTTPException(503, "Stopped part way. Use Set position to say where the magnet is.")
        square = get_square(axis)
        end = (square + squares) % SQUARES
        put_square(axis, end)
        return {"from": round(start, 3) if isinstance(start, float) else start, "to": board_state(end),
                "squares": squares, "axis": axis,
                "steps": steps, "corners": corners, "motor_steps": motor_steps, "segments": segments,
                "passed_go": not 0 <= square + squares < SQUARES,
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
    normal, c_in, c_out = steps_for(axis)
    steps = abs(motor_steps)
    zone = zone_starts(axis)
    weights = {"plain": 0.0, "in": 0.0, "out": 0.0}
    for k, c, w, a, _ in path_hops(start, start + moved):
        if a in zone:
            steps -= w * hop_cost(axis, k, c, a)                    # the zone is a fixed block: tune only the rest
        else:
            weights[k] += w
    plain, n_in, n_out = weights["plain"], weights["in"], weights["out"]
    if zone and plain + n_in + n_out < 1e-9:
        raise HTTPException(422, "That move is entirely inside the fast zone. Tune the zone with the guided run "
                                 "(scope: Fast zone).")
    if steps <= 0:
        raise HTTPException(422, "The fast zone alone already takes more steps than that move sent.")
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


def parse_primary(primary):
    """"normal", "in:<corner>", "out:<corner>" or "zone:<square>" -> (kind, corner) with kind plain/in/out, or
    ("zone", square) for the fast zone's own hop that leaves that square."""
    kind, _, c = str(primary).partition(":")
    if kind == "normal" and not c:
        return "plain", None
    if kind in ("in", "out") and c.isdigit() and int(c) in CORNER_SQUARES:
        return kind, int(c)
    if kind == "zone" and c.isdigit() and int(c) < SQUARES:
        return "zone", int(c)
    raise HTTPException(422, 'primary must be "normal", "in:<corner>", "out:<corner>" (corner 0, 10, 20 or 30) '
                             'or "zone:<square>"')


def set_value(axis, primary, value):
    """Set the one step value `primary` names, for this motor; returns the old value. Caller holds the lock."""
    kind, corner = parse_primary(primary)
    if kind == "zone":
        if corner not in zone_starts(axis):
            raise HTTPException(422, f"square {corner} is not in this motor's fast zone")
        old = ZONE_COSTS[axis].get(corner)
        ZONE_COSTS[axis][corner] = value
    elif kind == "plain":
        old = STEPS[axis][0]
        STEPS[axis] = (value,) + tuple(STEPS[axis][1:])
    else:
        pair = list(CORNERS[axis].get(corner, STEPS[axis][1:]))     # a corner starts from the shared values
        k = 0 if kind == "in" else 1
        old, pair[k] = pair[k], value
        CORNERS[axis][corner] = tuple(pair)
    save_tuning()
    return old


def position_reply(axis):
    return {"position": round(get_position(axis), 3), "square": get_square(axis), "offset": offsets[axis]}


@app.post("/tune/leg")
def tune_leg(body: dict):
    """Tune from one stop of a guided run. The magnet went from `start` (a position, may be fractional) to `actual`
    (where the user clicked it) using exactly `sent` motor steps; `squares` is what was commanded (its sign says
    which way round the board). Only the `primary` value is solved, from the share of the path it covers:
    new = (|sent| - what the other hops cost now) / (weight of primary hops covered). The magnet is then recorded at
    `actual`. A leg that touches the Free Parking zone can only tune Free Parking's own values."""
    axis = pick_axis(body.get("axis"))
    try:
        start, actual = float(body["start"]), float(body["actual"])
        squares, sent = int(body["squares"]), int(body["sent"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "a leg needs axis, primary, start, squares, sent and actual")
    kind, corner = parse_primary(body.get("primary"))
    primary = body["primary"]
    if ZONES[axis] and kind in ("in", "out"):                           # a corner hop inside the zone is the zone's own
        hop_a = (corner - 1) % SQUARES if kind == "in" else corner
        if hop_a in zone_starts(axis):
            kind, corner, primary = "zone", hop_a, f"zone:{hop_a}"
    if kind == "zone" and corner not in zone_starts(axis):
        raise HTTPException(422, f"square {corner} is not in this motor's fast zone")
    if not all(math.isfinite(v) for v in (start, actual)):
        raise HTTPException(422, "start and actual must be numbers")
    start, actual = start % SQUARES, actual % SQUARES                  # 40 is GO again, -0.2 is 39.8
    d = (actual - start) % SQUARES
    moved = d + SQUARES * round((squares - d) / SQUARES)               # the whole number of laps closest to the command
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A token is still moving")
    try:
        hops = path_hops(start, start + moved)
        touchy = {(a, (a + 1) % SQUARES) for a in zone_starts(axis)} or FP_ZONE_HOPS   # the fast zone, if one is set
        own = kind == "zone" or (not ZONES[axis] and corner == FP_CORNER)           # may tune the zone / Free Parking
        if any((a, b) in touchy and w > 1e-3 for _, _, w, a, b in hops) and not own:
            put_position(axis, actual)
            return {"axis": axis, "primary": primary, "skipped": True,
                    "message": "skipped: this leg touches Free Parking", **position_reply(axis)}
        if abs(moved) < 0.005:
            raise HTTPException(422, "The magnet is still on the start, so there is nothing to tune from.")
        if sent == 0 or (moved > 0) != (sent > 0):
            raise HTTPException(422, "The magnet went the opposite way to the command. That is the direction "
                                     "wiring, not the step values.")
        weight, rest = 0.0, 0.0
        for k, c, w, a, _ in hops:
            mine = (a == corner) if kind == "zone" else ((k, c) == (kind, corner) and a not in zone_starts(axis))
            if mine:
                weight += w
            else:
                rest += w * hop_cost(axis, k, c, a)
        if weight < 0.05:
            raise HTTPException(422, f"That leg covers almost none of {primary} "
                                     f"({weight:.2f} of a hop), so it can't tune it.")
        new = round((abs(sent) - rest) / weight)
        if not 1 <= new <= 5000:
            raise HTTPException(422, f"That leg would make {primary} {new}, outside 1-5000. "
                                     "Check that you clicked where the magnet really is.")
        old = set_value(axis, primary, new)
        put_position(axis, actual)
        return {"axis": axis, "primary": primary, "skipped": False, "old": old, "new": new,
                "weight": round(weight, 3), "undo": {"primary": primary, "value": old},
                **position_reply(axis)}
    finally:
        lock.release()


@app.post("/tune/value")
def tune_value(body: dict):
    """Set one step value directly (the guided run's Undo). Body: axis, primary ("normal", "in:20", ...), value."""
    axis = pick_axis(body.get("axis"))
    try:
        value = int(body["value"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "value must be a whole number")
    parse_primary(body.get("primary"))
    if not 1 <= value <= 5000:
        raise HTTPException(422, "step values must be 1-5000")
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A token is still moving")
    try:
        old = set_value(axis, body["primary"], value)
        return {"axis": axis, "primary": body["primary"], "old": old, "new": value}
    finally:
        lock.release()


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
def tune_speed(plain_us: Optional[int] = None, corner_us: Optional[int] = None, axis: Optional[int] = None,
               corner: Optional[int] = None, in_us: Optional[int] = None, out_us: Optional[int] = None,
               clear: bool = False):
    """Set the step period (microseconds, smaller = faster), saved in tuning.json. Without `corner`: plain_us for plain
    squares and corner_us for corner legs. With `corner` (0, 10, 20 or 30): that corner's own period into it
    (in_us) and out of it (out_us, default the same as in_us; corner_us also works as the default for both), or
    clear=true to go back to the shared corner period."""
    axis = pick_axis(axis)
    if corner is not None:
        if corner not in CORNER_SQUARES:
            raise HTTPException(422, "corner must be 0, 10, 20 or 30")
        period_in = in_us if in_us is not None else corner_us
        period_out = out_us if out_us is not None else period_in
        if not clear and (period_in is None or not all(200 <= v <= 20000 for v in (period_in, period_out))):
            raise HTTPException(422, "periods must be 200-20000 microseconds")
    elif plain_us is None or corner_us is None or not all(200 <= v <= 20000 for v in (plain_us, corner_us)):
        raise HTTPException(422, "periods must be 200-20000 microseconds")
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A token is still moving")
    try:
        if corner is None:
            PERIODS[axis] = (plain_us, corner_us)
            save_tuning()
            return {"axis": axis, "plain_us": plain_us, "corner_us": corner_us}
        if clear:
            CORNER_PERIODS[axis].pop(corner, None)
        else:
            CORNER_PERIODS[axis][corner] = (period_in, period_out)
        save_tuning()
        return {"axis": axis, "corner": corner,
                "corner_periods": {str(c): list(v) for c, v in CORNER_PERIODS[axis].items()}}
    finally:
        lock.release()


@app.post("/tune/zone")
def tune_zone(start: Optional[int] = None, hops: Optional[int] = None, period_us: Optional[int] = None,
              axis: Optional[int] = None, clear: bool = False):
    """Set this motor's fast zone: `hops` hops starting with the hop that leaves square `start`, all at `period_us`
    (smaller = faster; 0 = keep the usual speeds), in either direction. clear=true removes it. Saved in tuning.json."""
    axis = pick_axis(axis)
    if not clear:
        if start is None or hops is None or period_us is None:
            raise HTTPException(422, "a zone needs start, hops and period_us")
        if not 0 <= start < SQUARES or not 1 <= hops <= 20 or not (period_us == 0 or 200 <= period_us <= 20000):
            raise HTTPException(422, f"start must be 0-{SQUARES - 1}, hops 1-20, period_us 0 (no speed change) or 200-20000")
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A token is still moving")
    try:
        ZONES[axis] = None if clear else (start, hops, period_us)
        fill_zone_costs(axis)
        save_tuning()
        return {"axis": axis, "zone": list(ZONES[axis]) if ZONES[axis] else None}
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
