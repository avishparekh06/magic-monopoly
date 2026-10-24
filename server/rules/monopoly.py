"""Monopoly rules, kept apart from the engine's state machine.

The engine owns phases, turn order and payments; this module answers
rule questions about squares (what happens on landing, how much rent is).
Swapping in a different game (e.g. a simpler cooperative one) means
writing another module with the same functions.

Engine-facing helpers: square data, rent, and card lookups.
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


BUYABLE = ("property", "railroad", "utility")


def is_buyable(sq: dict) -> bool:
    return sq["type"] in BUYABLE


def rent(index: int, owner_squares: list[int], dice_total: int) -> int:
    """Rent owed for landing on `index`, given every square its owner holds."""
    sq = square(index)
    b = board()
    if sq["type"] == "property":
        group = b["groups"][sq["group"]]["squares"]
        whole_group = all(i in owner_squares for i in group)
        return sq["rent"] * (2 if whole_group else 1)
    if sq["type"] == "railroad":
        owned = sum(1 for i in owner_squares if square(i)["type"] == "railroad")
        return b["railroad_rent"][owned - 1]
    if sq["type"] == "utility":
        owned = sum(1 for i in owner_squares if square(i)["type"] == "utility")
        return b["utility_multiplier"][owned - 1] * dice_total
    raise ValueError(f"square {index} has no rent")


def nearest(position: int, square_type: str) -> int:
    """The next square of `square_type` going forward from `position`."""
    for step in range(1, NUM_SQUARES + 1):
        i = (position + step) % NUM_SQUARES
        if square(i)["type"] == square_type:
            return i
    raise ValueError(f"no {square_type} on the board")
