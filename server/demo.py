"""The demo scenario: two players lined up by hand, then the game state "plays" a few moves on the real belt."""

# Square numbers are server/board.json ids: 1 = Black Forest, 3 = Gingerbread House.
DEMO_PLAYERS = [
    {"name": "Player 1", "kind": "in_person", "position": 3},    # Gingerbread House
    {"name": "Player 2", "kind": "remote", "position": 1},       # Black Forest
]
# Played in this order after the tokens are lined up: (player id, squares forward). It ends with Player 1 on square 6
# and Player 2 on square 4, and Player 1 then rolls by camera.
DEMO_MOVES = [("p2", 3), ("p1", 3)]
