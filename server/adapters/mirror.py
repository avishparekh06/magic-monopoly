"""Mirror: turns engine events into one in-character spoken line.

in:  an engine event + the current state
out: a line of text, or None if the event isn't worth announcing.
The board page speaks lines with the browser's speechSynthesis.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..engine import BANK, GameState
from ..rules.monopoly import square


class Mirror(ABC):
    @abstractmethod
    async def line(self, event: dict, state: GameState) -> str | None:
        ...


class TemplateMirror(Mirror):
    """Stub: one template per event type. Grandma never needs a screen, so
    every payment touching her also says her new balance."""

    async def line(self, event: dict, state: GameState) -> str | None:
        kind = event.get("type", "")
        method = getattr(self, f"_{kind}", None)
        return method(event, state) if method else None

    # --- helpers ---
    @staticmethod
    def _name(state: GameState, pid: str) -> str:
        if pid == BANK:
            return "the bank"
        return next((p.name for p in state.players if p.id == pid), "Someone")

    @staticmethod
    def _grandma_balance(state: GameState, *pids: str) -> str:
        g = state.players[0]
        return f" {g.name}, you now have {g.balance} gold." if g.id in pids else ""

    # --- lobby ---
    def _player_connected(self, e, s):
        return f"{self._name(s, e['player'])} has stepped through the looking glass!"

    def _player_disconnected(self, e, s):
        return f"{self._name(s, e['player'])}'s reflection has faded... the link will bring them back."

    def _game_started(self, e, s):
        return (f"Mirror, mirror, on the wall... welcome, {s.players[0].name}! "
                "Your family has come to your table. Let the tale begin!")

    # --- turns ---
    def _turn_started(self, e, s):
        p = s.player(e["player"])
        if p.kind == "in_person":
            return f"{p.name}, it's your turn. Roll the dice into the tray!"
        return f"{p.name}, it's your turn!"

    def _dice_not_visible(self, e, s):
        return f"{self._name(s, e['player'])}, I couldn't quite see the dice. Please roll again!"

    def _rolled(self, e, s):
        return f"{self._name(s, e['player'])} rolled {e['value']}!"

    def _move_failed(self, e, s):
        return f"Oh dear, {self._name(s, e['player'])}'s token got stuck. Let me give it a nudge..."

    def _salary(self, e, s):
        return (f"{self._name(s, e['player'])} passes the Castle Gate and collects {e['amount']} gold!"
                + self._grandma_balance(s, e["player"]))

    def _landed(self, e, s):
        return f"{self._name(s, e['player'])} arrives at {square(e['square'])['name']}."

    def _offer(self, e, s):
        p = s.player(e["player"])
        place = square(e["square"])["name"]
        if p.kind == "in_person":
            return f"{p.name}, would you like to buy {place} for {e['price']} gold? Just say yes or no."
        return f"{p.name}, would you like to buy {place} for {e['price']} gold?"

    def _cant_afford(self, e, s):
        return f"{self._name(s, e['player'])} can't afford {square(e['square'])['name']} just yet."

    def _decided(self, e, s):
        if e["answer"] == "no":
            return f"{self._name(s, e['player'])} lets {square(e['square'])['name']} wait for another day."
        return None  # the payment line says the rest

    def _payment_requested(self, e, s):
        payer = self._name(s, e["payer"])
        if e["reason"] == "buy":
            what = f"{e['amount']} gold for {square(e['square'])['name']}"
        else:
            what = f"{e['amount']} gold to {self._name(s, e['payee'])}"
        return f"{payer}, please tap your card on the reader to pay {what}."

    def _paid(self, e, s):
        payer, payee = self._name(s, e["from"]), self._name(s, e["to"])
        if e["reason"] == "buy":
            text = f"{payer} bought {square(e['square'])['name']} for {e['amount']} gold!"
        elif e["reason"] == "rent":
            text = f"{payer} paid {payee} {e['amount']} gold in rent for {square(e['square'])['name']}."
        else:
            text = f"{payer} paid {payee} {e['amount']} gold."
        return text + self._grandma_balance(s, e["from"], e["to"])

    def _bankrupt(self, e, s):
        return f"{self._name(s, e['player'])} has run out of gold and bows out of the tale."

    def _game_over(self, e, s):
        return f"The tale is told! {self._name(s, e['winner'])} rules the kingdom!"


class MuseMirror(Mirror):
    """Real implementation: Meta Muse Spark LLM writes the line."""

    def __init__(self) -> None:
        raise NotImplementedError("Muse mirror not built yet; run with --sim")

    async def line(self, event: dict, state: GameState) -> str | None:
        raise NotImplementedError
