"""Turn a stored game into the text and totals shown on the page."""

from dataclasses import dataclass
from datetime import datetime, timezone

from app.scoring import add_round, outcome
from app.store import Game

MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def format_total(value: int) -> str:
    if value < 0:
        return f"−{abs(value)}"
    return str(value)


def format_signed(value: int) -> str:
    if value > 0:
        return f"+{value}"
    return format_total(value)


def format_parts(dutch_cards: int, blitz_left: int) -> str:
    return f"{dutch_cards} Dutch, {blitz_left} Blitz"


def format_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    hour = parsed.hour % 12 or 12
    suffix = "AM" if parsed.hour < 12 else "PM"
    return (
        f"{MONTHS[parsed.month - 1]} {parsed.day}, {parsed.year}, "
        f"{hour}:{parsed.minute:02d} {suffix} UTC"
    )


def join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return names[0] if names else ""
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + ", and " + names[-1]


@dataclass(frozen=True)
class StatusLine:
    finished: bool
    winners: tuple[str, ...]
    leaders: tuple[str, ...]
    headline: str
    subhead: str
    list_detail: str
    state_label: str


def describe(names_in_seat_order: list[str], totals: dict[str, int], target: int, round_count: int) -> StatusLine:
    if round_count == 0:
        return StatusLine(
            finished=False,
            winners=(),
            leaders=(),
            headline=f"First to {target}",
            subhead="Enter the score for round 1.",
            list_detail="No rounds yet",
            state_label="In progress",
        )

    result = outcome(totals, target)
    score = format_total(result.leading_score)
    if result.finished:
        names = join_names(list(result.winners))
        if len(result.winners) == 1:
            headline = f"{names} wins with {score}"
            subhead = f"Highest total wins. First to {target}."
        else:
            headline = f"{names} tie with {score}"
            subhead = f"Shared win. No tiebreaker round. First to {target}."
        return StatusLine(
            finished=True,
            winners=result.winners,
            leaders=result.leaders,
            headline=headline,
            subhead=subhead,
            list_detail=headline,
            state_label="Final",
        )

    if len(result.leaders) == len(names_in_seat_order):
        headline = f"All tied at {score}"
    elif len(result.leaders) == 1:
        headline = f"{result.leaders[0]} leads with {score}"
    else:
        headline = f"{join_names(list(result.leaders))} lead with {score}"
    return StatusLine(
        finished=False,
        winners=(),
        leaders=result.leaders,
        headline=headline,
        subhead=f"First to {target}. Round {round_count + 1} is next.",
        list_detail=headline,
        state_label="In progress",
    )


@dataclass
class Cell:
    score: int
    score_label: str
    total_label: str
    parts_label: str


@dataclass
class SheetRound:
    number: int
    cells: list[Cell]


@dataclass
class PlayerView:
    id: int
    name: str
    seat: int
    total: int
    total_label: str
    tag: str | None


@dataclass
class GameView:
    id: int
    target_score: int
    created_label: str
    players: list[PlayerView]
    rounds: list[SheetRound]
    round_count: int
    next_round: int
    finished: bool
    winners: list[str]
    headline: str
    subhead: str
    list_detail: str
    state_label: str


@dataclass
class GameSummary:
    id: int
    names_label: str
    detail_label: str
    meta_label: str
    active: bool


def present(game: Game) -> GameView:
    totals = {player.name: 0 for player in game.players}
    sheet: list[SheetRound] = []
    for rnd in game.rounds:
        by_player = {entry.player_id: entry for entry in rnd.entries}
        if set(by_player) != {player.id for player in game.players}:
            raise ValueError("A saved round is missing a player.")
        totals = add_round(
            totals,
            {player.name: by_player[player.id].score for player in game.players},
        )
        sheet.append(
            SheetRound(
                number=rnd.number,
                cells=[
                    Cell(
                        score=by_player[player.id].score,
                        score_label=format_signed(by_player[player.id].score),
                        total_label=format_total(totals[player.name]),
                        parts_label=format_parts(
                            by_player[player.id].dutch_cards,
                            by_player[player.id].blitz_left,
                        ),
                    )
                    for player in game.players
                ],
            )
        )

    status = describe(
        [player.name for player in game.players],
        totals,
        game.target_score,
        len(game.rounds),
    )
    show_lead = bool(game.rounds) and not status.finished and len(status.leaders) < len(game.players)
    players = []
    for player in game.players:
        if status.finished and player.name in status.winners:
            tag = "Win"
        elif show_lead and player.name in status.leaders:
            tag = "Lead"
        else:
            tag = None
        players.append(
            PlayerView(
                id=player.id,
                name=player.name,
                seat=player.seat,
                total=totals[player.name],
                total_label=format_total(totals[player.name]),
                tag=tag,
            )
        )
    round_count = len(game.rounds)
    return GameView(
        id=game.id,
        target_score=game.target_score,
        created_label=format_timestamp(game.created_at),
        players=players,
        rounds=sheet,
        round_count=round_count,
        next_round=round_count + 1,
        finished=status.finished,
        winners=list(status.winners),
        headline=status.headline,
        subhead=status.subhead,
        list_detail=status.list_detail,
        state_label=status.state_label,
    )


def summarize(game: Game) -> GameSummary:
    view = present(game)
    rounds = "1 round" if view.round_count == 1 else f"{view.round_count} rounds"
    return GameSummary(
        id=view.id,
        names_label=", ".join(player.name for player in view.players),
        detail_label=view.list_detail,
        meta_label=(
            f"{view.state_label} · {rounds} · target {view.target_score} · {view.created_label}"
        ),
        active=not view.finished,
    )
