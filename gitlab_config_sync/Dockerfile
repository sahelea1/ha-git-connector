ARG BUILD_FROM=ghcr.io/hassio-addons/base:20.1.1
# hadolint ignore=DL3006
FROM ${BUILD_FROM}

# Keep Python predictable and quiet, and allow pip in Alpine's managed env.
ENV LANG="C.UTF-8" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONPATH="/usr/src/gitsync"

# System dependencies: git + ssh client for repository access, Python runtime.
# hadolint ignore=DL3018
RUN apk add --no-cache \
        git \
        openssh-client \
        python3 \
        py3-pip

# Python dependencies.
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

# s6 service definitions and the application itself.
COPY rootfs /
COPY app /usr/src/gitsync/gitsync

# Build metadata.
ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION="Back up and restore Home Assistant configuration with a private GitLab repository"
ARG BUILD_NAME="GitLab Config Sync"
ARG BUILD_REF
ARG BUILD_REPOSITORY
ARG BUILD_VERSION

LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version="${BUILD_VERSION}" \
    maintainer="sahelea1" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.source="https://github.com/sahelea1/ha-git-connector" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.created="${BUILD_DATE}" \
    org.opencontainers.image.revision="${BUILD_REF}" \
    org.opencontainers.image.version="${BUILD_VERSION}"
