"""The demo scenario: two players lined up by hand, then the game state "plays" a few moves on the real belt."""

# Square numbers are server/board.json ids: 3 = Gingerbread House, 4 = Rumpelstiltskin's Bargain,
# 5 = Geppetto's Toy Workshop, 6 = Stepmother's Manor, 7 = Crystal Ball (Chance).
DEMO_PLAYERS = [
    {"name": "Player 1", "kind": "in_person", "position": 5},    # Geppetto's Toy Workshop
    {"name": "Player 2", "kind": "remote", "position": 3},       # Gingerbread House
]
# Played in this order after the tokens are lined up: (player id, squares forward). It ends with Player 1 on square 6
# and Player 2 on square 4, and Player 1 then rolls by camera.
DEMO_MOVES = [("p2", 1), ("p1", 1)]

# Player 2's first roll is fixed: dice 1 and 2, so from square 4 it lands on Crystal Ball (Chance, square 7).
DEMO_FORCED_ROLLS = {"p2": [(1, 2)]}


def is_fresh_demo(state) -> bool:
    """True for a demo game nobody has played yet: Player 1 and 2 still on their lined-up squares, turn 1."""
    return ([p.name for p in state.players] == [d["name"] for d in DEMO_PLAYERS]
            and [p.position for p in state.players] == [d["position"] for d in DEMO_PLAYERS]
            and state.turn == 1 and state.phase.value == "WAITING_FOR_ROLL")
