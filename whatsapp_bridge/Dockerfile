ARG BUILD_FROM=alpine:3.21

# ---------- build stage: compile the Go bridge ----------
FROM golang:1.24-alpine AS build

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

# The bridge only renders the pairing QR as half-block characters, which the
# Home Assistant log panel mangles. Also print the raw code so it can be turned
# into a QR elsewhere. Harmless no-op if upstream changes this line.
RUN sed -i 's|qrterminal.GenerateHalfBlock(evt.Code, qrterminal.L, os.Stdout)|fmt.Println("QR_CODE_RAW:", evt.Code); \&|' main.go

RUN CGO_ENABLED=1 go build -trimpath -ldflags "-s -w" -o /out/whatsapp-bridge .

# ---------- runtime stage ----------
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 jq sqlite ca-certificates tzdata

COPY --from=build /out/whatsapp-bridge /usr/local/bin/whatsapp-bridge
COPY unanswered_api.py unanswered.sql /opt/unanswered/
COPY run.sh /run.sh
RUN chmod +x /run.sh

# /data is the Supervisor-managed persistent volume; the bridge writes ./store
WORKDIR /data

CMD ["/run.sh"]
