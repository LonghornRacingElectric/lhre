# Dev image for the VCU SIL (README.md): Bazelisk builds the firmware library,
# Python and fmpy step the car model. run.sh mounts the repo read-only; the
# image holds tools only.
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852

ARG TARGETARCH
ARG BAZELISK_VERSION=v1.30.0
ARG BAZELISK_SHA256_arm64=b2bcc040c213b44586b121bda6d983c35df121b917c3a7a66fb16f2fa0df1a4f
ARG BAZELISK_SHA256_amd64=a248ae2b2b62b5baebb748f6fba7203bff1a27e5d01b55399a8406744cb539e8

# git: Bazel fetches git_override modules, and the workspace status stamp
# reads the checkout.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git python3 python3-venv \
 && rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    case "$TARGETARCH" in \
      arm64) sha="$BAZELISK_SHA256_arm64" ;; \
      amd64) sha="$BAZELISK_SHA256_amd64" ;; \
      *) echo "unsupported architecture $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/bazel \
      "https://github.com/bazelbuild/bazelisk/releases/download/${BAZELISK_VERSION}/bazelisk-linux-${TARGETARCH}"; \
    echo "${sha}  /usr/local/bin/bazel" | sha256sum -c -; \
    chmod +x /usr/local/bin/bazel

COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

# Run as the host user so files written to mounted directories belong to
# them. The image's own user may already hold that UID, and the host's GID
# may already be a system group (20 is dialout here, staff on macOS). Root
# (sudo run.sh) keeps root and gets a second name with the same UID.
ARG UID=1000
ARG GID=1000
RUN set -eu; \
    if [ "$UID" = 0 ]; then \
      useradd -o -m -u 0 -g 0 dev; \
    else \
      existing="$(getent passwd "$UID" | cut -d: -f1 || true)"; \
      if [ -n "$existing" ]; then userdel -r "$existing" 2>/dev/null || userdel "$existing"; fi; \
      getent group "$GID" >/dev/null || groupadd -g "$GID" dev; \
      useradd -m -u "$UID" -g "$GID" dev; \
    fi; \
    mkdir -p /home/dev/.cache/bazel /home/dev/.cache/bazelisk; \
    chown -R "$UID:$GID" /home/dev/.cache; \
    git config --system --add safe.directory /work/lhre

USER dev
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /work/lhre
