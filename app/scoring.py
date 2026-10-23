"""Dutch Blitz scoring rules.

Round score = (cards in Dutch piles) * 1 - (cards left in the Blitz pile) * 2.
A round score may be negative. Running totals carry from round to round.

The game ends at the end of the round in which at least one total reaches the
target (75 unless the table chose another target when the game was created).
The winner is whoever has the highest total after that round. If several
players share that highest total, they are co-winners. Play does not continue
into a tiebreaker round.

Card counts follow a physical Dutch Blitz deck: the Blitz pile is 10 cards, and
a player deck is 40 cards, so Dutch cards are 0–40 and Blitz cards left are 0–10.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.errors import ScoringError

MIN_PLAYERS = 2
MAX_PLAYERS = 8
DEFAULT_TARGET = 75
MIN_TARGET = 1
MAX_TARGET = 999
MAX_DUTCH_CARDS = 40
MAX_BLITZ_LEFT = 10
MAX_NAME_LENGTH = 40

_WHOLE_NUMBER = re.compile(r"[0-9]+")


def require_count(value: int, *, label: str, maximum: int) -> int:
    """Return value when it is a whole number from 0 through maximum."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ScoringError(f"{label} must be a whole number from 0 to {maximum}.")
    return value


def parse_count(raw: str, *, label: str, maximum: int) -> int:
    """Parse a card count typed into the score form."""
    if not isinstance(raw, str):
        raise ScoringError(f"{label} must be a whole number from 0 to {maximum}.")
    text = raw.strip()
    if len(text) > 3 or _WHOLE_NUMBER.fullmatch(text) is None:
        raise ScoringError(f"{label} must be a whole number from 0 to {maximum}.")
    return require_count(int(text), label=label, maximum=maximum)


def round_score(dutch_cards: int, blitz_left: int) -> int:
    """Score one player for one round. The result can be negative."""
    dutch = require_count(dutch_cards, label="Dutch cards", maximum=MAX_DUTCH_CARDS)
    blitz = require_count(blitz_left, label="Blitz cards left", maximum=MAX_BLITZ_LEFT)
    return dutch - 2 * blitz


def validate_target(target: int) -> int:
    if (
        isinstance(target, bool)
        or not isinstance(target, int)
        or not MIN_TARGET <= target <= MAX_TARGET
    ):
        raise ScoringError(
            f"Target score must be a whole number from {MIN_TARGET} to {MAX_TARGET}."
        )
    return target


def parse_target(raw: str) -> int:
    if not isinstance(raw, str):
        raise ScoringError(
            f"Target score must be a whole number from {MIN_TARGET} to {MAX_TARGET}."
        )
    text = raw.strip()
    if len(text) > 4 or _WHOLE_NUMBER.fullmatch(text) is None:
        raise ScoringError(
            f"Target score must be a whole number from {MIN_TARGET} to {MAX_TARGET}."
        )
    return validate_target(int(text))


def validate_players(names: Sequence[str]) -> list[str]:
    """Clean names and require 2–8 distinct players.

    Blank rows are allowed only at the end, so a form can render spare name
    fields. A gap in the middle is a mistake, not an unused seat.
    """
    if isinstance(names, str) or not isinstance(names, Sequence):
        raise ScoringError(f"A game needs {MIN_PLAYERS} to {MAX_PLAYERS} players.")

    normalized: list[str] = []
    for name in names:
        if not isinstance(name, str):
            raise ScoringError("Player names must be text.")
        normalized.append(" ".join(name.split()))

    while normalized and normalized[-1] == "":
        normalized.pop()

    if any(name == "" for name in normalized):
        raise ScoringError("Fill in every player name. Leave unused rows blank at the end.")
    if not MIN_PLAYERS <= len(normalized) <= MAX_PLAYERS:
        raise ScoringError(f"A game needs {MIN_PLAYERS} to {MAX_PLAYERS} players.")

    seen: set[str] = set()
    for name in normalized:
        if len(name) > MAX_NAME_LENGTH:
            raise ScoringError(f"Player names must be {MAX_NAME_LENGTH} characters or fewer.")
        key = name.casefold()
        if key in seen:
            raise ScoringError(f"{name} is listed twice.")
        seen.add(key)
    return normalized


def add_round(totals: dict, scores: dict) -> dict:
    """Add one round's scores onto running totals.

    ``scores`` must name exactly the same players as ``totals``. Key order of
    ``totals`` is preserved (seat order, when the caller built it that way).
    """
    if set(scores) != set(totals):
        raise ScoringError("Scores must include every player exactly once.")
    updated = {}
    for name, total in totals.items():
        score = scores[name]
        if isinstance(score, bool) or not isinstance(score, int):
            raise ScoringError("Round scores must be whole numbers.")
        updated[name] = total + score
    return updated


@dataclass(frozen=True)
class Outcome:
    """Who is ahead, and whether the game is over.

    ``winners`` is empty until the game is over. ``leaders`` always lists every
    player who shares the highest total, in the order of ``totals``.
    """

    finished: bool
    winners: tuple[str, ...]
    leaders: tuple[str, ...]
    leading_score: int
    target: int


def outcome(totals: dict[str, int], target: int) -> Outcome:
    """Decide leaders and winners from totals after a completed round."""
    checked = validate_target(target)
    if not totals:
        return Outcome(finished=False, winners=(), leaders=(), leading_score=0, target=checked)

    best = max(totals.values())
    leaders = tuple(name for name, score in totals.items() if score == best)
    finished = best >= checked
    return Outcome(
        finished=finished,
        winners=leaders if finished else (),
        leaders=leaders,
        leading_score=best,
        target=checked,
    )


def winners(totals: dict[str, int], target: int) -> tuple[str, ...]:
    """Players who share the highest total once the target has been reached."""
    return outcome(totals, target).winners
