"""User-facing errors for rules and missing games."""


class AppError(Exception):
    """An error whose message can be shown on the page."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ScoringError(AppError):
    """A score, name, or target broke the rules of the game."""


class NotFoundError(AppError):
    """The requested game does not exist."""
