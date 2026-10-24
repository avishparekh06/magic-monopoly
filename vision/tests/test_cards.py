import cv2
import pytest
from vision import synth
from vision.cards import CardMatcher, find_card

TITLES = ["fairy godmother", "wolf at the door", "golden goose", "poisoned apple", "glass slipper"]


@pytest.fixture(scope="module")
def matcher(tmp_path_factory):
    d = tmp_path_factory.mktemp("cards")
    for t in TITLES:
        cv2.imwrite(str(d / (t.replace(" ", "_") + ".png")), synth.card(t))
    return CardMatcher(str(d))


@pytest.mark.parametrize("title", TITLES)
@pytest.mark.parametrize("angle", [0, 6, -8, 180, 91])
def test_identifies_card_at_angles(matcher, title, angle):
    r = matcher.identify(synth.card_zone(synth.card(title), angle=angle))
    assert r.card_id == title.replace(" ", "_"), (title, angle, r.card_id, r.score, r.second, r.reason)


def test_empty_zone_is_no_card(matcher):
    r = matcher.identify(synth.card_zone(None))
    assert r.reason == "no_card" and r.card_id is None


def test_unknown_card_is_no_match_or_ambiguous(matcher):
    r = matcher.identify(synth.card_zone(synth.card("troll toll")))
    assert r.card_id is None and r.reason in ("no_match", "ambiguous")


def test_find_card_returns_quad():
    q = find_card(synth.card_zone(synth.card("golden goose"), angle=5))
    assert q is not None and q.shape == (4, 2)
