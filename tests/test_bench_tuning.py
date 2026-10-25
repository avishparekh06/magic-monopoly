"""The bench tool's tuning: fractional positions, per-stop /tune/leg, the Free Parking rules and per-corner speed.

arduino/server.py is loaded fresh in dry-run mode with its state and tuning files in a temp dir."""
import json

import pytest
from fastapi.testclient import TestClient

from server.hardware import motor_server

AXIS = 1
TRUE_NORMAL, TRUE_CORNER, TRUE_FP = 210, (250, 400), (640, 910)       # Free Parking is far bigger than the rest


@pytest.fixture
def hw(monkeypatch, tmp_path):
    monkeypatch.setenv("DRY_RUN", "1")
    m = motor_server(fresh=True)
    monkeypatch.setattr(m, "STATE_FILE", str(tmp_path / "board_state.json"))
    monkeypatch.setattr(m, "TUNING_FILE", str(tmp_path / "tuning.json"))
    m.STEPS[AXIS] = (195, 199, 361)                       # known starting values, whatever tuning.json said
    m.PERIODS[AXIS] = (3000, 1500)
    for table in (m.CORNERS, m.CORNER_PERIODS):
        table[AXIS] = {}
    m.ZONES[AXIS] = None
    m.ZONE_COSTS[AXIS] = {}
    m.current_square = 0
    m.offsets[AXIS] = 0.0
    return m


@pytest.fixture
def client(hw):
    return TestClient(hw.app)


def true_hop_costs(hw):
    """Motor steps of each of the 40 hops on the 'real belt'."""
    costs = []
    for k in range(40):
        kind, corner = hw.hop_kind(k, (k + 1) % 40)
        if kind == "plain":
            costs.append(TRUE_NORMAL)
        else:
            costs.append((TRUE_FP if corner == 20 else TRUE_CORNER)[0 if kind == "in" else 1])
    return costs


def true_end(hw, start, sent):
    """Where the magnet really ends up when `sent` motor steps are sent from position `start`."""
    costs = true_hop_costs(hw)
    x, left, sign = start, abs(sent), 1 if sent > 0 else -1
    while left > 1e-9:
        k = int(x // 1) if sign > 0 else int(-(-x // 1)) - 1          # the hop the magnet is on, in its direction
        room = (k + 1 - x) if sign > 0 else (x - k)
        cost = costs[k % 40] * room
        if cost <= left:
            x, left = x + sign * room, left - cost
        else:
            x, left = x + sign * left / costs[k % 40], 0
    return x % 40


def do_leg(client, hw, to, primary):
    """Send the magnet to square `to` from wherever it really is, as the bench tool does: plan from the recorded
    position, 'click where it really ended', tune."""
    start = hw.get_position(AXIS) % 40
    squares = (to - hw.get_square(AXIS) + 20) % 40 - 20            # the short way, from the saved square
    sent = hw.plan_move(hw.get_position(AXIS), squares, AXIS)[2]
    actual = round(true_end(hw, start, sent), 3)
    r = client.post("/tune/leg", json={"axis": AXIS, "primary": primary, "start": start, "squares": squares,
                                       "sent": sent, "actual": actual})
    return r


# --- fractional positions -------------------------------------------------------------------------------------

def test_board_state_is_unchanged_at_offset_zero(client):
    assert client.get("/board", params={"axis": AXIS}).json() == {"square": 0, "name": "GO", "is_corner": True}


def test_board_at_records_a_fractional_position(client, hw):
    r = client.post("/board/at", params={"position": 21.6, "axis": AXIS}).json()
    assert r["square"] == 22 and r["offset"] == pytest.approx(-0.4)
    assert hw.get_position(AXIS) == pytest.approx(21.6)
    assert client.get("/axes").json()["axes"][AXIS]["offset"] == pytest.approx(-0.4)
    assert client.post("/board/at", params={"position": 41, "axis": AXIS}).status_code == 422
    client.post("/board/set", params={"square": 5, "axis": AXIS})              # set position resets the offset
    assert client.get("/board", params={"axis": AXIS}).json() == {"square": 5, "name": "Square 5", "is_corner": False}


def test_offset_is_saved_and_old_state_files_still_load(hw, tmp_path):
    hw.put_position(AXIS, 19.5)
    saved = json.load(open(hw.STATE_FILE))
    assert saved["axes"][str(AXIS)] == 19 and saved["offsets"][str(AXIS)] == 0.5
    assert hw.load_offsets()[AXIS] == 0.5
    json.dump({"square": 7}, open(hw.STATE_FILE, "w"))                          # the oldest format
    assert hw.load_offsets()[AXIS] == 0.0 and hw.load_squares()[hw.MOTOR_AXIS] == 7
    json.dump({"square": 7, "axes": {"1": 7}}, open(hw.STATE_FILE, "w"))
    assert hw.load_offsets()[AXIS] == 0.0 and hw.load_squares()[1] == 7


def test_whole_square_plans_are_the_old_numbers(hw):
    assert hw.plan_move(9, 2, AXIS) == (0, 2, 199 + 361)           # 9->10 in, 10->11 out
    assert hw.plan_move(5, 3, AXIS) == (3, 0, 3 * 195)
    assert hw.plan_move(11, -2, AXIS) == (0, 2, -(361 + 199))
    assert hw.plan_move(12, -3, AXIS) == (1, 2, -(195 + 361 + 199))
    assert hw.plan_segments(8, 3, AXIS) == [(195, 3000), (199 + 361, 1500)]
    assert hw.plan_segments(11, -2, AXIS) == [(-560, 1500)]          # both hops are corner legs: one piece
    assert hw.plan_segments(12, -3, AXIS) == [(-195, 3000), (-560, 1500)]


def test_start_at_19_5_costs_half_a_hop(hw):
    hw.put_position(AXIS, 19.5)
    assert hw.get_position(AXIS) == pytest.approx(19.5)
    # 19.5 -> 20 is half of the hop into Free Parking, then 20 -> 21 is the whole hop out
    assert hw.plan_move(19.5, 1, AXIS)[2] == round(199 / 2)
    assert hw.plan_move(19.5, 2, AXIS)[2] == round(199 / 2 + 361)
    assert hw.plan_segments(19.5, 2, AXIS) == [(round(199 / 2 + 361), 1500)]
    # at 20.4 (square 20, offset 0.4) a move of 1 goes to 21: 0.6 of the hop out
    assert hw.plan_move(20.4, 1, AXIS)[2] == round(0.6 * 361)
    # backwards from 19.5 by 1 ends on square 18: half of the hop into Free Parking, then one whole plain hop
    assert hw.plan_move(19.5, -1, AXIS)[2] == -round(0.5 * 199 + 195)
    # and the pieces always add up to the total
    for start in (0.3, 9.8, 19.5, 20.4, 29.7, 39.9):
        for squares in (-45, -3, -1, 1, 2, 12, 40):
            total = hw.plan_move(start, squares, AXIS)[2]
            assert sum(s for s, _ in hw.plan_segments(start, squares, AXIS)) == total


def test_move_from_a_fractional_position_ends_in_the_middle(client, hw):
    client.post("/board/at", params={"position": 19.5, "axis": AXIS})
    r = client.post("/board/move", params={"squares": 2, "axis": AXIS}).json()
    assert r["to"] == {"square": 21, "name": "Square 21", "is_corner": False}
    assert r["from"] == 19.5 and r["motor_steps"] == round(199 / 2 + 361)
    assert hw.offsets[AXIS] == 0.0


# --- /tune/leg ------------------------------------------------------------------------------------------------

def test_normal_leg_solves_the_normal_value(client, hw):
    client.post("/board/set", params={"square": 1, "axis": AXIS})
    r = do_leg(client, hw, 9, "normal").json()
    assert r["old"] == 195 and r["new"] == TRUE_NORMAL and r["skipped"] is False
    assert hw.STEPS[AXIS][0] == TRUE_NORMAL and r["undo"] == {"primary": "normal", "value": 195}
    assert json.load(open(hw.TUNING_FILE))["steps"][str(AXIS)][0] == TRUE_NORMAL
    assert hw.get_position(AXIS) == pytest.approx(1 + 8 * 195 / TRUE_NORMAL, abs=0.001)   # where it was clicked


def test_every_corner_value_converges_within_three_rounds(client, hw):
    client.post("/board/set", params={"square": 0, "axis": AXIS})
    stops = [(0, 1, "out:0"), (1, 9, "normal"), (9, 10, "in:10"), (10, 11, "out:10"), (11, 19, "normal"),
             (19, 20, "in:20"), (20, 21, "out:20"), (21, 29, "normal"), (29, 30, "in:30"), (30, 31, "out:30"),
             (31, 39, "normal"), (39, 0, "in:0")]
    for _ in range(3):
        for frm, to, primary in stops:
            r = do_leg(client, hw, to, primary)
            assert r.status_code == 200, r.text
    assert hw.STEPS[AXIS][0] == pytest.approx(TRUE_NORMAL, abs=2)
    for c in (0, 10, 30):
        assert hw.CORNERS[AXIS][c] == pytest.approx(TRUE_CORNER, abs=2)
    assert hw.CORNERS[AXIS][20] == pytest.approx(TRUE_FP, abs=2)


def test_free_parking_alone_converges_with_repeated_trios(client, hw):
    client.post("/board/set", params={"square": 19, "axis": AXIS})
    for _ in range(3):
        assert do_leg(client, hw, 20, "in:20").status_code == 200
        assert do_leg(client, hw, 21, "out:20").status_code == 200
        # the way back is only a resync: no tuning, the position becomes where the magnet really is
        sent = hw.plan_move(hw.get_position(AXIS), -2, AXIS)[2]
        client.post("/board/at", params={"position": round(true_end(hw, hw.get_position(AXIS), sent), 3), "axis": AXIS})
    assert hw.CORNERS[AXIS][20] == pytest.approx(TRUE_FP, abs=2)
    assert hw.STEPS[AXIS] == (195, 199, 361)                       # nothing shared was touched
    assert set(hw.CORNERS[AXIS]) == {20}


def test_free_parking_leg_changes_only_its_own_value(client, hw):
    client.post("/board/set", params={"square": 19, "axis": AXIS})
    r = do_leg(client, hw, 20, "in:20").json()
    assert r["old"] == 199 and r["new"] == TRUE_FP[0]
    assert hw.CORNERS[AXIS][20] == (TRUE_FP[0], 361)               # the out value was copied from the shared one
    assert hw.STEPS[AXIS] == (195, 199, 361)


def test_normal_leg_touching_free_parking_changes_nothing(client, hw):
    client.post("/board/set", params={"square": 11, "axis": AXIS})
    before = (hw.STEPS[AXIS], dict(hw.CORNERS[AXIS]))
    r = do_leg(client, hw, 22, "normal").json()
    assert r["skipped"] is True and "Free Parking" in r["message"]
    assert (hw.STEPS[AXIS], hw.CORNERS[AXIS]) == before
    assert hw.get_position(AXIS) == pytest.approx(r["position"]) and r["position"] != 11    # the click is recorded
    r = client.post("/tune/leg", json={"axis": AXIS, "primary": "in:10", "start": 18.0, "squares": 4,
                                       "sent": 900, "actual": 21.5}).json()
    assert r["skipped"] is True and hw.get_position(AXIS) == pytest.approx(21.5)
    assert (hw.STEPS[AXIS], hw.CORNERS[AXIS]) == before


def test_other_corners_still_tune_when_the_leg_is_far_from_free_parking(client, hw):
    client.post("/board/set", params={"square": 29, "axis": AXIS})
    assert do_leg(client, hw, 30, "in:30").json()["new"] == TRUE_CORNER[0]


def test_leg_refusals(client, hw):
    def leg(**kw):
        body = {"axis": AXIS, "primary": "normal", "start": 1.0, "squares": 8, "sent": 1680, "actual": 9.0}
        return client.post("/tune/leg", json={**body, **kw})
    assert leg(primary="in:10").status_code == 422                  # covers no corner hop at all
    assert leg(primary="sideways").status_code == 422
    assert leg(primary="in:7").status_code == 422
    assert leg(sent=-1680).status_code == 422                       # went the other way
    assert leg(actual=1.0).status_code == 422                       # never moved
    assert leg(sent=60000).status_code == 422                        # > 5000 per hop
    assert leg(sent=3).status_code == 422                           # < 1 per hop
    assert hw.STEPS[AXIS] == (195, 199, 361)
    assert leg().status_code == 200


def test_leg_wraps_around_go_and_goes_backwards(client, hw):
    r = client.post("/tune/leg", json={"axis": AXIS, "primary": "in:0", "start": 39.0, "squares": 1,
                                       "sent": 199, "actual": 0.0}).json()
    assert r["new"] == 199 and hw.CORNERS[AXIS][0] == (199, 361)
    # the same hop travelled backwards from GO: sent is negative
    r = client.post("/tune/leg", json={"axis": AXIS, "primary": "in:0", "start": 0.0, "squares": -1,
                                       "sent": -300, "actual": 39.0}).json()
    assert r["old"] == 199 and r["new"] == 300 and hw.CORNERS[AXIS][0] == (300, 361)


def test_partial_primary_weight_and_value_undo(client, hw):
    # the magnet started 0.5 into the hop out of Jail (10.5 -> 11): half a hop of "out:10"
    r = client.post("/tune/leg", json={"axis": AXIS, "primary": "out:10", "start": 10.5, "squares": 1,
                                       "sent": 180, "actual": 11.0}).json()
    assert r["weight"] == 0.5 and r["new"] == 360
    u = client.post("/tune/value", json={"axis": AXIS, "primary": "out:10", "value": r["old"]}).json()
    assert u["new"] == 361 and hw.CORNERS[AXIS][10] == (199, 361)
    assert client.post("/tune/value", json={"axis": AXIS, "primary": "normal", "value": 0}).status_code == 422
    client.post("/tune/value", json={"axis": AXIS, "primary": "normal", "value": 200})
    assert hw.STEPS[AXIS][0] == 200
    # under 0.05 of the primary hop is refused
    r = client.post("/tune/leg", json={"axis": AXIS, "primary": "out:10", "start": 10.97, "squares": 1,
                                       "sent": 11, "actual": 11.0})
    assert r.status_code == 422


# --- per-corner speed -----------------------------------------------------------------------------------------

def test_corner_period_is_used_for_that_corner_only(client, hw):
    r = client.post("/tune/speed", params={"axis": AXIS, "corner": 20, "in_us": 700, "out_us": 900}).json()
    assert r["corner_periods"] == {"20": [700, 900]}
    assert client.get("/axes").json()["axes"][AXIS]["corner_periods"] == {"20": [700, 900]}
    assert hw.plan_segments(18, 4, AXIS) == [(195, 3000), (199, 700), (361, 900), (195, 3000)]
    assert hw.plan_segments(8, 3, AXIS) == [(195, 3000), (199 + 361, 1500)]       # Jail keeps the shared period
    assert hw.plan_segments(22, -4, AXIS) == [(-195, 3000), (-361, 900), (-199, 700), (-195, 3000)]
    client.post("/tune/speed", params={"axis": AXIS, "corner": 20, "in_us": 700})
    assert hw.CORNER_PERIODS[AXIS][20] == (700, 700)                              # out defaults to in
    assert hw.plan_segments(19, 2, AXIS) == [(199 + 361, 700)]                    # same period: one piece
    assert client.post("/tune/speed", params={"axis": AXIS, "corner": 20, "in_us": 100}).status_code == 422
    assert client.post("/tune/speed", params={"axis": AXIS, "corner": 7, "in_us": 700}).status_code == 422
    assert client.post("/tune/speed", params={"axis": AXIS}).status_code == 422
    client.post("/tune/speed", params={"axis": AXIS, "corner": 20, "clear": True})
    assert hw.CORNER_PERIODS[AXIS] == {}
    assert client.post("/tune/speed", params={"axis": AXIS, "plain_us": 2500, "corner_us": 1200}).json()["plain_us"] == 2500


def test_old_tuning_file_still_loads_and_new_one_round_trips(hw):
    json.dump({"steps": {"1": [200, 210, 370]}, "periods": {"1": [2800, 1400]},
               "corners": {"1": {"20": [300, 500]}}}, open(hw.TUNING_FILE, "w"))
    hw.load_tuning()
    assert hw.STEPS[1] == (200, 210, 370) and hw.PERIODS[1] == (2800, 1400)
    assert hw.CORNERS[1] == {20: (300, 500)} and hw.CORNER_PERIODS[1] == {}
    hw.CORNER_PERIODS[1] = {20: (600, 800)}
    hw.save_tuning()
    hw.CORNER_PERIODS[1] = {}
    hw.load_tuning()
    assert hw.CORNER_PERIODS[1] == {20: (600, 800)}


def test_fast_zone_sets_the_period_for_its_hops_in_both_directions(client, hw):
    r = client.post("/tune/zone", params={"axis": AXIS, "start": 19, "hops": 4, "period_us": 800})
    assert r.json()["zone"] == [19, 4, 800]
    forward = hw.plan_segments(18, 6, AXIS)                           # 18 -> 24: plain, hops 19..23 fast, plain
    assert [period for _, period in forward] == [3000, 800, 3000]
    assert sum(steps for steps, _ in forward) == hw.plan_move(18, 6, AXIS)[2]
    back = hw.plan_segments(24, -6, AXIS)
    assert [period for _, period in back] == [3000, 800, 3000]
    assert sum(steps for steps, _ in back) == hw.plan_move(24, -6, AXIS)[2]


def test_fast_zone_is_saved_and_can_be_cleared(client, hw):
    client.post("/tune/zone", params={"axis": AXIS, "start": 19, "hops": 4, "period_us": 800})
    assert json.load(open(hw.TUNING_FILE))["zones"][str(AXIS)] == [19, 4, 800]
    assert client.get("/axes").json()["axes"][AXIS]["zone"] == [19, 4, 800]
    client.post("/tune/zone", params={"axis": AXIS, "clear": "true"})
    assert client.get("/axes").json()["axes"][AXIS]["zone"] is None
    assert 800 not in [period for _, period in hw.plan_segments(18, 6, AXIS)]


@pytest.mark.parametrize("params", [{"start": 40, "hops": 4, "period_us": 800}, {"start": 19, "hops": 0, "period_us": 800},
                                    {"start": 19, "hops": 4, "period_us": 100}, {"start": 19}])
def test_fast_zone_rejects_bad_values(client, hw, params):
    assert client.post("/tune/zone", params={"axis": AXIS, **params}).status_code == 422
    assert hw.ZONES[AXIS] is None


def test_a_leg_through_a_wider_zone_does_not_tune_the_shared_values(client, hw):
    leg = {"axis": AXIS, "primary": "normal", "start": 21, "squares": 8, "sent": 1600, "actual": 28.5}
    client.post("/tune/zone", params={"axis": AXIS, "start": 19, "hops": 4, "period_us": 800})
    r = client.post("/tune/leg", json=leg).json()                    # 21 -> 29 crosses the zone's last hop, 22 -> 23
    assert r["skipped"] is True
    assert hw.STEPS[AXIS] == (195, 199, 361)
    client.post("/tune/zone", params={"axis": AXIS, "clear": "true"})
    assert not client.post("/tune/leg", json=leg).json().get("skipped")   # no zone: 21 -> 29 is clear of Free Parking


def set_zone(client, start=19, hops=4, period=800):
    return client.post("/tune/zone", params={"axis": AXIS, "start": start, "hops": hops, "period_us": period})


def test_zone_hops_get_their_own_costs_copied_from_the_shared_ones(client, hw):
    set_zone(client)
    assert hw.ZONE_COSTS[AXIS] == {19: 199, 20: 361, 21: 195, 22: 195}        # in, out, then two plain hops
    assert client.get("/axes").json()["axes"][AXIS]["zone_costs"] == {"19": 199, "20": 361, "21": 195, "22": 195}
    assert hw.plan_move(18, 6, AXIS)[2] == 195 + 199 + 361 + 195 + 195 + 195      # nothing changed when the zone was set


def test_changing_the_shared_values_leaves_the_zone_alone_and_the_reverse(client, hw):
    set_zone(client)
    hw.STEPS[AXIS] = (300, 250, 450)
    assert hw.plan_move(21, 2, AXIS)[2] == 390                  # 21 -> 23 is two zone hops: still their own 195 each
    assert hw.plan_move(23, 2, AXIS)[2] == 600                  # 23 -> 25 is two plain hops: the new shared 300 each
    r = client.post("/tune/leg", json={"axis": AXIS, "primary": "zone:21", "start": 21, "squares": 1,
                                       "sent": 195, "actual": 21.5}).json()
    assert r["skipped"] is False and r["new"] == 390 and r["primary"] == "zone:21"      # half the hop took 195 steps
    assert hw.ZONE_COSTS[AXIS][21] == 390
    assert hw.STEPS[AXIS] == (300, 250, 450)                    # the rest of the board did not move


def test_a_corner_primary_inside_the_zone_becomes_the_zones_own_hop(client, hw):
    set_zone(client)
    r = client.post("/tune/leg", json={"axis": AXIS, "primary": "in:20", "start": 19, "squares": 1,
                                       "sent": 199, "actual": 19.5}).json()
    assert r["primary"] == "zone:19" and hw.ZONE_COSTS[AXIS][19] == 398
    assert hw.CORNERS[AXIS] == {}                               # the corner's shared overrides were not touched


def test_zone_primary_outside_the_zone_is_refused(client, hw):
    set_zone(client)
    r = client.post("/tune/leg", json={"axis": AXIS, "primary": "zone:30", "start": 29, "squares": 1,
                                       "sent": 195, "actual": 30})
    assert r.status_code == 422


def test_lap_tuning_treats_the_zone_as_a_fixed_block(client, hw):
    set_zone(client)
    hw.ZONE_COSTS[AXIS][21] = 999                                # a very different hop inside the zone
    sent = hw.plan_move(0, 40, AXIS)[2]
    r = client.get("/tune/suggest", params={"axis": AXIS, "start": 0, "squares": 40, "motor_steps": sent,
                                            "actual": 0, "basis": "all"}).json()
    assert r["suggested"] == r["current"]                        # the odd zone hop is not blamed on the shared values


def test_a_move_entirely_inside_the_zone_cannot_be_click_tuned(client, hw):
    set_zone(client)
    r = client.get("/tune/suggest", params={"axis": AXIS, "start": 19, "squares": 4, "motor_steps": 900, "actual": 22.5})
    assert r.status_code == 422


def test_zone_costs_are_saved_and_loaded(client, hw):
    set_zone(client)
    hw.ZONE_COSTS[AXIS][21] = 250
    client.post("/tune/value", json={"axis": AXIS, "primary": "zone:20", "value": 480})
    saved = json.load(open(hw.TUNING_FILE))["zone_costs"][str(AXIS)]
    assert saved["20"] == 480 and saved["19"] == 199


def test_a_zone_with_period_zero_keeps_its_own_distances_but_the_usual_speeds(client, hw):
    before = [period for _, period in hw.plan_segments(18, 6, AXIS)]
    assert set_zone(client, period=0).json()["zone"] == [19, 4, 0]
    assert [period for _, period in hw.plan_segments(18, 6, AXIS)] == before          # no speed push
    hw.ZONE_COSTS[AXIS][20] += 100
    assert hw.plan_move(18, 6, AXIS)[2] == 195 + 199 + 361 + 100 + 195 + 195 + 195     # but the zone's own distance counts
    assert set_zone(client, period=100).status_code == 422                           # 1-199 is still refused


def test_settings_files_are_written_whole_with_no_temp_file_left(client, hw, tmp_path):
    set_zone(client)
    client.post("/board/set", params={"square": 7, "axis": AXIS})
    assert json.load(open(hw.TUNING_FILE))["zones"][str(AXIS)] == [19, 4, 800]
    assert json.load(open(hw.STATE_FILE))["axes"][str(AXIS)] == 7
    assert [f.name for f in tmp_path.iterdir() if f.name.endswith(".tmp")] == []


def test_an_unreadable_settings_file_is_reported_not_ignored(hw, capsys):
    open(hw.TUNING_FILE, "w").write("{ this is not json")
    hw.load_tuning()
    assert "could not be read" in capsys.readouterr().out
    import os
    os.remove(hw.TUNING_FILE)
    hw.load_tuning()                                                    # no file yet is normal: no warning
    assert capsys.readouterr().out == ""
