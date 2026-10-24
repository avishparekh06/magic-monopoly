# HackTX 26 — magical board game (working context)

HackTX 26 runs Oct 24–25, 2026. Theme: fairy tale / magical. Judging is Sun 2–5 PM and the pitch is 5:30–6 PM.
Optimize for a demo that works reliably in front of judges, not for completeness.

## The project
A physical board game in the style of Monopoly, reskinned as a fairy tale. Tokens move by themselves: a magnet rides on a
GT2 belt under a thin acrylic plate and drags a token with a steel-washer base along the top. **Physical first:** real dice
and cards on the table drive the game; a laptop server is the source of truth; phones and the web view are companion screens.

Three workstreams, each with its own section below:
1. **Hardware** — Arduino firmware, serial control, belt lanes. Lives in `arduino/`.
2. **App** — game server, rules engine, web/phone views, Muse AI. Grows out of `arduino/server.py`.
3. **Vision** — OpenCV process that reads dice and cards and posts events to the app. Not started.

Priority order: tokens move from code (done for one lane) → rules engine → physical dice → phone/web view → voice + Muse
narrator → generated art.

Conventions across all three:
- Python runs on the laptop; the Arduino only steps motors. Tunables are env vars with sane defaults in code.
- Keep keys in env vars (`MUSE_API_KEY`). Never commit them. `board_state.json` is gitignored.
- Keep a manual override for everything the camera or hardware does (enter a roll by hand, re-home a token).

---

## 1. Hardware

### Mechanics
- One belt loop per player (three nested loops around the rim), each with a motor pulley and 3 idlers. The carriage hangs off
  the outside of the belt on one attachment point so it can pass the corner pulleys. Lanes ≥2 cm apart so magnets don't
  grab a neighbour's token. Non-steel top plate (3 mm acrylic / hardboard).
- **Corners are the riskiest part.** Fallback: fewer lanes or a smaller board.
- Electronics: Arduino Uno + CNC Shield V3 + A4988 drivers + NEMA-17s, 12 V 5 A supply. Set Vref before connecting motors,
  check polarity before powering the shield, never hot-plug a motor. Full wiring order and parts list are in
  `arduino/hacktx26-build-plan.md` (its GRBL and camera-closed-loop proposals are superseded; see below).

### What exists (`arduino/`)
- **`Nema_17_Stepper_Motor_With_Arduino.ino`** — custom firmware, **not GRBL**. Non-blocking step loop for 3 axes at a fixed
  step period (no acceleration). Pins: STEP 2/3/4, DIR 5/6/7, EN 8 (X, Y, Z). 115200 baud. Prints `ready` on boot.
  Line protocol (newline-terminated):
  - `M <axis> <steps> [period_us]` → `ok`, then `done <axis>` when finished. Negative steps reverse.
    If a lane runs backwards, swap LOW/HIGH in `startMove`.
  - `E 1` / `E 0` enable / release motors. `S` stop all. `Z` zero step counters. `P` → `pos x y z`. Anything else → `err`.
- **`control.py`** — `Board` class over pyserial: `move(axis, steps, period_us, wait)`, `move_mm(axis, mm, speed_mm_s)`,
  `wait()`, `stop()`, `motors(on)`, `zero()`, `pos()`. Auto-finds the port (usbmodem / usbserial / ttyACM / COM) and sleeps
  2 s after opening because the Uno resets. Bench REPL: `python control.py`, then `0 200`, `mm 1 -100 30`,
  `multi 0 100 1 50`, `pos`, `zero`, `stop`, `on`, `off`.
  - Assumes **full-step** (`MICROSTEP = 1`, no jumper caps): 200 steps/rev, 40 mm/rev, **5 steps/mm**. If jumpers are
    installed, change `MICROSTEP` (1/16 = 80 steps/mm) or every distance is off by 16×. Step period floors at 200 µs.
- Motion units used by the app: `NORMAL_STEPS` (default 225 motor steps per ordinary square) and `CORNER_STEPS`
  (default 500, **placeholder until the corner geometry is measured**). Both are env vars read by `server.py`.

### Next
- Measure `NORMAL_STEPS` and `CORNER_STEPS` on the real board; update the defaults.
- Confirm which way each lane runs and fix direction per axis if needed.
- Add acceleration or lower speed if tokens slip at start/stop.
- Keep the firmware protocol stable; the app depends on it.

---

## 2. App (game server, rules, views, AI)

### Decisions
- **Token positions are dead-reckoned** from commanded moves, not camera-tracked. Zero tokens at GO when a game starts;
  provide a one-click re-home (`/board/set`) for when a belt skips.
- **Backend is a Python FastAPI server on the laptop**, the single source of truth. Expose it through **ngrok** so a master
  game-state view is reachable by judges and phones.
- **Event log in SQLite** (rolled, moved, bought, paid rent, drew card, …); state is a replay of events. Gives save/resume,
  stats, history ("Chronicles") and crash recovery. On resume, tokens physically drive back to their squares (demo moment).
- **WebSocket broadcast** of state to all viewers. Phones send intents; the server validates against the rules and applies them.
- **Rules engine is pure Python** (state + action → new state), testable without hardware (`DRY_RUN=1`).
- **Meta Muse** wherever it fits (sponsor / judging angle):
  - **Muse Spark** (`muse-spark-1.3`, text + image): narrator / royal treasurer, parses voice intents into structured actions
    the rules engine validates, and a vision fallback for unclear dice or cards.
  - **Muse Voice Transcribe**: voice commands ("buy it", "pass", "end turn").
  - **Muse Image**: art for the virtual board, property cards, end-of-game storybook. Pre-generate and cache; never block play.
  - API `https://api.meta.ai/v1`, reportedly OpenAI/Anthropic-SDK compatible via base URL swap. **Unverified — test with a
    one-line call first.** No Muse TTS documented; use browser `speechSynthesis`.

### What exists (`arduino/server.py`, `arduino/ui.html`)
- **`server.py`** — FastAPI app. Run from inside `arduino/` (it does `from control import Board`):
  `uvicorn server:app --reload`. Deps: fastapi, uvicorn, pyserial (no requirements.txt yet).
  - Env: `DRY_RUN=1` (no Arduino, state still updates — use for UI/logic work), `NORMAL_STEPS`, `CORNER_STEPS`,
    `SQUARE_MM` (30) and `SPEED_MM_S` (20) (the last two only used by `/roll`).
  - Board model: 40 squares, GO = 0, clockwise, corners {0, 10, 20, 30}. `plan_move` charges `CORNER_STEPS` for a corner
    and `NORMAL_STEPS` otherwise, charging the square entered going forward and the square left going back, so moves are
    reversible to the same motor position.
  - **Only axis 0 (one token) is tracked**, persisted in `board_state.json`. `/roll/{player}` moves axes 0–2 by mm but
    does not touch the board position; it predates the square model.
  - Endpoints: `GET /` (serves `ui.html`), `GET /board`, `POST /board/move?squares=±n`,
    `POST /board/goto?square=n&direction=auto|forward|back`, `POST /board/set?square=n` (re-home, no motor move),
    `POST /move?steps=&corners=` (raw), `POST /roll/{player}`, `POST /stop`, `GET /pos`.
    Non-blocking lock → 409 while a token is moving; motor failure → 503.
- **`ui.html`** — single-file control panel: 11×11 Monopoly grid (GO bottom-right), click a square to drive the magnet,
  "Set position" mode, back/forward 1, stop. Standard Monopoly names; not yet fairy-tale reskinned.

### Next
- Per-lane `current_square` (three tokens), then a `Player`/`Game` model and the rules engine (dice, buy, rent, jail,
  bankruptcy, simplified). Route all token moves through it; `/board/*` becomes a debug/admin surface.
- SQLite event log replacing `board_state.json`; `POST /vision/events` endpoint for the vision module (schema in §3).
- WebSocket state broadcast + phone companion view (cash, properties, history) + master view for judges.
- Fairy-tale reskin of squares, cards and names (shared with the vision card references in `cards/`).
- Muse integration: verify the API first, then narrator → voice intents → images.

---

## 3. Vision (dice + card reading)

### Decisions
- **One camera** overhead, pointing down at a central **action zone**, not the whole board:
  - a **dice tray** (felt-lined, dark background, white dice with black pips, ideally ≥19 mm) where players physically roll;
  - a **card zone** where drawn fairy-tale Chance / Community Chest cards are placed face up to be read.
- Output is events to the app, e.g. `POST /vision/events` (JSON lines):
  - `{"type":"dice_rolled","values":[3,5],"total":8,"doubles":false}`
  - `{"type":"dice_unclear","reason":"touching|count_mismatch|unreadable","seen":[...]}` → the game asks for a re-roll
  - `{"type":"card_drawn","card_id":"fairy_godmother","score":...}` / `{"type":"card_unknown",...}` / `{"type":"card_removed"}`

### What exists
Nothing yet. A printable ArUco sheet (`DICT_4X4_50` IDs 0–3) for mat corners exists outside the repo.

### Plan
1. **Calibration:** map the camera image to a top-down view of the mat in mm. Click 4 mat corners once and save to
   `zones.json`, or auto-detect the ArUco markers. Define dice and card zones in mm in `zones.json`.
2. **Roll detection state machine:** frame-difference motion in the tray → "rolling" → still ~0.6 s → read 3 consecutive
   frames and require agreement → emit. Ignore stillness after a hand passes through without a roll (the stable frame
   must differ from the last frame that was read).
3. **Dice reading:** Otsu threshold for white dice on dark felt → external contours → filter by expected die area (≈2× area
   means touching → `dice_unclear`) → warp each die to a square → count dark circular pips (circularity and area filters,
   ignore the border) → each value 1–6.
4. **Cards:** largest 4-corner bright contour in the card zone → perspective-warp to portrait 250×350 → ORB-match against
   `cards/<card_id>.png` (also rotated 180°). Require a minimum good-match count and a clear lead over second best.
   No confident match → optional Muse Spark fallback (send the crop, ask for JSON with the title).
5. **Debug overlay window** (zones, dice values, card ID, state); doubles as a demo-screen visual.
6. Lock camera autofocus, exposure and white balance where the platform allows; macOS AVFoundation often ignores these.
7. **Test without hardware:** synthetic dice and card images plus a recorded video (`--source file.mp4`).
   The app keeps a manual "enter roll" override (`/board/move?squares=n` works today).

---

## Team split
hardware / vision / game logic + AI / app. If a later tier slips, the demo must still work with: one lane moving from code,
a manual roll entry, and the master web view.
