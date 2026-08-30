# WhatsApp Bridge - Home Assistant local add-on

Runs [lharries/whatsapp-mcp](https://github.com/lharries/whatsapp-mcp)'s Go bridge
on the Pi (always on, so it never misses messages) plus a small read-only HTTP API
that answers one question for n8n: **which chats am I still owing a reply?**

The MCP server itself is deliberately *not* included - it speaks stdio and is meant
for a chat client. n8n gets JSON over HTTP instead.

## What runs inside

| Process | Port | Purpose |
|---|---|---|
| `whatsapp-bridge` (Go) | 8080 | WhatsApp Web session, writes `/data/store/messages.db`, REST API for sending. **Unauthenticated - not exposed by default.** |
| `unanswered_api.py` | 8081 | Read-only view on that database. Bearer token. This is what n8n calls. |

## Install

1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories** → add
   `https://github.com/klangenk/ha-addons`. (On Home Assistant OS there is no host
   shell, so the alternative is copying this folder to `/addons/whatsapp_bridge/`
   through the Samba share or SSH add-on.)
2. Install **WhatsApp Bridge** from the new repository section. The first build
   compiles whatsmeow from source with CGO - expect **10-20 minutes on a Pi 4**,
   and the Pi needs internet.
3. Configuration tab → set `api_token` to a long random string. Save.
4. Start, then open the **Log** tab.

Updates need a bumped `version:` in `config.yaml`; without one the Supervisor does
not notice a push.

## First start: pairing

The log prints a QR code. If the block characters render cleanly, scan it with
WhatsApp → Settings → Linked devices. If the log panel mangles them, look for the
line `QR_CODE_RAW: 2@abc...` and turn that string into a QR code yourself
(e.g. `qrencode -t ANSI "2@abc..."` on any machine).

The code rotates every ~20 seconds, so grab a fresh one if the first attempt fails.
After pairing, history sync takes several minutes on a busy account.

**WhatsApp expires the linked-device session after roughly 20 days** - you will have
to re-scan. The add-on log is where that shows up. If history ever gets out of sync,
stop the add-on, delete `/data/store/messages.db` and `whatsapp.db`, and start over.

## Options

| Option | Default | Meaning |
|---|---|---|
| `api_token` | *(empty)* | Bearer token for :8081. Empty = no auth (logged as a warning). |
| `grace_hours` | `4` | Ignore messages newer than this - you may still be typing. |
| `lookback_days` | `14` | Ignore anything older; long-dead chats are not todos. |
| `include_groups` | `false` | Groups almost always end on someone else's message. |

## API

```
GET /health
GET /api/unanswered?hours=4&days=14&context=15&groups=0
Authorization: Bearer <api_token>
```

Query parameters override the add-on options per call. `context` is how many recent
messages of each chat to include - the LLM stage needs the window, not just the last
line. Returns:

```json
[{
  "jid": "4915...@s.whatsapp.net",
  "chat_name": "Sarah",
  "last_message_id": "3EB0...",
  "waiting_since": "2026-08-29 14:42:43.351+02:00",
  "hours_waiting": 26.0,
  "last_message": "Und um wieviel Uhr?",
  "messages": [{"from": "them", "at": "...", "text": "Hey, passt Samstag?"}]
}]
```

`503` before the first pairing (no database yet) - that is expected, not a failure.

## Calling it from n8n

Both add-ons sit on the same Docker network, so n8n reaches this one by hostname -
nothing has to be published to the LAN, which is why `8081/tcp` maps to `null`
by default.

The hostname is the add-on's **full slug with underscores turned into dashes**,
and that slug carries a prefix identifying where the add-on came from:

| Installed from | Slug | Hostname |
|---|---|---|
| this repository | `<repo-hash>_whatsapp_bridge` | `<repo-hash>-whatsapp-bridge` |
| a folder in `/addons` | `local_whatsapp_bridge` | `local-whatsapp-bridge` |

The repo hash is stable per repository. Read it off any Supervisor log line about
this add-on, or off the URL of its page in the UI:

```
http://<repo-hash>-whatsapp-bridge:8081/api/unanswered?hours=4&context=15
```

Authenticate with an n8n **Header Auth** credential sending
`Authorization: Bearer <api_token>`.

To reach the API from a browser instead, set a free host port under
**Configuration -> Network** (8081 itself is often taken) and use
`http://<pi-ip>:<that port>/...`.

## Upstream, and what is patched

Upstream has had no commit since 2025-07 and pins whatsmeow from 2025-03. That
whatsmeow announces a WhatsApp Web client version the servers reject on sight:

```
[Client ERROR] Client outdated (405) connect failure (client version: 2.3000.1021018791)
```

So the build pins a current whatsmeow (`WHATSMEOW_REF`) instead, and
`patch_upstream.sh` adapts the old sources to it. Five calls gained a
`context.Context` first parameter in the meantime:

| Call | Now |
|---|---|
| `sqlstore.New(...)` | `sqlstore.New(ctx, ...)` |
| `container.GetFirstDevice()` | `GetFirstDevice(ctx)` |
| `client.Download(msg)` | `Download(ctx, msg)` |
| `client.GetGroupInfo(jid)` | `GetGroupInfo(ctx, jid)` |
| `Store.Contacts.GetContact(jid)` | `GetContact(ctx, jid)` |

The same script also makes the pairing QR readable in the HA log panel. It checks
that every substitution actually changed the file, so if either pin moves the build
fails with the patch name rather than shipping something subtly broken.

The Go builder is `golang:1.27-alpine` because current whatsmeow declares
`go 1.26` / `toolchain go1.27` - an older builder would pull a toolchain mid-build
on the Pi.

## Base image

This add-on builds on plain Alpine, not on a Home Assistant base image: the HA
bases ship s6-overlay, which would mean `init: false` plus an `s6-rc.d` service
tree for what is two processes. Alpine plus `init: true` lets the Supervisor
provide PID 1 and `run.sh` supervise both children.

One trap when changing `build.yaml`: the Supervisor validates `build_from`
against a regex that demands **at least two slash-separated path components**.
A bare `alpine:3.21` does not match, and the failure is silent - the whole build
config is discarded and the add-on falls back to the HA base image. The
s6-overlay in that image then refuses to start under `init: true`:

```
s6-overlay-suexec: fatal: can only run as pid 1
```

So always write the image fully qualified (`docker.io/library/alpine:3.21`).
The runtime stage of the Dockerfile checks for `/init` and fails the build
loudly if this ever regresses.

## Security notes

- Port 8080 is set to `null` in `config.yaml`, so the bridge's send/download REST API
  is not reachable from the LAN. It has no authentication whatsoever - only open it
  if you deliberately want n8n to *send* WhatsApp messages, and understand that
  anything on your network could then do the same.
- `/data/store/messages.db` is a plaintext copy of your entire WhatsApp history.
  It lives in the Supervisor's add-on volume and is included in Home Assistant
  backups. Consider excluding this add-on from cloud-uploaded backups.
- The upstream repo is pinned by commit in the `Dockerfile` (`WA_MCP_REF`), so a
  rebuild cannot silently pull new code. Bump it deliberately.
