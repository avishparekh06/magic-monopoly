"""Pure game engine: the single source of truth.

No FastAPI, no hardware, no I/O. Everything that changes the game goes
through one function:

    new_state, events = apply_action(state, actor, action, rng)

- `actor` is who is acting: "p1".."p3" for a player, or "system" for
  inputs that come from adapters (move_done, camera reads, voice) and
  from the host/board page (start).
- `action` is a dict like {"action": "start"}.
- Illegal actions raise EngineError; the state passed in is never mutated.
- `events` is a list of dicts like {"type": "game_started"} that the server
  broadcasts and feeds to adapters (Mirror lines, move commands).

A turn:
    WAITING_FOR_ROLL --roll (kid) / dice read (Grandma)--> MOVING
    MOVING --move_done--> RESOLVING --> one of
        WAITING_FOR_DECISION (unowned: buy yes/no)
        WAITING_FOR_PAYMENT  (Grandma owes: waits for her card tap;
                              remote payers settle immediately)
        WAITING_FOR_CARD     (Crystal Ball / Chest of Spells: the player draws, camera reads)
        END_OF_TURN --> next player's WAITING_FOR_ROLL
                        (or a pay-the-fine decision if they're in the tower)
"""
from __future__ import annotations

import copy
import random
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Callable

from .rules import monopoly as rules

MAX_PLAYERS = 3
STARTING_BALANCE = 1500
SYSTEM = "system"
BANK = "bank"
PLAYER_KINDS = ("in_person", "remote")  # AI seats are out of scope for v1


class Phase(str, Enum):
    LOBBY = "LOBBY"
    WAITING_FOR_ROLL = "WAITING_FOR_ROLL"
    MOVING = "MOVING"                          # waits for move_done from the bridge
    RESOLVING = "RESOLVING"
    WAITING_FOR_DECISION = "WAITING_FOR_DECISION"  # buy yes/no
    WAITING_FOR_PAYMENT = "WAITING_FOR_PAYMENT"    # Grandma's card tap; auto for remote
    WAITING_FOR_CARD = "WAITING_FOR_CARD"
    END_OF_TURN = "END_OF_TURN"
    GAME_OVER = "GAME_OVER"


class EngineError(Exception):
    """An action that is illegal right now. The message is shown to the user."""


@dataclass
class Player:
    id: str          # "p1".."p3"; player n owns lane n
    name: str
    kind: str        # "in_person" | "remote"
    lane: int
    position: int = 0
    balance: int = STARTING_BALANCE
    properties: list[int] = field(default_factory=list)
    in_jail: bool = False
    jail_turns: int = 0
    jail_free_cards: int = 0
    bankrupt: bool = False
    connected: bool = False


@dataclass
class GameState:
    game_id: str
    players: list[Player]
    phase: Phase = Phase.LOBBY
    current: int = 0              # index into players
    turn: int = 0
    last_roll: int | None = None
    last_dice: list[int] | None = None   # both dice for app rolls; None for camera reads (total only)
    # What the engine is waiting on:
    #   {"kind": "move", "player": "p2", "from": 3, "to": 8}
    #   {"kind": "decision", "type": "buy", "player": "p1", "square": 6, "price": 100}
    #   {"kind": "decision", "type": "jail", "player": "p2", "price": 50}
    #   {"kind": "payment", "payer": "p1", "payee": "p2", "amount": 25, "reason": "rent", "square": 6}
    #   {"kind": "card", "deck": "crystal", "for": "p2"}
    pending: dict | None = None
    payment_queue: list[dict] = field(default_factory=list)  # payments still to come this turn
    last_card: dict | None = None   # {"id": "crystal_07", "for": "p2"}: shown in that kid's app
    winner: str | None = None

    def player(self, pid: str) -> Player:
        for p in self.players:
            if p.id == pid:
                return p
        raise EngineError(f"no such player: {pid}")

    @property
    def current_player(self) -> Player:
        return self.players[self.current]

    def owner_of(self, square: int) -> Player | None:
        return next((p for p in self.players if square in p.properties), None)


def new_game(game_id: str, specs: list[dict]) -> GameState:
    """Build a game in LOBBY. specs: [{"name": "Grandma", "kind": "in_person"}, ...]

    The first player must be in_person (the host at the table). Any other seat may be
    in_person too (rolls the real dice, no phone needed) or remote (rolls in their app).
    Lanes and ids are assigned in order.
    """
    if not 2 <= len(specs) <= MAX_PLAYERS:
        raise EngineError(f"a game needs 2 to {MAX_PLAYERS} players")
    if specs[0].get("kind") != "in_person":
        raise EngineError("the first player must be the in-person player")
    players = []
    for i, spec in enumerate(specs):
        kind = spec.get("kind")
        name = (spec.get("name") or "").strip()
        if kind not in PLAYER_KINDS:
            raise EngineError(f"unknown player kind: {kind}")
        if not name:
            raise EngineError("every player needs a name")
        position = spec.get("position") or 0          # debug: start where the physical token already is
        if not isinstance(position, int) or not 0 <= position < rules.NUM_SQUARES:
            raise EngineError(f"start position must be 0-{rules.NUM_SQUARES - 1}")
        players.append(Player(
            id=f"p{i + 1}", name=name[:20], kind=kind, lane=i + 1, position=position,
            # Grandma is present from the start; remote kids connect by link.
            connected=kind != "remote",
        ))
    return GameState(game_id=game_id, players=players)


Handler = Callable[[GameState, str, dict, random.Random], list[dict]]


def apply_action(state: GameState, actor: str, action: dict,
                 rng: random.Random | None = None) -> tuple[GameState, list[dict]]:
    name = action.get("action")
    handler = _HANDLERS.get(name)
    if handler is None:
        raise EngineError(f"unknown action: {name}")
    s = copy.deepcopy(state)
    events = handler(s, actor, action, rng or random.Random())
    return s, events


def to_public(state: GameState) -> dict:
    """JSON-ready snapshot sent to every page. Contains no secrets."""
    d = asdict(state)
    d["phase"] = state.phase.value
    d["current_player"] = state.current_player.id
    d["owners"] = {str(sq): p.id for p in state.players for sq in p.properties}
    return d


def from_public(d: dict) -> GameState:
    """Inverse of to_public: rebuild a GameState from a saved snapshot."""
    fields = {f for f in GameState.__dataclass_fields__}
    data = {k: v for k, v in d.items() if k in fields}
    data["players"] = [Player(**p) for p in d["players"]]
    data["phase"] = Phase(d["phase"])
    return GameState(**data)


# --- checks -----------------------------------------------------------------

def _require_system(actor: str) -> None:
    if actor != SYSTEM:
        raise EngineError("only the game host can do that")


def _require_phase(s: GameState, *phases: Phase) -> None:
    if s.phase not in phases:
        raise EngineError(_WAITING_ON.get(s.phase, lambda s: f"can't do that now ({s.phase.value})")(s))


_WAITING_ON = {
    Phase.LOBBY: lambda s: "the game hasn't started yet",
    Phase.WAITING_FOR_ROLL: lambda s: f"waiting for {s.current_player.name} to roll",
    Phase.MOVING: lambda s: f"{s.current_player.name}'s token is still moving",
    Phase.WAITING_FOR_DECISION: lambda s: f"waiting for {s.player(s.pending['player']).name} to decide",
    Phase.WAITING_FOR_PAYMENT: lambda s: f"waiting for {s.player(s.pending['payer']).name} to pay",
    Phase.GAME_OVER: lambda s: "the game is over",
}


def _require_turn(s: GameState, actor: str) -> Player:
    cur = s.current_player
    if actor != cur.id:
        raise EngineError(f"not your turn — it's {cur.name}'s turn")
    return cur


# --- lobby ------------------------------------------------------------------

def _set_connected(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    _require_system(actor)
    p = s.player(a.get("player", ""))
    connected = bool(a.get("connected"))
    if p.kind != "remote" or p.connected == connected:
        return []
    p.connected = connected
    return [{"type": "player_connected" if connected else "player_disconnected", "player": p.id}]


def _start(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    _require_system(actor)
    if s.phase != Phase.LOBBY:
        raise EngineError("the game has already started")
    missing = [p.name for p in s.players if not p.connected]
    if missing and not a.get("force"):
        raise EngineError("still waiting for " + ", ".join(missing))
    s.current = 0
    s.turn = 1
    return [{"type": "game_started", "players": [p.id for p in s.players]}] + _start_turn(s)


# --- debugging: align the game with the physical board -----------------------

def _set_position(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    """Host/debug: put a player's token on a square without a move (no salary, nothing resolved).
    Used when the physical piece was placed or jogged by hand, so the two boards agree."""
    _require_system(actor)
    if s.phase == Phase.MOVING:
        raise EngineError("wait for the token to stop moving")
    p = s.player(a.get("player", ""))
    square = a.get("square")
    if not isinstance(square, int) or not 0 <= square < rules.NUM_SQUARES:
        raise EngineError(f"square must be 0-{rules.NUM_SQUARES - 1}")
    frm, p.position = p.position, square
    return [{"type": "position_set", "player": p.id, "from": frm, "to": square}]


def _land(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    """Host/debug: pretend the current player just landed on `square` (no roll, no belt, no salary) and
    resolve it: an offer, rent, a card draw, tax, the tower. For testing cards and rules."""
    _require_system(actor)
    _require_phase(s, Phase.WAITING_FOR_ROLL)
    p = s.current_player
    square = a.get("square")
    if not isinstance(square, int) or not 0 <= square < rules.NUM_SQUARES:
        raise EngineError(f"square must be 0-{rules.NUM_SQUARES - 1}")
    frm, p.position = p.position, square
    s.last_roll, s.last_dice = None, None
    return [{"type": "position_set", "player": p.id, "from": frm, "to": square}] + _resolve(s, p, {})


# --- rolling and moving -----------------------------------------------------

def _roll(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    """A remote player presses Roll; the engine rolls for them."""
    _require_phase(s, Phase.WAITING_FOR_ROLL)
    p = _require_turn(s, actor)
    if p.kind == "in_person":
        raise EngineError(f"{p.name} rolls the real dice into the tray")
    d1, d2 = rng.randint(1, 6), rng.randint(1, 6)
    return _start_move(s, p, d1 + d2, [d1, d2])


def _dice(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    """The camera read the physical dice (total only). The table dice count for whoever's
    turn it is, in person or remote: a remote player may still press Roll instead."""
    _require_system(actor)
    _require_phase(s, Phase.WAITING_FOR_ROLL)
    p = s.current_player
    value = a.get("value")
    if not isinstance(value, int) or not 2 <= value <= 12:
        raise EngineError("dice total must be 2-12")
    return _start_move(s, p, value, None)


def _dice_not_visible(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    _require_system(actor)
    _require_phase(s, Phase.WAITING_FOR_ROLL)
    p = s.current_player
    return [{"type": "dice_not_visible", "player": p.id}]   # stay in WAITING_FOR_ROLL


def _start_move(s: GameState, p: Player, total: int, dice: list[int] | None) -> list[dict]:
    s.last_roll, s.last_dice = total, dice
    rolled = {"type": "rolled", "player": p.id, "value": total, "dice": dice}
    return [rolled] + _begin_move(s, p, (p.position + total) % rules.NUM_SQUARES)


def _begin_move(s: GameState, p: Player, to: int, **flags) -> list[dict]:
    """Send a token forward to `to`. Flags travel with the move until move_done:
    no_salary (backwards / to-the-tower moves), to_jail, rent_multiplier,
    utility_multiplier (from "nearest ride/well" cards)."""
    frm = p.position
    s.phase = Phase.MOVING
    s.pending = {"kind": "move", "player": p.id, "from": frm, "to": to, **flags}
    # The room sends this to the move bridge; nothing resolves until move_done.
    # Belts only run forward, so even "go back 3" travels forward around the board.
    return [{"type": "move", "player": p.id, "lane": p.lane, "from": frm, "to": to}]


def _moving_player(s: GameState, actor: str, a: dict) -> Player:
    _require_system(actor)
    _require_phase(s, Phase.MOVING)
    p = s.player(s.pending["player"])
    if a.get("lane") != p.lane:
        raise EngineError(f"expected lane {p.lane} to report, got lane {a.get('lane')}")
    return p


def _move_done(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    p = _moving_player(s, actor, a)
    move = s.pending
    frm, to = move["from"], move["to"]
    if a.get("square") != to:
        raise EngineError(f"token reported at square {a.get('square')}, expected {to}")
    p.position = to
    s.pending = None
    events = [{"type": "moved", "player": p.id, "from": frm, "to": to}]
    if to < frm and not move.get("no_salary"):  # wrapped past 39: passed or landed on Once Upon a Time
        salary = rules.board()["go_salary"]
        p.balance += salary
        events.append({"type": "salary", "player": p.id, "amount": salary})
    if move.get("to_jail"):
        p.in_jail, p.jail_turns = True, 0
        return events + [{"type": "jailed", "player": p.id}] + _end_turn(s)
    return events + _resolve(s, p, move)


def _move_failed(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    p = _moving_player(s, actor, a)
    return [{"type": "move_failed", "player": p.id, "reason": a.get("reason", "unknown")}]


def _cancel_move(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    """Host gives up on a stuck move: the token stays where it was and the same player rolls again.
    (If the piece was moved by hand, send move_done instead so the landing resolves.)"""
    _require_system(actor)
    _require_phase(s, Phase.MOVING)
    p = s.player(s.pending["player"])
    to = s.pending["to"]
    s.pending = None
    s.phase = Phase.WAITING_FOR_ROLL
    s.last_roll, s.last_dice = None, None
    return [{"type": "move_cancelled", "player": p.id, "to": to}]


def _retry_move(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    _require_system(actor)
    _require_phase(s, Phase.MOVING)
    p = s.player(s.pending["player"])
    return [{"type": "move", "player": p.id, "lane": p.lane,
             "from": s.pending["from"], "to": s.pending["to"]}]


# --- landing ------------------------------------------------------------------

def _resolve(s: GameState, p: Player, move: dict) -> list[dict]:
    s.phase = Phase.RESOLVING
    idx = p.position
    sq = rules.square(idx)
    events: list[dict] = [{"type": "landed", "player": p.id, "square": idx}]

    if rules.is_buyable(sq):
        owner = s.owner_of(idx)
        if owner is None:
            if p.balance < sq["price"]:
                events.append({"type": "cant_afford", "player": p.id, "square": idx, "price": sq["price"]})
                return events + _end_turn(s)
            s.phase = Phase.WAITING_FOR_DECISION
            s.pending = {"kind": "decision", "type": "buy", "player": p.id, "square": idx, "price": sq["price"]}
            events.append({"type": "offer", "player": p.id, "square": idx, "price": sq["price"]})
            return events
        if owner.id != p.id:
            dice = s.last_roll or 0
            if sq["type"] == "utility" and move.get("utility_multiplier"):
                amount = move["utility_multiplier"] * dice
            else:
                amount = rules.rent(idx, owner.properties, dice) * move.get("rent_multiplier", 1)
            return events + _request_payment(s, _payment(p.id, owner.id, amount, "rent", square=idx))
        return events + _end_turn(s)

    if sq["type"] == "card":
        # Grandma draws the physical card for whoever landed; the camera reads it.
        s.phase = Phase.WAITING_FOR_CARD
        s.pending = {"kind": "card", "deck": sq["deck"], "for": p.id}
        return events + [{"type": "card_requested", "player": p.id, "deck": sq["deck"]}]
    if sq["type"] == "tax":
        return events + _request_payment(s, _payment(p.id, BANK, sq["amount"], "tax", square=idx))
    if sq["type"] == "go_to_jail":
        return events + [{"type": "cursed", "player": p.id}] + _send_to_jail(s, p)
    return events + _end_turn(s)  # Once Upon a Time, Fairy Ring, just visiting the tower


def _send_to_jail(s: GameState, p: Player) -> list[dict]:
    return _begin_move(s, p, rules.board()["jail_square"], no_salary=True, to_jail=True)


# --- cards ----------------------------------------------------------------------

def _card(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    """A game card was read: by the camera (system) or picked in the drawer's
    app when the camera couldn't read it."""
    if s.phase != Phase.WAITING_FOR_CARD:
        raise EngineError("nobody is drawing a card right now")
    pend = s.pending
    p = s.player(pend["for"])
    if actor != SYSTEM and actor != p.id:
        raise EngineError(f"that card is for {p.name}")
    card = rules.card(a.get("id", ""))
    if card is None:
        raise EngineError(f"unknown card: {a.get('id')}")
    if card["deck"] != pend["deck"]:
        decks = rules.cards()["decks"]
        raise EngineError(f"that's a {decks[card['deck']]['name']} card — "
                          f"{p.name} needs a {decks[pend['deck']]['name']} card")
    s.pending = None
    s.last_card = {"id": card["id"], "for": p.id}
    return [{"type": "card_drawn", "player": p.id, "id": card["id"]}] + _apply_card(s, p, card["effect"])


def _apply_card(s: GameState, p: Player, eff: dict) -> list[dict]:
    kind = eff["kind"]
    if kind == "advance":
        return _begin_move(s, p, eff["to"])
    if kind == "advance_nearest":
        to = rules.nearest(p.position, eff["type"])
        flags = {k: eff[k] for k in ("rent_multiplier", "utility_multiplier") if k in eff}
        return _begin_move(s, p, to, **flags)
    if kind == "back":
        return _begin_move(s, p, (p.position - eff["spaces"]) % rules.NUM_SQUARES, no_salary=True)
    if kind == "go_to_jail":
        return _send_to_jail(s, p)
    if kind == "collect":
        p.balance += eff["amount"]
        return [{"type": "paid", "from": BANK, "to": p.id, "amount": eff["amount"], "reason": "card"}] + _end_turn(s)
    if kind == "pay":
        return _request_payment(s, _payment(p.id, BANK, eff["amount"], "card"))
    others = [o for o in s.players if o.id != p.id and not o.bankrupt]
    if kind == "collect_from_each":
        s.payment_queue = [_payment(o.id, p.id, eff["amount"], "card") for o in others]
        return _next_payment(s)
    if kind == "pay_each":
        s.payment_queue = [_payment(p.id, o.id, eff["amount"], "card") for o in others]
        return _next_payment(s)
    if kind == "jail_free":
        p.jail_free_cards += 1
        return [{"type": "got_jail_key", "player": p.id}] + _end_turn(s)
    if kind == "repairs":
        # Per-building repair bills. Houses are out of scope in v1, so nobody owns any and the bill is 0.
        houses = hotels = 0
        amount = houses * eff["per_house"] + hotels * eff["per_hotel"]
        events = [{"type": "repairs", "player": p.id, "amount": amount, "houses": houses, "hotels": hotels}]
        if amount:
            return events + _request_payment(s, _payment(p.id, BANK, amount, "card"))
        return events + _end_turn(s)
    raise EngineError(f"unknown card effect: {kind}")


# --- decisions (buy, or pay to leave the tower) ---------------------------------

def _decide(s: GameState, pid: str, yes: bool, square: int | None = None) -> list[dict]:
    _require_phase(s, Phase.WAITING_FOR_DECISION)
    pend = s.pending
    if pid != pend["player"]:
        raise EngineError(f"it's {s.player(pend['player']).name}'s decision")
    p = s.player(pid)
    s.pending = None
    answer = "yes" if yes else "no"

    if pend["type"] == "jail":
        events = [{"type": "decided", "player": pid, "about": "jail", "answer": answer}]
        if yes:
            return events + _request_payment(s, _payment(pid, BANK, pend["price"], "jail_fine", then="release"))
        p.jail_turns += 1
        return events + [{"type": "stayed_in_jail", "player": pid}] + _end_turn(s)

    if square is not None and square != pend["square"]:
        s.pending = pend
        raise EngineError("that's not the place on offer")
    events = [{"type": "decided", "player": pid, "about": "buy", "square": pend["square"], "answer": answer}]
    if not yes:
        return events + _end_turn(s)
    return events + _request_payment(s, _payment(pid, BANK, pend["price"], "buy", square=pend["square"]))


def _buy(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    return _decide(s, actor, True, a.get("square"))


def _decline(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    return _decide(s, actor, False)


def _decision(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    """A yes/no heard by the voice adapter (system) or sent by the player themself."""
    pid = a.get("player") if actor == SYSTEM else actor
    answer = a.get("answer")
    if answer not in ("yes", "no"):
        raise EngineError("answer must be yes or no")
    return _decide(s, pid, answer == "yes")


# --- payments -----------------------------------------------------------------
#
# Every payment is a dict made by _payment() and goes through _request_payment().
# "then" says what happens after it settles: "end_turn" (default) or "release"
# (paid the tower fine; the same player now rolls). Several payments in a row
# (e.g. "collect 10 from each player") wait in state.payment_queue.

def _payment(payer: str, payee: str, amount: int, reason: str, **extra) -> dict:
    return {"kind": "payment", "payer": payer, "payee": payee, "amount": amount, "reason": reason, **extra}


def _request_payment(s: GameState, pay: dict) -> list[dict]:
    payer = s.player(pay["payer"])
    if payer.kind == "in_person" and payer.balance >= pay["amount"]:
        s.phase = Phase.WAITING_FOR_PAYMENT
        s.pending = pay
        return [{"type": "payment_requested", **{k: v for k, v in pay.items() if k not in ("kind", "then")}}]
    # Remote payments are approved automatically; so is a payment nobody can
    # cover (bankruptcy is automatic for everyone, Grandma included).
    return _settle(s, pay)


def _settle(s: GameState, pay: dict) -> list[dict]:
    s.pending = None
    payer = s.player(pay["payer"])
    payee = None if pay["payee"] == BANK else s.player(pay["payee"])
    amount = pay["amount"]
    if payer.balance < amount:
        return _bankrupt(s, payer, payee, pay)
    payer.balance -= amount
    if payee:
        payee.balance += amount
    event = {"type": "paid", "from": payer.id, "to": pay["payee"], "amount": amount, "reason": pay["reason"]}
    if "square" in pay:
        event["square"] = pay["square"]
    events = [event]
    if pay["reason"] == "buy":
        payer.properties.append(pay["square"])
        events.append({"type": "bought", "player": payer.id, "square": pay["square"]})
    return events + _after_payment(s, pay)


def _after_payment(s: GameState, pay: dict) -> list[dict]:
    if s.payment_queue:
        return _next_payment(s)
    if pay.get("then") == "release" and not s.player(pay["payer"]).bankrupt:
        p = s.player(pay["payer"])
        p.in_jail, p.jail_turns = False, 0
        s.phase = Phase.WAITING_FOR_ROLL
        return [{"type": "released", "player": p.id}]
    return _end_turn(s)


def _next_payment(s: GameState) -> list[dict]:
    # Drop payments involving anyone who went bankrupt earlier in the queue.
    gone = {p.id for p in s.players if p.bankrupt}
    s.payment_queue = [q for q in s.payment_queue if q["payer"] not in gone and q["payee"] not in gone]
    if not s.payment_queue:
        return _end_turn(s)
    return _request_payment(s, s.payment_queue.pop(0))


def _bankrupt(s: GameState, payer: Player, payee: Player | None, pay: dict) -> list[dict]:
    """Payer hands over everything they have and leaves; their places return to the bank."""
    given = payer.balance
    if payee:
        payee.balance += given
    payer.balance = 0
    payer.bankrupt = True
    payer.in_jail = False
    payer.properties.clear()
    events = [
        {"type": "paid", "from": payer.id, "to": pay["payee"], "amount": given, "reason": pay["reason"]},
        {"type": "bankrupt", "player": payer.id, "owed": pay["amount"]},
    ]
    alive = [p for p in s.players if not p.bankrupt]
    if len(alive) == 1:
        s.phase = Phase.GAME_OVER
        s.winner = alive[0].id
        s.payment_queue = []
        return events + [{"type": "game_over", "winner": s.winner}]
    return events + _after_payment(s, pay)


def _paying_player(s: GameState, actor: str) -> Player:
    _require_system(actor)
    if s.phase != Phase.WAITING_FOR_PAYMENT:
        raise EngineError("nobody needs to pay right now")
    return s.player(s.pending["payer"])


def _player_card(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    """The camera saw a player card on the reader spot."""
    payer = _paying_player(s, actor)
    if a.get("player") != payer.id:
        tapped = next((p.name for p in s.players if p.id == a.get("player")), a.get("player"))
        raise EngineError(f"that's {tapped}'s card — {payer.name} is the one paying")
    return _settle(s, s.pending)


def _confirm_payment(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    """Fallback when the card tap can't be read: the host confirms by hand."""
    _paying_player(s, actor)
    return _settle(s, s.pending)


# --- turns ----------------------------------------------------------------------

def _end_turn(s: GameState) -> list[dict]:
    s.phase = Phase.END_OF_TURN
    ended = s.current_player.id
    n = len(s.players)
    nxt = (s.current + 1) % n
    while s.players[nxt].bankrupt:
        nxt = (nxt + 1) % n
    s.current = nxt
    s.turn += 1
    s.pending = None
    return [{"type": "turn_ended", "player": ended}] + _start_turn(s)


def _start_turn(s: GameState) -> list[dict]:
    """Begin the current player's turn. A player in Rapunzel's Tower uses a key
    if they have one; otherwise they're offered to pay the fine, and on their
    third turn inside they must pay it. (Camera reads give totals only, so
    there's no rolling doubles to escape.)"""
    p = s.current_player
    s.phase = Phase.WAITING_FOR_ROLL
    events = [{"type": "turn_started", "player": p.id}]
    if not p.in_jail:
        return events
    if p.jail_free_cards:
        p.jail_free_cards -= 1
        p.in_jail, p.jail_turns = False, 0
        return events + [{"type": "used_jail_key", "player": p.id}]
    fine = rules.board()["jail_fine"]
    if p.jail_turns >= 2:
        return events + [{"type": "must_pay_fine", "player": p.id, "amount": fine}] + \
            _request_payment(s, _payment(p.id, BANK, fine, "jail_fine", then="release"))
    if p.balance < fine:
        p.jail_turns += 1
        return events + [{"type": "stayed_in_jail", "player": p.id}] + _end_turn(s)
    s.phase = Phase.WAITING_FOR_DECISION
    s.pending = {"kind": "decision", "type": "jail", "player": p.id, "price": fine}
    return events + [{"type": "jail_offer", "player": p.id, "price": fine}]


_HANDLERS: dict[str, Handler] = {
    "set_connected": _set_connected,
    "set_position": _set_position,
    "land": _land,
    "start": _start,
    "roll": _roll,
    "dice": _dice,
    "dice_not_visible": _dice_not_visible,
    "move_done": _move_done,
    "move_failed": _move_failed,
    "retry_move": _retry_move,
    "cancel_move": _cancel_move,
    "buy": _buy,
    "decline": _decline,
    "decision": _decision,
    "card": _card,
    "player_card": _player_card,
    "confirm_payment": _confirm_payment,
}
