"""The demo scenario: two players lined up by hand, then the game state "plays" a few moves on the real belt."""

# Square numbers are server/board.json ids: 1 = Black Forest, 3 = Gingerbread House, 5 = Geppetto's Toy Workshop,
# 7 = Crystal Ball (Chance).
DEMO_PLAYERS = [
    {"name": "Player 1", "kind": "in_person", "position": 3},    # Gingerbread House
    {"name": "Player 2", "kind": "remote", "position": 1},       # Black Forest
]
# Nothing moves when the demo game opens: the tokens are lined up by hand on those squares and the belt is told so.
DEMO_MOVES: list[tuple[str, int]] = []
DEMO_FIRST_PLAYER = "p1"
# The first roll of each player is fixed, so the demo goes to the same places every time:
#   Player 1 clicks Roll Dice on the board: 1 + 1 = 2, from Gingerbread House (3) to Geppetto's Toy Workshop (5).
#   Player 2 rolls on the phone: 2 + 4 = 6, from Black Forest (1) to Crystal Ball (Chance, 7).
# After that Player 1 rolls the real dice (the camera reads them), and every later roll is real.
DEMO_FORCED_ROLLS = {"p1": [(1, 1)], "p2": [(2, 4)]}


def is_fresh_demo(state) -> bool:
    """True for a demo game nobody has played yet: Player 1 and 2 still on their lined-up squares, turn 1."""
    return ([p.name for p in state.players] == [d["name"] for d in DEMO_PLAYERS]
            and [p.position for p in state.players] == [d["position"] for d in DEMO_PLAYERS]
            and state.turn == 1 and state.phase.value == "WAITING_FOR_ROLL"
            and state.current_player.id == DEMO_FIRST_PLAYER)


def is_demo_game(state) -> bool:
    """True for any game made from DEMO_PLAYERS (played or not), found by its player names."""
    return [p.name for p in state.players] == [d["name"] for d in DEMO_PLAYERS]
