"""Mirror: turns engine events into one in-character spoken line.

in:  an engine event + the current state
out: a line of text, or None if the event isn't worth announcing.
The board page speaks lines with the browser's speechSynthesis.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..engine import GameState


class Mirror(ABC):
    @abstractmethod
    async def line(self, event: dict, state: GameState) -> str | None:
        ...


class TemplateMirror(Mirror):
    """Stub: one template string per event type. More templates arrive in M5."""

    TEMPLATES = {
        "player_connected": "{name} has stepped through the looking glass!",
        "player_disconnected": "{name}'s reflection has faded... the link will bring them back.",
        "game_started": "Mirror, mirror, on the wall... welcome, {grandma}! Your family has come to your table. Let the tale begin!",
        "turn_started": "{name}, it is your turn.",
    }

    async def line(self, event: dict, state: GameState) -> str | None:
        template = self.TEMPLATES.get(event.get("type", ""))
        if template is None:
            return None
        names = {p.id: p.name for p in state.players}
        return template.format(
            name=names.get(event.get("player", ""), "Someone"),
            grandma=state.players[0].name,
        )


class MuseMirror(Mirror):
    """Real implementation: Meta Muse Spark LLM writes the line."""

    def __init__(self) -> None:
        raise NotImplementedError("Muse mirror not built yet; run with --sim")

    async def line(self, event: dict, state: GameState) -> str | None:
        raise NotImplementedError
