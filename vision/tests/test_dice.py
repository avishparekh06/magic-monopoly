import pytest
from vision import synth
from vision.dice import read_dice

PX = 3.0


@pytest.mark.parametrize("a", range(1, 7))
@pytest.mark.parametrize("b", range(1, 7))
def test_reads_every_pair(a, b):
    for seed in (0, 1, 2):
        r = read_dice(synth.tray([a, b], PX, seed=seed * 50 + a * 6 + b), PX)
        assert r.ok, (a, b, seed, r.reason, r.values)
        assert sorted(r.values) == sorted([a, b])


def test_touching_dice_are_flagged():
    r = read_dice(synth.tray([3, 4], PX, touching=True), PX)
    assert r.reason == "touching"


def test_empty_tray():
    r = read_dice(synth.tray([], PX), PX)
    assert r.reason == "empty" and r.values == []


def test_one_die_is_count_mismatch():
    r = read_dice(synth.tray([5], PX), PX)
    assert r.reason == "count_mismatch" and r.values == [5]


def test_three_dice_is_count_mismatch():
    r = read_dice(synth.tray([1, 2, 3], PX, size_mm=(200, 200)), PX)
    assert r.reason == "count_mismatch" and sorted(r.values) == [1, 2, 3]


def test_doubles_and_total():
    r = read_dice(synth.tray([6, 6], PX, seed=3), PX)
    assert r.ok and r.doubles and r.total == 12
