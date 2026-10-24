import collections
import json
import os
import random
import threading
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from control import Board

SQUARE_MM = float(os.environ.get("SQUARE_MM", 30))   # belt travel per Monopoly square, tune on the real board
SPEED_MM_S = float(os.environ.get("SPEED_MM_S", 20))
DRY_RUN = os.environ.get("DRY_RUN") == "1"            # skip the Arduino and just return rolls
NORMAL_STEPS = int(os.environ.get("NORMAL_STEPS", 225))   # motor steps per normal step (same as typing "0 225" in control.py)
CORNER_STEPS = int(os.environ.get("CORNER_STEPS", 500))   # used only by /move
CORNER_IN = int(os.environ.get("CORNER_IN", 300))         # square before a corner -> middle of the corner
CORNER_OUT = int(os.environ.get("CORNER_OUT", 500))       # middle of the corner -> square after it
SQUARES = 40                                           # standard Monopoly board, square 0 = GO
CORNER_SQUARES = {0, 10, 20, 30}                       # GO, Jail, Free Parking, Go To Jail
SQUARE_NAMES = {0: "GO", 10: "Jail", 20: "Free Parking", 30: "Go To Jail"}
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "board_state.json")


def load_square():
    try:
        with open(STATE_FILE) as f:
            return int(json.load(f)["square"]) % SQUARES
    except (OSError, ValueError, KeyError):
        return 0                                       # no saved position, assume GO


def save_square(square):
    with open(STATE_FILE, "w") as f:
        json.dump({"square": square}, f)


current_square = load_square()
LANES = 3                                            # player N drives axis N (0 = X, 1 = Y, 2 = Z)

app = FastAPI(title="Belt Dice")
lock = threading.Lock()
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
def move(steps: int = 0, corners: int = 0):
    """Move by `steps` normal steps plus `corners` corner steps (motor steps), on axis 0. Negative goes back."""
    axis = 0
    motor_steps = steps * NORMAL_STEPS + corners * CORNER_STEPS
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A token is still moving")
    try:
        drive(axis, motor_steps)
        return {"axis": axis, "steps": steps, "corners": corners,
                "motor_steps": motor_steps, "moved": bool(motor_steps) and not DRY_RUN}
    finally:
        lock.release()


def drive(axis, motor_steps):
    """Send a move to the Arduino (caller holds the lock). Skipped in dry run."""
    if motor_steps and not DRY_RUN:
        try:
            get_board().move(axis, motor_steps)
        except Exception as e:
            raise HTTPException(503, f"Motor move failed: {e}")


def board_state(square):
    return {"square": square, "name": SQUARE_NAMES.get(square, f"Square {square}"),
            "is_corner": square in CORNER_SQUARES}


@app.get("/board")
def board_pos():
    """Where the token is on the Monopoly board."""
    return board_state(current_square)


@app.post("/board/set")
def board_set(square: int):
    """Tell the server where the token physically is (no motor move). Use after moving it by hand."""
    global current_square
    if not 0 <= square < SQUARES:
        raise HTTPException(422, f"square must be 0-{SQUARES - 1}")
    current_square = square
    save_square(current_square)
    return board_state(current_square)


def plan_move(start, squares):
    """Cost of moving `squares` (negative = back) from `start`.

    A hop costs the same in both directions, so forward then back returns to the same motor position.
    Into a corner costs CORNER_IN, out of one costs CORNER_OUT, every other hop costs NORMAL_STEPS.
    """
    n = abs(squares)
    if squares >= 0:
        hops = [((start + i - 1) % SQUARES, (start + i) % SQUARES) for i in range(1, n + 1)]
    else:
        hops = [((start - i) % SQUARES, (start - i + 1) % SQUARES) for i in range(1, n + 1)]
    steps = corners = total = 0
    for a, b in hops:                                    # each hop is a forward hop a -> b
        if b in CORNER_SQUARES:
            corners, total = corners + 1, total + CORNER_IN
        elif a in CORNER_SQUARES:
            corners, total = corners + 1, total + CORNER_OUT
        else:
            steps, total = steps + 1, total + NORMAL_STEPS
    return steps, corners, (total if squares >= 0 else -total)


def run_board_move(squares):
    """Move the token `squares` squares (negative = back), save and report where it ended up."""
    global current_square
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A token is still moving")
    try:
        start = current_square
        steps, corners, motor_steps = plan_move(start, squares)
        drive(0, motor_steps)
        current_square = (start + squares) % SQUARES
        save_square(current_square)
        return {"from": start, "to": board_state(current_square), "squares": squares,
                "steps": steps, "corners": corners, "motor_steps": motor_steps,
                "passed_go": not 0 <= start + squares < SQUARES,
                "moved": bool(motor_steps) and not DRY_RUN}
    finally:
        lock.release()


@app.post("/board/move")
def board_move(squares: int):
    """Move the token by `squares` squares around the board (negative goes back)."""
    return run_board_move(squares)


@app.post("/board/goto")
def board_goto(square: int, direction: str = "auto"):
    """Move the token to `square`. direction: auto (shortest way), forward, or back."""
    if not 0 <= square < SQUARES:
        raise HTTPException(422, f"square must be 0-{SQUARES - 1}")
    if direction not in ("auto", "forward", "back"):
        raise HTTPException(422, "direction must be auto, forward or back")
    ahead = (square - current_square) % SQUARES          # squares to go forward
    behind = ahead - SQUARES if ahead else 0              # squares to go back, as a negative
    if direction == "forward" or (direction == "auto" and ahead <= -behind):
        squares = ahead
    else:
        squares = behind
    return run_board_move(squares)


@app.post("/stop")
def stop():
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
