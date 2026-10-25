"""When the belt cannot move: the game stays put, and the host retries, confirms by hand, or cancels."""
import pytest

from server.engine import SYSTEM, EngineError, Phase, apply_action, new_game
from tests.test_store import PLAYERS


def moving():
    s = new_game("g", PLAYERS)
    for p in s.players:
        p.connected = True
    s, _ = apply_action(s, SYSTEM, {"action": "start"})
    s, _ = apply_action(s, SYSTEM, {"action": "dice", "value": 5})
    s, ev = apply_action(s, SYSTEM, {"action": "move_failed", "lane": 1, "reason": "unreachable"})
    assert s.phase == Phase.MOVING and s.player("p1").position == 0 and ev[0]["type"] == "move_failed"
    return s


def test_moved_by_hand_counts_as_move_done():
    s = moving()
    s, ev = apply_action(s, SYSTEM, {"action": "move_done", "lane": 1, "square": 5})
    assert s.player("p1").position == 5 and [e["type"] for e in ev][:2] == ["moved", "landed"]


def test_cancel_keeps_the_token_and_the_turn():
    s = moving()
    s, ev = apply_action(s, SYSTEM, {"action": "cancel_move"})
    assert s.phase == Phase.WAITING_FOR_ROLL and s.current_player.id == "p1"
    assert s.player("p1").position == 0 and s.pending is None and s.last_roll is None
    assert ev == [{"type": "move_cancelled", "player": "p1", "to": 5}]
    with pytest.raises(EngineError):
        apply_action(s, SYSTEM, {"action": "cancel_move"})          # nothing to cancel now
    with pytest.raises(EngineError):
        apply_action(moving(), "p1", {"action": "cancel_move"})     # host only


def test_retry_reissues_the_same_move():
    s = moving()
    s, ev = apply_action(s, SYSTEM, {"action": "retry_move"})
    assert ev == [{"type": "move", "player": "p1", "lane": 1, "from": 0, "to": 5}]


def test_cancel_of_a_curse_still_jails_and_ends_the_turn():
    s = new_game("g", PLAYERS)
    for p in s.players:
        p.connected = True
    s, _ = apply_action(s, SYSTEM, {"action": "start"})
    s, _ = apply_action(s, SYSTEM, {"action": "land", "square": 30})          # Cursed! -> moving to the tower
    assert s.phase == Phase.MOVING and s.pending.get("to_jail")
    s, ev = apply_action(s, SYSTEM, {"action": "cancel_move"})
    assert s.player("p1").in_jail and s.current_player.id == "p2"
    assert [e["type"] for e in ev][:2] == ["move_cancelled", "jailed"]


def test_cancel_of_a_card_move_ends_the_turn():
    s = new_game("g", PLAYERS)
    for p in s.players:
        p.connected = True
    s, _ = apply_action(s, SYSTEM, {"action": "start"})
    s, _ = apply_action(s, SYSTEM, {"action": "land", "square": 7})
    s, _ = apply_action(s, SYSTEM, {"action": "card", "id": "crystal_01"})     # advance to Mirror Hall
    assert s.phase == Phase.MOVING
    s, _ = apply_action(s, SYSTEM, {"action": "cancel_move"})
    assert s.current_player.id == "p2" and s.player("p1").position == 7
