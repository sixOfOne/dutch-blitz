# Image for the game-night scorekeeper.
#
# GitHub Actions builds this and pushes it to ghcr.io/sixofone/dutch-blitz.
# The EC2 host does not install Python packages itself. It pulls this image.
#
# The tag and the digest are one pin. 3.11-slim is the release line (the
# same Python the host tools use). The @sha256 digest is the immutable
# snapshot Dependabot bumps. A floating tag alone would let a rebuilt
# base image change what we ship without a commit.
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# The virtualenv is the only thing copied into the runtime stage, so the
# runtime image does not need a compiler or pip's download cache.
RUN python -m venv /opt/venv
COPY requirements.txt .
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt \
    && /opt/venv/bin/pip uninstall -y pip setuptools wheel

FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

# Fixed ids so the host can chown the score directory to the same user.
# 10001 is not a login account. The host does not create a matching user.
RUN groupadd --system --gid 10001 dutchblitz \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin dutchblitz

COPY --from=build /opt/venv /opt/venv

WORKDIR /opt/dutch-blitz
COPY app ./app
# Host backup and idle shutdown import this package. Cloud-init copies it
# out of the image onto the host, where the AWS CLI and shutdown live.
COPY src/dutch_blitz_host ./hostpkg/dutch_blitz_host

RUN chmod -R a+rX /opt/venv /opt/dutch-blitz

# 0.0.0.0 is inside the container network namespace. The host publishes
# that port only on 127.0.0.1, and Caddy is the public listener.
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/dutch-blitz/hostpkg \
    DUTCH_BLITZ_HOST=0.0.0.0 \
    DUTCH_BLITZ_PORT=8000

# Links the GHCR package to this repository when Actions publishes it.
LABEL org.opencontainers.image.source="https://github.com/sixOfOne/dutch-blitz" \
      org.opencontainers.image.title="dutch-blitz" \
      org.opencontainers.image.description="Dutch Blitz scorekeeper"

USER dutchblitz

EXPOSE 8000

# TCP only. An HTTP request would run the app middleware, which writes
# /var/lib/dutch-blitz/last-activity and would reset the idle shutdown.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import socket; s=socket.create_connection(('127.0.0.1', 8000), 2); s.close()"]

CMD ["python", "-m", "app"]
