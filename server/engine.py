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
        END_OF_TURN --> next player's WAITING_FOR_ROLL
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
    #   {"kind": "decision", "player": "p1", "square": 6, "price": 100}
    #   {"kind": "payment", "payer": "p1", "payee": "p2", "amount": 25, "reason": "rent", "square": 6}
    #   {"kind": "card", "deck": "spell", "for": "p2"}            (milestone 4)
    pending: dict | None = None
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

    The first player must be Grandma (in_person); the rest remote.
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
        if i > 0 and kind == "in_person":
            raise EngineError("only one in-person player")
        if not name:
            raise EngineError("every player needs a name")
        players.append(Player(
            id=f"p{i + 1}", name=name[:20], kind=kind, lane=i + 1,
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
        if name in _NOT_YET:
            raise EngineError(f"'{name}' isn't implemented yet")
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
    s.phase = Phase.WAITING_FOR_ROLL
    s.current = 0
    s.turn = 1
    return [
        {"type": "game_started", "players": [p.id for p in s.players]},
        {"type": "turn_started", "player": s.current_player.id},
    ]


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
    """The camera read Grandma's physical dice (total only)."""
    _require_system(actor)
    _require_phase(s, Phase.WAITING_FOR_ROLL)
    p = s.current_player
    if p.kind != "in_person":
        raise EngineError(f"it's {p.name}'s turn — they roll in their app")
    value = a.get("value")
    if not isinstance(value, int) or not 2 <= value <= 12:
        raise EngineError("dice total must be 2-12")
    return _start_move(s, p, value, None)


def _dice_not_visible(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    _require_system(actor)
    _require_phase(s, Phase.WAITING_FOR_ROLL)
    p = s.current_player
    if p.kind != "in_person":
        raise EngineError(f"it's {p.name}'s turn — they roll in their app")
    return [{"type": "dice_not_visible", "player": p.id}]   # stay in WAITING_FOR_ROLL


def _start_move(s: GameState, p: Player, total: int, dice: list[int] | None) -> list[dict]:
    s.last_roll, s.last_dice = total, dice
    frm, to = p.position, (p.position + total) % rules.NUM_SQUARES
    s.phase = Phase.MOVING
    s.pending = {"kind": "move", "player": p.id, "from": frm, "to": to}
    return [
        {"type": "rolled", "player": p.id, "value": total, "dice": dice},
        # The room sends this to the move bridge; nothing resolves until move_done.
        {"type": "move", "player": p.id, "lane": p.lane, "from": frm, "to": to},
    ]


def _moving_player(s: GameState, actor: str, a: dict) -> Player:
    _require_system(actor)
    _require_phase(s, Phase.MOVING)
    p = s.player(s.pending["player"])
    if a.get("lane") != p.lane:
        raise EngineError(f"expected lane {p.lane} to report, got lane {a.get('lane')}")
    return p


def _move_done(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    p = _moving_player(s, actor, a)
    frm, to = s.pending["from"], s.pending["to"]
    if a.get("square") != to:
        raise EngineError(f"token reported at square {a.get('square')}, expected {to}")
    p.position = to
    s.pending = None
    events = [{"type": "moved", "player": p.id, "from": frm, "to": to}]
    if to < frm:  # wrapped past 39: passed or landed on Castle Gate
        salary = rules.board()["go_salary"]
        p.balance += salary
        events.append({"type": "salary", "player": p.id, "amount": salary})
    return events + _resolve(s, p)


def _move_failed(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    p = _moving_player(s, actor, a)
    return [{"type": "move_failed", "player": p.id, "reason": a.get("reason", "unknown")}]


def _retry_move(s: GameState, actor: str, a: dict, rng: random.Random) -> list[dict]:
    _require_system(actor)
    _require_phase(s, Phase.MOVING)
    p = s.player(s.pending["player"])
    return [{"type": "move", "player": p.id, "lane": p.lane,
             "from": s.pending["from"], "to": s.pending["to"]}]


# --- landing ------------------------------------------------------------------

def _resolve(s: GameState, p: Player) -> list[dict]:
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
            s.pending = {"kind": "decision", "player": p.id, "square": idx, "price": sq["price"]}
            events.append({"type": "offer", "player": p.id, "square": idx, "price": sq["price"]})
            return events
        if owner.id != p.id:
            amount = rules.rent(idx, owner.properties, s.last_roll or 0)
            return events + _request_payment(s, p, owner.id, amount, "rent", square=idx)
    # Cards, taxes and Cursed! are milestone 4; for now they're quiet squares.
    return events + _end_turn(s)


# --- buy decision -------------------------------------------------------------

def _decide(s: GameState, pid: str, yes: bool, square: int | None = None) -> list[dict]:
    _require_phase(s, Phase.WAITING_FOR_DECISION)
    pend = s.pending
    if pid != pend["player"]:
        raise EngineError(f"it's {s.player(pend['player']).name}'s decision")
    if square is not None and square != pend["square"]:
        raise EngineError("that's not the place on offer")
    p = s.player(pid)
    s.pending = None
    events = [{"type": "decided", "player": pid, "square": pend["square"], "answer": "yes" if yes else "no"}]
    if not yes:
        return events + _end_turn(s)
    return events + _request_payment(s, p, BANK, pend["price"], "buy", square=pend["square"])


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

def _request_payment(s: GameState, payer: Player, payee: str, amount: int,
                     reason: str, **extra) -> list[dict]:
    """Every payment is created here. Only Grandma approves hers (card tap)."""
    pay = {"kind": "payment", "payer": payer.id, "payee": payee, "amount": amount, "reason": reason, **extra}
    if payer.kind == "in_person" and payer.balance >= amount:
        s.phase = Phase.WAITING_FOR_PAYMENT
        s.pending = pay
        return [{"type": "payment_requested", **{k: v for k, v in pay.items() if k != "kind"}}]
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
    return events + _end_turn(s)


def _bankrupt(s: GameState, payer: Player, payee: Player | None, pay: dict) -> list[dict]:
    """Payer hands over everything they have and leaves; their places return to the bank."""
    given = payer.balance
    if payee:
        payee.balance += given
    payer.balance = 0
    payer.bankrupt = True
    payer.properties.clear()
    events = [
        {"type": "paid", "from": payer.id, "to": pay["payee"], "amount": given, "reason": pay["reason"]},
        {"type": "bankrupt", "player": payer.id, "owed": pay["amount"]},
    ]
    alive = [p for p in s.players if not p.bankrupt]
    if len(alive) == 1:
        s.phase = Phase.GAME_OVER
        s.winner = alive[0].id
        return events + [{"type": "game_over", "winner": s.winner}]
    return events + _end_turn(s)


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
    s.phase = Phase.WAITING_FOR_ROLL
    return [{"type": "turn_ended", "player": ended},
            {"type": "turn_started", "player": s.current_player.id}]


_HANDLERS: dict[str, Handler] = {
    "set_connected": _set_connected,
    "start": _start,
    "roll": _roll,
    "dice": _dice,
    "dice_not_visible": _dice_not_visible,
    "move_done": _move_done,
    "move_failed": _move_failed,
    "retry_move": _retry_move,
    "buy": _buy,
    "decline": _decline,
    "decision": _decision,
    "player_card": _player_card,
    "confirm_payment": _confirm_payment,
}

# Actions the protocol already defines; implemented in milestone 4.
_NOT_YET = {"card"}
