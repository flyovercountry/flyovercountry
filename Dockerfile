FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml requirements.lock ./

# Hash-verified dependencies in their own layer, before backend/ is copied,
# so a code edit does not reinstall them.
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY backend/ backend/
COPY backend/network/entrypoint.sh /entrypoint.sh

# The project itself, without re-resolving the dependencies above.
RUN pip install --no-cache-dir --no-deps .

# Unprivileged runtime user at the host user's uid/gid (README, Container users).
ARG DOCKER_UID=1000
ARG DOCKER_GID=1000
RUN groupadd -g ${DOCKER_GID} docker && useradd -u ${DOCKER_UID} -g ${DOCKER_GID} -m docker

ENTRYPOINT ["/entrypoint.sh"]
