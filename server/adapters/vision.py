"""Vision: the overhead webcam reading dice, game cards and player cards.

out: {"type": "dice", "value": 7}
   | {"type": "dice_not_visible"}
   | {"type": "card", "id": "spell_07"}
   | {"type": "player_card", "player": "p1"}

An adapter pushes reads by awaiting `emit(event)`. It does not decide whether
a read is wanted; the engine accepts only what it is currently waiting for.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Awaitable, Callable

from ..rules.monopoly import card

Emit = Callable[[dict], Awaitable[None]]
PLAYER_IDS = ("p1", "p2", "p3")


class Vision(ABC):
    @abstractmethod
    async def start(self, emit: Emit) -> None:
        """Begin watching; call `emit` for every read."""


class StubVision(Vision):
    """Reads come from the dev panel on the board page instead of a camera."""

    def __init__(self) -> None:
        self._emit: Emit | None = None

    async def start(self, emit: Emit) -> None:
        self._emit = emit

    async def dev_input(self, msg: dict) -> None:
        """msg: {"source": "vision", "type": ..., ...}. Raises ValueError if malformed."""
        kind = msg.get("type")
        if kind == "dice":
            value = int(msg.get("value", 0))
            if not 2 <= value <= 12:
                raise ValueError("dice total must be 2-12")
            event = {"type": "dice", "value": value}
        elif kind == "dice_not_visible":
            event = {"type": "dice_not_visible"}
        elif kind == "card":
            if card(msg.get("id", "")) is None:
                raise ValueError(f"unknown card: {msg.get('id')}")
            event = {"type": "card", "id": msg["id"]}
        elif kind == "player_card":
            if msg.get("player") not in PLAYER_IDS:
                raise ValueError("player card must be p1-p3")
            event = {"type": "player_card", "player": msg["player"]}
        else:
            raise ValueError(f"unknown vision event: {kind}")
        if self._emit is None:
            raise ValueError("vision not started")
        await self._emit(event)


class ArucoVision(Vision):
    """Real implementation: OpenCV + ArUco markers on dice tray, card spot, reader spot."""

    def __init__(self, camera_index: int = 0):
        raise NotImplementedError("webcam vision not built yet; run with --sim")

    async def start(self, emit: Emit) -> None:
        raise NotImplementedError
