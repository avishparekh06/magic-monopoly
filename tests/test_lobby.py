import json

import pytest

from server.engine import SYSTEM, EngineError, Phase, apply_action, new_game, to_public

SPECS = [
    {"name": "Grandma", "kind": "in_person"},
    {"name": "Maya", "kind": "remote"},
    {"name": "Leo", "kind": "remote"},
]


def connect(state, pid):
    return apply_action(state, SYSTEM, {"action": "set_connected", "player": pid, "connected": True})


def test_lanes_assigned_in_order():
    s = new_game("g", SPECS)
    assert [(p.id, p.lane, p.name) for p in s.players] == [
        ("p1", 1, "Grandma"), ("p2", 2, "Maya"), ("p3", 3, "Leo")]
    assert s.phase == Phase.LOBBY
    assert all(p.balance == 1500 and p.position == 0 for p in s.players)


@pytest.mark.parametrize("specs", [
    SPECS[:1],                                             # alone
    SPECS + [{"name": "Extra", "kind": "remote"}],         # 4 players
    [SPECS[1], SPECS[0]],                                  # Grandma not first
    [SPECS[0], {"name": "", "kind": "remote"}],            # no name
    [SPECS[0], {"name": "Bot", "kind": "robot"}],          # bad kind
    [SPECS[0], {"name": "Bot", "kind": "ai"}],             # AI seats are out for v1
])
def test_bad_rosters_rejected(specs):
    with pytest.raises(EngineError):
        new_game("g", specs)


def test_several_players_at_the_table():
    s = new_game("g", [SPECS[0], {"name": "Two", "kind": "in_person"}, SPECS[2]])
    assert [p.kind for p in s.players] == ["in_person", "in_person", "remote"]
    assert [p.connected for p in s.players] == [True, True, False]   # table players need no phone


def test_host_ready_remote_players_not():
    s = new_game("g", SPECS)
    assert [p.connected for p in s.players] == [True, False, False]


def test_start_waits_for_every_remote_seat():
    s = new_game("g", SPECS)
    s, _ = connect(s, "p2")
    with pytest.raises(EngineError, match="Leo"):
        apply_action(s, SYSTEM, {"action": "start"})
    s, _ = connect(s, "p3")
    s2, events = apply_action(s, SYSTEM, {"action": "start"})
    assert s2.phase == Phase.WAITING_FOR_ROLL
    assert s2.current_player.id == "p1"
    assert [e["type"] for e in events] == ["game_started", "turn_started"]


def test_force_start():
    s, _ = apply_action(new_game("g", SPECS), SYSTEM, {"action": "start", "force": True})
    assert s.phase == Phase.WAITING_FOR_ROLL


def test_remote_cannot_start_or_fake_presence():
    s = new_game("g", SPECS)
    with pytest.raises(EngineError):
        apply_action(s, "p2", {"action": "start", "force": True})
    with pytest.raises(EngineError):
        apply_action(s, "p2", {"action": "set_connected", "player": "p3", "connected": True})


def test_cannot_start_twice():
    s, _ = apply_action(new_game("g", SPECS), SYSTEM, {"action": "start", "force": True})
    with pytest.raises(EngineError):
        apply_action(s, SYSTEM, {"action": "start", "force": True})


def test_apply_action_does_not_mutate_input():
    s = new_game("g", SPECS)
    connect(s, "p2")
    assert s.player("p2").connected is False


def test_reconnect_is_a_no_op():
    s, _ = connect(new_game("g", SPECS), "p2")
    _, events = connect(s, "p2")
    assert events == []


def test_unknown_and_early_actions_rejected():
    s = new_game("g", SPECS)
    with pytest.raises(EngineError, match="unknown"):
        apply_action(s, SYSTEM, {"action": "teleport"})
    with pytest.raises(EngineError, match="hasn't started"):
        apply_action(s, "p2", {"action": "roll"})


def test_public_state_is_json_and_has_no_tokens():
    d = to_public(new_game("g", SPECS))
    text = json.dumps(d)
    assert d["phase"] == "LOBBY" and d["current_player"] == "p1"
    assert "token" not in text


def test_attendance_can_change_without_recreating_players():
    original = new_game('g', SPECS)
    updated, events = apply_action(original, SYSTEM, {'action': 'set_attendance', 'target': 'p2', 'kind': 'in_person'})
    assert original.player('p2').kind == 'remote'
    assert updated.player('p2').kind == 'in_person' and updated.player('p2').connected
    assert events == [{'type': 'attendance_changed', 'player': 'p2', 'kind': 'in_person'}]
    updated, _ = apply_action(updated, SYSTEM, {'action': 'set_attendance', 'target': 'p2', 'kind': 'remote'})
    assert not updated.player('p2').connected
    assert [(p.id, p.lane, p.name, p.balance, p.position) for p in original.players] == [
        (p.id, p.lane, p.name, p.balance, p.position) for p in updated.players]
    with pytest.raises(EngineError, match='Maya'):
        apply_action(updated, SYSTEM, {'action': 'start'})


@pytest.mark.parametrize('actor,target,kind', [
    ('p2', 'p2', 'in_person'), (SYSTEM, 'p1', 'remote'),
    (SYSTEM, 'p9', 'in_person'), (SYSTEM, 'p2', 'invalid'),
])
def test_invalid_attendance_changes_leave_state_untouched(actor, target, kind):
    state = new_game('g', SPECS)
    snapshot = to_public(state)
    with pytest.raises(EngineError):
        apply_action(state, actor, {'action': 'set_attendance', 'target': target, 'kind': kind})
    assert to_public(state) == snapshot


def test_attendance_is_locked_after_start():
    state, _ = apply_action(new_game('g', SPECS), SYSTEM, {'action': 'start', 'force': True})
    with pytest.raises(EngineError, match='before the game starts'):
        apply_action(state, SYSTEM, {'action': 'set_attendance', 'target': 'p2', 'kind': 'in_person'})
