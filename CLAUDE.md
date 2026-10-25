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

### What exists (`server/`, `static/`, `tests/`) — "Magic Mirror Monopoly"
Run: `.venv/bin/python -m server.main --sim [--tunnel] [--db games.db]`. Tests: `.venv/bin/pytest tests` (85).
- **Roster model:** one in-person player ("Grandma", p1, lane 1) who rolls real dice and draws real cards, plus up to two
  **remote** players on phones who press Roll in their app (the engine rolls for them). Camera reads give a total only, so
  there are no doubles; jail is pay the fine, use a key card, or forced out on the third turn.
- **`server/engine.py`** — pure engine: `apply_action(state, actor, action, rng) -> (new_state, events)`. Phases:
  LOBBY → WAITING_FOR_ROLL → MOVING (waits for `move_done` from the bridge) → RESOLVING → WAITING_FOR_DECISION (buy) /
  WAITING_FOR_PAYMENT (in-person player's card tap; remote payers settle automatically) / WAITING_FOR_CARD → END_OF_TURN.
  Actions: start, roll, dice, dice_not_visible, move_done, move_failed, retry_move, buy, decline, decision, card,
  player_card, confirm_payment, set_connected. `to_public` / `from_public` round-trip the state as JSON.
- **`server/rules/monopoly.py` + `board.json` + `cards.json`** — fairy-tale board (Castle Gate = GO, Rapunzel's Tower =
  jail, Cursed! = go to jail, Fairy Ring = free parking); decks `spell` and `fairy`, ids `spell_01..16`, `fairy_01..16`.
  **These ids are the contract with the vision module** (`cards/<id>.png` and the ArUco marker map).
- **`server/rooms.py`** — one `GameRoom` per game: the only caller of `apply_action`, under a lock; broadcasts full state +
  events + Mirror lines over WebSocket; routes adapter events back in via `on_adapter_event`. Host and seat tokens live here.
- **`server/store.py`** — SQLite persistence: one row per applied action with a state snapshot. On startup every unfinished
  game is restored (`GameRoom.restore`): same host/seat tokens so links survive a restart, remote seats reset to
  disconnected, an in-flight move is re-issued, and `bridge.restore({lane: square})` is called so hardware can drive tokens
  back (after re-zeroing at Castle Gate). `--db` defaults to `games.db` (gitignored); tests use `:memory:`.
- **`server/main.py`** — FastAPI: pages `/` (create + previous games), `/game/{id}/join/{seat}` (phone), `/board/{id}`
  (laptop, dev panel, Chronicles); `POST /api/games`, `GET /api/games` (host links only for local requests),
  `/api/games/{id}/links|history|stats`, `/api/lan` (public tunnel base if up, else LAN), `/ws/{id}?seat=|host=|role=board`.
  **`POST /vision/events`**: accepts `vision/watch.py` events from this laptop only (forwarded requests get 403), maps
  dice_rolled→dice(total), dice_unclear→dice_not_visible, card_drawn→card(id), and sends them to the most recently
  active running game (or `?game=<id>`). The engine rejects reads it is not waiting for.
- **`server/tunnel.py`** — `--tunnel` spawns `cloudflared tunnel --url` (installed via Homebrew), parses the
  `https://*.trycloudflare.com` URL, and links/QR codes use it; falls back to the LAN address. `--public <url>` overrides.
- **`server/adapters/`** — `bridge` (StubBridge, or **ArduinoBridge** with `--bridge arduino`: lane 1 moves through
  `arduino/server.py`'s `board_goto`, loaded in-process by `server/hardware.py`; lanes 2–3 stay stubbed until that
  server tracks more than one token; `DRY_RUN=1` runs it without an Arduino),
  `vision` (StubVision with dev-panel input; `ArucoVision` stub, unused: the camera talks to `/vision/events` instead),
  `voice` (StubVoice from the dev panel, or **MuseVoice** with `--voice muse`: the board page's "Ears on" button records
  the laptop mic while the in-person player must answer yes/no, posts a 16 kHz WAV to `POST /voice/clip`, the server
  sends it to Muse Voice Transcribe (`POST https://api.meta.ai/v1/asr/transcribe`, multipart `request` + `audio`,
  `Authorization: Bearer $MUSE_API_KEY`), parses yes/no and dispatches the decision; covers buy offers and the jail fine),
  `mirror` (TemplateMirror narrator lines spoken by the board page via `speechSynthesis`; MuseMirror stub).
  `make_adapters(sim=False)` builds real vision + Mirror and fails until they exist; bridge and voice are chosen separately.
  **The Muse call is built from the docs (dev.meta.ai/docs/speech-to-text) and tested against a mock; run
  `scripts/muse_check.py` with the real key before the demo.**
- **`static/`** — `create.html` (roster, join links with QR codes from vendored `qrcode.min.js`, previous games with
  Resume / Chronicles), `kid.html` (phone), `board.html` + `board.js` (animated board, players, Mirror log, Chronicles
  stats, dev panel), `net.js` (reconnecting WebSocket helper). `server/cli.py` plays a full game in the terminal.
- **`--bridge arduino`** also mounts `arduino/server.py`'s motor endpoints (`/board/*`, `/move`, `/stop`, `/pos`) on
  the game server, so re-homing with `/board/set` is shared with the game. `README.md` has the full runbook.

### Next
1. **Belt bridge for all lanes**: `arduino/server.py` tracks one token (axis 0), so only lane 1 is real. Add per-lane
   square tracking there, and implement `ArduinoBridge.restore()` = home to Castle Gate then drive.
   `make_adapters(sim=False)` still builds all of vision/voice/Mirror real adapters at once; keep `--sim` + `--bridge arduino`
   as the demo configuration until those exist.
2. Decide one-vs-three physical players with the team; the engine currently allows exactly one in-person player.
3. Print cards with ArUco markers (ids 10–25 spells, 30–45 fairies, 50–52 player cards; see §3) and add `marker` to
   `cards.json`; put reference photos in `cards/<id>.png`.
4. Muse: `MUSE_API_KEY=... .venv/bin/python scripts/muse_check.py` (chat + transcribe) as soon as the key exists; then
   MuseMirror (narration via chat completions, `muse-spark-1.3`) → images. Voice is done pending that check.
5. Demo-day runbook: `--tunnel --db games.db`, open `/` on the laptop, scan QR codes, keep the dev panel for manual
   dice/card entry and `confirm_payment` as fallbacks.

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

### What exists (`vision/`, run with `.venv/bin/python`; see `vision/README.md`)
- **`probe.py [index]`** — opens the camera, prints which settings macOS honors, live view, `s` saves frames to `vision/samples/`.
- **`make_aruco_sheet.py`** — writes `vision/aruco_sheet.png` (DICT_4X4_50 ids 0–3). Print it; tape 0 TL, 1 TR, 2 BR, 3 BL.
- **`calibrate.py [--aruco] [--image f]`** — click 4 corners or auto-detect markers → `zones.json` (gitignored) + warped preview.
- **`zones.py`** — `Zones`: homography camera→mat, `warp(frame)`, `crop(mat, "dice_zone_mm"|"card_zone_mm")`. Mat and zones
  in mm; default mat 300×200, dice zone left, card zone right, `px_per_mm` 3, `die_mm` 19, `card_mm` 63×88.
- **`dice.py`** — `read_dice(tray_bgr, px_per_mm, die_mm)` → values, or reason `empty|touching|count_mismatch|unreadable`.
  Otsu → blobs filtered by expected die area and aspect → warp each die → count dark round pips. Guarded against an empty
  tray (Otsu split must be bright enough). 100% on synthetic trays.
- **`cards.py`** — `CardMatcher(cards_dir).identify(zone_bgr)` → `card_id` or reason `no_card|no_match|ambiguous`.
  Largest bright quad → warp to 250×350 portrait → ORB + ratio test vs `cards/<id>.png` (and rotated 180°).
  Needs ≥18 good matches and 1.4× lead over second best. 100% on synthetic cards at 0/±8/90/180°.
- **`watch.py`** — main loop. `--source clip.mp4`, `--no-post`, `--no-display`, `--server`. Dice state machine
  EMPTY→ROLLING→SETTLING→READ: motion = mean frame diff in tray > 6; still 0.6 s; 3 agreeing reads; a reading equal to the
  last emitted one (same values, dice within 0.4 die-widths) is ignored, so a hand passing over does not re-emit.
  Card: same id 4 frames → `card_drawn`; no card 6 frames → `card_removed`. Prints JSON lines and POSTs each event.
- **`synth.py`** — synthetic trays, cards and a synthetic mat video (`roll_video`) for tests and dry runs.
- **`tests/`** — `pytest vision/tests`: every dice pair × 3 seeds, touching/empty/count cases, cards at angles,
  and an end-to-end synthetic video through both state machines.
- Server side: `POST /vision/events` (logs + keeps last 200), `GET /vision/events?n=20` in `arduino/server.py`.
  The rules engine will consume these; today they are only stored.

- **`live.py [index]`** — live test window: drag a box around the dice area, "die px" slider, per-frame readout and the
  last confirmed roll. Settings in `vision/live.json`. Dice were tuned on real C920 frames (light wood table, angled):
  the die threshold sits between the background median and the brightest pixels, not Otsu.
- `watch.py` posts to the **game server's** `POST /vision/events` (default `http://127.0.0.1:8000`); the server maps the
  vocabulary (see §2). The C920 was index 0 last time; `probe.py --list` finds it again.

### Next
1. **ArUco-first card reading**: print markers on the new cards (one `DICT_4X4_100` family: 0–3 mat corners, 10–25
   spells, 30–45 fairies, 50–52 player cards; ~18 mm, white quiet zone, twice per card, matte stock). `cards.py` detects
   markers first and falls back to ORB. Card ids come from `server/cards.json`.
2. Mount the camera over the mat, run `calibrate.py`, set `zones.json`; keep the tray well inside the frame.
3. Tune `MOTION_THRESH` on real rolls with `live.py`; lock C920 focus/exposure (Logi Tune if `cap.set` is ignored).
4. Record a clean `.mp4` of rolls and card draws as a regression fixture and demo backup.
5. Optional: Muse Spark fallback on `dice_unclear` / `card_unknown`.

### Original plan (kept for reference)
---

## Team split
hardware / vision / game logic + AI / app. If a later tier slips, the demo must still work with: one lane moving from code,
a manual roll entry, and the master web view.
