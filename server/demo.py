"""The demo scenario: two players lined up by hand, then the game state "plays" a few moves on the real belt."""

# Square numbers are server/board.json ids: 1 = Black Forest, 3 = Gingerbread House, 5 = Geppetto's Toy Workshop,
# 6 = Stepmother's Manor, 7 = Crystal Ball (Chance).
DEMO_PLAYERS = [
    {"name": "Player 1", "kind": "in_person", "position": 5},    # Geppetto's Toy Workshop
    {"name": "Player 2", "kind": "remote", "position": 1},       # Black Forest
]
# Played in this order after the tokens are lined up: (player id, squares forward). It ends with Player 1 on square 6
# and Player 2 on square 3 (Gingerbread House), and Player 1 then rolls by camera.
DEMO_MOVES = [("p2", 2), ("p1", 1)]

# Player 2 (on the phone) takes the first turn. Their first two rolls are fixed at 2 and 2 (dice 1 and 1): from
# Gingerbread House (3) they land on Geppetto's Toy Workshop (5), then on Crystal Ball (Chance, 7). After that the
# rolls are real. (Two dice total at least 2, so 2 then 2 is the only way to reach square 7 in two rolls.)
DEMO_FIRST_PLAYER = "p2"
DEMO_FORCED_ROLLS = {"p2": [(1, 1), (1, 1)]}


def is_fresh_demo(state) -> bool:
    """True for a demo game nobody has played yet: Player 1 and 2 still on their lined-up squares, turn 1."""
    return ([p.name for p in state.players] == [d["name"] for d in DEMO_PLAYERS]
            and [p.position for p in state.players] == [d["position"] for d in DEMO_PLAYERS]
            and state.turn == 1 and state.phase.value == "WAITING_FOR_ROLL"
            and state.current_player.id == DEMO_FIRST_PLAYER)


def is_demo_game(state) -> bool:
    """True for any game made from DEMO_PLAYERS (played or not), found by its player names."""
    return [p.name for p in state.players] == [d["name"] for d in DEMO_PLAYERS]
