"""Deck colors and the icons printed on Dutch Blitz cards.

The color-to-icon pairs are the ones named on the manufacturer's sticker
packs (https://dutchblitz.com/products/dutch-blitz-sticker-pack):

- Original green pack: red carriage, blue plow, yellow pail, green pump.
  The sticker list spells the yellow symbol "Pale". The rules call that
  symbol a pail (https://dutchblitz.com/wp-content/uploads/Dutch_Blitz_Directions_Web.pdf),
  and the deck product page calls it a bucket.
- Purple pack: orange hat, navy blue wheelbarrow, pink water can,
  purple butter churn.

The blue expansion uses the same four original icons with the colors
switched. The maker describes the switch but does not publish which icon
lands on which color, so those decks are not a second set of defaults.
A player can still change the icon after picking a color.

The drawings are original geometric glyphs. They are not the card art.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from markupsafe import Markup

from app.errors import ScoringError
from app.scoring import validate_players


@dataclass(frozen=True)
class DeckIcon:
    id: str
    label: str
    svg: Markup


@dataclass(frozen=True)
class DeckColor:
    id: str
    label: str
    icon_id: str


@dataclass(frozen=True)
class DeckFace:
    """What to show for a stored color and icon. Unknown ids are dropped."""

    color: str | None
    icon: str | None
    text: str
    svg: Markup


def _svg(inner: str) -> Markup:
    return Markup(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round" '
        'aria-hidden="true" focusable="false">'
        f"{inner}</svg>"
    )


ICONS: tuple[DeckIcon, ...] = (
    DeckIcon(
        "carriage",
        "Carriage",
        _svg(
            '<circle cx="6.5" cy="17.2" r="2.1" fill="currentColor" stroke="none"/>'
            '<circle cx="16.2" cy="17.2" r="2.1" fill="currentColor" stroke="none"/>'
            '<path d="M3.2 14.8h16.2"/>'
            '<path d="M5 14.8V10h6.6l2.6 4.8"/>'
            '<path d="M7.2 10V7.2h3.6V10"/>'
        ),
    ),
    DeckIcon(
        "plow",
        "Plow",
        _svg(
            '<path d="M3.5 16.5h8"/>'
            '<path d="M11.5 16.5 18 8"/>'
            '<path d="M11.2 16.2 15 19l3.8-3.2-3.6-2.6z" fill="currentColor"/>'
        ),
    ),
    DeckIcon(
        "pail",
        "Pail",
        _svg(
            '<path d="M8 9.2h8l-1.15 9.3H9.15L8 9.2z" fill="currentColor" stroke="none"/>'
            '<path d="M8.2 9.2c.4-2.6 7.2-2.6 7.6 0"/>'
        ),
    ),
    DeckIcon(
        "pump",
        "Pump",
        _svg(
            '<path d="M10.5 20.5V9"/>'
            '<path d="M8.2 20.5h4.6"/>'
            '<path d="M10.5 12.5h6.2"/>'
            '<path d="M8.4 9h4.4"/>'
            '<path d="M6.2 6.2h5.2L13.2 9"/>'
            '<circle cx="6.2" cy="6.2" r="1.15" fill="currentColor" stroke="none"/>'
        ),
    ),
    DeckIcon(
        "hat",
        "Hat",
        _svg(
            '<path d="M3.5 16.2c2.2 1.6 14.8 1.6 17 0"/>'
            '<path d="M8 16.2c.3-3.2 1.7-6 4-6s3.7 2.8 4 6"/>'
            '<path d="M12 10.2V8"/>'
        ),
    ),
    DeckIcon(
        "wheelbarrow",
        "Wheelbarrow",
        _svg(
            '<circle cx="6.6" cy="17" r="2.3"/>'
            '<path d="M8.4 16.2 19.2 8.6"/>'
            '<path d="M9.6 16h8.2l2-4.6H11.2z" fill="currentColor" stroke="none"/>'
            '<path d="M17.6 11.2 20.4 13"/>'
        ),
    ),
    DeckIcon(
        "water-can",
        "Water can",
        _svg(
            '<path d="M6.8 11h7.4l1.4 2.1V19H6.8V11z"/>'
            '<path d="M14.2 11c1.8-.7 4.6-.3 5.8 1.3"/>'
            '<path d="M6.2 13.4c-1.7.8-1.7 3.2 0 4"/>'
            '<circle cx="19.6" cy="7.4" r=".7" fill="currentColor" stroke="none"/>'
            '<circle cx="21.5" cy="8.8" r=".7" fill="currentColor" stroke="none"/>'
            '<path d="M18.6 9.2c.6.2 1 .9.6 1.5"/>'
        ),
    ),
    DeckIcon(
        "butter-churn",
        "Butter churn",
        _svg(
            '<path d="M8 9.2h8V19H8z"/>'
            '<path d="M7.4 9.2h9.2"/>'
            '<path d="M12 9.2V4.2"/>'
            '<path d="M10 4.2h4"/>'
            '<path d="M8.6 19h6.8"/>'
        ),
    ),
)

# Order is the picker order: original pack, then purple expansion.
COLORS: tuple[DeckColor, ...] = (
    DeckColor("red", "Red", "carriage"),
    DeckColor("blue", "Blue", "plow"),
    DeckColor("yellow", "Yellow", "pail"),
    DeckColor("green", "Green", "pump"),
    DeckColor("orange", "Orange", "hat"),
    DeckColor("navy", "Navy blue", "wheelbarrow"),
    DeckColor("pink", "Pink", "water-can"),
    DeckColor("purple", "Purple", "butter-churn"),
)

ICON_BY_ID = {icon.id: icon for icon in ICONS}
COLOR_BY_ID = {color.id: color for color in COLORS}
EMPTY_FACE = DeckFace(None, None, "", Markup(""))


def deck_face(color: str | None, icon: str | None) -> DeckFace:
    """Resolve stored ids to a label and glyph. Unknown ids show nothing."""
    color_rec = COLOR_BY_ID.get(_token(color) or "")
    icon_rec = ICON_BY_ID.get(_token(icon) or "")
    parts = []
    if color_rec is not None:
        parts.append(color_rec.label)
    if icon_rec is not None:
        parts.append(icon_rec.label)
    return DeckFace(
        color=color_rec.id if color_rec is not None else None,
        icon=icon_rec.id if icon_rec is not None else None,
        text=", ".join(parts),
        svg=icon_rec.svg if icon_rec is not None else Markup(""),
    )


def assign_decks(
    names: Sequence[str],
    colors: Sequence[str | None] | None = None,
    icons: Sequence[str | None] | None = None,
) -> list[tuple[str, str | None, str | None]]:
    """Pair each kept player with a color and icon.

    Trailing blank name rows are dropped together with the color and icon
    on that row. An empty icon on a known color becomes that color's icon.
    Two players cannot share a color. Empty colors are allowed so a game
    saved without decks still has a shape the form can submit.
    """
    if isinstance(names, str) or not isinstance(names, Sequence):
        validate_players(names)

    raw = list(names)
    color_list = _align(colors, len(raw), invalid="Pick a deck color from the list.")
    icon_list = _align(icons, len(raw), invalid="Pick a deck icon from the list.")
    while raw and isinstance(raw[-1], str) and " ".join(raw[-1].split()) == "":
        raw.pop()
        color_list.pop()
        icon_list.pop()

    cleaned = validate_players(raw)
    resolved = [clean_deck(color, icon) for color, icon in zip(color_list, icon_list, strict=True)]
    _reject_duplicate_colors(resolved)
    return [(name, color, icon) for name, (color, icon) in zip(cleaned, resolved, strict=True)]


def clean_deck(color: str | None, icon: str | None) -> tuple[str | None, str | None]:
    """Return canonical ids. A color with no icon gets the official icon."""
    color_id = _required_token(color, invalid="Pick a deck color from the list.")
    icon_id = _required_token(icon, invalid="Pick a deck icon from the list.")
    if color_id is not None and color_id not in COLOR_BY_ID:
        raise ScoringError("Pick a deck color from the list.")
    if icon_id is not None and icon_id not in ICON_BY_ID:
        raise ScoringError("Pick a deck icon from the list.")
    if color_id is not None and icon_id is None:
        icon_id = COLOR_BY_ID[color_id].icon_id
    return color_id, icon_id


def _reject_duplicate_colors(choices: Sequence[tuple[str | None, str | None]]) -> None:
    seen: set[str] = set()
    for color, _icon in choices:
        if color is None or color not in seen:
            if color is not None:
                seen.add(color)
            continue
        label = COLOR_BY_ID[color].label
        raise ScoringError(f"{label} is already chosen. Each player needs a different deck color.")


def _align(values: Sequence[str | None] | None, length: int, *, invalid: str) -> list[str | None]:
    if values is None:
        return [None] * length
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise ScoringError(invalid)
    aligned = list(values)[:length]
    aligned.extend([None] * (length - len(aligned)))
    return aligned


def _required_token(value: str | None, *, invalid: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ScoringError(invalid)
    return _token(value)


def _token(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().casefold()
    return text or None
