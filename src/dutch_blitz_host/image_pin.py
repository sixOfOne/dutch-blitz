"""Pick a published scorekeeper image for the Game night workflow.

Terraform accepts ``ghcr.io/sixofone/dutch-blitz:sha-<commit>`` or a
sha256 digest. It rejects ``latest`` and any other moving tag. The
Image workflow publishes a ``sha-`` tag for each image it pushes, and
a ``main`` tag when that push is from ``refs/heads/main``. A commit
that does not change the image has no ``sha-`` tag. This module uses
the commit tag when its manifest exists, then the revision stored on
the ``main`` tag, then the newest successful Image run on ``main``.
The value it returns is always a ``sha-`` tag or a digest, and only
after that manifest exists.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping

IMAGE = "ghcr.io/sixofone/dutch-blitz"
OWNER_IMAGE = "sixofone/dutch-blitz"
PIN = re.compile(
    r"ghcr\.io/sixofone/dutch-blitz:sha-[0-9a-f]{7,40}"
    r"|ghcr\.io/sixofone/dutch-blitz@sha256:[0-9a-f]{64}"
)
_REVISION = "org.opencontainers.image.revision"
_ACCEPT = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)
_INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}

Opener = Callable[[urllib.request.Request], tuple[int, Mapping[str, str], bytes]]


def resolve_app_image(
    *,
    explicit: str,
    main_sha: str,
    github_repository: str,
    github_token: str = "",
    opener: Opener | None = None,
) -> str:
    """Return a pin whose manifest exists, or stop before Terraform runs."""
    fetch = opener if opener is not None else _urlopen
    chosen = explicit.strip()
    if chosen:
        if PIN.fullmatch(chosen) is None:
            raise SystemExit(
                "app_image must be ghcr.io/sixofone/dutch-blitz:sha-<commit> "
                "or ghcr.io/sixofone/dutch-blitz@sha256:<64 hex digits>."
            )
        token = _ghcr_token(fetch)
        if not _manifest_exists(_reference(chosen), token, fetch):
            raise SystemExit(f"app_image manifest was not found: {chosen}")
        return chosen

    short = _short_sha(main_sha)
    token = _ghcr_token(fetch)
    head = f"{IMAGE}:sha-{short}"
    if _manifest_exists(f"sha-{short}", token, fetch):
        return head

    published = _sha_from_main_tag(token, fetch)
    if published is not None and _manifest_exists(f"sha-{published}", token, fetch):
        return f"{IMAGE}:sha-{published}"

    for sha in _image_run_shas(github_repository, github_token, fetch):
        candidate = sha[:7]
        if _manifest_exists(f"sha-{candidate}", token, fetch):
            return f"{IMAGE}:sha-{candidate}"

    raise SystemExit(
        f"No published {IMAGE} manifest was found for sha-{short}. "
        "The main tag is missing or has no sha- tag, and no successful "
        "Image workflow run on main has a manifest. Run the Image workflow "
        "on main, or pass app_image."
    )


def write_github_output(path: str, image: str) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"app_image={image}\n")


def _short_sha(full: str) -> str:
    cleaned = full.strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", cleaned) is None:
        raise SystemExit("origin/main did not resolve to a commit.")
    return cleaned[:7]


def _reference(image: str) -> str:
    if "@" in image:
        return image.split("@", 1)[1]
    return image.rsplit(":", 1)[1]


def _urlopen(request: urllib.request.Request) -> tuple[int, Mapping[str, str], bytes]:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            headers = {key.lower(): value for key, value in response.headers.items()}
            return response.status, headers, response.read()
    except urllib.error.HTTPError as exc:
        headers = {key.lower(): value for key, value in exc.headers.items()}
        return exc.code, headers, exc.read()
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach ghcr.io or the GitHub API: {exc.reason}") from None


def _ghcr_token(opener: Opener) -> str:
    status, _headers, body = opener(
        urllib.request.Request(
            "https://ghcr.io/token?service=ghcr.io&scope=repository:sixofone/dutch-blitz:pull"
        )
    )
    if status != 200:
        raise SystemExit("Could not authenticate to ghcr.io to check the image manifest.")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SystemExit("ghcr.io returned a token response that was not JSON.") from exc
    token = payload.get("token") or payload.get("access_token") or ""
    if not isinstance(token, str) or not token:
        raise SystemExit("ghcr.io did not return a pull token.")
    return token


def _manifest_exists(reference: str, token: str, opener: Opener) -> bool:
    status, _headers, _body = _get_manifest(reference, token, opener)
    return status == 200


def _get_manifest(
    reference: str, token: str, opener: Opener
) -> tuple[int, Mapping[str, str], bytes]:
    return opener(
        urllib.request.Request(
            f"https://ghcr.io/v2/{OWNER_IMAGE}/manifests/{reference}",
            headers={"Authorization": f"Bearer {token}", "Accept": _ACCEPT},
        )
    )


def _get_blob(digest: str, token: str, opener: Opener) -> tuple[int, bytes]:
    status, _headers, body = opener(
        urllib.request.Request(
            f"https://ghcr.io/v2/{OWNER_IMAGE}/blobs/{digest}",
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    return status, body


def _sha_from_main_tag(token: str, opener: Opener) -> str | None:
    status, _headers, body = _get_manifest("main", token, opener)
    if status == 404:
        return None
    if status != 200:
        return None
    try:
        manifest = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(manifest, dict):
        return None
    manifest = _single_manifest(manifest, token, opener)
    if manifest is None:
        return None
    config = manifest.get("config")
    digest = config.get("digest") if isinstance(config, dict) else None
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        return None
    blob_status, blob = _get_blob(digest, token, opener)
    if blob_status != 200:
        return None
    try:
        image_config = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(image_config, dict):
        return None
    nested = image_config.get("config")
    labels = nested.get("Labels") if isinstance(nested, dict) else None
    revision = labels.get(_REVISION) if isinstance(labels, dict) else None
    if not isinstance(revision, str):
        return None
    cleaned = revision.strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", cleaned) is None:
        return None
    return cleaned[:7]


def _single_manifest(
    manifest: dict[str, object], token: str, opener: Opener
) -> dict[str, object] | None:
    media = manifest.get("mediaType")
    manifests = manifest.get("manifests")
    if media in _INDEX_TYPES or (isinstance(manifests, list) and "layers" not in manifest):
        digest = _linux_amd64_digest(manifests)
        if digest is None:
            return None
        status, _headers, body = _get_manifest(digest, token, opener)
        if status != 200:
            return None
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed
    return manifest


def _linux_amd64_digest(manifests: object) -> str | None:
    if not isinstance(manifests, list):
        return None
    fallback = ""
    for item in manifests:
        if not isinstance(item, dict):
            continue
        digest = item.get("digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            continue
        platform = item.get("platform")
        if (
            isinstance(platform, dict)
            and platform.get("os") == "linux"
            and platform.get("architecture") == "amd64"
        ):
            return digest
        if not fallback:
            fallback = digest
    return fallback or None


def _image_run_shas(repository: str, token: str, opener: Opener) -> list[str]:
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        raise SystemExit("GITHUB_REPOSITORY is not an owner/name.")
    url = (
        f"https://api.github.com/repos/{repository}/actions/workflows/image.yml/runs"
        "?branch=main&status=success&per_page=20"
    )
    status, body = _github_json(url, token, opener)
    if status != 200:
        return []
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return []
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        return []
    shas: list[str] = []
    for run in runs:
        if not isinstance(run, dict) or run.get("event") == "pull_request":
            continue
        sha = run.get("head_sha")
        if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha):
            shas.append(sha)
    return shas


def _github_json(url: str, token: str, opener: Opener) -> tuple[int, bytes]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "dutch-blitz-game-night",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, _response_headers, body = opener(urllib.request.Request(url, headers=headers))
    if status in {401, 403} and token:
        headers.pop("Authorization")
        status, _response_headers, body = opener(urllib.request.Request(url, headers=headers))
    return status, body
