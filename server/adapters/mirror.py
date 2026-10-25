"""Mirror: turns engine events into one in-character spoken line.

in:  an engine event + the current state
out: a line of text, or None if the event isn't worth announcing.
The board page speaks lines with the browser's speechSynthesis.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..engine import BANK, GameState
from ..rules.monopoly import card, cards, square


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
        return f"{p.name}, it's your turn! Roll the dice into the tray, or tap Roll on your phone."

    def _dice_not_visible(self, e, s):
        return f"{self._name(s, e['player'])}, I couldn't quite see the dice. Please roll again!"

    def _rolled(self, e, s):
        return f"{self._name(s, e['player'])} rolled {e['value']}!"

    def _move_failed(self, e, s):
        return f"Oh dear, {self._name(s, e['player'])}'s token got stuck. Let me give it a nudge..."

    def _salary(self, e, s):
        return (f"{self._name(s, e['player'])} passes Once Upon a Time and collects {e['amount']} gold!"
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
        if e["answer"] == "no" and e["about"] == "buy":
            return f"{self._name(s, e['player'])} lets {square(e['square'])['name']} wait for another day."
        return None  # the payment (or staying-in-the-tower) line says the rest

    def _payment_requested(self, e, s):
        payer = self._name(s, e["payer"])
        reason, amount = e["reason"], e["amount"]
        if reason == "buy":
            what = f"{amount} gold for {square(e['square'])['name']}"
        elif reason == "tax":
            what = f"the {square(e['square'])['name']} of {amount} gold"
        elif reason == "jail_fine":
            what = f"{amount} gold to leave the tower"
        else:
            what = f"{amount} gold to {self._name(s, e['payee'])}"
        return f"{payer}, please lay your card on the reader to pay {what}."

    def _paid(self, e, s):
        payer, payee = self._name(s, e["from"]), self._name(s, e["to"])
        reason, amount = e["reason"], e["amount"]
        if e["from"] == BANK:
            text = f"{payee} collects {amount} gold!"
        elif reason == "buy":
            text = f"{payer} bought {square(e['square'])['name']} for {amount} gold!"
        elif reason == "rent":
            text = f"{payer} paid {payee} {amount} gold in rent for {square(e['square'])['name']}."
        elif reason == "tax":
            text = f"{payer} paid the {square(e['square'])['name']} of {amount} gold."
        elif reason == "jail_fine":
            text = f"{payer} paid {amount} gold to escape the tower."
        else:
            text = f"{payer} paid {payee} {amount} gold."
        return text + self._grandma_balance(s, e["from"], e["to"])

    # --- cards ---
    def _card_requested(self, e, s):
        grandma = s.players[0]
        article = f"a {cards()['decks'][e['deck']]['name']} card"
        for_whom = "for yourself" if e["player"] == grandma.id else f"for {self._name(s, e['player'])}"
        return f"{grandma.name}, please draw {article} {for_whom} and lay it on the card spot."

    def _card_drawn(self, e, s):
        c = card(e["id"])
        return f"{c['title']}! {c['text']}"

    def _repairs(self, e, s):
        who = self._name(s, e["player"])
        if not e["amount"]:
            return f"Repairs are due, but {who} owns no cottages or castles. Nothing to pay!"
        return f"{who} owes {e['amount']} gold in repairs."

    def _got_jail_key(self, e, s):
        return f"{self._name(s, e['player'])} tucks the key away for later."

    # --- the tower ---
    def _cursed(self, e, s):
        return f"Cursed! {self._name(s, e['player'])} is whisked away to Rapunzel's Tower!"

    def _jailed(self, e, s):
        return f"{self._name(s, e['player'])} is locked in Rapunzel's Tower."

    def _jail_offer(self, e, s):
        p = s.player(e["player"])
        ask = "Just say yes or no." if p.kind == "in_person" else ""
        return f"{p.name}, would you like to pay {e['price']} gold to leave the tower? {ask}".strip()

    def _stayed_in_jail(self, e, s):
        return f"{self._name(s, e['player'])} waits in the tower a little longer."

    def _must_pay_fine(self, e, s):
        return f"{self._name(s, e['player'])} has waited three turns, so it's {e['amount']} gold to leave."

    def _released(self, e, s):
        p = s.player(e["player"])
        roll = "roll the dice into the tray" if p.kind == "in_person" else "roll"
        return f"The tower door creaks open! {p.name}, {roll}."

    def _used_jail_key(self, e, s):
        return f"{self._name(s, e['player'])} uses the magic key and slips out of the tower!"

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
