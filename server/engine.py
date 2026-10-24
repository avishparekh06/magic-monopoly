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
"""
from __future__ import annotations

import copy
import random
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Callable

MAX_PLAYERS = 3
STARTING_BALANCE = 1500
SYSTEM = "system"
PLAYER_KINDS = ("in_person", "remote", "ai")


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
    kind: str        # "in_person" | "remote" | "ai"
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
    # What the engine is waiting on, e.g.
    #   {"kind": "decision", "player": "p1", "square": 6, "price": 100}
    #   {"kind": "payment", "payer": "p1", "payee": "p2", "amount": 25, "reason": "rent"}
    #   {"kind": "card", "deck": "spell", "for": "p2"}
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


def new_game(game_id: str, specs: list[dict]) -> GameState:
    """Build a game in LOBBY. specs: [{"name": "Grandma", "kind": "in_person"}, ...]

    The first player must be Grandma (in_person); the rest remote or ai.
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
            # Grandma and AI seats are present from the start; remote kids connect by link.
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


# --- handlers -------------------------------------------------------------

def _require_system(actor: str) -> None:
    if actor != SYSTEM:
        raise EngineError("only the game host can do that")


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


_HANDLERS: dict[str, Handler] = {
    "set_connected": _set_connected,
    "start": _start,
}

# Actions the protocol already defines; implemented in milestones 3-4.
_NOT_YET = {"roll", "dice", "dice_not_visible", "move_done", "move_failed", "buy",
            "decline", "decision", "card", "player_card", "confirm_payment", "end_turn"}
