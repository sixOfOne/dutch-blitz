"""Run the score keeper: ``python -m app``."""

import uvicorn

from app.config import host, port


def main() -> None:
    uvicorn.run("app.main:app", host=host(), port=port())


if __name__ == "__main__":
    main()
