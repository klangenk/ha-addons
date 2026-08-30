ARG BUILD_FROM=docker.io/library/alpine:3.21

# ---------- build stage: compile the Go bridge ----------
# 1.27 because current whatsmeow declares go 1.26 / toolchain go1.27; an
# older builder would download a toolchain mid-build on the Pi.
FROM golang:1.27-alpine AS build

# Pinned so a rebuild produces the same binary. Bump deliberately.
ARG WA_MCP_REF=7d6a06dcdce1f01dfb24f60e1030d5efba9f3b88

# build-base: go-sqlite3 needs CGO, so gcc + musl headers are required
RUN apk add --no-cache git build-base

WORKDIR /src
RUN git init -q . \
 && git remote add origin https://github.com/lharries/whatsapp-mcp.git \
 && git fetch -q --depth 1 origin "${WA_MCP_REF}" \
 && git checkout -q FETCH_HEAD

WORKDIR /src/whatsapp-bridge

# Upstream pins whatsmeow from 2025-03 and has seen no commit since 2025-07.
# That whatsmeow announces a WhatsApp Web client version the servers now reject
# outright ("Client outdated (405) connect failure"), so pull a current one -
# pinned, like everything else here, so a rebuild stays reproducible.
ARG WHATSMEOW_REF=v0.0.0-20260828224850-0fadda796019
RUN go get go.mau.fi/whatsmeow@${WHATSMEOW_REF} && go mod tidy

# Adapts the 2025-03 upstream sources to that whatsmeow (five calls gained a
# context parameter) and prints the pairing QR in a form the HA log survives.
# The script verifies every substitution and fails the build if one misses.
COPY patch_upstream.sh /tmp/patch_upstream.sh
RUN sh /tmp/patch_upstream.sh main.go

RUN CGO_ENABLED=1 go build -trimpath -ldflags "-s -w" -o /out/whatsapp-bridge .

# ---------- runtime stage ----------
FROM ${BUILD_FROM}

# Guard: if build.yaml was rejected by the Supervisor, BUILD_FROM silently
# becomes the Home Assistant base image, whose s6-overlay entrypoint cannot
# run under init: true. Fail here with a readable message rather than ship a
# container that dies with "s6-overlay-suexec: fatal: can only run as pid 1".
RUN if [ -e /init ]; then echo "ERROR: base image ships s6-overlay; build.yaml was not applied" >&2; exit 1; fi

RUN apk add --no-cache python3 jq sqlite ca-certificates tzdata

COPY --from=build /out/whatsapp-bridge /usr/local/bin/whatsapp-bridge
COPY unanswered_api.py unanswered.sql /opt/unanswered/
COPY run.sh /run.sh
RUN chmod +x /run.sh

# /data is the Supervisor-managed persistent volume; the bridge writes ./store
WORKDIR /data

CMD ["/run.sh"]
