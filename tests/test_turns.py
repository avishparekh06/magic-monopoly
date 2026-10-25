"""Turn loop: rolling, moving, buying, payments and rent (milestone 3)."""
import pytest

from server.engine import SYSTEM, EngineError, Phase, apply_action, new_game

SPECS = [
    {"name": "Grandma", "kind": "in_person"},
    {"name": "Maya", "kind": "remote"},
    {"name": "Leo", "kind": "remote"},
]


class Dice:
    """Stand-in RNG: randint returns the queued values in order."""

    def __init__(self, *values):
        self.values = list(values)

    def randint(self, a, b):
        return self.values.pop(0)


def started():
    s, _ = apply_action(new_game("g", SPECS), SYSTEM, {"action": "start", "force": True})
    return s


def act(s, actor, action, rng=None, **kw):
    return apply_action(s, actor, {"action": action, **kw}, rng)


def types(events):
    return [e["type"] for e in events]


def finish_move(s):
    """What the move bridge does: report the token arrived."""
    p = s.player(s.pending["player"])
    return act(s, SYSTEM, "move_done", lane=p.lane, square=s.pending["to"])


def grandma_rolls(s, total):
    s, _ = act(s, SYSTEM, "dice", value=total)
    return finish_move(s)


def kid_rolls(s, pid, d1, d2):
    s, _ = act(s, pid, "roll", Dice(d1, d2))
    return finish_move(s)


def skip_turn(s):
    """Advance to the next player without touching the board."""
    s.current = (s.current + 1) % len(s.players)
    return s


def at_turn(s, pid):
    s.current = next(i for i, p in enumerate(s.players) if p.id == pid)
    return s


# --- rolling --------------------------------------------------------------

def test_grandma_camera_roll_moves_and_waits_for_move_done():
    s, events = act(started(), SYSTEM, "dice", value=6)
    assert s.phase == Phase.MOVING
    assert events[-1] == {"type": "move", "player": "p1", "lane": 1, "from": 0, "to": 6}
    assert s.player("p1").position == 0  # not moved until move_done


def test_engine_refuses_to_resolve_before_move_done():
    s, _ = act(started(), SYSTEM, "dice", value=6)           # heading to Gingerbread House
    with pytest.raises(EngineError, match="still moving"):
        act(s, SYSTEM, "decision", player="p1", answer="yes")
    with pytest.raises(EngineError):
        act(s, SYSTEM, "dice", value=4)                       # second read ignored
    with pytest.raises(EngineError, match="expected 6"):
        act(s, SYSTEM, "move_done", lane=1, square=7)
    with pytest.raises(EngineError, match="lane"):
        act(s, SYSTEM, "move_done", lane=2, square=6)
    with pytest.raises(EngineError):
        act(s, "p2", "move_done", lane=1, square=6)           # only the bridge reports moves
    s, events = act(s, SYSTEM, "move_done", lane=1, square=6)
    assert s.phase == Phase.WAITING_FOR_DECISION
    assert s.pending == {"kind": "decision", "type": "buy", "player": "p1", "square": 6, "price": 100}


def test_dice_not_visible_keeps_waiting_for_roll():
    s, events = act(started(), SYSTEM, "dice_not_visible")
    assert s.phase == Phase.WAITING_FOR_ROLL and types(events) == ["dice_not_visible"]


def test_grandma_cannot_use_roll_button_but_anyone_may_use_the_table_dice():
    with pytest.raises(EngineError, match="real dice"):
        act(started(), "p1", "roll")
    s = skip_turn(started())                      # p2 (remote) may roll the real dice too
    s, events = act(s, SYSTEM, "dice", value=7)
    assert s.phase == Phase.MOVING and events[-1]["lane"] == 2 and events[-1]["to"] == 7


def test_not_your_turn():
    with pytest.raises(EngineError, match="not your turn"):
        act(started(), "p2", "roll", Dice(1, 2))


def test_kid_roll_uses_injected_dice():
    s, events = act(skip_turn(started()), "p2", "roll", Dice(2, 3))
    assert s.last_roll == 5 and s.last_dice == [2, 3]
    assert events[-1]["to"] == 5 and events[-1]["lane"] == 2


def test_move_failed_then_retry():
    s, _ = act(started(), SYSTEM, "dice", value=6)
    s, events = act(s, SYSTEM, "move_failed", lane=1, reason="timeout")
    assert s.phase == Phase.MOVING and types(events) == ["move_failed"]
    s, events = act(s, SYSTEM, "retry_move")
    assert events == [{"type": "move", "player": "p1", "lane": 1, "from": 0, "to": 6}]


def test_passing_go_pays_salary():
    s = started()
    s.player("p1").position = 36
    s, events = grandma_rolls(s, 6)                            # 36 -> 2, wraps
    assert s.player("p1").position == 2
    assert {"type": "salary", "player": "p1", "amount": 200} in events
    assert s.player("p1").balance == 1700


def test_moves_wrap_forward_in_move_command():
    s = started()
    s.player("p1").position = 38
    _, events = act(s, SYSTEM, "dice", value=4)
    assert events[-1]["from"] == 38 and events[-1]["to"] == 2


# --- buying ---------------------------------------------------------------

def test_grandma_buys_by_voice_then_card_tap():
    s, _ = grandma_rolls(started(), 6)
    s, events = act(s, SYSTEM, "decision", player="p1", answer="yes")
    assert s.phase == Phase.WAITING_FOR_PAYMENT
    assert s.pending["payer"] == "p1" and s.pending["amount"] == 100
    assert s.player("p1").balance == 1500                      # nothing paid until she taps
    s, events = act(s, SYSTEM, "player_card", player="p1")
    assert s.player("p1").balance == 1400 and 6 in s.player("p1").properties
    assert "bought" in types(events)
    assert s.current_player.id == "p2" and s.phase == Phase.WAITING_FOR_ROLL


def test_player_card_from_wrong_player_refused():
    s, _ = grandma_rolls(started(), 6)
    s, _ = act(s, SYSTEM, "decision", player="p1", answer="yes")
    with pytest.raises(EngineError, match="Maya's card"):
        act(s, SYSTEM, "player_card", player="p2")
    assert s.phase == Phase.WAITING_FOR_PAYMENT


def test_player_card_when_nobody_owes_refused():
    with pytest.raises(EngineError, match="nobody needs to pay"):
        act(started(), SYSTEM, "player_card", player="p1")


def test_card_tap_applied_once():
    s, _ = grandma_rolls(started(), 6)
    s, _ = act(s, SYSTEM, "decision", player="p1", answer="yes")
    s, _ = act(s, SYSTEM, "player_card", player="p1")
    with pytest.raises(EngineError):
        act(s, SYSTEM, "player_card", player="p1")
    assert s.player("p1").balance == 1400


def test_confirm_payment_fallback():
    s, _ = grandma_rolls(started(), 6)
    s, _ = act(s, SYSTEM, "decision", player="p1", answer="yes")
    with pytest.raises(EngineError):
        act(s, "p2", "confirm_payment")
    s, _ = act(s, SYSTEM, "confirm_payment")
    assert s.player("p1").balance == 1400


def test_grandma_declines():
    s, _ = grandma_rolls(started(), 6)
    s, events = act(s, SYSTEM, "decision", player="p1", answer="no")
    assert s.player("p1").properties == [] and s.current_player.id == "p2"


def test_kid_buy_is_paid_automatically():
    s, _ = kid_rolls(skip_turn(started()), "p2", 1, 2)        # Three Bears' Den, 60
    with pytest.raises(EngineError, match="Maya's decision"):
        act(s, "p3", "buy", square=3)
    s, events = act(s, "p2", "buy", square=3)
    assert s.player("p2").balance == 1440 and s.player("p2").properties == [3]
    assert "payment_requested" not in types(events)


def test_buy_wrong_square_refused():
    s, _ = kid_rolls(skip_turn(started()), "p2", 1, 2)
    with pytest.raises(EngineError, match="not the place"):
        act(s, "p2", "buy", square=1)


def test_cant_afford_skips_offer():
    s = skip_turn(started())
    s.player("p2").balance = 50
    s, events = kid_rolls(s, "p2", 1, 2)
    assert "cant_afford" in types(events) and s.current_player.id == "p3"


# --- rent -------------------------------------------------------------------

def test_rent_kid_pays_grandma_automatically():
    s = at_turn(started(), "p2")
    s.player("p1").properties = [6]                            # Gingerbread House, rent 6
    s, events = kid_rolls(s, "p2", 2, 4)
    paid = next(e for e in events if e["type"] == "paid")
    assert paid == {"type": "paid", "from": "p2", "to": "p1", "amount": 6, "reason": "rent", "square": 6}
    assert s.player("p1").balance == 1506 and s.player("p2").balance == 1494
    assert s.current_player.id == "p3"


def test_rent_grandma_pays_kid_after_card_tap():
    s = started()
    s.player("p2").properties = [6]
    s, events = grandma_rolls(s, 6)
    assert s.phase == Phase.WAITING_FOR_PAYMENT
    assert s.pending == {"kind": "payment", "payer": "p1", "payee": "p2", "amount": 6,
                         "reason": "rent", "square": 6}
    s, _ = act(s, SYSTEM, "player_card", player="p1")
    assert s.player("p1").balance == 1494 and s.player("p2").balance == 1506


def test_color_group_doubles_rent():
    s = at_turn(started(), "p2")
    s.player("p1").properties = [6, 8, 9]                      # whole light-blue group
    s, _ = kid_rolls(s, "p2", 2, 4)
    assert s.player("p1").balance == 1512


def test_railroad_rent_scales_with_count():
    s = at_turn(started(), "p2")
    s.player("p3").properties = [5, 15]                        # two rides -> 50
    s, _ = kid_rolls(s, "p2", 2, 3)
    assert s.player("p3").balance == 1550


def test_utility_rent_uses_dice():
    s = at_turn(started(), "p2")
    s.player("p2").position = 5
    s.player("p3").properties = [12]                           # one utility -> 4x dice
    s, _ = kid_rolls(s, "p2", 3, 4)
    assert s.player("p3").balance == 1500 + 28


def test_own_property_is_free():
    s = at_turn(started(), "p2")
    s.player("p2").properties = [6]
    s, events = kid_rolls(s, "p2", 2, 4)
    assert "paid" not in types(events) and s.player("p2").balance == 1500


def test_bankrupt_on_rent_hands_over_everything():
    s = at_turn(started(), "p2")
    s.player("p1").properties = [39]                           # Crown Jewel Vault, rent 50
    s.player("p2").position = 33
    s.player("p2").balance = 30
    s.player("p2").properties = [1]
    s, events = kid_rolls(s, "p2", 3, 3)
    assert s.player("p2").bankrupt and s.player("p2").properties == []
    assert s.player("p1").balance == 1530
    assert "bankrupt" in types(events) and s.current_player.id == "p3"


def test_bankruptcy_skips_turn_and_ends_game():
    specs = SPECS[:2]
    s, _ = apply_action(new_game("g", specs), SYSTEM, {"action": "start", "force": True})
    s.player("p2").properties = [6]
    s.player("p1").balance = 3
    s, events = grandma_rolls(s, 6)                            # can't cover 6: no tap, automatic
    assert s.phase == Phase.GAME_OVER and s.winner == "p2"
    assert "payment_requested" not in types(events)


@pytest.mark.parametrize('pid', ['p1', 'p2'])
def test_shared_board_roll_uses_two_server_dice_for_current_player(pid):
    state = at_turn(started(), pid)
    updated, events = act(state, SYSTEM, 'board_roll', Dice(2, 3), target=pid, turn=state.turn,
                          value=12, dice=[6, 6])
    assert updated.last_dice == [2, 3] and updated.last_roll == 5
    assert updated.pending['to'] == 5
    assert events[0] == {'type': 'rolled', 'player': pid, 'value': 5, 'dice': [2, 3]}
    assert state.last_roll is None and state.phase == Phase.WAITING_FOR_ROLL


def test_shared_board_roll_rejects_stale_turns_and_player_impersonation():
    state = started()
    for actor, target, turn in [('p2', 'p1', state.turn), (SYSTEM, 'p2', state.turn),
                                (SYSTEM, 'p1', state.turn - 1), (SYSTEM, 'p1', None)]:
        with pytest.raises(EngineError):
            act(state, actor, 'board_roll', Dice(), target=target, turn=turn)
    moving, _ = act(state, SYSTEM, 'board_roll', Dice(2, 3), target='p1', turn=state.turn)
    with pytest.raises(EngineError):
        act(moving, SYSTEM, 'board_roll', Dice(), target='p1', turn=state.turn)
    offered, _ = finish_move(moving)
    next_turn, _ = act(offered, 'p1', 'decline')
    assert next_turn.phase == Phase.WAITING_FOR_ROLL
    with pytest.raises(EngineError, match='different turn'):
        act(next_turn, SYSTEM, 'board_roll', Dice(), target='p1', turn=state.turn)
