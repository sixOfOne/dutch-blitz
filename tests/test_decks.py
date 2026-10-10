"""Deck catalog and the rules for storing a color and icon on a player."""

import pytest

from app.decks import COLORS, ICONS, assign_decks, deck_face
from app.errors import ScoringError
from app.store import Player as StorePlayer


def test_player_deck_fields_default_to_empty():
    player = StorePlayer(id=1, name="Ada", seat=1)
    assert player.deck_color is None
    assert player.deck_icon is None


def test_official_pairs_match_the_sticker_packs():
    assert [(color.id, color.label, color.icon_id) for color in COLORS] == [
        ("red", "Red", "carriage"),
        ("blue", "Blue", "plow"),
        ("yellow", "Yellow", "pail"),
        ("green", "Green", "pump"),
        ("orange", "Orange", "hat"),
        ("navy", "Navy blue", "wheelbarrow"),
        ("pink", "Pink", "water-can"),
        ("purple", "Purple", "butter-churn"),
    ]
    assert [icon.id for icon in ICONS] == [
        "carriage",
        "plow",
        "pail",
        "pump",
        "hat",
        "wheelbarrow",
        "water-can",
        "butter-churn",
    ]


def test_icons_are_inline_svg_glyphs():
    for icon in ICONS:
        markup = str(icon.svg)
        assert markup.startswith("<svg")
        assert "currentColor" in markup
        without_namespace = markup.replace("http://www.w3.org/2000/svg", "")
        assert "http://" not in without_namespace
        assert "https://" not in without_namespace


def test_unknown_stored_ids_show_nothing():
    face = deck_face("mauve", "unicorn")
    assert face.color is None
    assert face.icon is None
    assert face.text == ""
    assert str(face.svg) == ""


def test_assign_decks_fills_the_official_icon_and_keeps_an_override():
    assigned = assign_decks(
        ["Ada", "Bo"],
        colors=["green", "red"],
        icons=[None, "wheelbarrow"],
    )
    assert assigned == [
        ("Ada", "green", "pump"),
        ("Bo", "red", "wheelbarrow"),
    ]


def test_assign_decks_rejects_a_repeated_color():
    with pytest.raises(ScoringError, match="Navy blue is already chosen"):
        assign_decks(["Ada", "Bo"], colors=["navy", "navy"], icons=["wheelbarrow", "hat"])
