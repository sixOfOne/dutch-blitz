"""The Game night image pin is a manifest that exists. No registry calls."""

import json
import urllib.request
from collections.abc import Mapping

import pytest

from dutch_blitz_host.image_pin import PIN, resolve_app_image

HEAD = "971efce6b5a8a708468ffb55dd15c71d019e4666"
OLDER = "746f0a230fb5eb6641f9f54932e48ce4988038ea"
NEWER = "39cd8fcc8bf932c1a55446d162277773c4620c66"
IMAGE = "ghcr.io/sixofone/dutch-blitz"
TOKEN_URL = "https://ghcr.io/token?service=ghcr.io&scope=repository:sixofone/dutch-blitz:pull"
RUNS_URL = (
    "https://api.github.com/repos/sixOfOne/dutch-blitz/actions/workflows/image.yml/runs"
    "?branch=main&status=success&per_page=20"
)


class _Registry:
    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, bytes]] = {}
        self.calls: list[str] = []

    def add(self, url: str, status: int, body: object) -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.routes[url] = (status, raw)

    def __call__(self, request: urllib.request.Request) -> tuple[int, Mapping[str, str], bytes]:
        self.calls.append(request.full_url)
        status, body = self.routes.get(request.full_url, (404, b""))
        return status, {}, body


def _manifest(revision: str | None = None) -> dict[str, object]:
    return {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": "sha256:" + "ab" * 32},
        "layers": [],
    }


def _config(revision: str) -> dict[str, object]:
    return {"config": {"Labels": {"org.opencontainers.image.revision": revision}}}


def _ready(registry: _Registry) -> None:
    registry.add(TOKEN_URL, 200, {"token": "pull-token"})


def test_current_commit_tag_is_used_when_its_manifest_exists():
    registry = _Registry()
    _ready(registry)
    registry.add(
        f"https://ghcr.io/v2/sixofone/dutch-blitz/manifests/sha-{HEAD[:7]}", 200, _manifest()
    )
    image = resolve_app_image(
        explicit="",
        main_sha=HEAD,
        github_repository="sixOfOne/dutch-blitz",
        opener=registry,
    )
    assert image == f"{IMAGE}:sha-{HEAD[:7]}"
    assert RUNS_URL not in registry.calls
    assert PIN.fullmatch(image)


def test_main_tag_revision_is_used_when_the_commit_tag_is_missing():
    registry = _Registry()
    _ready(registry)
    registry.add(
        f"https://ghcr.io/v2/sixofone/dutch-blitz/manifests/sha-{OLDER[:7]}", 200, _manifest()
    )
    registry.add("https://ghcr.io/v2/sixofone/dutch-blitz/manifests/main", 200, _manifest())
    registry.add(
        "https://ghcr.io/v2/sixofone/dutch-blitz/blobs/sha256:" + "ab" * 32,
        200,
        _config(OLDER),
    )
    image = resolve_app_image(
        explicit="",
        main_sha=HEAD,
        github_repository="sixOfOne/dutch-blitz",
        opener=registry,
    )
    assert image == f"{IMAGE}:sha-{OLDER[:7]}"
    assert ":main" not in image
    assert ":latest" not in image
    assert RUNS_URL not in registry.calls


def test_newest_image_workflow_run_is_used_when_the_main_tag_is_missing():
    registry = _Registry()
    _ready(registry)
    registry.add(
        RUNS_URL,
        200,
        {
            "workflow_runs": [
                {"event": "pull_request", "head_sha": "a" * 40},
                {"event": "push", "head_sha": NEWER},
                {"event": "push", "head_sha": OLDER},
            ]
        },
    )
    registry.add(
        f"https://ghcr.io/v2/sixofone/dutch-blitz/manifests/sha-{NEWER[:7]}", 200, _manifest()
    )
    image = resolve_app_image(
        explicit="",
        main_sha=HEAD,
        github_repository="sixOfOne/dutch-blitz",
        github_token="ghs_test",
        opener=registry,
    )
    assert image == f"{IMAGE}:sha-{NEWER[:7]}"
    assert all("Bearer ghs_test" not in call for call in registry.calls)


def test_a_missing_newer_run_falls_through_to_the_next_manifest():
    registry = _Registry()
    _ready(registry)
    registry.add(
        RUNS_URL,
        200,
        {
            "workflow_runs": [
                {"event": "push", "head_sha": NEWER},
                {"event": "push", "head_sha": OLDER},
            ]
        },
    )
    registry.add(
        f"https://ghcr.io/v2/sixofone/dutch-blitz/manifests/sha-{OLDER[:7]}", 200, _manifest()
    )
    image = resolve_app_image(
        explicit="",
        main_sha=HEAD,
        github_repository="sixOfOne/dutch-blitz",
        opener=registry,
    )
    assert image == f"{IMAGE}:sha-{OLDER[:7]}"


def test_explicit_pin_is_checked_and_not_replaced():
    registry = _Registry()
    _ready(registry)
    digest = "sha256:" + "cd" * 32
    chosen = f"{IMAGE}@{digest}"
    registry.add(f"https://ghcr.io/v2/sixofone/dutch-blitz/manifests/{digest}", 200, _manifest())
    assert (
        resolve_app_image(
            explicit=chosen,
            main_sha=HEAD,
            github_repository="sixOfOne/dutch-blitz",
            opener=registry,
        )
        == chosen
    )
    assert RUNS_URL not in registry.calls


def test_explicit_pin_without_a_manifest_stops_before_apply():
    registry = _Registry()
    _ready(registry)
    missing = f"{IMAGE}:sha-{HEAD[:7]}"
    with pytest.raises(SystemExit, match="manifest was not found"):
        resolve_app_image(
            explicit=missing,
            main_sha=OLDER,
            github_repository="sixOfOne/dutch-blitz",
            opener=registry,
        )


def test_latest_and_the_main_tag_are_rejected_as_an_override():
    registry = _Registry()
    _ready(registry)
    for bad in (f"{IMAGE}:latest", f"{IMAGE}:main"):
        with pytest.raises(SystemExit, match="sha-<commit>"):
            resolve_app_image(
                explicit=bad,
                main_sha=HEAD,
                github_repository="sixOfOne/dutch-blitz",
                opener=registry,
            )


def test_no_published_manifest_stops_before_apply():
    registry = _Registry()
    _ready(registry)
    registry.add(RUNS_URL, 200, {"workflow_runs": []})
    with pytest.raises(SystemExit, match="No published"):
        resolve_app_image(
            explicit="",
            main_sha=HEAD,
            github_repository="sixOfOne/dutch-blitz",
            opener=registry,
        )


def test_an_index_uses_the_linux_amd64_revision():
    registry = _Registry()
    _ready(registry)
    registry.add(
        "https://ghcr.io/v2/sixofone/dutch-blitz/manifests/main",
        200,
        {
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "digest": "sha256:" + "11" * 32,
                    "platform": {"os": "linux", "architecture": "arm64"},
                },
                {
                    "digest": "sha256:" + "22" * 32,
                    "platform": {"os": "linux", "architecture": "amd64"},
                },
            ],
        },
    )
    registry.add(
        "https://ghcr.io/v2/sixofone/dutch-blitz/manifests/sha256:" + "22" * 32,
        200,
        {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": "sha256:" + "33" * 32},
            "layers": [],
        },
    )
    registry.add(
        "https://ghcr.io/v2/sixofone/dutch-blitz/blobs/sha256:" + "33" * 32,
        200,
        _config(OLDER),
    )
    registry.add(
        f"https://ghcr.io/v2/sixofone/dutch-blitz/manifests/sha-{OLDER[:7]}", 200, _manifest()
    )
    image = resolve_app_image(
        explicit="",
        main_sha=HEAD,
        github_repository="sixOfOne/dutch-blitz",
        opener=registry,
    )
    assert image == f"{IMAGE}:sha-{OLDER[:7]}"
    assert "https://ghcr.io/v2/sixofone/dutch-blitz/manifests/sha256:" + "22" * 32 in registry.calls
