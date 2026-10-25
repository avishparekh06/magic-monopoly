"""Marker-first card reading, with ids taken from server/cards.json."""
import cv2
from vision import synth
from vision.cards import CardMatcher, load_marker_map


def test_marker_map_matches_game_data():
    m = load_marker_map()
    assert m[0] == "crystal_01" and m[16] == "chest_01" and m[31] == "chest_16"
    assert m[100] == "p1" and m[102] == "p3"
    assert len(m) == 35 and len(set(m.values())) == 35


def test_marker_identifies_card_without_reference_images(tmp_path):
    matcher = CardMatcher(str(tmp_path))                      # no reference images at all
    for mid, cid in [(0, "crystal_01"), (16, "chest_01"), (31, "chest_16"), (100, "p1")]:
        for angle in (0, 7, 180, 92):
            zone = synth.card_zone(synth.card_with_marker("Any Title", mid), angle=angle)
            r = matcher.identify(zone)
            assert r.card_id == cid, (mid, angle, r.card_id, r.reason)
            assert r.quad is not None


def test_unknown_marker_falls_back_to_orb(tmp_path):
    cv2.imwrite(str(tmp_path / "golden_goose.png"), synth.card("golden goose"))
    matcher = CardMatcher(str(tmp_path))
    zone = synth.card_zone(synth.card_with_marker("golden goose", 249))      # 249 is not a game marker
    assert matcher.identify(zone).card_id == "golden_goose"


def test_empty_zone_still_no_card(tmp_path):
    assert CardMatcher(str(tmp_path)).identify(synth.card_zone(None)).reason == "no_card"
