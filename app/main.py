"""Phone-friendly Dutch Blitz score pages."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import db_path as configured_db_path
from app.errors import NotFoundError, ScoringError
from app.scoring import (
    DEFAULT_TARGET,
    MAX_BLITZ_LEFT,
    MAX_DUTCH_CARDS,
    MAX_NAME_LENGTH,
    MAX_PLAYERS,
    MAX_TARGET,
    MIN_PLAYERS,
    MIN_TARGET,
    parse_count,
    parse_target,
)
from app.store import Game, GameStore
from app.views import present, summarize

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
templates.env.globals.update(
    DEFAULT_TARGET=DEFAULT_TARGET,
    MIN_PLAYERS=MIN_PLAYERS,
    MAX_PLAYERS=MAX_PLAYERS,
    MIN_TARGET=MIN_TARGET,
    MAX_TARGET=MAX_TARGET,
    MAX_DUTCH_CARDS=MAX_DUTCH_CARDS,
    MAX_BLITZ_LEFT=MAX_BLITZ_LEFT,
    MAX_NAME_LENGTH=MAX_NAME_LENGTH,
)


def create_app(db_path: str | None = None) -> FastAPI:
    selected = db_path

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = GameStore(selected if selected is not None else configured_db_path())
        store.init()
        app.state.store = store
        yield

    app = FastAPI(
        title="Dutch Blitz",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")
    _register_routes(app)
    return app


def _register_routes(app: FastAPI) -> None:
    @app.get("/")
    def home(request: Request):
        return _home(request, error=None, names=[], target=str(DEFAULT_TARGET))

    @app.post("/games")
    async def create_game(request: Request):
        form = await request.form()
        names = [str(value) for value in form.getlist("player_name")]
        target_raw = form.get("target_score")
        target_text = str(DEFAULT_TARGET) if target_raw is None or str(target_raw).strip() == "" else str(target_raw)
        try:
            game_id = _store(request).create_game(names, parse_target(target_text))
        except ScoringError as exc:
            return _home(
                request,
                error=exc.message,
                names=names,
                target=target_text,
                status_code=400,
            )
        return RedirectResponse(url=f"/games/{game_id}", status_code=303)

    @app.get("/games/{game_id}")
    def show_game(request: Request, game_id: int, edit: int = 0):
        game = _store(request).get_game(game_id)
        if game is None:
            return _missing(request)
        editing = edit == 1 and bool(game.rounds)
        draft = _draft_from_last(game) if editing else {}
        return _game_page(request, game, draft=draft, editing=editing)

    @app.post("/games/{game_id}/rounds")
    async def save_round(request: Request, game_id: int):
        return await _save_counts(request, game_id, replace=False)

    @app.post("/games/{game_id}/rounds/last")
    async def replace_round(request: Request, game_id: int):
        return await _save_counts(request, game_id, replace=True)

    @app.post("/games/{game_id}/undo")
    def undo_round(request: Request, game_id: int):
        store = _store(request)
        try:
            store.undo_last_round(game_id)
        except NotFoundError:
            return _missing(request)
        except ScoringError as exc:
            game = store.get_game(game_id)
            if game is None:
                return _missing(request)
            return _game_page(request, game, error=exc.message, status_code=400)
        return RedirectResponse(url=f"/games/{game_id}", status_code=303)


def _store(request: Request) -> GameStore:
    return request.app.state.store


def _home(
    request: Request,
    *,
    error: str | None,
    names: list[str],
    target: str,
    status_code: int = 200,
):
    games = [summarize(game) for game in _store(request).list_games()]
    padded = list(names[:MAX_PLAYERS])
    padded.extend([""] * (MAX_PLAYERS - len(padded)))
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "error": error,
            "form_names": padded,
            "form_target": target,
            "active_games": [game for game in games if game.active],
            "past_games": [game for game in games if not game.active],
        },
        status_code=status_code,
    )


def _game_page(
    request: Request,
    game: Game,
    *,
    error: str | None = None,
    draft: dict | None = None,
    editing: bool = False,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "game.html",
        {
            "game": present(game),
            "error": error,
            "draft": draft or {},
            "editing": editing,
        },
        status_code=status_code,
    )


def _missing(request: Request):
    return templates.TemplateResponse(request, "not_found.html", {}, status_code=404)


def _draft_from_last(game: Game) -> dict[str, dict[str, str]]:
    if not game.rounds:
        return {}
    return {
        str(entry.player_id): {
            "dutch": str(entry.dutch_cards),
            "blitz": str(entry.blitz_left),
        }
        for entry in game.rounds[-1].entries
    }


def _raw_draft(game: Game, form) -> dict[str, dict[str, str]]:
    draft = {}
    for player in game.players:
        dutch = form.get(f"dutch_{player.id}")
        blitz = form.get(f"blitz_{player.id}")
        draft[str(player.id)] = {
            "dutch": "" if dutch is None else str(dutch),
            "blitz": "" if blitz is None else str(blitz),
        }
    return draft


def _parsed_counts(game: Game, form) -> dict[int, tuple[int, int]]:
    counts = {}
    for player in game.players:
        dutch_raw = form.get(f"dutch_{player.id}")
        blitz_raw = form.get(f"blitz_{player.id}")
        if dutch_raw is None or blitz_raw is None:
            raise ScoringError("Enter Dutch and Blitz counts for every player.")
        counts[player.id] = (
            parse_count(str(dutch_raw), label=f"Dutch cards for {player.name}", maximum=MAX_DUTCH_CARDS),
            parse_count(str(blitz_raw), label=f"Blitz cards left for {player.name}", maximum=MAX_BLITZ_LEFT),
        )
    return counts


async def _save_counts(request: Request, game_id: int, *, replace: bool):
    store = _store(request)
    game = store.get_game(game_id)
    if game is None:
        return _missing(request)
    form = await request.form()
    draft = _raw_draft(game, form)
    try:
        counts = _parsed_counts(game, form)
        if replace:
            store.replace_last_round(game_id, counts)
        else:
            store.record_round(game_id, counts)
    except NotFoundError:
        return _missing(request)
    except ScoringError as exc:
        fresh = store.get_game(game_id)
        if fresh is None:
            return _missing(request)
        return _game_page(
            request,
            fresh,
            error=exc.message,
            draft=draft,
            editing=replace,
            status_code=400,
        )
    return RedirectResponse(url=f"/games/{game_id}", status_code=303)


app = create_app()
