"""User-facing errors for rules and missing games."""


class AppError(Exception):
    """An error whose message can be shown on the page."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ScoringError(AppError):
    """A score, name, or target broke the rules of the game."""


class NotFoundError(AppError):
    """The requested game does not exist, or this account cannot see it."""


class ForbiddenError(AppError):
    """The account can see the game but cannot do this."""


class AuthError(AppError):
    """The request has no usable Clerk session."""
