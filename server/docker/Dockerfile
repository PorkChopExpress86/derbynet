# syntax=docker/dockerfile:1.7
#
# The DerbyNet server image (#58): nginx on 80 and 443 in front of gunicorn,
# with every install's data on the /var/lib/derbynet volume.  The build
# context is the repository root:
#
#   docker buildx build -f server/docker/Dockerfile -t derbynet_server .
#
# server/docker/README.md says how to run it.  Dockerfile.dockerignore, next to
# this file, keeps the context to what the stages copy.

ARG UBUNTU=ubuntu:26.04@sha256:065aa8626841f16057b78b027b464cd59c4fac36c204c72dcbf48d66f5fa8a96
ARG UV=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff

FROM ${UV} AS uv

# The Python environment, for the platform being built.  Wheels only
# (--no-build), so no compiler is ever installed; a package with no wheel for
# the platform fails the build here.  The `serve` group adds gunicorn, and the
# dev and browser groups stay out.
FROM ${UBUNTU} AS venv
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON=/usr/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/derbynet/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
WORKDIR /src/server
COPY server/pyproject.toml server/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --group serve --no-build

# The static files, once, on the build machine's own platform: they are the
# same on every platform, so they are never built under QEMU.  The Tailwind
# standalone CLI builds app.css (#40), build_timer writes the browser timer and
# its profiles.js (#50), with a JDK for the timer's Java sources, and
# collectstatic gathers everything into /opt/derbynet/static.  Throwaway
# folders and secret key, so nothing of this stage's settings lands in the
# image.
FROM --platform=$BUILDPLATFORM ${UBUNTU} AS static
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 ca-certificates \
      openjdk-21-jdk-headless \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON=/usr/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/derbynet/venv \
    UV_LINK_MODE=copy
WORKDIR /src/server
COPY server/pyproject.toml server/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --no-build
COPY templates/timer.html templates/timer-profiles.js /src/templates/
COPY timer/src /src/timer/src
COPY timer/derbynet-timer/src /src/timer/derbynet-timer/src
COPY lib/jssc.jar lib/Java-WebSocket-1.5.2.jar lib/json-20210307.jar /src/lib/
COPY server/ ./
ENV DJANGO_SECRET_KEY=static-stage-only \
    DERBYNET_DATA_DIR=/tmp/static-stage/data \
    DERBYNET_CONFIG_DIR=/tmp/static-stage/config \
    DERBYNET_STATIC_ROOT=/opt/derbynet/static
RUN python3 -I ui/css_build.py fetch \
 && python3 -I ui/css_build.py build \
 && /opt/derbynet/venv/bin/python manage.py build_timer \
 && /opt/derbynet/venv/bin/python manage.py collectstatic --noinput --verbosity 0

FROM ${UBUNTU}
# tzdata for ZoneInfo, openssl for the first start's certificate, curl for the
# health check, tini to reap and to pass signals on.  No compiler.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 nginx openssl tzdata \
      tini curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/* \
 && useradd --system --user-group --home-dir /var/lib/derbynet --no-create-home \
      --shell /usr/sbin/nologin derbynet \
 && usermod -aG derbynet www-data
COPY --from=venv /opt/derbynet/venv /opt/derbynet/venv
COPY --from=static /opt/derbynet/static /opt/derbynet/static
# nginx serves every static file from /opt/derbynet/static, so the server's
# own static folders, which a dev bind mount replaces, are never read.
COPY server/ /opt/derbynet/server/
COPY server/docker/entrypoint.sh /opt/derbynet/entrypoint.sh
# Fails the build when a platform's wheel or a library it needs is missing.
RUN /opt/derbynet/venv/bin/python -c "import cv2, PIL, psycopg, gunicorn, django"

ENV PATH=/opt/derbynet/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DERBYNET_CONFIG_DIR=/var/lib/derbynet \
    DERBYNET_DATA_DIR=/var/lib/derbynet \
    DERBYNET_STATIC_ROOT=/opt/derbynet/static \
    TZ=America/New_York \
    MAX_UPLOAD_SIZE=16M \
    DERBYNET_REQUEST_TIMEOUT=30 \
    GUNICORN_WORKERS=4

WORKDIR /opt/derbynet/server
VOLUME /var/lib/derbynet
EXPOSE 80 443

# A 301 to the home page is Django answering (D20); curl fails on a 5xx or
# no answer.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -fsS -o /dev/null http://127.0.0.1/index.php || exit 1

ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/opt/derbynet/entrypoint.sh"]
