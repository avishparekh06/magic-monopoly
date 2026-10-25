# Magic Mirror Monopoly

A fairy-tale Monopoly board for HackTX 26. In-person players sit at the table with real dice and cards, remote players
join from their phones, and the tokens slide by themselves, pulled by magnets on motorized belts under the board.
One laptop next to the board runs everything: the game server, the motors, the overhead camera, and the
"Magic Mirror" that narrates out loud.

## What's in this repo

| Folder | What it is | Owner |
|---|---|---|
| `server/` | Game server: the rules engine, WebSocket server, adapters for motors/camera/voice/narrator, a text-mode demo | App |
| `static/` | The web pages: create a game, the remote players' phone page, and the board laptop page (with a dev panel) | App |
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

1. **Create a game.** On a laptop or phone, open **http://localhost:8000**. Enter your name as the in-person host,
   add one or two more players (in person or virtual), and tap **Create the game ✨**.
2. **Open the board page.** Copy the **board laptop** link and open it in a new window. This is the screen that sits
   next to the physical board. It shows the 2D board, the players, the Mirror's log, and the dev panel.
3. **Join as the remote players.** Copy each remote player's link and open it in its own window, or send it to a phone on the
   same Wi-Fi (see section 5). Each link drops that player straight into their seat; no sign-up. Reopening a link
   rejoins; opening it on a second device takes the seat over.
4. **Start.** When everyone shows ✓, press **Start the game** (on the create page or the board page).
5. **Hear the Mirror.** On the board page click **🔊 Wake the Mirror** once. Browsers only allow speech after a
   click; after that every Mirror line is spoken.

### Accounts, saved games and history

The home **Start Game** button opens **New game** and **Continue playing**.
Continue restores the selected session's roster, money, properties and turn; it
does not create a new session. Finished games remain available in **Game history
& stats**. Waiting lobbies can be continued to edit guests before starting. The
lobby's Start button still opens the selected board directly.

Home has two main actions: **Start Game** and **Game history & stats**. Inside
history, use **Games** for saved sessions and **Overall stats** for your personal
record. Each game row opens its results and one Continue action. The list filter
and collection stay selected when you go back. Extra numbers, account controls
and the dated activity log expand on demand.

Use **History & stats → Overall stats → Sign in / Create an account** for a persistent player identity. New host seats
link to the signed-in account automatically; each other guest can use **Link account
for stats**. In-person players can enter their own account details without signing
the host out. Virtual players can link from the home waiting screen or their game
stats. An account can occupy only one seat per game. Old anonymous sessions remain
available and can be explicitly linked; names alone never merge identities.

**Overall stats** shows games played and completed, wins, win rate, average finish,
property purchases, most commonly purchased properties (including ties), rent and
dice statistics. Purchases count recorded buy events, not landings or inherited
ownership. Average finish uses known winner/elimination results from completed
games only. Games with unknown ranks stay out of that average, and games without
a recorded winner stay out of win rate. Ongoing play contributes real purchases
and money activity but never an invented final placing. Hosted-only sessions are
resumable without counting as personal games.

Each session has expandable player summaries and a dated **Game activity** log. Open it from home, from
**Players & stats → Game history & stats** in fullscreen, from Chronicles, or from the phone
game's history button. Account sign-in and linked statistics survive server restarts.
All data stays on this game server's SQLite database; this is not a cloud-sync service.
Passwords use salted scrypt hashes and device sessions use hashed opaque tokens
in the database with HttpOnly cookies. The account tables are added automatically
to existing databases without replacing their saved games. There is no email or
password-recovery service configured.

The UI reference is the official
[MONOPOLY PLUS Xbox guide](https://dlassets-ssl.xboxlive.com/public/content/47c7cac4-8b21-4dbb-b7e4-e5a4fb758e23/GameManual/9bb779bf-af6b-45e4-9a54-12c636ede994/en-CA/index.html),
which documents leaderboards. Property-frequency and average-finish dashboards
are specific to this project and use its actual saved event history.

### Changing attendance

Before starting, use the **In person / Virtual** dropdown beside each
guest in the host lobby. Virtual seats receive join links and must connect before
the normal Start button becomes ready; in-person guests use the shared board and
need no phone. Switching a connected guest to in person closes their phone controls
with an explanation. Switching back to virtual reuses their join link. Choices and
links survive server restarts without changing player names, lanes, or balances.
The first seat is the board host and stays in person; attendance is locked after
the game starts. From the homepage, use **Start Game → Continue lobby** for a saved waiting game
to return to its guest list.

### Home-screen waiting

The home landscape includes a princess, a knight, and a fairy, using original SVG
art that matches the guest crests. It has no separate courtyard card or minigame
frame. On desktop the characters stand in the grass beside the setup form; on
phones they appear below the title. A local preview is available before creating
a game. Move the cursor or drag to walk; click, tap **Jump**, or press Space to hop.
Arrow keys and WASD also move a focused character.

After creating a lobby, the host controls the princess and virtual guests control
the knight or fairy for their seat. Connected guests replace the local preview
and movement is shared. Join links take waiting guests to this home view; when the
host starts, the host page opens the board directly and virtual guests automatically enter their phone game controls. The
roaming scene is absent from both the board and phone game pages. Attendance
editing and join links remain in the host's guest list. Temporary roaming positions
never change balances, board positions, dice or saved game history.

### Board view

**Full board** shows the complete 2D storybook map. **Follow token** opens the
3D world and follows the active piece. The same view switch works in fullscreen;
there is no separate 2D/3D toggle. The last selected view is remembered per game.
The full board stays available when WebGL is unavailable.

The 2D map uses the miniature world's cream tiles, timber-and-gold frame, green
castle grounds, pond and plum roofs. The lobby shows all 40 spaces. In Follow token,
the camera follows the existing walking animation and then the next active player.
Drag or use the left/right arrow keys to look around; click Follow token again to recenter.
Full board returns to the complete flat map without changing the game or token movement.
Three.js 0.180.0 is served locally from `static/vendor/three/` with its MIT license; the 3D
view makes no CDN requests and does not change the game engine.

Board place names follow the 0–39 layout in `design/Game design.pdf`, cross-checked against
`design/All Cards.pdf`. Prices, rents, positions, card effects, and physical markers are unchanged.

**Players & stats** and the player strip open an illustrated collection of owned
cards. All 28 properties, workshops and utilities have original SVG artwork based
on the places in the design PDFs, styled to match the miniature board. Cards are
ordered by color/tale, stay at a compact card size even for a single property,
show collection progress and ownership-based rent, and reveal details on selection.
**Show money** displays the selected player’s current gold as illustrated note
stacks using the design’s 500/100/50/20/10/5/1 denominations. The notes are a visual
breakdown of the balance, not a stored bill inventory or a new payment control.
The display updates when balances change and works inside fullscreen. The phone's **My cards** uses the same collection. Held escape
cards show their saved count; individual escape-card identities are not stored.
Browsing never changes game state or adds building/mortgage rules. Regenerate
illustrations with `python3 scripts/property_art.py`; prices and rent stay in live
board data. With the server running, `node tests/browser/property-cards.mjs` checks
artwork coverage, rent calculations, collection browsing, mobile and fullscreen.

Landed properties open an on-board deed with **Buy** and **No thanks** choices. Tower-release
choices and payment instructions also appear inside the board. In-person purchases still
wait for the player-card reader; **Card reader not available?** exposes the existing manual
confirmation fallback. A drawn card's title and text appear when its physical card is read.
In simulation, the on-board card picker lists only the required deck; it does not draw or
apply a random card automatically. A physical-dice total can also be entered in simulation.

Use **Fullscreen** inside the game view to expand only the board and these controls.
**Exit fullscreen** (or Escape) restores the page, and **Full board / Follow token** remains
available while fullscreen. Fullscreen availability depends on browser support.

The board also keeps a compact player balance strip inside fullscreen. Select a player
or use **Players** to browse owned places and their normal rent (including completed
color sets, workshops, and utility multipliers). This view is read-only. A new turn or
required action closes it so the game prompt is visible again. Escape closes the
property view before leaving fullscreen.

Use **M** for the board map/follow toggle and **R** to reset the camera; shortcuts do
not run while typing, using form controls, or browsing properties. **Reset view** is
also available on wide screens. The UI reference is the official
[MONOPOLY PLUS Xbox game help](https://dlassets-ssl.xboxlive.com/public/content/47c7cac4-8b21-4dbb-b7e4-e5a4fb758e23/GameManual/9bb779bf-af6b-45e4-9a54-12c636ede994/en-CA/index.html),
which provides separate board-map and property-browsing controls. Game rules remain
those implemented by this project.

### On-screen dice

The shared board has a large **Roll Dice** button for the current player, including
in fullscreen. The server rolls two dice and validates the player and turn before
moving the token; clients cannot supply the result. Repeated clicks and stale-turn
requests are rejected. The existing phone Roll button and physical dice input remain
available.

Digital rolls toss two rounded, pip-marked 3D dice onto a nearby clear board space.
A brief camera shot shows the toss and exact resulting faces, then returns to the
selected camera mode. Prompts wait for the toss to settle. The animation is visual
and does not delay physical motors or change the server's game state. Reduced-motion
mode settles the dice immediately. Without WebGL, simple animated dice show the same
server result. Physical camera reads currently provide only a total; the UI reports
that total without inventing individual dice values.

### Playing an in-person turn with the dev panel

For physical play, in-person players use real dice and voice. In simulation the **🛠️ Dev panel** on the board page (bottom of the right column)
stands in for the camera, microphone and motors:

| Real world | Dev panel |
|---|---|
| The in-person player rolls the dice into the tray | **Camera — dice tray:** pick a total, **Read dice** (or **Dice not visible** to make the Mirror ask for a reroll) |
| The in-person player says "yes" / "no" | **Voice:** **Yes** / **No**, or type what they said and press **Hear** |
| The in-person player lays their player card on the reader to pay | **Camera — reader spot:** **card on reader** |
| Someone at the table lays a drawn Crystal Ball / Chest of Spells card on the card spot | **Camera — card spot:** pick the card, **Read card** |
| The card tap isn't read | **Payment fallback:** **Confirm payment** |
| A token gets stuck | **Motors:** **Retry stuck move** |

Remote players play from their phone: **🎲 Roll**, **Buy** / **No thanks**, and the tower choice. If the camera can't read a
card drawn for them, their phone shows a "which card was it?" list.

Useful starting rolls for the host from Once Upon a Time: **6** = Stepmother's Manor (buy it), **7** = Crystal Ball (card),
**4** = Rumpelstiltskin's Bargain (tax), **10** = Rapunzel's Tower (just visiting).

### Text-only demo (no browser)

```bash
python -m server.cli                    # Ava (in person), Maya, Leo
python -m server.cli Ava Maya --seed 3
```

Type the in-person player's dice total (or press Enter for a random roll), answer `y`/`n`, and press Enter to "tap" their card.

---

## 3. Run with the real motors

Only **lane 1 (the host's token)** moves through the real belts today, because `arduino/server.py` tracks one
token (axis 0). The other tokens (lanes 2 and 3) are simulated and shown on the board page until it supports
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

Place the host's token on **Once Upon a Time** (square 0), then:

```bash
source .venv/bin/activate
python -m server.main --sim --bridge arduino
```

- `--bridge arduino` sends the host's game moves through `arduino/server.py` (its `plan_move` and the Arduino).
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

**Guided run** (Tune section): pick a scope (All corners, GO, Jail, Free Parking or Go To Jail) and rounds (default 1),
put the magnet dead centre on the first stop's square, press Set position and click it, then press Guided run. It
runs one leg and waits. Click exactly where the magnet really is (or **It's dead centre**). That stop is tuned
immediately, solving only the value the leg is about (normal, or one corner's in/out), and the position becomes the
spot you clicked (it can sit between squares, shown like `21 (+0.60)`). The next leg is planned from there, and
nothing is jogged. **Undo that** reverts the value and lets you click again, **Next stop** (or Enter) goes on,
**Finish** ends the run. It never advances by itself. A single corner repeats into-it, out-of-it, then a resync
leg back, so you can run Free Parking again and again until it lands right.

**Free Parking is special.** Measurements around Free Parking and Kentucky Avenue (the hops 19 → 20 and 20 → 21,
`FP_ZONE_HOPS` in `arduino/server.py`) never change the shared values: a leg touching them only tunes Free Parking's
own in/out values (primary `in:20` / `out:20`), and any other leg that touches them is skipped (the position is still
recorded). Free Parking can also have its own step period (the **Free Parking speed** boxes, smaller = faster),
saved in `tuning.json` as `corner_periods`; other corners keep the shared corner speed.

Endpoints: `POST /tune/leg` (one stop: axis, primary, start, squares, sent, actual), `POST /tune/value` (set one
value, used by Undo), `POST /board/at?position=` (set a fractional position), `POST /tune/speed?corner=20&in_us=&out_us=`.

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

Remote players' phones must reach the laptop.

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
| `--bridge arduino` | The host's token through `arduino/server.py` and the Arduino; other tokens simulated. |
| `--port` | Port to serve on (default 8000). |
| `--host` | `0.0.0.0` (default) lets phones on the network connect. |
| `--seed` | Make remote players' dice rolls repeatable. |

### Pages

| URL | Who | What |
|---|---|---|
| `/` | The host | Create a game, share links, lobby, Start |
| `/game/<game_id>/join/<seat_token>` | Each remote player | Their phone page |
| `/board/<game_id>` | The laptop by the board | 2D board, Mirror voice, dev panel |

### Motor endpoints (from `arduino/server.py`, with `--bridge arduino`)

| Endpoint | Does |
|---|---|
| `GET /board` | Where the host's token is |
| `POST /board/goto?square=N&direction=auto\|forward\|back` | Drive the token to square N |
| `POST /board/move?squares=±N` | Move N squares (negative = back) |
| `POST /board/move-many` | Move several belts at the same time (see below) |
| `POST /board/set?square=N` | Re-home: record where the token really is (no motor move) |
| `POST /move?steps=N&corners=N` | Raw motor steps (doesn't change the tracked square) |
| `POST /stop` | Stop all motors |
| `GET /pos` | Motor step counters |
| `POST /vision/events`, `GET /vision/events?n=20` | Camera events in / recent camera events |

**Moving several belts at once** (bench tool): each axis uses its own tuned values.

```
curl -X POST localhost:8001/board/move-many -H 'content-type: application/json' \
  -d '{"moves": [{"axis": 0, "squares": 3}, {"axis": 2, "squares": -5}]}'
```

The belts start together. A move is a few segments (corner legs and plain squares run at different speeds); the first
segment of every belt starts at once, the next starts when all have finished the last, and a belt with fewer segments
sits out the later rounds. Answers 409 if a token is moving, 422 for a repeated or unknown axis, and 503 if a motor
fails or `/stop` is pressed part way (the answer names the axes that need Set position; belts that finished keep
their new square). Under `DRY_RUN=1` it skips the motors and still updates the squares.

### Tests

```bash
pytest -q                 # everything
pytest -q tests           # game server only
pytest -q vision/tests    # camera reader only
```

For history/stats navigation, account flows, continuation, mobile sizing and
fullscreen, run `.venv/bin/python tests/browser/check_navigation.py`. This uses
Chrome and Node with a disposable database/server on port 8001 (choose another
with `--port`). It leaves existing games untouched. Set `CHROME_BIN` if Chrome
is installed outside the default macOS location. With the local server running,
`node tests/browser/dice-presentation.mjs` checks dice/movement sequencing, and
`node tests/browser/token-camera.mjs` checks stable camera height, smooth corner
following, no dice cutaways or idle recentering, a fixed overview, and visible
dice from all 40 spaces in landscape and portrait. Both use isolated display fixtures.

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `No Arduino port found. Check the USB cable.` | Plug in the Arduino, or add `DRY_RUN=1` to run without it. |
| Motor commands fail or time out | Close the Arduino IDE Serial Monitor (only one program can use the port), check the 12 V supply, restart the server. |
| A token runs the wrong way | Swap `LOW`/`HIGH` in `startMove` in the `.ino` and re-upload (see `CLAUDE.md`). |
| Every distance is ~16× off | Microstep jumpers are installed; change `MICROSTEP` in `arduino/control.py` (see `CLAUDE.md`). |
| "This game doesn't exist" / "This game has ended" | Open **Start Game → Continue playing**. Check that the server uses the same saved database (`--db`); a missing or deleted database cannot restore an old game. |
| A phone can't open the join link | Same Wi-Fi? Try a phone hotspot (section 5). Allow Python through the macOS firewall. |
| No sound from the Mirror | Click **🔊 Wake the Mirror** on the board page, and check the laptop's volume. |
| Buttons show "not your turn" / "nobody needs to pay right now" | That's the game refusing an action it isn't waiting for; read the board page's status line for what it wants. |
| `Address already in use` | Another server is on port 8000. Stop it, or use `--port 8001`. |

The game embeds **Planewalker** by Neale Davidson / Pixel Sagas for its interface
and canvas board labels. Regular, bold, italic, and bold-italic faces are served
locally from `static/fonts/planewalker/`; the original font license is included there.

## Flags added Saturday night

| Flag | What it does |
| --- | --- |
| `--bridge step --belt-url https://... --belt-key ...` | Drive track 1 through the remote belt gateway (`POST /step?squares=n&key=`, `GET /position`). URL and key can live in `.env` as `BELT_URL` / `BELT_KEY`. |
| `--bridge http --belt-url http://host:8001 [--belt-lanes N]` | Drive lanes through a running `arduino/server.py` over HTTP (`/board/goto?axis=`). |
| `--belt-lanes N` | How many lanes the belt really drives; higher lanes are simulated. |
| `--debug-sync` | Testing: the game adopts the belt's reported square between turns (polls `/position`, accepts `POST /belt/position`). Also a checkbox in the board dev panel. |
| `--voice muse` | Yes/no answers through Meta Muse Voice Transcribe from the board page's microphone (`MUSE_API_KEY` in `.env`). |
| `--mirror muse` | Narration written by Muse Spark (one short sentence per action) with the template lines as fallback. |
| `--speech browser` / `--speech elevenlabs` | Who reads the Mirror's lines aloud. `browser` (default) is the laptop's/phone's own voice and costs nothing. `elevenlabs` uses ElevenLabs ("George", `eleven_v4`) and spends credits; needs `ELEVENLABS_API_KEY` in `.env` (run `npx -y @elevenlabs/cli@latest onboard init` once). Falls back to the browser voice if a clip fails. |
| `--dev` | Show the board page's dev panel (manual dice and cards, yes/no, confirm payment, motors, token positions). Without it the board is clean for the demo; the stuck-move popup still appears. |
| `--tunnel` / `--public URL` | Public address for phones: start a cloudflared quick tunnel, or use a given base URL. Join links and QR codes use it. |
| `--db games.db` | SQLite file games are saved to and resumed from. Finished-with games can be ended from the session hub ("End this game"). |

Common ways to run it:

```
# Practice, no credits: template narrator, browser voice, dev panel
.venv/bin/python -m server.main --sim --dev

# Muse narrator, free browser voice, dev panel
.venv/bin/python -m server.main --sim --mirror muse --dev

# Full demo: Muse narrator + voice yes/no, ElevenLabs voice, public links, saved games
.venv/bin/python -m server.main --sim --mirror muse --voice muse --speech elevenlabs --tunnel --db games.db

# Same, with track 1 on the real belt
.venv/bin/python -m server.main --sim --bridge step --mirror muse --voice muse --speech elevenlabs --tunnel --db games.db
```

Camera: add `--camera 0` to the server command (it starts `vision/live.py 0 --post`: dice, and the ArUco card markers while the game waits for a card; the board shows what it sees). By hand: `vision/live.py 0 --post`; `--dice` / `--cards` pin a mode. `vision/watch.py --frames` (calibrated mat, posts scene frames for the Mirror). Markers to print: `vision/card_markers/markers_letter.pdf`.

### Card instruction update

Marker **9** (`crystal_10`, Crystal Ball card 10, **Spoilers!**) now means
**Move forward 3 spaces**. Its printed marker stays the same. Update the physical
card's instruction text; the original `design/All Cards.pdf` still describes the
older backwards action. Forward movement resolves the destination normally and
collects 200 gold if it passes Once Upon a Time.
