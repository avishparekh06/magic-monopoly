# HackTX 26 Build Plan: Magnetic Belt Race Board

Oct 22, 2026 · Avish

Three belt-driven lanes under a thin plate slide magnetic game pieces for HackTX 26 (Oct 24-25), run from one Arduino and a laptop, and the plan is to build one lane first and copy it twice.

## Concept

A flat board where game pieces slide on their own, pulled by magnets hidden under a thin plate. Each of 3 players gets one lane with its own motor, belt and magnet, so all three pieces can move at once. The magic is that nothing visible touches the pieces.

- **The hook:** tokens move by themselves around a Monopoly board, driven by a laptop that watches the board through a webcam.
- **The game is Monopoly.** Each of 3 players has a token on its own lane around the rim. A dice roll sets how many squares the token moves.
- **The AI layer:** an AI banker handles rent, purchases and auctions, and the computer players choose their buys. The stretch goal is a language model that speaks and runs the game.
- **Why separate lanes:** tokens never block each other, so three tokens can share the same square on different lanes.
- **The cost of this choice:** the tokens must go around the corners, and a rigid carriage on top of the belt cannot pass the pulleys. The carriage has to ride on the outside of the belt loop. That corner is untested and is the first thing to build.

## System at a glance

```
Laptop (Python)
  ├─ USB serial ──> Arduino Uno + CNC Shield ──> 3 stepper motors ──> 3 belt lanes ──> magnets under the plate
  └─ Webcam <───────────────────────────────── watches the pieces, feeds positions back
```

The laptop sends commands to the Arduino over USB. The Arduino steps three motors, and each motor's belt pulls a magnet under its piece. The webcam watches the pieces and feeds their positions back to the laptop.

## Parts list

Quantities are for 3 straight lanes of about 30 cm. "Found" means a listing was identified from the link text, and none of the listings, prices or delivery dates have been verified.

| Part | Qty | Notes | Status |
| --- | --- | --- | --- |
| Arduino Uno R3 clone (ELEGOO UNO R3) with USB cable | 1 | Not the Maker-UNO. Check the cable is included | Found |
| CNC Shield V3 + 4 A4988 drivers + heatsinks | 1 kit | 1 spare driver. Pins look pre-soldered in the photo | Found |
| Jumper caps, 2.54 mm | 9 | The 120-pack covers it | Found |
| NEMA-17 stepper motor | 3 | Aim for 1.5 A or less per phase, with plug-in cable | Found 1 listing, need 3 |
| 12 V 5 A power supply (ALITOVE) | 1 | Confirm plug is 2.1 or 2.5 mm | Found |
| DC pigtail, female 5.5 x 2.1 mm, bare wire | 1 | Fits a 2.1 mm plug only | Found |
| GT2 belt, 6 mm | about 2.3 m | Zeelo kit has 5 m | Found |
| GT2 20-tooth motor pulley, 5 mm bore | 3 | Check the kit includes 3 | Check |
| GT2 20-tooth idler pulley with bearing, 5 mm bore | 3 + 1 spare | Wangdd22 5-pack | Found |
| Neodymium disc magnets, about 10 x 3 mm | 3 | Check the MIN-CI size first | Check |
| Steel flat washers (piece bases) | 3+ | Hardware store | Not bought |
| Zip ties | a few | Belt clamp | Found |
| Multimeter | 1 | Sets driver current; borrow | Borrow |
| Small screwdrivers, hex key set | 1 each | Pulley set screws, trim pot, screw terminal | Not bought |

Not needed: servos, endstops, the breadboard, extra drivers, a 3D printer, a fan.

**Monopoly loop changes:** about 9 idler pulleys (3 per lane) plus a spare, and about 1.2-1.6 m of belt per lane, so roughly 3.5-4.8 m in total for a board 30-40 cm on a side. A 5 m strip barely covers the larger board.

## Mechanics

Build one lane first. A lane is a belt loop between a motor pulley at one end and an idler at the other, with a small carriage clamped to the belt's top run and a magnet on top of the carriage.

1. **Layout:** mount a motor (on an L-bracket) at one end and an idler bolt at the other, about 30 cm apart, with the belt's top run horizontal.
2. **Carriage:** cut a stiff 3 mm plate about 40 x 12 mm. Overlap the belt ends teeth-to-teeth on its underside and clamp them with zip ties.
3. **Magnet:** glue the disc magnet to the top of the carriage, facing up, so its top sits about 1 mm under the plate.
4. **Guide:** put a smooth strip under the belt's top run and a side rail on each side, so the carriage cannot rock or drift sideways. Add felt or PTFE tape under the carriage.
5. **Plate:** use a non-steel top plate (3 mm acrylic or hardboard) above everything, including the pulley flanges.
6. **Pieces:** use steel washers as piece bases. Keep lanes at least 2 cm apart so one magnet does not tug a neighbour's piece.
7. **Tension:** snug but not tight. The belt should give slightly when pressed, because a loose belt skips teeth.

**The constraint that shaped this design:** a rigid carriage cannot wrap around a pulley, so the magnet only travels the straight run between the pulleys (stop about 2 cm short of each). That is why the lanes are straight and not loops around the rim.

**Update for Monopoly:** the game needs tokens to go all the way around, so the plan switches from straight lanes to three nested loops, one per player. Each loop has 1 motor pulley and 3 idlers, and the carriage hangs off the outside of the belt on a single attachment point so it can pass the corners. Lanes must stay about 2 cm apart, so the track around the board has to be wider than on a standard Monopoly board. Build and test one corner before anything else.

## Electronics setup

Do these in order, and test each step before moving on.

1. **Flash first.** With the shield off, plug the Uno into the laptop and install the Arduino IDE. Install the CH340 driver only if the port does not show up. Upload GRBL (or your AccelStepper sketch), open the serial monitor and send `$$` to confirm it answers.
2. **Prepare the shield.** Stick a heatsink on each A4988. Seat the drivers in the X, Y and Z slots, noting the pin 1 / enable orientation. Put three jumper caps under each driver for 1/16 microstepping.
3. **Set the current limit (Vref) on each driver** with a multimeter and a small screwdriver on the trim pot, before any motor is connected. Use the formula for your driver's sense-resistor value (read the marking on the board), and keep each motor at or under its rated current.
4. **Wire the power with the supply unplugged.** Screw the pigtail's + and − into the shield's power terminal. Check which wire is positive with the multimeter first, because reversed polarity can destroy the drivers.
5. **Connect the motors with the power off.** One motor to X, one to Y, one to Z. Never plug or unplug a motor while the board is powered.
6. **Power on and test.** Keep the Uno on USB, then plug in the 12 V supply. In GRBL set steps per mm for each axis (`$100`, `$101`, `$102`).

For a 20-tooth GT2 pulley (2 mm pitch, 40 mm of belt per revolution) at 1/16 microstepping, that is 3200 steps per revolution, or **80 steps/mm**. At 1/4 microstepping it is 20 steps/mm.

7. **Jog a short distance.** Send something like `G91 G1 X50 F600` and check the carriage moves about 50 mm in the right direction. Raise speeds only after it moves smoothly.

**One firmware choice to make:** stock GRBL moves all three axes together as one machine, so lanes cannot run at independent speeds. If the game needs that, use the Arduino AccelStepper library with a simple serial protocol. The Uno handles only roughly 4,000-5,000 total steps per second that way, so use 1/4 microstepping and moderate speeds.

## Software

All of it runs on the laptop in Python. The Arduino only turns motors.

- **Motor control:** `pyserial` sends G-code (GRBL) or simple lane commands (AccelStepper) over USB.
- **Piece tracking:** OpenCV on the webcam finds each piece (colored caps, or ArUco markers on top) and reports its position along its lane.
- **Closed loop:** compare the piece's measured position with its target and correct the motor. This tolerates loose belts and stretch, since the camera checks the piece, not the motor.
- **Human player:** MediaPipe hand tracking can map one player's hand position to a lane target, so a person drives a piece without touching the board.
- **Game logic:** a small state machine for the game (positions, turn or tick, win check).
- **AI layer:** the computer players pick moves, and a language model can generate events or rules as structured data that the game validates before it runs them.

All of this is a proposal. None of it has been built or tested yet.

## Scope and priorities

Everything below is in scope, ordered so the demo still works if the later tiers slip.

| Tier | Feature | How | Notes |
| --- | --- | --- | --- |
| 1 Must | Tokens move to squares from code | Distances per square sent over serial; camera corrects drift | Needs the corner test to pass first |
| 1 Must | Monopoly rules engine | Python state machine: dice, buy, rent, jail, bankruptcy (simplified) | Can be built and tested with no hardware |
| 1 Must | Play against an AI | Rule-based buying and trading first; a language model for talk and strategy later | Its token is driven like any other lane |
| 2 Should | Phone app: cash and properties | Web app (FastAPI + WebSocket) on each player's phone; buy and pay buttons | Use a laptop hotspot, not venue Wi-Fi |
| 2 Should | Gestures: roll dice, buy property | MediaPipe Hands: shake or throw to roll, thumbs up to buy | Virtual dice; reading physical dice is a separate project |
| 3 Stretch | Face recognition: who is acting | face_recognition or InsightFace with a photo enrolled at the start | Needs a second camera facing the players |
| 3 Stretch | Voice AI banker | Speech in, language model, speech out | Only after tiers 1 and 2 work |

Face recognition is only a few lines with an off-the-shelf library, but it needs each player enrolled, steady lighting and a camera that actually sees faces. The overhead webcam will not. It also handles biometric data, so keep it local, ask for consent and delete it after the demo. Turn order or a gesture already tells you who is acting, so keep this one last.

With up to 4 people on a team, a natural split is hardware, vision and gestures, game and AI, and the app.

## Timeline

The event runs Oct 24-25. Sunday judging is 2-5 PM and the pitching competition is 5:30-6 PM (from hacktx.com). The "Any rules?" FAQ was unreadable, so confirm whether code or hardware may be built before the event.

| When | Goal |
| --- | --- |
| Thu night (now) | Order the missing parts. Check each listing's delivery date against Saturday. Borrow a multimeter and, if possible, an Uno. Check the event rules. |
| Fri | Mock a carriage from cardboard and test the magnet-through-plate gap. When parts arrive: flash GRBL, set Vref, jog one motor. |
| Sat morning | Check-in 8-10 AM, opening ceremony 10:30-11:30 AM (prizes and challenges revealed), team matching 12-1 PM. Decide the game once the challenges are known. |
| Sat afternoon | Build lane 1 end to end, with webcam tracking of its piece. |
| Sat night | Copy lane 1 into lanes 2 and 3. Connect the game logic and AI. |
| Sun morning | Tune the closed loop, rehearse the demo, record a backup video. |
| Sun 1-2 PM | Judging and pitch workshops. |
| Sun 2-6 PM | Judging, then the pitching competition. |

If parts arrive late, build the demo with fewer lanes (one or two) rather than cutting the closed loop.

## Risks and fallbacks

| Risk | Why it matters | Fallback |
| --- | --- | --- |
| Parts miss Saturday | Nothing can be tested without motors, shield and belt | Build 1-2 lanes; borrow an Uno and multimeter; mock the rest on video |
| Magnet does not hold the piece through the plate | The whole effect fails | Test the gap with cardboard first; use a thinner plate or a stronger magnet |
| Plug size mismatch (2.1 vs 2.5 mm) | Supply will not plug into the pigtail | Buy a 2.1/2.5 pigtail, or cut the plug and wire straight into the terminal |
| Reversed power polarity | Can destroy the drivers | Check the wire with a multimeter before connecting; keep the spare driver |
| Belt skips or stretches | Piece position drifts | Retension; rely on the camera closed loop |
| Lanes need independent speeds | Stock GRBL moves axes together | Use AccelStepper with 1/4 microstepping |
| Neighbouring magnet pulls another lane's piece | Wrong piece moves | Keep lanes 2 cm or more apart; test spacing |
| Event rules limit pre-building | Pre-built work may not be allowed | Read the rules FAQ and ask an organizer before Saturday |
| Carriage jams or tilts at a loop corner | Monopoly tokens must go around the whole rim | Test one corner first; if it fails, drop to a smaller board or 2 lanes, or hand tokens between side segments |

## Open decisions

- [ ] Board size and lane spacing: 3 lanes at about 2 cm need a wider track than a normal Monopoly board. Is a custom board fine?
- [ ] How much of Monopoly: full 40 squares and money, or a simplified version?
- [ ] Does the carriage on the outside of the loop pass the corners? Test with string and a cardboard flag.
- [ ] Does the team have a 3D printer or tool access, or is it zip ties and wood only?
- [ ] Will motors, drivers and belt arrive before Saturday? Check each delivery date.
- [ ] Who can lend an Arduino Uno and a multimeter?
- [ ] Plug size of the ALITOVE supply: 2.1 or 2.5 mm?
- [ ] Do the event rules allow hardware or code built before Saturday?
