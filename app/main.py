"""Phone-friendly Dutch Blitz score pages."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth import SESSION_COOKIE, AuthError, DevBypassVerifier, verifier_from_env
from app.config import clerk_browser_config
from app.config import db_path as configured_db_path
from app.errors import ForbiddenError, NotFoundError, ScoringError
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


def create_app(db_path: str | None = None, verifier=None) -> FastAPI:
    selected = db_path
    selected_verifier = verifier

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = GameStore(selected if selected is not None else configured_db_path())
        store.init()
        app.state.store = store
        app.state.verifier = selected_verifier if selected_verifier is not None else verifier_from_env()
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

    @app.middleware("http")
    async def require_user(request: Request, call_next):
        if request.url.path.startswith("/static"):
            return await call_next(request)
        verifier_impl = getattr(request.app.state, "verifier", None)
        if verifier_impl is None:
            return _unauthenticated(request)
        try:
            request.state.user = verifier_impl.verify(_token_from_request(request))
        except AuthError:
            return _unauthenticated(request)
        return await call_next(request)

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
            game_id = _store(request).create_game(
                names,
                parse_target(target_text),
                owner_id=_user(request),
            )
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
        user = _user(request)
        game = _store(request).get_game(game_id, user)
        if game is None:
            return _missing(request)
        editing = _is_owner(game, user) and edit == 1 and bool(game.rounds)
        draft = _draft_from_last(game) if editing else {}
        return _game_page(request, game, draft=draft, editing=editing)

    @app.post("/games/{game_id}/invite")
    async def update_invite(request: Request, game_id: int):
        form = await request.form()
        mode = str(form.get("mode") or "")
        if mode not in {"create", "rotate", "disable"}:
            return _missing(request)
        user = _user(request)
        try:
            _store(request).set_invite(game_id, owner_id=user, mode=mode)
        except NotFoundError:
            return _missing(request)
        except ForbiddenError as exc:
            return _forbidden(request, game_id, exc.message)
        return RedirectResponse(url=f"/games/{game_id}", status_code=303)

    @app.post("/games/{game_id}/seats/{player_id}/claim")
    def claim_seat(request: Request, game_id: int, player_id: int):
        user = _user(request)
        try:
            _store(request).claim_own_seat(game_id, player_id, owner_id=user)
        except NotFoundError:
            return _missing(request)
        except ForbiddenError as exc:
            return _forbidden(request, game_id, exc.message)
        except ScoringError as exc:
            return _game_or_missing(request, game_id, error=exc.message, status_code=400)
        return RedirectResponse(url=f"/games/{game_id}", status_code=303)

    @app.post("/games/{game_id}/seats/{player_id}/revoke")
    def revoke_seat(request: Request, game_id: int, player_id: int):
        user = _user(request)
        try:
            _store(request).revoke_seat(game_id, player_id, owner_id=user)
        except NotFoundError:
            return _missing(request)
        except ForbiddenError as exc:
            return _forbidden(request, game_id, exc.message)
        except ScoringError as exc:
            return _game_or_missing(request, game_id, error=exc.message, status_code=400)
        return RedirectResponse(url=f"/games/{game_id}", status_code=303)

    @app.get("/join/{token}")
    def show_invite(request: Request, token: str):
        return _invite_page(request, token, error=None)

    @app.post("/join/{token}")
    async def accept_invite(request: Request, token: str):
        form = await request.form()
        raw_seat = form.get("player_id")
        user = _user(request)
        store = _store(request)
        if raw_seat is None or not str(raw_seat).isdigit():
            return _invite_page(request, token, error="Pick an open seat.", status_code=400)
        try:
            game_id = store.join_game(token, int(raw_seat), user_id=user)
        except NotFoundError:
            return _invite_missing(request)
        except ScoringError as exc:
            return _invite_page(request, token, error=exc.message, status_code=400)
        return RedirectResponse(url=f"/games/{game_id}", status_code=303)

    @app.post("/games/{game_id}/rounds")
    async def save_round(request: Request, game_id: int):
        return await _save_counts(request, game_id, replace=False)

    @app.post("/games/{game_id}/rounds/last")
    async def replace_round(request: Request, game_id: int):
        return await _save_counts(request, game_id, replace=True)

    @app.post("/games/{game_id}/undo")
    def undo_round(request: Request, game_id: int):
        store = _store(request)
        user = _user(request)
        try:
            store.undo_last_round(game_id, owner_id=user)
        except NotFoundError:
            return _missing(request)
        except ForbiddenError as exc:
            return _forbidden(request, game_id, exc.message)
        except ScoringError as exc:
            game = store.get_game(game_id, user)
            if game is None:
                return _missing(request)
            return _game_page(request, game, error=exc.message, status_code=400)
        return RedirectResponse(url=f"/games/{game_id}", status_code=303)


def _store(request: Request) -> GameStore:
    return request.app.state.store


def _user(request: Request) -> str:
    return request.state.user.user_id


def _is_owner(game: Game, user_id: str) -> bool:
    return game.owner_id == user_id


def _token_from_request(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header[:7].lower() == "bearer ":
        token = header[7:].strip()
        if token:
            return token
    return request.cookies.get(SESSION_COOKIE, "")


def _wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


def _auth_context(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    verifier = getattr(request.app.state, "verifier", None)
    bypass = isinstance(verifier, DevBypassVerifier) and user is not None
    return {
        "signed_in": user is not None,
        "dev_bypass": bypass,
        "clerk": {"ready": False} if bypass else clerk_browser_config(),
    }


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    payload = _auth_context(request)
    payload.update(context)
    return templates.TemplateResponse(request, name, payload, status_code=status_code)


def _unauthenticated(request: Request):
    if _wants_json(request):
        return JSONResponse({"detail": "Sign in to continue."}, status_code=401)
    return _render(request, "sign_in.html", {}, status_code=401)


def _home(
    request: Request,
    *,
    error: str | None,
    names: list[str],
    target: str,
    status_code: int = 200,
):
    user = _user(request)
    games = [summarize(game, viewer_id=user) for game in _store(request).list_games(user)]
    padded = list(names[:MAX_PLAYERS])
    padded.extend([""] * (MAX_PLAYERS - len(padded)))
    return _render(
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
    user = _user(request)
    is_owner = _is_owner(game, user)
    your_name = next((player.name for player in game.players if player.user_id == user), None)
    return _render(
        request,
        "game.html",
        {
            "game": present(game),
            "error": error,
            "draft": draft or {},
            "editing": editing,
            "is_owner": is_owner,
            "your_name": your_name,
            "share": _share_context(request, game) if is_owner else None,
        },
        status_code=status_code,
    )


def _share_context(request: Request, game: Game) -> dict:
    user = _user(request)
    url = None
    if game.invite_token:
        url = f"{str(request.base_url).rstrip('/')}/join/{game.invite_token}"
    seats = []
    for player in game.players:
        if player.user_id is None:
            state = "open"
        elif player.user_id == user:
            state = "you"
        else:
            state = "taken"
        seats.append({"id": player.id, "name": player.name, "state": state})
    return {"url": url, "seats": seats}


def _forbidden(request: Request, game_id: int, message: str):
    game = _store(request).get_game(game_id, _user(request))
    if game is None:
        return _missing(request)
    return _game_page(request, game, error=message, status_code=403)


def _game_or_missing(request: Request, game_id: int, *, error: str, status_code: int):
    game = _store(request).get_game(game_id, _user(request))
    if game is None:
        return _missing(request)
    return _game_page(request, game, error=error, status_code=status_code)


def _invite_page(request: Request, token: str, *, error: str | None, status_code: int = 200):
    preview = _store(request).describe_invite(token, user_id=_user(request))
    if preview is None:
        return _invite_missing(request)
    if request.method == "GET" and preview.already_joined and error is None:
        return RedirectResponse(url=f"/games/{preview.game_id}", status_code=303)
    return _render(
        request,
        "invite.html",
        {"invite": preview, "token": token, "error": error},
        status_code=status_code,
    )


def _invite_missing(request: Request):
    return _render(request, "invite_missing.html", {}, status_code=404)


def _missing(request: Request):
    return _render(request, "not_found.html", {}, status_code=404)


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
    user = _user(request)
    game = store.get_game(game_id, user)
    if game is None:
        return _missing(request)
    if replace and not _is_owner(game, user):
        return _game_page(
            request,
            game,
            error="Only the person who started this game can do that.",
            status_code=403,
        )
    form = await request.form()
    draft = _raw_draft(game, form)
    try:
        counts = _parsed_counts(game, form)
        if replace:
            store.replace_last_round(game_id, counts, owner_id=user)
        else:
            store.record_round(game_id, counts, owner_id=user)
    except NotFoundError:
        return _missing(request)
    except ForbiddenError as exc:
        return _game_page(request, game, error=exc.message, draft=draft, status_code=403)
    except ScoringError as exc:
        fresh = store.get_game(game_id, user)
        if fresh is None:
            return _missing(request)
        return _game_page(
            request,
            fresh,
            error=exc.message,
            draft=draft,
            editing=replace and _is_owner(fresh, user),
            status_code=400,
        )
    return RedirectResponse(url=f"/games/{game_id}", status_code=303)


app = create_app()
