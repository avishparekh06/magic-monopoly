# Magic Mirror Monopoly

A fairy-tale Monopoly board for HackTX 26. Grandma plays at her own table with real dice and cards, the grandkids
join from their phones, and the tokens slide by themselves, pulled by magnets on motorized belts under the board.
One laptop next to the board runs everything: the game server, the motors, the overhead camera, and the
"Magic Mirror" that narrates out loud.

## What's in this repo

| Folder | What it is | Owner |
|---|---|---|
| `server/` | Game server: the rules engine, WebSocket server, adapters for motors/camera/voice/narrator, a text-mode demo | App |
| `static/` | The web pages: create a game, the kids' phone app, and the board laptop page (with a dev panel) | App |
| `tests/` | Tests for the game server (no hardware needed) | App |
| `arduino/` | Motor firmware (`.ino`), `control.py` (talks to the Arduino over USB), `server.py` (motor endpoints, used by the game server) | Hardware |
| `vision/` | Overhead-camera dice and card reader. See [`vision/README.md`](vision/README.md) | Vision |

`arduino/ui.html` is an old bench-test page kept for reference; the board page's dev panel replaces it.

---

## 1. Setup (once)

You need **Python 3.10 or newer** (tested on 3.14) and macOS or Linux.

```bash
git clone https://github.com/avishparekh06/magic-monopoly.git
cd magic-monopoly
python3 -m venv .venv
source .venv/bin/activate          # do this in every new terminal
pip install -r requirements.txt
```

Check everything works:

```bash
pytest -q                          # all tests should pass; no Arduino or camera needed
```

---

## 2. Run the game without any hardware (simulation)

This is the quickest way to try it and the fallback for the demo.

```bash
source .venv/bin/activate
python -m server.main --sim
```

You should see `Uvicorn running on http://0.0.0.0:8000`. Leave that terminal open.

Then:

1. **Create a game.** On a laptop or phone, open **http://localhost:8000**. Keep "Grandma" as the in-person player,
   add one or two grandchildren, and tap **Create the game ✨**.
2. **Open the board page.** Copy the **board laptop** link and open it in a new window. This is the screen that sits
   next to the physical board. It shows the 2D board, the players, the Mirror's log, and the dev panel.
3. **Join as the kids.** Copy each grandchild's link and open it in its own window, or send it to a phone on the
   same Wi-Fi (see section 5). Each link drops that kid straight into their seat; no sign-up. Reopening a link
   rejoins; opening it on a second device takes the seat over.
4. **Start.** When everyone shows ✓, press **Start the game** (on the create page or the board page).
5. **Hear the Mirror.** On the board page click **🔊 Wake the Mirror** once. Browsers only allow speech after a
   click; after that every Mirror line is spoken.

### Playing Grandma's turn with the dev panel

Grandma never uses a screen. In simulation the **🛠️ Dev panel** on the board page (bottom of the right column)
stands in for the camera, microphone and motors:

| Real world | Dev panel |
|---|---|
| Grandma rolls the dice into the tray | **Camera — dice tray:** pick a total, **Read dice** (or **Dice not visible** to make the Mirror ask for a reroll) |
| Grandma says "yes" / "no" | **Voice:** **Yes** / **No**, or type what she said and press **Hear** |
| Grandma lays her player card on the reader to pay | **Camera — reader spot:** **Grandma's card on reader** |
| Grandma lays a drawn Crystal Ball / Chest of Spells card on the card spot | **Camera — card spot:** pick the card, **Read card** |
| The card tap isn't read | **Payment fallback:** **Confirm payment** |
| A token gets stuck | **Motors:** **Retry stuck move** |

Kids play from their app: **🎲 Roll**, **Buy** / **No thanks**, and the tower choice. If the camera can't read a
card drawn for them, their app shows a "which card was it?" list.

Useful starting rolls for Grandma from Castle Gate: **6** = Royal Ballroom (buy it), **7** = Crystal Ball (card),
**4** = King's Tithe (tax), **10** = Rapunzel's Tower (just visiting).

### Text-only demo (no browser)

```bash
python -m server.cli                    # Grandma, Maya, Leo
python -m server.cli Grandma Maya --seed 3
```

Type Grandma's dice total (or press Enter for a random roll), answer `y`/`n`, and press Enter to "tap" her card.

---

## 3. Run with the real motors

Only **lane 1 (Grandma's token)** moves through the real belts today, because `arduino/server.py` tracks one
token (axis 0). The grandkids' tokens (lanes 2 and 3) are simulated and shown on the board page until it supports
more lanes.

### 3a. Flash the firmware (once per Arduino)

1. Open `arduino/Nema_17_Stepper_Motor_With_Arduino.ino` in the Arduino IDE.
2. Board: **Arduino Uno**. Select its USB port. Upload.
3. Optional check: open the Serial Monitor at **115200 baud**; it prints `ready`. Type `P` and press Enter; it
   answers `pos 0 0 0`. **Close the Serial Monitor afterwards**: only one program can use the USB port.

Wiring, power and driver setup (Vref, polarity, never hot-plug a motor) are in `arduino/hacktx26-build-plan.md`
and the Hardware section of `CLAUDE.md`. The firmware turns the motors on at boot.

### 3b. Bench-test the belts (optional)

```bash
python arduino/control.py
```

Commands: `0 200` (axis 0, 200 steps), `0 -200` (back), `mm 0 40` (40 mm), `pos`, `zero`, `stop`, `on`, `off`,
`help`, `quit`. `control.py` assumes **full-step** (no microstep jumpers): 200 steps/rev, 5 steps/mm.

### 3c. Start the game server with the motors

Place Grandma's token on **Once Upon a Time** (square 0), then:

```bash
source .venv/bin/activate
python -m server.main --sim --bridge arduino
```

- `--bridge arduino` sends Grandma's game moves through `arduino/server.py` (its `plan_move` and the Arduino).
  The default, `--bridge stub`, fakes every move.
- `--sim` still simulates the camera, voice and narrator with the dev panel.
- The server also serves the motor endpoints from `arduino/server.py` (section 6).

Without an Arduino plugged in, add `DRY_RUN=1` to run the motor code without moving anything:

```bash
DRY_RUN=1 python -m server.main --sim --bridge arduino
```

### 3d. Tuning the belt distances

`arduino/server.py` turns squares into motor steps. Each motor (axis 0, 1, 2) has its own three values: a normal
square, the square before a corner → the corner (`corner in`), and a corner → the square after it (`corner out`).
The defaults are in `AXIS_STEPS` in `arduino/server.py`, and the board endpoints drive `MOTOR_AXIS` (default 1)
unless a request passes `axis`.

**Tune from the bench tool** (`cd arduino && python -m uvicorn server:app --port 8001`, then open
http://localhost:8001): pick the motor, make a move, press **Tune last move**, then click the square where the
magnet really stopped. It suggests new values; **Apply and set position** saves them in `arduino/tuning.json`
(which wins over the defaults) and records where the magnet is. Test a plain stretch first (it tunes the normal
value), then a move through a corner (it tunes the two corner values). Longer moves give finer results.

Environment variables for the default motor, set before starting the server (a saved `tuning.json` overrides them):

| Variable | Default | Meaning |
|---|---|---|
| `MOTOR_AXIS` | 1 | the motor the board endpoints and the game drive |
| `NORMAL_STEPS` | per motor | motor steps for an ordinary square |
| `CORNER_IN` | per motor | the square before a corner → the corner |
| `CORNER_OUT` | per motor | a corner → the square after it |
| `DRY_RUN` | off | `1` = plan moves but don't talk to the Arduino |

Example: `NORMAL_STEPS=230 CORNER_IN=310 python -m server.main --sim --bridge arduino`

### 3e. If a belt slips (re-home)

The server remembers where the token is from the moves it sent (saved in `arduino/board_state.json`). If the token
ends up somewhere else, tell the server where it really is, without moving the motor:

```bash
curl -X POST "http://localhost:8000/board/set?square=0"    # the token is really on square 0
```

To drive it somewhere by hand: `curl -X POST "http://localhost:8000/board/goto?square=10&direction=forward"`.
Stop everything: `curl -X POST http://localhost:8000/stop`.

---

## 4. The camera (vision)

The camera reader lives in `vision/` and has its own setup and calibration steps: see
[`vision/README.md`](vision/README.md). Its `watch.py` sends events to `http://127.0.0.1:8000/vision/events`, which
the game server answers when started with `--bridge arduino`.

**Not wired yet:** camera events are only stored (see `GET /vision/events`); they don't drive the game yet. Until
then, use the dev panel's camera buttons.

---

## 5. Playing on phones

The kids' phones must reach the laptop.

- **Same Wi-Fi (easiest):** start the server, then create the game at `http://localhost:8000` on the laptop. The
  join links automatically use the laptop's Wi-Fi address, e.g. `http://192.168.1.20:8000/game/...`.
- **Venue or campus Wi-Fi often blocks devices from talking to each other.** If a phone can't open a link, turn on a
  phone hotspot and connect the laptop and the phones to it.
- **macOS firewall:** if macOS asks whether Python may accept incoming connections, click **Allow**.
- **From anywhere (optional):** `brew install cloudflared`, then `cloudflared tunnel --url http://localhost:8000`.
  Open the printed `https://….trycloudflare.com` address and **create the game from that address**, so the join
  links use it too.

---

## 6. Reference

### Server options

```
python -m server.main [--sim] [--bridge stub|arduino] [--port 8000] [--host 0.0.0.0] [--seed N]
```

| Option | Meaning |
|---|---|
| `--sim` | Simulate camera, voice and narrator (use the dev panel). Without it, the real ones are required (not built yet). |
| `--bridge stub` | Fake every token move (default). |
| `--bridge arduino` | Grandma's token through `arduino/server.py` and the Arduino; other tokens simulated. |
| `--port` | Port to serve on (default 8000). |
| `--host` | `0.0.0.0` (default) lets phones on the network connect. |
| `--seed` | Make the kids' dice rolls repeatable. |

### Pages

| URL | Who | What |
|---|---|---|
| `/` | Parent / grandkid | Create a game, share links, lobby, Start |
| `/game/<game_id>/join/<seat_token>` | Each grandkid | Their phone app |
| `/board/<game_id>` | The laptop by the board | 2D board, Mirror voice, dev panel |

### Motor endpoints (from `arduino/server.py`, with `--bridge arduino`)

| Endpoint | Does |
|---|---|
| `GET /board` | Where Grandma's token is |
| `POST /board/goto?square=N&direction=auto\|forward\|back` | Drive the token to square N |
| `POST /board/move?squares=±N` | Move N squares (negative = back) |
| `POST /board/set?square=N` | Re-home: record where the token really is (no motor move) |
| `POST /move?steps=N&corners=N` | Raw motor steps (doesn't change the tracked square) |
| `POST /stop` | Stop all motors |
| `GET /pos` | Motor step counters |
| `POST /vision/events`, `GET /vision/events?n=20` | Camera events in / recent camera events |

### Tests

```bash
pytest -q                 # everything
pytest -q tests           # game server only
pytest -q vision/tests    # camera reader only
```

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `No Arduino port found. Check the USB cable.` | Plug in the Arduino, or add `DRY_RUN=1` to run without it. |
| Motor commands fail or time out | Close the Arduino IDE Serial Monitor (only one program can use the port), check the 12 V supply, restart the server. |
| A token runs the wrong way | Swap `LOW`/`HIGH` in `startMove` in the `.ino` and re-upload (see `CLAUDE.md`). |
| Every distance is ~16× off | Microstep jumpers are installed; change `MICROSTEP` in `arduino/control.py` (see `CLAUDE.md`). |
| "This game doesn't exist" / "This game has ended" | Games live in memory; restarting the server ends them. Create a new game. |
| A phone can't open the join link | Same Wi-Fi? Try a phone hotspot (section 5). Allow Python through the macOS firewall. |
| No sound from the Mirror | Click **🔊 Wake the Mirror** on the board page, and check the laptop's volume. |
| Buttons show "not your turn" / "nobody needs to pay right now" | That's the game refusing an action it isn't waiting for; read the board page's status line for what it wants. |
| `Address already in use` | Another server is on port 8000. Stop it, or use `--port 8001`. |
