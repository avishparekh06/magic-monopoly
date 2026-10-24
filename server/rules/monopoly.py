"""Monopoly rules, kept apart from the engine's state machine.

The engine owns phases, turn order and payments; this module answers
rule questions about squares (what happens on landing, how much rent is).
Swapping in a different game (e.g. a simpler cooperative one) means
writing another module with the same functions.

Milestone 1-2: data loading only. Landing/rent/purchase logic lands in M3-4.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent
NUM_SQUARES = 40


@lru_cache(maxsize=1)
def board() -> dict:
    with open(DATA_DIR / "board.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def cards() -> dict:
    with open(DATA_DIR / "cards.json", encoding="utf-8") as f:
        return json.load(f)


def square(index: int) -> dict:
    return board()["squares"][index]


def card(card_id: str) -> dict | None:
    return next((c for c in cards()["cards"] if c["id"] == card_id), None)


def steps_between(frm: int, to: int) -> int:
    """Squares travelled going forward from `frm` to `to`, wrapping past 39."""
    return (to - frm) % NUM_SQUARES
