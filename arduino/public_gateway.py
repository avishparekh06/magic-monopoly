"""Public front door for the belt board: the only thing the internet can reach.

Run this, then tunnel THIS port (ngrok http 8002). The bench tool on 8001 stays private: it has jog, tuning and
set-position controls that must never be public. This gateway exposes one action, step forward N squares, behind a
secret key, and passes it to the bench tool, which owns the Arduino and remembers where the magnet is. Set the
position in the bench tool first; every step starts from there.

    cd arduino && python -m uvicorn public_gateway:app --port 8002
    ngrok http 8002

    https://<tunnel>/step?squares=3&key=<key>          (GET or POST)
    https://<tunnel>/position?key=<key>

Both say which square the magnet is on, using the game's own ids and names (server/board.json).

The key is PUBLIC_KEY if set, else the contents of arduino/public_key.txt (made on first run, git-ignored).
"""
import asyncio
import json
import os
import secrets
import time
from typing import Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.environ.get("BENCH_URL", "http://127.0.0.1:8001")           # the private bench tool
MAX_SQUARES = int(os.environ.get("MAX_STEP_SQUARES", 12))              # most squares one request may move
KEY_FILE = os.path.join(HERE, "public_key.txt")
BOARD_FILE = os.path.join(HERE, "..", "server", "board.json")         # the game's squares: the list index is the square id


def load_board():
    try:
        with open(BOARD_FILE, encoding="utf-8") as f:
            return json.load(f)["squares"]
    except (OSError, ValueError, KeyError):
        return []


BOARD = load_board()


def describe(square):
    """The game's square for this id (the motor's square number is the same number)."""
    entry = BOARD[square] if 0 <= square < len(BOARD) else {}
    return {"id": square, "name": entry.get("name", f"Square {square}"), "type": entry.get("type")}


def load_key():
    if os.environ.get("PUBLIC_KEY"):
        return os.environ["PUBLIC_KEY"]
    try:
        with open(KEY_FILE) as f:
            key = f.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_urlsafe(18)
    with open(KEY_FILE, "w") as f:
        f.write(key + "\n")
    os.chmod(KEY_FILE, 0o600)
    return key


KEY = load_key()
app = FastAPI(title="Belt Board (public)", docs_url=None, redoc_url=None, openapi_url=None)
busy = asyncio.Lock()                                                  # one move at a time


def make_client():
    return httpx.AsyncClient(base_url=BENCH, timeout=180)             # a long move takes a while


def check_key(request: Request, key: Optional[str], x_key: Optional[str]):
    given = key or x_key or ""
    if not secrets.compare_digest(given.encode(), KEY.encode()):
        who = request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")
        print(f"{time.strftime('%H:%M:%S')} refused {request.url.path} from {who}: wrong or missing key", flush=True)
        raise HTTPException(401, "missing or wrong key")


async def ask_bench(method, path, **params):
    try:
        async with make_client() as client:
            r = await client.request(method, path, params=params)
    except httpx.HTTPError:
        raise HTTPException(502, "The bench tool isn't running (port 8001).")
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        raise HTTPException(r.status_code, data.get("detail", "the bench tool refused"))
    return data


@app.get("/")
def root():
    return {"service": "belt board", "step": "/step?squares=N&key=..."}


@app.api_route("/step", methods=["GET", "POST"])
async def step(request: Request, squares: int = 1, key: Optional[str] = None,
               x_key: Optional[str] = Header(None)):
    """Move the magnet forward `squares` squares from where the bench tool says it is."""
    check_key(request, key, x_key)
    if not 1 <= squares <= MAX_SQUARES:
        raise HTTPException(422, f"squares must be 1-{MAX_SQUARES}")
    if busy.locked():
        raise HTTPException(409, "still moving")
    async with busy:
        who = request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")
        print(f"{time.strftime('%H:%M:%S')} step {squares} from {who}", flush=True)
        r = await ask_bench("POST", "/board/move", squares=squares)
    here = describe(r["to"]["square"])
    return {"ok": True, "square": here["id"], "name": here["name"], "type": here["type"],
            "from": describe(r["from"]), "squares": r["squares"], "motor_steps": r["motor_steps"],
            "moved": r["moved"], "passed_go": r["passed_go"]}


@app.get("/position")
async def position(request: Request, key: Optional[str] = None, x_key: Optional[str] = Header(None)):
    """Where the bench tool says the magnet is."""
    check_key(request, key, x_key)
    here = describe((await ask_bench("GET", "/board"))["square"])
    return {"square": here["id"], "name": here["name"], "type": here["type"]}
