"""Play a game in the terminal: a demo fallback that needs no browser or hardware.

    python -m server.cli                       # Grandma, Maya, Leo
    python -m server.cli Grandma Maya --seed 3

Uses the real engine and the template Mirror. Moves finish instantly (as
if the motors were done), Grandma's dice are typed in (or random), and
her card tap is a keypress.
"""
from __future__ import annotations

import argparse
import asyncio
import random

from .adapters.mirror import TemplateMirror
from .engine import SYSTEM, EngineError, GameState, Phase, apply_action, new_game
from .rules.monopoly import cards, square

mirror = TemplateMirror()


def show(state: GameState, events: list[dict]) -> None:
    for ev in events:
        line = asyncio.run(mirror.line(ev, state))
        if line:
            print(f"  🪞 {line}")


def status(state: GameState) -> None:
    parts = [f"{p.name} 🪙{p.balance} @ {square(p.position)['name']}"
             + (" (locked in)" if p.in_jail else "") + (" (out)" if p.bankrupt else "")
             for p in state.players]
    print("\n" + " | ".join(parts))


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip().lower()
    except EOFError:
        raise SystemExit


def next_input(state: GameState, rng: random.Random) -> tuple[str, dict]:
    """Ask the right person for whatever the engine is waiting on."""
    cur = state.current_player
    pend = state.pending
    if state.phase == Phase.WAITING_FOR_ROLL:
        if cur.kind == "in_person":
            raw = ask(f"{cur.name}: dice total 2-12, Enter = random, x = camera can't see > ")
            if raw == "x":
                return SYSTEM, {"action": "dice_not_visible"}
            value = int(raw) if raw.isdigit() else rng.randint(1, 6) + rng.randint(1, 6)
            return SYSTEM, {"action": "dice", "value": value}
        ask(f"{cur.name}: press Enter to roll > ")
        return cur.id, {"action": "roll"}
    if state.phase == Phase.MOVING:   # pretend the motors finished
        mover = state.player(pend["player"])
        return SYSTEM, {"action": "move_done", "lane": mover.lane, "square": pend["to"]}
    if state.phase == Phase.WAITING_FOR_DECISION:
        who = state.player(pend["player"])
        if pend["type"] == "jail":
            question = f"{who.name}: pay {pend['price']} to leave the tower? y/n > "
        else:
            question = f"{who.name}: buy {square(pend['square'])['name']} for {pend['price']}? y/n > "
        yes = ask(question).startswith("y")
        if who.kind == "in_person":
            return SYSTEM, {"action": "decision", "player": who.id, "answer": "yes" if yes else "no"}
        return who.id, {"action": "buy" if yes else "decline"}
    if state.phase == Phase.WAITING_FOR_CARD:
        deck = [c["id"] for c in cards()["cards"] if c["deck"] == pend["deck"]]
        raw = ask(f"Card read by the camera ({deck[0]}..{deck[-1]}), Enter = random > ")
        return SYSTEM, {"action": "card", "id": raw or rng.choice(deck)}
    if state.phase == Phase.WAITING_FOR_PAYMENT:
        raw = ask(f"Whose card is tapped? p1/p2/p3, Enter = {pend['payer']} > ") or pend["payer"]
        return SYSTEM, {"action": "player_card", "player": raw}
    raise SystemExit(f"nothing to do in {state.phase.value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Play Magic Mirror Monopoly in the terminal")
    parser.add_argument("names", nargs="*", default=["Grandma", "Maya", "Leo"],
                        help="first name is Grandma (in person); the rest are remote players")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    specs = [{"name": args.names[0], "kind": "in_person"}] + [{"name": n, "kind": "remote"} for n in args.names[1:]]
    state = new_game("cli", specs)
    state, events = apply_action(state, SYSTEM, {"action": "start", "force": True}, rng)
    show(state, events)

    while state.phase != Phase.GAME_OVER:
        if state.phase == Phase.WAITING_FOR_ROLL:
            status(state)
        actor, action = next_input(state, rng)
        try:
            state, events = apply_action(state, actor, action, rng)
        except EngineError as e:
            print(f"  ✋ {e}")
            continue
        show(state, events)


if __name__ == "__main__":
    main()
