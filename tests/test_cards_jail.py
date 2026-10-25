"""Cards, taxes and Rapunzel's Tower (milestone 4)."""
import pytest

from server.engine import SYSTEM, EngineError, Phase
from tests.test_turns import Dice, act, at_turn, finish_move, grandma_rolls, kid_rolls, started, types

SPELLS, TITHE, CURSED, TOWER = 7, 4, 30, 10


def maya_on_spells():
    """Maya (p2) rolls from 4 onto a Crystal Ball square; Grandma is asked to draw."""
    s = at_turn(started(), "p2")
    s.player("p2").position = 4
    s, events = kid_rolls(s, "p2", 1, 2)
    return s, events


def read(s, card_id, actor=SYSTEM):
    return act(s, actor, "card", id=card_id)


# --- drawing --------------------------------------------------------------

def test_landing_on_spells_asks_grandma_to_draw_for_the_kid():
    s, events = maya_on_spells()
    assert s.phase == Phase.WAITING_FOR_CARD
    assert s.pending == {"kind": "card", "deck": "crystal", "for": "p2"}
    assert {"type": "card_requested", "player": "p2", "deck": "crystal"} in events


def test_camera_card_read_applies_effect_once():
    s, _ = maya_on_spells()
    s, events = read(s, "crystal_08")                            # Goblin Gold: collect 50
    assert s.player("p2").balance == 1550
    assert s.last_card == {"id": "crystal_08", "for": "p2"}
    assert "card_drawn" in types(events) and s.current_player.id == "p3"
    with pytest.raises(EngineError, match="nobody is drawing"):
        read(s, "crystal_08")


def test_wrong_deck_refused():
    s, _ = maya_on_spells()
    with pytest.raises(EngineError, match="Chest of Spells card"):
        read(s, "chest_02")
    with pytest.raises(EngineError, match="unknown card"):
        read(s, "crystal_99")


def test_player_card_is_not_a_game_card():
    s, _ = maya_on_spells()
    with pytest.raises(EngineError, match="nobody needs to pay"):
        act(s, SYSTEM, "player_card", player="p1")


def test_kid_fallback_picker_only_for_the_drawer():
    s, _ = maya_on_spells()
    with pytest.raises(EngineError, match="for Maya"):
        read(s, "crystal_08", actor="p3")
    with pytest.raises(EngineError, match="camera"):            # the drawer may pick only after the camera gives up
        read(s, "crystal_08", actor="p2")
    s, ev = act(s, SYSTEM, "card_unreadable")
    assert ev == [{"type": "card_unreadable", "player": "p2", "deck": "crystal"}]
    s, _ = read(s, "crystal_08", actor="p2")
    assert s.player("p2").balance == 1550


# --- card effects ---------------------------------------------------------

def test_advance_to_once_upon_a_time_moves_and_pays_salary():
    s, _ = maya_on_spells()
    s, events = read(s, "crystal_02")
    assert s.phase == Phase.MOVING and events[-1]["to"] == 0
    s, events = finish_move(s)
    assert s.player("p2").position == 0 and s.player("p2").balance == 1700


def test_go_back_three_travels_forward_without_salary_and_pays_tax():
    s, _ = maya_on_spells()
    s, events = read(s, "crystal_10")                            # back 3: 7 -> 4 (King's Tithe)
    assert events[-1] == {"type": "move", "player": "p2", "lane": 2, "from": SPELLS, "to": TITHE}
    s, events = finish_move(s)
    assert "salary" not in types(events)
    assert s.player("p2").balance == 1300                      # paid the 200 tithe automatically


def test_nearest_ride_pays_double_rent():
    s, _ = maya_on_spells()
    s.player("p3").properties = [15]                           # Pumpkin Coach
    s, _ = read(s, "crystal_05")
    assert s.pending["to"] == 15
    s, _ = finish_move(s)
    assert s.player("p3").balance == 1550                      # 25 x 2


def test_nearest_well_pays_ten_times_dice():
    s, _ = maya_on_spells()                                    # rolled 1 + 2 = 3
    s.player("p1").properties = [12]
    s, _ = read(s, "crystal_07")
    s, _ = finish_move(s)
    assert s.player("p2").balance == 1500 - 30
    assert s.player("p1").balance == 1530


def test_collect_from_each_waits_for_grandmas_tap_then_others_auto():
    s = at_turn(started(), "p2")
    s.player("p2").position = 0
    s, _ = kid_rolls(s, "p2", 1, 1)                            # Chest of Spells at 2
    s, events = read(s, "chest_09")                            # birthday: 10 from each
    assert s.phase == Phase.WAITING_FOR_PAYMENT and s.pending["payer"] == "p1"
    s, events = act(s, SYSTEM, "player_card", player="p1")
    assert s.player("p2").balance == 1520
    assert s.player("p1").balance == 1490 and s.player("p3").balance == 1490
    assert s.current_player.id == "p3" and s.payment_queue == []


def test_grandma_pays_each_with_one_tap_per_payment():
    s = started()
    s.player("p1").position = 4
    s, _ = grandma_rolls(s, 3)                                 # Crystal Ball
    s, _ = read(s, "crystal_15")                                 # pay each player 50
    assert s.pending["payee"] == "p2"
    s, _ = act(s, SYSTEM, "player_card", player="p1")
    assert s.pending["payee"] == "p3"
    s, _ = act(s, SYSTEM, "player_card", player="p1")
    assert s.player("p1").balance == 1400 and s.current_player.id == "p2"


def test_jail_key_card_is_kept():
    s, _ = maya_on_spells()
    s, events = read(s, "crystal_09")
    assert s.player("p2").jail_free_cards == 1 and "got_jail_key" in types(events)


def test_go_to_tower_card():
    s, _ = maya_on_spells()
    s, _ = read(s, "crystal_11")
    assert s.pending["to"] == TOWER
    s, events = finish_move(s)
    assert s.player("p2").in_jail and s.player("p2").position == TOWER
    assert "salary" not in types(events) and s.current_player.id == "p3"


def test_bankrupt_while_paying_birthday_money_skips_later_payments():
    s = at_turn(started(), "p2")
    s.player("p2").position = 0
    s.player("p3").balance = 5
    s, _ = kid_rolls(s, "p2", 1, 1)
    s, _ = read(s, "chest_09")
    s, events = act(s, SYSTEM, "player_card", player="p1")
    assert s.player("p3").bankrupt and s.player("p2").balance == 1515
    assert s.current_player.id == "p1"                        # Leo is skipped


# --- taxes ----------------------------------------------------------------

def test_grandma_taps_to_pay_tax():
    s, events = grandma_rolls(started(), 4)                    # King's Tithe
    assert s.phase == Phase.WAITING_FOR_PAYMENT
    assert s.pending["payee"] == "bank" and s.pending["amount"] == 200
    s, _ = act(s, SYSTEM, "player_card", player="p1")
    assert s.player("p1").balance == 1300


def test_dragons_toll_kid_pays_automatically():
    s = at_turn(started(), "p2")
    s.player("p2").position = 35
    s, _ = kid_rolls(s, "p2", 1, 2)
    assert s.player("p2").balance == 1400


# --- Cursed! and Rapunzel's Tower -------------------------------------------

def jail_maya():
    s = at_turn(started(), "p2")
    s.player("p2").position = 25
    s, events = kid_rolls(s, "p2", 2, 3)                       # 25 -> 30 Cursed!
    assert "cursed" in types(events) and s.phase == Phase.MOVING
    assert events[-1] == {"type": "move", "player": "p2", "lane": 2, "from": CURSED, "to": TOWER}
    s, events = finish_move(s)                                 # 30 -> 10 wraps but pays no salary
    assert "salary" not in types(events) and s.player("p2").in_jail
    return at_turn(s, "p1")


def come_back_to(s, pid):
    """Run the turn order until it's `pid`'s turn again (others' turns are skipped)."""
    s = at_turn(s, {"p2": "p1", "p3": "p2", "p1": "p3"}[pid])
    s.pending = None
    s.phase = Phase.WAITING_FOR_ROLL
    from server.engine import _end_turn
    events = _end_turn(s)
    return s, events


def test_cursed_sends_to_tower_without_salary():
    s = jail_maya()
    assert s.player("p2").balance == 1500 and s.player("p2").position == TOWER


def test_in_tower_offered_fine_and_can_stay():
    s, events = come_back_to(jail_maya(), "p2")
    assert s.phase == Phase.WAITING_FOR_DECISION and s.pending["type"] == "jail"
    assert {"type": "jail_offer", "player": "p2", "price": 50} in events
    with pytest.raises(EngineError):
        act(s, "p2", "roll", Dice(1, 1))
    s, events = act(s, "p2", "decline")
    assert s.player("p2").in_jail and s.player("p2").jail_turns == 1
    assert s.current_player.id == "p3"


def test_paying_fine_releases_and_rolls_same_turn():
    s, _ = come_back_to(jail_maya(), "p2")
    s, events = act(s, "p2", "buy")
    assert "released" in types(events)
    assert not s.player("p2").in_jail and s.player("p2").balance == 1450
    assert s.phase == Phase.WAITING_FOR_ROLL and s.current_player.id == "p2"
    s, _ = act(s, "p2", "roll", Dice(1, 2))
    assert s.pending["from"] == TOWER


def test_third_turn_in_tower_must_pay():
    s = jail_maya()
    s.player("p2").jail_turns = 2
    s, events = come_back_to(s, "p2")
    assert "must_pay_fine" in types(events)
    assert not s.player("p2").in_jail and s.player("p2").balance == 1450
    assert s.phase == Phase.WAITING_FOR_ROLL


def test_grandma_pays_fine_by_voice_and_tap():
    s = started()
    s.player("p1").in_jail, s.player("p1").position = True, TOWER
    s, events = come_back_to(s, "p1")
    assert s.pending["type"] == "jail"
    s, _ = act(s, SYSTEM, "decision", player="p1", answer="yes")
    assert s.phase == Phase.WAITING_FOR_PAYMENT and s.pending["reason"] == "jail_fine"
    s, _ = act(s, SYSTEM, "player_card", player="p1")
    assert not s.player("p1").in_jail and s.phase == Phase.WAITING_FOR_ROLL
    assert s.current_player.id == "p1"


def test_jail_key_used_automatically():
    s = jail_maya()
    s.player("p2").jail_free_cards = 1
    s, events = come_back_to(s, "p2")
    assert "used_jail_key" in types(events)
    assert not s.player("p2").in_jail and s.phase == Phase.WAITING_FOR_ROLL


def test_visiting_the_tower_is_harmless():
    s, events = grandma_rolls(started(), 10)
    assert not s.player("p1").in_jail and s.current_player.id == "p2"
